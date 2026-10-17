#!/usr/bin/env python3
"""
compile.py - 一键把 woc 包安装成终端可直接调用的 woc / wocc / woc_chat 命令。

用法:
    python3 compile.py                    # 交互式安装（询问命令名与别名）
    python3 compile.py --key sk-xxxx      # With API KEY
    python3 compile.py --no-key           # 只安装命令
    python3 compile.py --force-config     # 已存在配置也覆盖
    python3 compile.py --prefix ~/.local/bin
    python3 compile.py --uninstall        # 卸载
    python3 compile.py --uninstall --purge-config
"""

import argparse
import getpass
import os
import re
import shutil
import stat
import subprocess
import sys
import textwrap
from pathlib import Path

# ---------- 常量 ----------
# 命令名来自 [prefix] 配置（安装时会询问），默认 woc / wocc / woc_chat。
COMMAND_KEYS = ("woc_prefix", "wocc_prefix", "woc_chat_prefix")
ENTRY_SCRIPTS = {
    "woc_prefix": "woc.py",
    "wocc_prefix": "wocc.py",
    "woc_chat_prefix": "woc_chat.py",
}
DEFAULT_SRC = "woc.py"
PACKAGE_SRC = "woc"
CONFIG_DIR = Path.home() / ".config" / "woc"
CONFIG_FILE = CONFIG_DIR / "config.toml"
# 命令名会直接变成可执行文件名，只允许安全字符
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

# shell 配置块标记，方便幂等追加 / 卸载时删除
MARK_BEGIN = "# >>> woc initialize >>>"
MARK_END = "# <<< woc initialize <<<"


# ---------- 通用工具 ----------
def log(msg: str) -> None:
    print(f"[compile] {msg}")


def die(msg: str, code: int = 1) -> None:
    print(f"[compile] ❌ {msg}", file=sys.stderr)
    sys.exit(code)


# ---------- 定位源文件 ----------
def find_source(explicit: str | None) -> Path:
    if explicit:
        p = Path(explicit).expanduser().resolve()
        if not p.is_file():
            die(f"源文件不存在: {p}")
        return p

    here = Path(__file__).resolve().parent
    for name in (DEFAULT_SRC, "woc", "wocc"):
        p = here / name
        if p.is_file():
            return p
    die(f"在 {here} 下找不到 {DEFAULT_SRC}，请用 --src 指定")


# ---------- [prefix] 配置 ----------
_CONFIG_MODULE = None


