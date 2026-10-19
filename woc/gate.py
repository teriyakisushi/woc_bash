"""危险命令的结构化判定。

旧实现是"整条命令做子串匹配"，会把 printf / grep / man 正文里的 reboot 当成
关机命令。这里改成按 shell 语法看**命令位置**：

1. 按分隔符切分简单命令（尊重引号、转义、$()、反引号、括号）；
2. 剥掉 sudo / env / timeout / nice 等包装器，只看真正的命令名；
3. 只对命令名和关键参数做规则匹配；
4. 对 sh -c / eval / find -exec / xargs 递归检查；
5. 解析失败、或 stdin 被喂给 shell 时，回退到旧的子串规则（fail-closed）。

它仍然不是沙箱：动态拼接、脚本文件内容、编码混淆都可能绕过；人工确认才是边界。
"""

import os
import posixpath
import re
import shlex


# ---------- 旧规则：只在回退路径使用 ----------

_LEGACY_PATTERNS = (
    (r"\brm\s+-rf\s+/(\s|$)", "递归删除根目录"),
    (r"\bmkfs\b", "格式化文件系统"),
    (r"\bdd\s+.*of=/dev/[sh]d", "直接写块设备"),
    (r":\(\)\s*\{.*\};\s*:", "fork 炸弹"),
    (r"\bchmod\s+-R\s+777\s+/(\s|$)", "把根目录权限改成 777"),
    (r">\s*/dev/[sh]d", "直接写块设备"),
    (r"\b(shutdown|reboot|halt|poweroff)\b", "关机 / 重启"),
)

_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_FORK_BOMB_RE = re.compile(r":\(\)\s*\{.*\};\s*:")
_BLOCK_DEVICE_RE = re.compile(
    r"^/dev/(?:sd[a-z]|hd[a-z]|vd[a-z]|xvd[a-z]|nvme\d|mmcblk\d|"
    r"loop\d|md\d|dm-\d|disk/|mapper/|sr\d|mem$|kmem$|port$)"
)
# stdin 直接喂给 shell：内容在命令里看不到，只能保守回退。
_DYNAMIC_SHELL_RE = re.compile(
    r"(?:\||<{1,3})\s*(?:sudo\s+)?(?:ba|z|da|k|fi|c|tc)?sh\b"
    r"|\b(?:ba|z|da|k|fi|c|tc)?sh\s*(?:<<<|<<|-s\b)"
)

_RESERVED = frozenset({
    "if", "then", "else", "elif", "while", "until", "do", "done", "for",
    "case", "esac", "fi", "in", "function", "select", "coproc", "!", "{", "}",
})

_SUDO_SHORT_ARG = frozenset({"-u", "-g", "-p", "-C", "-h", "-r", "-t", "-U",
                             "-T", "-R", "-D", "-P"})
_SUDO_LONG_ARG = frozenset({"--user", "--group", "--prompt", "--close-from",
                            "--host", "--role", "--type", "--other-user",
                            "--command-timeout", "--chroot", "--chdir",
                            "--preserve-groups"})
_ENV_SHORT_ARG = frozenset({"-u", "-C", "-S"})
_ENV_LONG_ARG = frozenset({"--unset", "--chdir", "--split-string"})
_TIMEOUT_SHORT_ARG = frozenset({"-s", "-k"})
_TIMEOUT_LONG_ARG = frozenset({"--signal", "--kill-after"})
_IONICE_ARG = frozenset({"-c", "-n", "-p"})
_XARGS_ARG = frozenset({"-a", "-d", "-E", "-I", "-L", "-n", "-P", "-s"})

_SHELLS = ("sh", "bash", "zsh", "dash", "ksh", "fish", "csh", "tcsh")
# 这些包装器不在白名单里，也没有逐个解析参数：只要参数里出现危险词就保守拦截。
# 代价是 `chroot ... grep reboot` 这类也会误报，但它们很少出现在 woc 的日常命令里。
_CONSERVATIVE_HEADS = frozenset({
    "pkexec", "runuser", "chroot", "systemd-run", "nsenter", "unshare",
    "setpriv", "capsh", "watch", "setsid", "strace", "ltrace", "at", "batch",
})


