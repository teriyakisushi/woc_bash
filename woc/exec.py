import re
import shlex
import shutil
import subprocess
import sys

from .sanitize import clean_text

SUDO_RE = re.compile(r"(?:^|[;&|]\s*|\s)(?:sudo|doas)\s")


def needs_privilege(cmd):
    """命令是否显式调用 sudo/doas（需要密码，或已有可用的 sudo ticket）。"""
    return bool(SUDO_RE.search(cmd))


def sudo_ticket_valid():
    """探测当前是否已有 sudo ticket；用 -n 保证绝不弹密码。"""
    try:
        proc = subprocess.run(
            ["sudo", "-n", "true"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return proc.returncode == 0


def run_command_foreground(cmd):
    """在前台执行命令，让 sudo 直接接管终端。

    输出不捕获、不反馈给模型：root 权限能读到的内容不该因此进入 prompt。
    返回 (exit_code, error)。
    """
    try:
        return subprocess.run(["bash", "-c", cmd]).returncode, None
    except KeyboardInterrupt:
        return 130, None
    except OSError as exc:
        return 1, f"执行失败: {exc}"


def format_foreground_result(code, error=None):
    note = error or "命令在终端前台交互执行，输出未捕获（sudo 密码由用户在终端直接输入）。"
    return (
        "<exec_result>\n"
        f"exit_code: {code}\n"
        "stdout:\n"
        f"{clean_text(note)}\n"
        "stderr:\n\n"
        "</exec_result>"
    )


# ---------- pty 捕获（sudo 需要密码时） ----------
PTY_CAPTURE_LIMIT = 200_000  # 内部缓冲上限，避免超大输出吃内存
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# 常见密钥写法：password=...、token: ...、api_key=...，以及名字里**带**关键词的
# 字段（`SECRET_KEY` / `AWS_SECRET_ACCESS_KEY` / `GITHUB_TOKEN` / `DB_PASSWD`）。
# 旧版用 `\b(password|...)` 精确匹配字段名，`AWS_SECRET_ACCESS_KEY=` 这类整条漏掉。
_SECRET_WORDS = (
    "password", "passwd", "passphrase", "secret", "token", "apikey",
    "api_key", "api-key", "access_key", "accesskey", "private_key",
    "credential", "authorization", "session_key",
)
_SECRET_NAME = r"[A-Za-z_][A-Za-z0-9_.-]{0,63}"
_SECRET_PAIR_RE = re.compile(
    r"(?P<name>" + _SECRET_NAME + r")(?P<sep>\s*[=:]\s*)(?P<value>\S{1,4096})")

# `Authorization: Bearer abc` / `-H "token abc"`：值本身是 "Bearer"，真正的密钥
# 在下一个词里，所以先把整个凭据吃掉。必须排在字段名规则之前。
_BEARER_RE = re.compile(r"(?i)\b(bearer|basic|digest|token)\s+[A-Za-z0-9._~+/=-]{6,}")

# PEM 私钥整块（`BEGIN RSA PRIVATE KEY` 里的内容同样是密钥）。
_PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----.*?-----END [A-Z0-9 ]*PRIVATE KEY-----",
    re.S)

# `systemctl show -p Environment` / `Environment=` 行：值里是一整串 KEY=VALUE。
_ENVIRONMENT_LINE_RE = re.compile(r"(?im)^(?P<name>Environment)\s*[=:]\s*(?P<value>.+)$")


def pty_capture_available():
    return shutil.which("script") is not None


def _relay(chunk):
    """把 pty 输出原样转发到终端，用户才能看到 sudo 的密码提示。"""
    stream = getattr(sys, "stdout", None)
    buffer = getattr(stream, "buffer", None)
    try:
        if buffer is not None:
            buffer.write(chunk)
            buffer.flush()
        elif stream is not None:
            stream.write(chunk.decode("utf-8", "replace"))
            stream.flush()
    except (AttributeError, OSError, ValueError):
        pass


def _clean_pty_output(text):
    text = _ANSI_RE.sub("", text)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def redact_secrets(text):
    """捕获内容发给模型前，抹掉常见密钥字段的值。

    宁可多抹：字段名里出现关键词就整值替换 —— 漏掉的代价（密钥进 prompt、进会话）
    远大于多抹一个单词的可读性损失。裸 token（没有字段名）不在覆盖范围内：
    没有字段名就没有判据，只能靠 `READ_ONLY` 的参数级复核不让它被执行。
    """
    if not text:
        return text
    text = _PRIVATE_KEY_RE.sub("-----BEGIN PRIVATE KEY-----***-----END PRIVATE KEY-----", text)
    text = _BEARER_RE.sub(lambda m: m.group(1) + " ***", text)

    def _pair(match):
        value = match.group("value")
        # 上面已经处理过 Bearer/Basic，别把占位符再抹一遍。
        if value in ("***", "Bearer", "Basic", "Digest"):
            return match.group(0)
        name = match.group("name")
        lowered = name.lower()
        if any(word in lowered for word in _SECRET_WORDS):
            return name + match.group("sep") + "***"
        return match.group(0)

    text = _SECRET_PAIR_RE.sub(_pair, text)
    return _ENVIRONMENT_LINE_RE.sub(lambda m: m.group("name") + "=***", text)


def run_command_capture_pty(cmd, max_output=8000):
    """在 pty 里执行命令：sudo 能正常要求密码，同时把输出回传给调用方。

    stdout/stderr 由 pty 合并，返回值的 stderr 恒为空。密码由 sudo 自己关闭
    回显，不会进入输出；捕获文本还会做常见密钥字段的脱敏。
    """
    inner = "bash -c " + shlex.quote(cmd)
    try:
        proc = subprocess.Popen(
            ["script", "-q", "-e", "-c", inner, "/dev/null"],
            stdin=None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
    except OSError as exc:
        return "", f"启动 pty 失败: {exc}", 1

    chunks = []
    kept = 0
    code = 1
    try:
        while proc.stdout is not None:
            chunk = proc.stdout.read(4096)
            if not chunk:
                break
            _relay(chunk)
            if kept < PTY_CAPTURE_LIMIT:
                take = chunk[:PTY_CAPTURE_LIMIT - kept]
                chunks.append(take)
                kept += len(take)
        code = proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        code = 130
    finally:
        try:
            if proc.stdout is not None:
                proc.stdout.close()
        except OSError:
            pass

    text = _clean_pty_output(b"".join(chunks).decode("utf-8", "replace"))
    text = redact_secrets(text)
    if len(text) > max_output:
        text = text[:max_output] + f"\n...[truncated {len(text) - max_output} chars]"
    return text, "(stdout/stderr 已由 pty 合并)", code


def syntax_error(cmd):
    """用 `bash -n` 预检语法：只解析、不执行。语法正确返回 None。"""
    try:
        proc = subprocess.run(
            ["bash", "-n", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        # 检查本身不可用时不要阻断正常执行。
        return None
    if proc.returncode == 0:
        return None
    err = proc.stderr.decode("utf-8", "replace").strip()
    return err or f"bash -n 退出码 {proc.returncode}"


# Execution Commands
def run_command_capture(cmd, max_output=8000, timeout=120):
    try:
        proc = subprocess.run(
            ["bash", "-c", cmd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace")
        err = proc.stderr.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired:
        out, err, code = "", f"[woc] 命令超过 {timeout}s 未结束，已终止", 124
    except KeyboardInterrupt:
        out, err, code = "", "[woc] 用户中断 (Ctrl+C)", 130
    except Exception as e:
        out, err, code = "", f"[woc] 执行失败: {e}", 1

    def truncate(s):
        if len(s) <= max_output:
            return s
        return s[:max_output] + f"\n...[truncated {len(s) - max_output} chars]"

    return truncate(out), truncate(err), code


def format_exec_result(out, err, code):
    return (
        "<exec_result>\n"
        f"exit_code: {code}\n"
        "stdout:\n"
        f"{clean_text(out)}\n"
        "stderr:\n"
        f"{clean_text(err)}\n"
        "</exec_result>"
    )