def _config_module():
    """直接加载 woc/config.py，避免为读配置而导入整个 woc 包。"""
    global _CONFIG_MODULE
    if _CONFIG_MODULE is None:
        import importlib.util
        path = Path(__file__).resolve().parent / PACKAGE_SRC / "config.py"
        spec = importlib.util.spec_from_file_location("woc_config", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _CONFIG_MODULE = module
    return _CONFIG_MODULE


def current_prefix() -> dict:
    """读取 [prefix]，并把非法/空值换成默认值，避免拼出危险文件名。"""
    module = _config_module()
    names = dict(module.load_config()["prefix"])
    for key in ("alias",) + COMMAND_KEYS:
        value = (names.get(key) or "").strip()
        if not NAME_RE.match(value):
            names[key] = module.PREFIX_DEFAULTS[key]
    return names


def ask_prefix(current: dict) -> dict:
    """交互式询问命令名与别名；直接回车保留当前值。"""
    print()
    print("命令名与提示别名（直接回车保留当前值）:")
    try:
        values = {
            "alias": input(f"  日志/提示别名 alias [{current['alias']}]: "),
            "woc_prefix": input(f"  woc 命令名 [{current['woc_prefix']}]: "),
            "wocc_prefix": input(f"  wocc 命令名 [{current['wocc_prefix']}]: "),
            "woc_chat_prefix": input(
                f"  woc_chat 命令名 [{current['woc_chat_prefix']}]: "),
        }
    except (KeyboardInterrupt, EOFError):
        print()
        return dict(current)
    return _validate_prefix(values, current)


def _validate_prefix(values: dict, fallback: dict) -> dict:
    names = dict(fallback)
    for key in ("alias",) + COMMAND_KEYS:
        value = (values.get(key) or "").strip()
        if not value:
            continue
        if not NAME_RE.match(value):
            print(f"[compile] ⚠️  {key}={value!r} 不合法，保留 {fallback[key]!r}")
            continue
        names[key] = value
    commands = [names[key] for key in COMMAND_KEYS]
    if len(set(commands)) != len(commands):
        print("[compile] ⚠️  woc / wocc / woc_chat 命令名不能重复，已全部回退默认值")
        for key in COMMAND_KEYS:
            names[key] = fallback[key]
    return names


def _render_prefix_section(names: dict) -> str:
    return (
        "[prefix]\n"
        f'alias = "{names["alias"]}"\n'
        f'woc_prefix = "{names["woc_prefix"]}"\n'
        f'wocc_prefix = "{names["wocc_prefix"]}"\n'
        f'woc_chat_prefix = "{names["woc_chat_prefix"]}"\n'
    )


def _drop_section(lines: list, section: str) -> list:
    header = f"[{section}]"
    kept = []
    skipping = False
    for line in lines:
        stripped = line.strip()
        if stripped == header:
            skipping = True
            continue
        if skipping and stripped.startswith("[") and stripped.endswith("]"):
            skipping = False
        if not skipping:
            kept.append(line)
    return kept


def write_prefix_config(names: dict) -> None:
    """把 [prefix] 写进 config.toml，保留文件里的其它段落。"""
    path = CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if path.exists():
        with open(path, encoding="utf-8", newline="") as handle:
            lines = handle.read().splitlines()
    lines = _drop_section(lines, "prefix")
    body = "\n".join(lines).rstrip("\n")
    text = (body + "\n\n" if body else "") + _render_prefix_section(names)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)
    path.chmod(0o600)
    log(f"已写入 [prefix] 配置: {path}")


def command_names(names: dict) -> list:
    """[(命令名, 入口脚本), ...]"""
    return [(names[key], ENTRY_SCRIPTS[key]) for key in COMMAND_KEYS]


# ---------- 语法检查 ----------
def check_syntax(src: Path) -> None:
    log(f"检查语法: {src}")
    try:
        source = src.read_text(encoding="utf-8")
        compile(source, str(src), "exec")
    except SyntaxError as e:
        die(f"语法错误: {e}")
    except Exception as e:
        die(f"读取失败: {e}")