def _legacy_reason(text):
    for pattern, label in _LEGACY_PATTERNS:
        if re.search(pattern, text):
            return label
    return None


# ---------- 引号/转义处理 ----------

def _strip_quoted(text):
    """把引号内的内容替换成空格，只留下 shell 会当代码执行的部分。"""
    out = []
    quote = None
    escape = False
    for ch in text:
        if escape:
            out.append(" ")
            escape = False
            continue
        if ch == "\\":
            out.append(" ")
            escape = True
            continue
        if quote:
            out.append(" ")
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            out.append(" ")
            continue
        out.append(ch)
    return "".join(out)


def _skip_balanced(text, index):
    """index 指向 '('，返回匹配的 ')' 之后的位置。"""
    depth = 0
    quote = None
    escape = False
    i = index
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return n


def _skip_backtick(text, index):
    i = index + 1
    n = len(text)
    while i < n:
        if text[i] == "\\":
            i += 2
            continue
        if text[i] == "`":
            return i + 1
        i += 1
    return n


def _split_segments(cmd):
    """按 shell 分隔符切分；$() / 反引号内部不切。"""
    segments = []
    buf = []
    quote = None
    escape = False
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if escape:
            buf.append(ch)
            escape = False
            i += 1
            continue
        if ch == "\\":
            buf.append(ch)
            escape = True
            i += 1
            continue
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if ch == "`":
            buf.append(ch)
            i += 1
            while i < n:
                char = cmd[i]
                buf.append(char)
                if char == "\\" and i + 1 < n:
                    buf.append(cmd[i + 1])
                    i += 2
                    continue
                i += 1
                if char == "`":
                    break
            continue
        if ch == "$" and i + 1 < n and cmd[i + 1] == "(":
            depth = 1
            buf.append("$(")
            i += 2
            while i < n and depth:
                char = cmd[i]
                if char == "\\" and i + 1 < n:
                    buf.append(char)
                    buf.append(cmd[i + 1])
                    i += 2
                    continue
                if char == "(":
                    depth += 1
                elif char == ")":
                    depth -= 1
                buf.append(char)
                i += 1
            continue
        if ch in ";|&\n":
            if buf:
                segments.append("".join(buf))
                buf = []
            i += 1
            continue
        if ch in "()":
            if buf:
                segments.append("".join(buf))
                buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    if buf:
        segments.append("".join(buf))
    return segments


def _extract_substitutions(text):
    """取出会被执行的 $(...)、`...`、<(...)、>(...)。"""
    out = []
    quote = None
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if quote == "'":
            if ch == "'":
                quote = None
            i += 1
            continue
        if quote == '"':
            if ch == '"':
                quote = None
                i += 1
                continue
            # 双引号里的 $(...) 和 `...` 仍然会执行，继续往下识别
        elif ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "`":
            end = _skip_backtick(text, i)
            inner = text[i + 1:end - 1]
            if inner:
                out.append(inner)
            i = end
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            end = _skip_balanced(text, i + 1)
            inner = text[i + 2:end - 1]
            if inner:
                out.append(inner)
            i = end
            continue
        if quote is None and ch in "<>" and i + 1 < n and text[i + 1] == "(":
            end = _skip_balanced(text, i + 1)
            inner = text[i + 2:end - 1]
            if inner:
                out.append(inner)
            i = end
            continue
        i += 1
    return out


def _read_word(text, index):
    n = len(text)
    while index < n and text[index].isspace():
        index += 1
    if index >= n:
        return "", index
    if text[index] in "\"'":
        quote = text[index]
        index += 1
        buf = []
        while index < n:
            ch = text[index]
            if ch == "\\" and quote == '"' and index + 1 < n:
                buf.append(text[index + 1])
                index += 2
                continue
            if ch == quote:
                index += 1
                break
            buf.append(ch)
            index += 1
        return "".join(buf), index
    start = index
    while (index < n and not text[index].isspace()
           and text[index] not in ";|&<>()"):
        index += 1
    return text[start:index], index


