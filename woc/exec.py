import subprocess

from .sanitize import clean_text

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

