"""woc-fs：纯 bash 场景下的文件读写命令（read / write / edit）。

为什么不让模型直接用 `cat` / `sed -i` / heredoc：

- `cat` 一个 500 行的文件 ≈ 8k tokens 进对话，而且不可逆；`read --lines 40:80`
  只灌 40 行，还带行号，后面 `edit` 才对得准。
- `sed -i` 要把正则、`&`、`/`、多行、UTF-8 边界挨个逃逸一遍，而且“匹配到 17 处”
  是静默的；`edit` 只做字面量替换，0 处或多处都报错。
- `cat > f <<'EOF'` 在内容本身出现 EOF 时直接断，回灌进 prompt 还会二次损坏；
  `write` 从 stdin 或 `@file` 取内容。

落盘用临时文件 + `os.replace` 保原子性，并保留原文件 mode；9p / 网络文件系统不支持
原子改名时退化成原地写（见 HANDOFF 的“环境坑”）。

这是给模型调用的命令，不是库：stdout 只放数据（会回灌给模型），stderr 只放一行
error；输出刻意不加 `[alias]` 日志前缀，否则会污染命令输出。

用法：

    woc-fs read  PATH [--lines 40:80] [--head N] [--tail N] [--max-chars N] [--raw]
    woc-fs write PATH [--content TEXT] [--mkdir] [--backup] [--append] [--dry-run]
    woc-fs edit  PATH --old OLD [--new NEW] [--all|--occurrence N] [--backup] [--dry-run]

内容参数支持 `@file`（从文件读）与 `-`（从 stdin 读），绕开引号地狱；两者不能
同时指向 stdin。多行内容要么走 `@file`，要么加 `--escapes` 在命令行里写
`\n` / `\t` / `\\`。
退出码：0 成功；1 失败（stderr 一行 error）；2 参数错误（argparse）。
"""

import argparse
import difflib
import errno
import os
import sys
import tempfile


PROG = "woc-fs"
DEFAULT_HEAD = 60
DEFAULT_MAX_CHARS = 20000
BINARY_SNIFF = 8192
# edit 成功后回显改动处：上下各 3 行，最多 12 行（模型靠这段确认自己改对了）。
CONTEXT_RADIUS = 3
MAX_CONTEXT_LINES = 12
# os.replace 失败时允许退化成原地写的 errno：9p(ENOTSUP)、跨设备(EXDEV)，
# 某些 FUSE / SMB 挂载报 EPERM。
_FALLBACK_ERRNOS = (errno.EXDEV, errno.EPERM, getattr(errno, "ENOTSUP", -1))


class Failure(Exception):
    """可预期的失败：只打一行 error，退出码 1。"""


# ---------- 读 ----------
def read_stdin():
    """stdin 全量读成 str。测试里 sys.stdin 可能是 StringIO（没有 .buffer）。"""
    buffer = getattr(sys.stdin, "buffer", None)
    if buffer is not None:
        return buffer.read().decode("utf-8", "replace")
    return sys.stdin.read()


def load_text(spec):
    """把 --content / --old / --new 的值解析成文本：`@file` 或 `-`。"""
    if spec == "-":
        return read_stdin()
    if spec.startswith("@"):
        path = spec[1:]
        try:
            with open(path, "r", encoding="utf-8", newline="") as handle:
                return handle.read()
        except OSError as exc:
            raise Failure(f"{path}: {exc.strerror or exc}")
    return spec