def _redirect_targets(text):
    """返回所有输出重定向的目标（> / >> / >| / &> / >&）。"""
    targets = []
    quote = None
    escape = False
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\":
            escape = True
            i += 1
            continue
        if quote:
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch == "`":
            i = _skip_backtick(text, i)
            continue
        if ch == "$" and i + 1 < n and text[i + 1] == "(":
            i = _skip_balanced(text, i + 1)
            continue
        if ch == ">":
            j = i + 1
            if j < n and text[j] in ">|&":
                j += 1
            target, j = _read_word(text, j)
            if target and not target.startswith("&"):
                targets.append(target)
            i = j
            continue
        if ch == "&" and i + 1 < n and text[i + 1] == ">":
            j = i + 2
            if j < n and text[j] == ">":
                j += 1
            target, j = _read_word(text, j)
            if target and not target.startswith("&"):
                targets.append(target)
            i = j
            continue
        i += 1
    return targets


def _is_block_device(path):
    path = (path or "").strip().strip("'\"")
    if not path:
        return False
    return bool(_BLOCK_DEVICE_RE.match(path))


def _normalize_target(path):
    path = (path or "").strip().strip("'\"")
    if not path:
        return ""
    path = re.sub(r"^/+", "/", path)
    try:
        path = posixpath.normpath(path)
    except (TypeError, ValueError):
        return path
    return re.sub(r"^/+", "/", path)


# ---------- 包装器剥离 ----------

