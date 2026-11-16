import os
import sys

from .cli.chat import woc_chat_main
from .cli.completion import SHELLS, render as render_completion
from .cli.single import woc_main
from .config import load_config, prefix
from .context import (BODY_BUDGET, install_templates, lint_templates,
                      load_templates, project_template_dir, stable_context,
                      user_template_dir)

_LOG_ALIAS = "woc"
_COMMAND = "woc"
_COMMANDS = ["woc", "wocc", "woc_chat"]


def _cmd_list_templates(query=None):
    templates, sources = load_templates()
    print(f"{_tag()} template directories (later ones take precedence):")
    for path, exists in sources:
        print(f"  {'✓' if exists else '·'} {path}"
              f"{'' if exists else '  (missing)'}")
    if not templates:
        print()
        print(f"{_tag()} no templates yet. Create ~/.config/woc/templates/<id>.md.")
        return 0

    detail = {}
    if query is None:
        stable_context(query="", templates=templates, detail=detail)
    else:
        stable_context(query=query, templates=templates, detail=detail)

    print()
    print(f"{_tag()} loaded {len(templates)} templates")
    print(f'      index: {detail.get("indexed", 0)} (always complete, about {detail.get("index_chars", 0)} chars)')
    print(f'      bodies: {len(detail.get("bodies", []))}, strategy {detail.get("strategy")}, about {detail.get("body_chars", 0)} chars (budget {BODY_BUDGET})')
    if query is not None:
        print(f"      query: {query!r}")
    print()

    body_ids = set(detail.get("bodies", []))
    scores = detail.get("scores", {})
    for tpl in templates:
        mark = "body" if tpl.id in body_ids else "index only"
        score = scores.get(tpl.id)
        suffix = f"  score={score}" if query is not None and score is not None else ""
        print(f"  [{mark}] @{tpl.id}{suffix}")
        print(f"      {tpl.desc or '(no desc)'}")
        if tpl.when:
            print(f"      applies when: {tpl.when}")
        if tpl.tags:
            print(f"      tags: {', '.join(tpl.tags)}")
        print(f"      {tpl.path}")
    if query is None:
        print()
        print(f'{_tag()} to see which bodies a query selects, run: {_COMMAND} --templates --query "your question"')
    return 0


def _cmd_lint_templates():
    templates, _sources = load_templates()
    if not templates:
        print(f"{_tag()} no templates to check.", file=sys.stderr)
        return 0

    errors, warnings = lint_templates(templates)
    for tid, msg in errors:
        print(f"{_tag()} ✗ {tid}: {msg}", file=sys.stderr)
    for tid, msg in warnings:
        print(f"{_tag()} ⚠ {tid}: {msg}", file=sys.stderr)

    print(f"{_tag()} checked {len(templates)} templates: {len(errors)} errors, {len(warnings)} warnings")
    return 1 if errors else 0


def _cmd_install_templates(ids):
    copied, skipped, missing, failed = install_templates(ids or None)
    src_dir = project_template_dir()
    dest_dir = user_template_dir()

    print(f'{_tag()} source: {src_dir or "(the current directory is not in a project)"}')
    print(f"{_tag()} target: {dest_dir}")
    for tid in copied:
        print(f"  ✓ installed {tid}")
    for tid in skipped:
        print(f"  = up to date {tid} (unchanged)")
    for tid in missing:
        print(f"  ✗ not found at project level: {tid}", file=sys.stderr)
    for tid, reason in failed:
        print(f"  ✗ failed to write {tid}: {reason}",
              file=sys.stderr)

    if not copied and not skipped:
        if failed:
            print(f"{_tag()} no templates were installed (all writes failed)",
                  file=sys.stderr)
        else:
            print(f"{_tag()} no installable templates.")
        return 1 if (missing or failed) else 0

    print(f"{_tag()} done: installed {len(copied)}, unchanged {len(skipped)}"
          + (f", missing {len(missing)}" if missing else "")
          + (f", failed {len(failed)}" if failed else ""))
    print(f"{_tag()} these templates are now available in all projects (user-level wins over project-level with the same id)")
    print(f"{_tag()} note: the <templates> block changed; the prefix cache will be rebuilt once on the next request.")
    return 1 if (missing or failed) else 0


def _split_query(rest):
    """从 ['--query', '文本'] 或 ['这是文本'] 里取出查询串。"""
    if not rest:
        return None
    if rest[0] == "--query":
        return " ".join(rest[1:]) or None
    return " ".join(rest)


def _cmd_completion(rest):
    shell = rest[0] if rest else ""
    if shell not in SHELLS:
        print(f"usage: {_COMMAND} --completion bash|zsh",
              file=sys.stderr)
        return 2
    print(render_completion(shell, _COMMANDS))
    return 0


def _tag():
    return f"[{_LOG_ALIAS}]"


def main():
    global _LOG_ALIAS, _COMMAND, _COMMANDS
    cfg = load_config()
    _LOG_ALIAS = prefix(cfg, "alias")
    _COMMAND = prefix(cfg, "woc_prefix")
    _COMMANDS = [prefix(cfg, "woc_prefix"), prefix(cfg, "wocc_prefix"),
                 prefix(cfg, "woc_chat_prefix")]
    prog = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    if prog == "wocc":
        # 延迟导入：单轮 woc 不需要 loop / 多轮模块，保持启动路径不变。
        from .cli.multi import wocc_main
        wocc_main(argv)
    elif argv and argv[0] in ("-c", "--chat"):
        woc_chat_main(argv[1:], name=_COMMAND, via_woc=True)
    elif argv and argv[0] == "--completion":
        sys.exit(_cmd_completion(argv[1:]))
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