def read_text(path):
    """按字节读，拒绝二进制，保留原始换行。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except IsADirectoryError:
        raise Failure(f"{path}: is a directory")
    except OSError as exc:
        raise Failure(f"{path}: {exc.strerror or exc}")
    if b"\x00" in raw[:BINARY_SNIFF]:
        raise Failure(f"{path}: binary file, refused")
    return raw.decode("utf-8", "replace")


def _is_file_spec(spec):
    """`@file` / `-` 是内容来源，里面的反斜杠是原文，不做转义展开。"""
    return spec == "-" or spec.startswith("@")


def _unescape(text):
    r"""把命令行里的 `\n` / `\t` / `\\` 展开（只在显式 --escapes 时调用）。"""
    out = []
    index = 0
    while index < len(text):
        char = text[index]
        if char == "\\" and index + 1 < len(text):
            nxt = text[index + 1]
            if nxt == "n":
                out.append("\n")
                index += 2
                continue
            if nxt == "t":
                out.append("\t")
                index += 2
                continue
            if nxt == "\\":
                out.append("\\")
                index += 2
                continue
        out.append(char)
        index += 1
    return "".join(out)


# ---------- 写 ----------
def _backup_path(path):
    """备份名单调递增，永不覆盖：PATH.bak / PATH.bak.1 / PATH.bak.2 …"""
    if not os.path.exists(path + ".bak"):
        return path + ".bak"
    index = 1
    while os.path.exists(f"{path}.bak.{index}"):
        index += 1
    return f"{path}.bak.{index}"


def atomic_write(path, text, backup=False, dry_run=False):
    """临时文件 + os.replace；保留 mode；备份到不重名的 `<path>.bak[.N]`。

    返回 (文件是否已存在, 备份路径)；没备份时备份路径是空串。
    """
    existed = os.path.exists(path)
    if dry_run:
        return existed, ""

    saved = ""
    if backup and existed:
        saved = _backup_path(path)
        try:
            with open(path, "rb") as src, open(saved, "wb") as dst:
                dst.write(src.read())
        except OSError as exc:
            raise Failure(f"{saved}: {exc.strerror or exc}")

    directory = os.path.dirname(os.path.abspath(path)) or "."
    try:
        os.makedirs(directory, exist_ok=True)
        handle_fd, tmp = tempfile.mkstemp(dir=directory, prefix=".woc-fs-")
    except OSError as exc:
        raise Failure(f"{directory}: {exc.strerror or exc}")

    try:
        with os.fdopen(handle_fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
        if existed:
            try:
                os.chmod(tmp, os.stat(path).st_mode & 0o7777)
            except OSError:
                pass
        try:
            os.replace(tmp, path)
            tmp = ""
        except OSError as exc:
            if exc.errno not in _FALLBACK_ERRNOS:
                raise Failure(f"{path}: {exc.strerror or exc}")
            with open(path, "w", encoding="utf-8", newline="") as handle:
                handle.write(text)
            os.unlink(tmp)
            tmp = ""
    except OSError as exc:
        raise Failure(f"{path}: {exc.strerror or exc}")
    finally:
        if tmp and os.path.exists(tmp):
            os.unlink(tmp)
    return existed, saved


def count_lines(text):
    """行数：`a\\n` 算 1 行，`a\\nb` 算 2 行，空串算 0 行。"""
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


# ---------- 子命令 ----------
def cmd_read(args):
    text = read_text(args.path)
    lines = text.splitlines(keepends=True)
    total = len(lines)

    if args.lines:
        head, _sep, tail = args.lines.partition(":")
        try:
            lo = int(head) if head.strip() else 1
            hi = int(tail) if tail.strip() else total
        except ValueError:
            raise Failure(f"--lines expects A:B, got {args.lines!r}")
    elif args.tail is not None:
        lo, hi = max(1, total - args.tail + 1), total
    else:
        lo, hi = 1, min(total, DEFAULT_HEAD if args.head is None else args.head)

    lo = max(1, lo)
    hi = min(total, hi)
    if total and hi < lo:
        raise Failure(f"{args.path}: line range {lo}-{hi} is empty (file has {total} lines)")

    if args.raw:
        body = "".join(lines[lo - 1:hi]) if total else ""
    else:
        width = max(4, len(str(total)))
        rows = [f"{args.path}: {lo}-{hi} of {total} lines"]
        for index in range(lo - 1, hi):
            rows.append(f"{index + 1:>{width}}| {lines[index].rstrip(chr(13) + chr(10))}")
        body = "\n".join(rows) + "\n"

    limit = DEFAULT_MAX_CHARS if args.max_chars is None else args.max_chars
    if limit > 0 and len(body) > limit:
        body = body[:limit].rstrip() + f"\n[{args.path}: truncated at {limit} chars; narrow --lines/--head to see more]\n"
    sys.stdout.write(body)


def cmd_write(args):
    text = load_text(args.content) if args.content is not None else read_stdin()
    if args.escapes and args.content is not None and not _is_file_spec(args.content):
        text = _unescape(text)
    directory = os.path.dirname(os.path.abspath(args.path)) or "."

    if args.append:
        if args.mkdir:
            try:
                os.makedirs(directory, exist_ok=True)
            except OSError as exc:
                raise Failure(f"{directory}: {exc.strerror or exc}")
        if not args.dry_run:
            try:
                with open(args.path, "a", encoding="utf-8", newline="") as handle:
                    handle.write(text)
            except OSError as exc:
                raise Failure(f"{args.path}: {exc.strerror or exc}")
        verb = "would append" if args.dry_run else "appended"
        print(f"{args.path}: {verb} {len(text)} chars, {count_lines(text)} lines")
        return

    if not args.mkdir and not os.path.isdir(directory):
        raise Failure(f"{directory}: no such directory (add --mkdir to create it)")
    _existed, saved = atomic_write(args.path, text, backup=args.backup,
                                   dry_run=args.dry_run)
    verb = "would write" if args.dry_run else "wrote"
    note = f" (backup {saved})" if saved else ""
    print(f"{args.path}: {verb} {len(text)} chars, {count_lines(text)} lines{note}")


def _closest_line(text, old):
    """0 匹配时给个方向：找最像的一行，附行号。"""
    lines = text.splitlines()
    needle = (old.splitlines() or [old])[0].strip()
    if not needle:
        return ""
    stripped = [line.strip() for line in lines]
    close = difflib.get_close_matches(needle, stripped, n=1, cutoff=0.6)
    if not close:
        return ""
    index = stripped.index(close[0])
    snippet = stripped[index]
    if len(snippet) > 80:
        snippet = snippet[:77] + "..."
    return f" (closest match at line {index + 1}: {snippet})"


def _line_of(text, offset):
    """offset 落在第几行（1 基）。"""
    return text.count("\n", 0, offset) + 1


def _context_rows(text, first, added):
    """改动处 ±CONTEXT_RADIUS 行，带行号；改动行前缀 `> `，其余 `  `。"""
    lines = text.splitlines(keepends=True)
    total = len(lines)
    last = first + max(added, 1) - 1
    lo = max(1, first - CONTEXT_RADIUS)
    hi = min(total, last + CONTEXT_RADIUS)
    if hi - lo + 1 > MAX_CONTEXT_LINES:
        hi = lo + MAX_CONTEXT_LINES - 1
    width = max(4, len(str(total)))
    marked = set(range(first, min(last, total) + 1))
    rows = []
    for index in range(lo - 1, hi):
        flag = "> " if index + 1 in marked else "  "
        rows.append(f"{flag}{index + 1:>{width}}| "
                    f"{lines[index].rstrip(chr(13) + chr(10))}")
    return rows


def _spots_text(text, positions):
    """命中位置 -> `line 7` / `lines 3, 9, 12`。"""
    numbers = [str(_line_of(text, offset)) for offset in positions]
    if len(numbers) == 1:
        return f"line {numbers[0]}"
    shown = ", ".join(numbers[:5])
    if len(numbers) > 5:
        shown += " ..."
    return f"lines {shown}"


def cmd_edit(args):
    if args.all and args.occurrence is not None:
        raise Failure("--all and --occurrence are mutually exclusive")
    if args.old == "-" and args.new == "-":
        raise Failure("--old and --new cannot both use - (stdin can only be read once); use @file, or pass one of them on the command line")

    old = load_text(args.old)
    new = "" if args.new is None else load_text(args.new)
    if args.escapes:
        if not _is_file_spec(args.old):
            old = _unescape(old)
        if args.new is not None and not _is_file_spec(args.new):
            new = _unescape(new)
    if not old:
        raise Failure("--old is empty; nothing to replace")

    text = read_text(args.path)
    count = text.count(old)
    if count == 0:
        raise Failure(f"--old was not found in {args.path}{_closest_line(text, old)}")

    if args.occurrence is not None:
        if not 1 <= args.occurrence <= count:
            raise Failure(f"--occurrence {args.occurrence} is out of range: --old matched {count} times in {args.path}")
        position = -1
        for _ in range(args.occurrence):
            position = text.find(old, position + 1)
        positions = [position]
        updated = text[:position] + new + text[position + len(old):]
    elif args.all:
        positions = []
        start = 0
        while True:
            found = text.find(old, start)
            if found < 0:
                break
            positions.append(found)
            start = found + 1
        updated = text.replace(old, new)
    elif count > 1:
        spots = []
        start = 0
        while len(spots) < 5:
            found = text.find(old, start)
            if found < 0:
                break
            spots.append(str(text.count("\n", 0, found) + 1))
            start = found + 1
        more = " ..." if count > len(spots) else ""
        raise Failure(f'--old matched {count} times in {args.path} (lines {", ".join(spots)}{more}); use --all to replace all, or --occurrence N to replace only the Nth')
    else:
        position = text.find(old)
        positions = [position]
        updated = text.replace(old, new, 1)

    atomic_write(args.path, updated, backup=args.backup, dry_run=args.dry_run)
    verb = "would replace" if args.dry_run else "replaced"
    print(f"{args.path}: {verb} {len(positions)} place(s) at "
          f"{_spots_text(text, positions)}, "
          f"{count_lines(text)} -> {count_lines(updated)} lines")
    if len(positions) == 1:
        first = _line_of(updated, positions[0])
        for row in _context_rows(updated, first, count_lines(new)):
            print(row)



def build_parser(prog=PROG):
    parser = argparse.ArgumentParser(
        prog=prog,
        description="File read/write (read / write / edit): designed for the model, one summary line plus data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Content arguments accept @file (read from a file) and - (read from stdin). Exit codes: 0 success / 1 failure / 2 argument error.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="read|write|edit")

    read = sub.add_parser("read", help="read a line window with line numbers")
    read.add_argument("path")
    read.add_argument("--lines",
                      help="line range, 1-based and inclusive, e.g. 40:80; 40: means through EOF")
    read.add_argument("--head", type=int,
                      help=f"first N lines (default {DEFAULT_HEAD})")
    read.add_argument("--tail", type=int, help="last N lines")
    read.add_argument("--max-chars", type=int, dest="max_chars",
                      help=f"output character limit (default {DEFAULT_MAX_CHARS}; 0 means unlimited)")
    read.add_argument("--raw", action="store_true",
                      help="emit only content, without header or line numbers")

    write = sub.add_parser("write", help="write a whole file (or --append)")
    write.add_argument("path")
    write.add_argument("--content",
                       help="content; @file reads a file, - reads stdin; omitted means stdin")
    write.add_argument("--mkdir", action="store_true",
                       help="create missing parent directories")
    write.add_argument("--backup", action="store_true",
                       help="back up to <path>.bak first (then .bak.1, .bak.2 …; never overwrites)")
    write.add_argument("--append", action="store_true",
                       help="append instead of overwrite")
    write.add_argument("--escapes", action="store_true",
                       help="expand \\n \\t \\\\ in --content (not in @file or stdin)")
    write.add_argument("--dry-run", action="store_true", dest="dry_run",
                       help="report a summary without writing")

    edit = sub.add_parser("edit", help="literal replacement (must match exactly once by default)")
    edit.add_argument("path")
    edit.add_argument("--old", required=True,
                      help="text to replace; @file / - also work")
    edit.add_argument("--new", help="replacement; omitted means delete")
    edit.add_argument("--all", action="store_true",
                      help="replace every match")
    edit.add_argument("--backup", action="store_true",
                      help="back up to <path>.bak first (then .bak.1, .bak.2 …; never overwrites)")
    edit.add_argument("--occurrence", type=int, metavar="N",
                      help="replace only the Nth match (1-based); no longer requires a unique match")
    edit.add_argument("--escapes", action="store_true",
                      help="expand \\n \\t \\\\ in --old / --new (not in @file or stdin)")
    edit.add_argument("--dry-run", action="store_true", dest="dry_run",
                      help="report a summary without writing")
    return parser


COMMANDS = {"read": cmd_read, "write": cmd_write, "edit": cmd_edit}


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        COMMANDS[args.cmd](args)
    except Failure as exc:
        sys.stderr.write(f"error: {exc}\n")
        return 1
    except BrokenPipeError:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
