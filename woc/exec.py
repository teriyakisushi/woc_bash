import re
import subprocess

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