# ---------- 安装可执行文件 ----------
def install_binary(src: Path, target_dir: Path, names: dict) -> list[Path]:
    """
    把 src 安装到 target_dir 下，命令名来自 [prefix] 配置。
    返回实际安装的路径列表。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    installed = []

    package_src = src.parent / PACKAGE_SRC
    if not package_src.is_dir():
        die(f"找不到包目录: {package_src}")
    for path in package_src.rglob("*.py"):
        check_syntax(path)
    installed_package = target_dir / "lib" / PACKAGE_SRC
    installed_package.parent.mkdir(parents=True, exist_ok=True)
    if installed_package.exists():
        shutil.rmtree(installed_package)
    shutil.copytree(package_src, installed_package)

    for cmd_name, script_name in command_names(names):
        script_src = src.parent / script_name
        if not script_src.is_file():
            die(f"找不到入口脚本: {script_src}")
        dst = target_dir / cmd_name
        installed_content = script_src.read_text(encoding="utf-8")
        if not installed_content.startswith("#!"):
            installed_content = "#!/usr/bin/env python3\n" + installed_content
        # The command directory and package directory cannot share the same
        # name, so make the sibling installed package importable.
        installed_content = installed_content.replace(
            "#!/usr/bin/env python3\n",
            "#!/usr/bin/env python3\n"
            "import os\nimport sys\n"
            "sys.path.insert(0, os.path.join(os.path.dirname(__file__), \"lib\"))\n",
            1,
        )
        dst.write_text(installed_content, encoding="utf-8")
        mode = dst.stat().st_mode
        dst.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        log(f"已安装: {dst}")
        installed.append(dst)

    return installed


# ---------- 检测当前 shell 的 rc 文件 ----------
def detect_rc_files() -> list[Path]:
    shell = os.environ.get("SHELL", "")
    home = Path.home()
    candidates: list[Path] = []

    if "zsh" in shell:
        candidates.append(home / ".zshrc")
    elif "bash" in shell:
        candidates.append(home / ".bashrc")
        if sys.platform == "darwin":
            candidates.append(home / ".bash_profile")
    else:
        candidates.append(home / ".bashrc")
        candidates.append(home / ".zshrc")

    seen = set()
    result = []
    for c in candidates:
        if c not in seen:
            seen.add(c)
            result.append(c)
    return result


# ---------- 幂等写入 / 移除 rc 块 ----------
def upsert_block(path: Path, body: str) -> None:
    """把 body 包在 MARK_BEGIN/END 之间写入 rc，已存在则替换。"""
    block = f"{MARK_BEGIN}\n{body.rstrip()}\n{MARK_END}\n"

    if path.exists():
        text = path.read_text(encoding="utf-8")
    else:
        text = ""

    if MARK_BEGIN in text and MARK_END in text:
        pre = text.split(MARK_BEGIN)[0]
        post = text.split(MARK_END, 1)[1]
        new_text = pre + block + post.lstrip("\n")
        action = "更新"
    else:
        sep = "" if text.endswith("\n") or not text else "\n"
        new_text = text + sep + ("\n" if text else "") + block
        action = "写入"

    path.write_text(new_text, encoding="utf-8")
    log(f"{action} shell 配置: {path}")


def remove_block(path: Path) -> None:
    if not path.exists():
        return
    text = path.read_text(encoding="utf-8")
    if MARK_BEGIN not in text or MARK_END not in text:
        return
    pre = text.split(MARK_BEGIN)[0]
    post = text.split(MARK_END, 1)[1]
    path.write_text(pre.rstrip("\n") + "\n" + post.lstrip("\n"), encoding="utf-8")
    log(f"已从 {path} 移除 woc 配置块")


# ---------- 配置存在性 / 交互 ----------
def config_exists() -> bool:
    return CONFIG_FILE.is_file()


def confirm_overwrite() -> bool:
    try:
        ans = input(
            f"[compile] 检测到已有配置 {CONFIG_FILE}\n"
            f"[compile] 是否覆盖？[y/N] "
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return ans in ("y", "yes")


def confirm_delete_config() -> bool:
    try:
        ans = input(
            f"[compile] 确认删除配置 {CONFIG_FILE}？[y/N] "
        ).strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return False
    return ans in ("y", "yes")


# ---------- 写 API Key 到 config.toml ----------
def write_api_key(key: str, base: str | None, model: str | None) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)

    lines = []
    if base:
        lines.append(f'api_base = "{base}"')
    lines.append(f'api_key  = "{key}"')
    if model:
        lines.append(f'model    = "{model}"')

    CONFIG_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    CONFIG_FILE.chmod(0o600)
    log(f"已写入配置: {CONFIG_FILE} (权限 600)")


def prompt_key() -> str:
    print()
    print("请输入 API Key（直接回车跳过，稍后自己配也行）:")
    try:
        return getpass.getpass("API Key: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return ""


def maybe_write_config(args) -> None:
    """
    决定是否写入 config.toml。
    规则：
      - 已存在 + 未指定 --force-config：
          * 命令行带了 --key 且可交互 → 询问是否覆盖
          * 否则 → 跳过，保留原配置
      - 不存在，或指定了 --force-config → 走正常写入流程
    """
    exists = config_exists()

    if exists and not args.force_config:
        if args.key and sys.stdin.isatty():
            if not confirm_overwrite():
                log("保留已有配置，跳过 API Key 写入")
                return
        else:
            log(f"检测到已有配置: {CONFIG_FILE}")
            log("保留已有配置，跳过 API Key 写入（如需覆盖请加 --force-config）")
            return

    if exists and args.force_config:
        log(f"--force-config: 将覆盖 {CONFIG_FILE}")

    key = args.key
    if key is None and not args.no_key and sys.stdin.isatty():
        key = prompt_key()

    if key:
        write_api_key(key, args.api_base, args.model)
    elif args.no_key:
        log("跳过 API Key 配置（--no-key）")
    else:
        log("未提供 API Key，记得之后配 ~/.config/woc/config.toml 或 WOC_API_KEY")


# ---------- 模板全局安装 ----------
def _template_helpers():
    """从本仓库导入 woc.context 的模板工具。"""
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    from woc.context import install_templates, load_templates, user_template_dir
    return here, install_templates, load_templates, user_template_dir


def do_list_templates() -> None:
    here, _install, load_templates, user_dir = _template_helpers()
    templates, _ = load_templates(cwd=str(here))
    if not templates:
        log(f"{here / '.woc' / 'templates'} 下没有模板")
        return
    log(f"本仓库可安装的模板（{len(templates)} 个）:")
    for tpl in templates:
        print(f"    {tpl.id:24s} {tpl.desc or '(未写 desc)'}")
    print()
    log(f"安装到全局: python3 compile.py --global-templates")
    log(f"安装指定项: python3 compile.py --global-templates id1 id2")
    log(f"目标目录  : {user_dir()}")


def do_global_templates(ids: list) -> None:
    here, install_templates, _load, user_dir = _template_helpers()
    copied, skipped, missing, failed = install_templates(
        ids or None, project_dir=here, dry_run=False
    )
    log(f"来源: {here / '.woc' / 'templates'}")
    log(f"目标: {user_dir()}")
    for tid in copied:
        log(f"✅ 已安装 {tid}")
    for tid in skipped:
        log(f"=  已是最新 {tid}")
    for tid in missing:
        print(f"[compile] ❌ 找不到模板: {tid}", file=sys.stderr)
    for tid, reason in failed:
        print(f"[compile] ❌ 写入失败 {tid}: {reason}", file=sys.stderr)
    if not copied and not skipped:
        log("没有可安装的模板" if not failed else "全部写入失败")
        if missing or failed:
            sys.exit(1)
        return
    log(f"完成: 安装 {len(copied)} 个, 未变 {len(skipped)} 个"
        + (f", 失败 {len(failed)} 个" if failed else ""))
    log("这些模板现在在所有项目里都可用（用户级优先，会覆盖项目级同名 id）")
    log("注意: <templates> 段已变化，下一次请求的前缀缓存会重建一次")
    if missing or failed:
        sys.exit(1)


# ---------- 验证 ----------
def verify(target_dir: Path, rc_files: list[Path], names: dict) -> None:
    shell = os.environ.get("SHELL", "/bin/bash")
    rc_sources = " ".join(f'[ -f "{p}" ] && . "{p}";' for p in rc_files)

    for cmd_name, _script in command_names(names):
        bin_path = target_dir / cmd_name
        if not bin_path.exists():
            die(f"安装后找不到可执行文件: {bin_path}")

        test_cmd = f'{rc_sources} command -v {cmd_name}'
        try:
            out = subprocess.run(
                [shell, "-c", test_cmd],
                capture_output=True, text=True, timeout=10,
            )
            found = out.stdout.strip()
        except Exception as e:
            log(f"验证 {name} 跳过（{e}）")
            found = ""

        if found:
            log(f"✅ 验证通过: {found}")
        else:
            log(f"⚠️  在当前 shell 会话里还找不到 {cmd_name}，请手动执行:")
            for p in rc_files:
                log(f"    source {p}")


# ---------- 安装 / 卸载 ----------
def do_install(args) -> None:
    src = find_source(args.src)
    target_dir = Path(args.prefix).expanduser().resolve()
    names = current_prefix()
    if sys.stdin.isatty():
        names = ask_prefix(names)

    log(f"源文件: {src}")
    log(f"安装目录: {target_dir}")

    check_syntax(src)
    for script in ENTRY_SCRIPTS.values():
        check_syntax(src.parent / script)
    install_binary(src, target_dir, names)

    # shell 配置：PATH
    rc_files = detect_rc_files()
    body = f'export PATH="{target_dir}:$PATH"'
    for rc in rc_files:
        upsert_block(rc, body)

    # API Key / config.toml（含"已存在不覆盖"逻辑）
    maybe_write_config(args)
    write_prefix_config(names)

    # 验证
    verify(target_dir, rc_files, names)

    print()
    log("🎉 安装完成！")
    log("新开一个终端，或执行下面的命令让当前会话生效：")
    for rc in rc_files:
        print(f'    source {rc}')
    print()
    log("然后试试：")
    print(f'    {names["woc_prefix"]}  查看当前目录下最大的 10 个文件')
    print(f'    {names["wocc_prefix"]} 帮我看看当前运行中的进程')


def do_uninstall(args) -> None:
    target_dir = Path(args.prefix).expanduser().resolve()
    names = current_prefix()
    targets = {names[key] for key in COMMAND_KEYS}
    targets.update({"woc", "wocc", "woc_chat"})  # 旧版本安装的默认命令名

    for cmd_name in sorted(targets):
        bin_path = target_dir / cmd_name
        if bin_path.exists():
            bin_path.unlink()
            log(f"已删除: {bin_path}")
        else:
            log(f"未找到: {bin_path}（跳过）")

    package_path = target_dir / "lib" / PACKAGE_SRC
    if package_path.exists():
        shutil.rmtree(package_path)
        log(f"已删除: {package_path}")

    for rc in detect_rc_files():
        remove_block(rc)

    if CONFIG_FILE.exists() and args.purge_config:
        if sys.stdin.isatty() and not confirm_delete_config():
            log("保留配置")
        else:
            CONFIG_FILE.unlink()
            log(f"已删除配置: {CONFIG_FILE}")
    elif CONFIG_FILE.exists():
        log(f"保留配置: {CONFIG_FILE}（如需删除加 --purge-config）")

    log("卸载完成")


# ---------- CLI ----------
def main() -> None:
    parser = argparse.ArgumentParser(
        prog="compile.py",
        description="把 woc 包一键安装成终端命令（默认 woc / wocc / woc_chat，安装时可改名）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            示例:
              python3 compile.py
              python3 compile.py --key sk-xxxx
              python3 compile.py --prefix ~/.local/bin --no-key
              python3 compile.py --force-config --key sk-xxxx
              python3 compile.py --uninstall --purge-config
        """),
    )
    parser.add_argument("--src", help=f"源脚本路径（默认同目录下的 {DEFAULT_SRC}）")
    parser.add_argument("--prefix", default="~/bin",
                        help="安装目录（默认 ~/bin）")
    parser.add_argument("--key", help="API Key，指定后不交互询问")
    parser.add_argument("--api-base", help="API Base URL")
    parser.add_argument("--model", help="模型名")
    parser.add_argument("--no-key", action="store_true",
                        help="完全跳过 API Key 配置")
    parser.add_argument("--force-config", action="store_true",
                        help="即使 ~/.config/woc/config.toml 已存在也覆盖它")
    parser.add_argument("--uninstall", action="store_true",
                        help="卸载 woc / wocc")
    parser.add_argument("--purge-config", action="store_true",
                        help="卸载时同时删除 ~/.config/woc/config.toml")
    parser.add_argument("--global-templates", nargs="*", metavar="ID",
                        help="把本仓库 .woc/templates 下的模板安装到 "
                             "~/.config/woc/templates（装到全局，所有项目可用）。"
                             "不给 ID 表示全部；可指定若干 ID")
    parser.add_argument("--list-templates", action="store_true",
                        help="列出本仓库可安装的模板后退出")

    args = parser.parse_args()

    try:
        if args.list_templates:
            do_list_templates()
        elif args.global_templates is not None:
            do_global_templates(args.global_templates)
        elif args.uninstall:
            do_uninstall(args)
        else:
            do_install(args)
    except KeyboardInterrupt:
        print("\n[compile] 已取消", file=sys.stderr)
        sys.exit(130)


if __name__ == "__main__":
    main()