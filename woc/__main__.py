import os
import sys

from .cli.chat import woc_chat_main
from .cli.multi import wocc_main
from .cli.single import woc_main
from .config import load_config, prefix
from .context import (BODY_BUDGET, install_templates, lint_templates,
                      load_templates, project_template_dir, stable_context,
                      user_template_dir)

_LOG_ALIAS = "woc"
_COMMAND = "woc"


def _cmd_list_templates(query=None):
    templates, sources = load_templates()
    print(f"{_tag()} template 目录（后者优先级更高）:")
    for path, exists in sources:
        print(f"  {'✓' if exists else '·'} {path}{'' if exists else '  (不存在)'}")
    if not templates:
        print()
        print(f"{_tag()} 还没有任何模板。新建 ~/.config/woc/templates/<id>.md 即可。")
        return 0

    detail = {}
    if query is None:
        stable_context(query="", templates=templates, detail=detail)
    else:
        stable_context(query=query, templates=templates, detail=detail)

    print()
    print(f"{_tag()} 已加载 {len(templates)} 个模板")
    print(f"      索引层: {detail.get('indexed', 0)} 个（恒定全量，"
          f"约 {detail.get('index_chars', 0)} 字符）")
    print(f"      正文层: {len(detail.get('bodies', []))} 个，策略 {detail.get('strategy')}，"
          f"约 {detail.get('body_chars', 0)} 字符（预算 {BODY_BUDGET}）")
    if query is not None:
        print(f"      查询: {query!r}")
    print()

    body_ids = set(detail.get("bodies", []))
    scores = detail.get("scores", {})
    for tpl in templates:
        mark = "正文" if tpl.id in body_ids else "仅索引"
        score = scores.get(tpl.id)
        suffix = f"  score={score}" if query is not None and score is not None else ""
        print(f"  [{mark}] @{tpl.id}{suffix}")
        print(f"      {tpl.desc or '(未写 desc)'}")
        if tpl.when:
            print(f"      适用: {tpl.when}")
        if tpl.tags:
            print(f"      tags: {', '.join(tpl.tags)}")
        print(f"      {tpl.path}")
    if query is None:
        print()
        print(f'{_tag()} 想看某次查询会选中哪些正文，用: {_COMMAND} --templates --query "你的问题"')
    return 0


def _cmd_lint_templates():
    templates, _sources = load_templates()
    if not templates:
        print(f"{_tag()} 没有模板可检查。", file=sys.stderr)
        return 0

    errors, warnings = lint_templates(templates)
    for tid, msg in errors:
        print(f"{_tag()} ✗ {tid}: {msg}", file=sys.stderr)
    for tid, msg in warnings:
        print(f"{_tag()} ⚠ {tid}: {msg}", file=sys.stderr)

    print(f"{_tag()} 已检查 {len(templates)} 个模板: "
          f"{len(errors)} 个错误, {len(warnings)} 个提示")
    return 1 if errors else 0


def _cmd_install_templates(ids):
    copied, skipped, missing, failed = install_templates(ids or None)
    src_dir = project_template_dir()
    dest_dir = user_template_dir()

    print(f"{_tag()} 来源: {src_dir or '(当前目录不属于任何项目)'}")
    print(f"{_tag()} 目标: {dest_dir}")
    for tid in copied:
        print(f"  ✓ 已安装 {tid}")
    for tid in skipped:
        print(f"  = 已是最新 {tid}（未改动）")
    for tid in missing:
        print(f"  ✗ 项目级找不到 {tid}", file=sys.stderr)
    for tid, reason in failed:
        print(f"  ✗ 写入失败 {tid}: {reason}", file=sys.stderr)

    if not copied and not skipped:
        if failed:
            print(f"{_tag()} 没有模板被安装（全部写入失败）", file=sys.stderr)
        else:
            print(f"{_tag()} 没有可安装的模板。")
        return 1 if (missing or failed) else 0

    print(f"{_tag()} 完成: 安装 {len(copied)} 个, 未变 {len(skipped)} 个"
          + (f", 缺失 {len(missing)} 个" if missing else "")
          + (f", 失败 {len(failed)} 个" if failed else ""))
    print(f"{_tag()} 现在这些模板在所有项目里都可用（用户级优先，会覆盖项目级同名 id）")
    print(f"{_tag()} 注意: <templates> 段已变化，下一次请求的前缀缓存会重建一次。")
    return 1 if (missing or failed) else 0


def _split_query(rest):
    """从 ['--query', '文本'] 或 ['这是文本'] 里取出查询串。"""
    if not rest:
        return None
    if rest[0] == "--query":
        return " ".join(rest[1:]) or None
    return " ".join(rest)


def _tag():
    return f"[{_LOG_ALIAS}]"


def main():
    global _LOG_ALIAS, _COMMAND
    cfg = load_config()
    _LOG_ALIAS = prefix(cfg, "alias")
    _COMMAND = prefix(cfg, "woc_prefix")
    prog = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    if prog == "wocc":
        wocc_main(argv)
    elif argv and argv[0] in ("-c", "--chat"):
        woc_chat_main(argv[1:], name=_COMMAND, via_woc=True)
    elif argv and argv[0] == "--templates":
        sys.exit(_cmd_list_templates(_split_query(argv[1:])))
    elif argv and argv[0] == "--check-templates":
        sys.exit(_cmd_lint_templates())
    elif argv and argv[0] == "--install-templates":
        sys.exit(_cmd_install_templates(argv[1:]))
    else:
        woc_main(argv)


if __name__ == "__main__":
    main()
