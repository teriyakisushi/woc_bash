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
# 常见密钥写法：password=...、token: ...、api_key=...
_SECRET_RE = re.compile(
    r"(?i)\b(password|passwd|passphrase|secret|token|api[_-]?key)\b"
    r"(\s*[=:]\s*)(\S+)")


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
    """捕获内容发给模型前，抹掉常见密钥字段的值。"""
    return _SECRET_RE.sub(r"\1\2***", text)


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