def _unwrap_sudo(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return argv[i + 1:]
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in _SUDO_LONG_ARG and "=" not in tok:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            short = tok[:2]
            if short in _SUDO_SHORT_ARG:
                i += 1 if len(tok) > 2 else 2
            else:
                i += 1
            continue
        return argv[i:]
    return []


def _unwrap_env(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            return argv[i + 1:]
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in _ENV_LONG_ARG and "=" not in tok:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            short = tok[:2]
            if short in _ENV_SHORT_ARG:
                i += 1 if len(tok) > 2 else 2
            else:
                i += 1
            continue
        if _ASSIGN_RE.match(tok):
            i += 1
            continue
        return argv[i:]
    return []


def _unwrap_timeout(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in _TIMEOUT_LONG_ARG and "=" not in tok:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            short = tok[:2]
            if short in _TIMEOUT_SHORT_ARG:
                i += 1 if len(tok) > 2 else 2
            else:
                i += 1
            continue
        break
    if i < len(argv):
        i += 1  # duration
    return argv[i:]


def _unwrap_nice(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--adjustment="):
            i += 1
            continue
        if tok == "--adjustment":
            i += 2
            continue
        if tok.startswith("-n"):
            i += 1 if len(tok) > 2 else 2
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        break
    return argv[i:]


def _unwrap_ionice(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            if tok in ("--class", "--classdata", "--pid") and "=" not in tok:
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            short = tok[:2]
            if short in _IONICE_ARG:
                i += 1 if len(tok) > 2 else 2
            else:
                i += 1
            continue
        break
    return argv[i:]


def _unwrap_stdbuf(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and tok != "-":
            if tok in ("-i", "-o", "-e"):
                i += 2
            else:
                i += 1
            continue
        break
    return argv[i:]


def _unwrap_exec(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-a", "--argv0"):
            i += 2
            continue
        if tok.startswith("--argv0="):
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        break
    return argv[i:]


def _unwrap_command(argv):
    if any(tok in ("-v", "-V") for tok in argv[1:]):
        return []
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        break
    return argv[i:]


def _unwrap_time(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok in ("-p", "-v"):
            i += 1
            continue
        if tok.startswith("-") and tok != "-":
            i += 1
            continue
        break
    return argv[i:]


def _strip_wrappers(argv):
    while argv:
        tok = argv[0]
        if tok in _RESERVED:
            argv = argv[1:]
            continue
        if _ASSIGN_RE.match(tok):
            argv = argv[1:]
            continue
        head = os.path.basename(tok)
        if head in ("sudo", "doas"):
            argv = _unwrap_sudo(argv)
        elif head == "env":
            argv = _unwrap_env(argv)
        elif head == "timeout":
            argv = _unwrap_timeout(argv)
        elif head == "nice":
            argv = _unwrap_nice(argv)
        elif head == "ionice":
            argv = _unwrap_ionice(argv)
        elif head == "stdbuf":
            argv = _unwrap_stdbuf(argv)
        elif head == "exec":
            argv = _unwrap_exec(argv)
        elif head == "command":
            argv = _unwrap_command(argv)
        elif head == "time":
            argv = _unwrap_time(argv)
        elif head == "nohup":
            argv = argv[1:]
            if argv and argv[0] == "--":
                argv = argv[1:]
        elif head == "builtin":
            argv = argv[1:]
        elif head in ("busybox", "toybox"):
            argv = argv[1:]
        else:
            return argv
    return []


# ---------- 规则 ----------

def _rm_reason(argv):
    recursive = False
    after = False
    targets = []
    for tok in argv[1:]:
        if tok == "--":
            after = True
            continue
        if not after and tok.startswith("-") and tok != "-":
            if tok.startswith("--"):
                if tok == "--recursive":
                    recursive = True
            else:
                cluster = tok[1:]
                if "r" in cluster or "R" in cluster:
                    recursive = True
            continue
        targets.append(tok)
    if not recursive:
        return None
    for target in targets:
        normalized = _normalize_target(target)
        if normalized == "/" or normalized.startswith("/*"):
            return "递归删除根目录"
    return None


def _chmod_reason(argv):
    recursive = False
    after = False
    mode = None
    targets = []
    for tok in argv[1:]:
        if tok == "--":
            after = True
            continue
        if not after and tok.startswith("-") and tok != "-":
            if tok == "--recursive" or (not tok.startswith("--") and "R" in tok[1:]):
                recursive = True
            continue
        if mode is None:
            mode = tok
        else:
            targets.append(tok)
    if not recursive or mode not in ("777", "0777"):
        return None
    for target in targets:
        normalized = _normalize_target(target)
        if normalized == "/" or normalized.startswith("/*"):
            return "把根目录权限改成 777"
    return None


def _systemctl_reason(argv):
    args = [tok for tok in argv[1:] if not tok.startswith("-")]
    if not args:
        return None
    verb = args[0]
    if verb in ("reboot", "poweroff", "halt", "kexec", "suspend", "hibernate"):
        return "关机 / 重启"
    if verb in ("start", "restart", "isolate", "reload", "try-restart",
                "force-reload"):
        units = ("reboot.target", "poweroff.target", "halt.target", "kexec.target")
        if any(tok in units for tok in args[1:]):
            return "关机 / 重启"
    return None


def _shell_script(argv):
    """从 sh/bash/zsh -c 的参数里取脚本；没有 -c 返回 None。"""
    for i, tok in enumerate(argv[1:], 1):
        if tok == "--":
            continue
        if tok == "--command":
            return argv[i + 1] if i + 1 < len(argv) else None
        if tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]:
            return argv[i + 1] if i + 1 < len(argv) else None
    return None


def _find_payloads(argv):
    payloads = []
    i = 0
    while i < len(argv):
        if argv[i] in ("-exec", "-execdir", "-ok", "-okdir"):
            j = i + 1
            body = []
            while j < len(argv) and argv[j] not in (";", "+"):
                body.append(argv[j])
                j += 1
            if body:
                payloads.append(" ".join(body))
            i = j
        i += 1
    return payloads


def _xargs_command(argv):
    i = 1
    while i < len(argv):
        tok = argv[i]
        if tok == "--":
            i += 1
            break
        if tok.startswith("--"):
            if tok in ("--arg-file", "--delimiter", "--eof", "--replace",
                       "--max-lines", "--max-args", "--max-procs", "--max-chars"):
                i += 2
            else:
                i += 1
            continue
        if tok.startswith("-") and tok != "-":
            short = tok[:2]
            if short in _XARGS_ARG:
                i += 1 if len(tok) > 2 else 2
            else:
                i += 1
            continue
        break
    return argv[i:]


def _has_dynamic_shell(cmd):
    return bool(_DYNAMIC_SHELL_RE.search(_strip_quoted(cmd)))


def _analyze_segment(seg, depth=0):
    try:
        argv = shlex.split(seg, posix=True)
    except ValueError:
        return _legacy_reason(seg)
    argv = _strip_wrappers(argv)
    if not argv:
        return None
    head = os.path.basename(argv[0])

    if head == "rm":
        return _rm_reason(argv)
    if head == "mkfs" or head.startswith("mkfs.") or head == "mke2fs":
        return "格式化文件系统"
    if head == "mkswap" and any(_is_block_device(tok) for tok in argv[1:]):
        return "格式化文件系统"
    if head == "dd":
        for tok in argv[1:]:
            if tok.startswith("of=") and _is_block_device(tok[3:]):
                return "直接写块设备"
        return None
    if head in ("shutdown", "reboot", "halt", "poweroff"):
        return "关机 / 重启"
    if head in ("init", "telinit"):
        if any(tok in ("0", "6") for tok in argv[1:]):
            return "关机 / 重启"
        return None
    if head == "kexec":
        if "-e" in argv[1:] or "--exec" in argv[1:]:
            return "关机 / 重启"
        return None
    if head == "systemctl":
        return _systemctl_reason(argv)
    if head == "chmod":
        return _chmod_reason(argv)
    if head == "tee":
        for tok in argv[1:]:
            if not tok.startswith("-") and _is_block_device(tok):
                return "直接写块设备"
        return None
    if head in _SHELLS:
        script = _shell_script(argv)
        if script is not None:
            return _analyze(script, depth + 1)
        return None
    if head in _CONSERVATIVE_HEADS:
        return _legacy_reason(seg)
    if head == "eval":
        return _analyze(" ".join(argv[1:]), depth + 1)
    if head == "su":
        script = _shell_script(argv)
        if script is not None:
            return _analyze(script, depth + 1)
        return None
    if head == "find":
        for payload in _find_payloads(argv):
            reason = _analyze(payload, depth + 1)
            if reason:
                return reason
        return None
    if head == "xargs":
        rest = _xargs_command(argv)
        if rest:
            return _analyze(" ".join(rest), depth + 1)
        return None
    if head == "awk":
        joined = " ".join(argv[1:])
        if (re.search(r"system\s*\(", joined)
                or re.search(r"\|\s*['\"]?(?:sh|bash)\b", joined)):
            return _legacy_reason(seg)
        return None
    if head == "sed":
        joined = " ".join(argv[1:])
        if (re.search(r"system\s*\(", joined)
                or re.search(r"(?:^|[;\s])e(?:\s|$)", joined)):
            return _legacy_reason(seg)
        return None
    if head in ("python", "python2", "python3", "perl", "ruby", "node",
                "php", "lua", "osascript", "Rscript"):
        if any(tok in ("-c", "-e", "-r") for tok in argv[1:]):
            return _legacy_reason(seg)
        return None
    return None


def _analyze(cmd, depth=0):
    if not cmd or depth > 4:
        return None
    unquoted = _strip_quoted(cmd)
    if _FORK_BOMB_RE.search(unquoted):
        return "fork 炸弹"
    for inner in _extract_substitutions(cmd):
        reason = _analyze(inner, depth + 1)
        if reason:
            return reason
    for target in _redirect_targets(cmd):
        if _is_block_device(target):
            return "直接写块设备"
    # 结构检查之后再看 stdin 喂 shell：这样 `... | bash > /dev/nvme0n1`
    # 也能被重定向规则抓住，而不是直接交给旧的子串规则。
    if _has_dynamic_shell(cmd):
        return _legacy_reason(cmd)
    for seg in _split_segments(cmd):
        reason = _analyze_segment(seg, depth)
        if reason:
            return reason
    return None


def dangerous_reason(cmd):
    """返回命中的危险规则说明；安全命令返回 None。"""
    if not cmd:
        return None
    try:
        return _analyze(cmd, 0)
    except Exception:
        # 解析器自身出错时宁可回到旧的保守规则。
        return _legacy_reason(cmd)


def is_dangerous(cmd):
    return dangerous_reason(cmd) is not None
