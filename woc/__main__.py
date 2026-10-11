import os
import sys

from .cli.chat import woc_chat_main
from .cli.multi import wocc_main
from .cli.single import woc_main
from .context import (FULL_INJECTION_LIMIT, lint_templates, load_templates,
                      template_dirs)


def _cmd_list_templates():
    templates, sources = load_templates()
    print("[woc] template 目录（后者优先级更高）:")
    for path, exists in sources:
        print(f"  {'✓' if exists else '·'} {path}{'' if exists else '  (不存在)'}")
    if not templates:
        print()
        print("[woc] 还没有任何模板。新建 ~/.config/woc/templates/<id>.md 即可。")
        return 0

    strategy = "全量注入" if len(templates) <= FULL_INJECTION_LIMIT else "关键词检索 top-N"
    print()
    print(f"[woc] 已加载 {len(templates)} 个模板（当前策略: {strategy}，"
          f"阈值 {FULL_INJECTION_LIMIT}）:")
    for tpl in templates:
        print(f"  @{tpl.id}")
        print(f"      {tpl.desc or '(未写 desc)'}")
        if tpl.tags:
            print(f"      tags: {', '.join(tpl.tags)}")
        print(f"      {tpl.path}")
    return 0


def _cmd_lint_templates():
    templates, _sources = load_templates()
    if not templates:
        print("[woc] 没有模板可检查。", file=sys.stderr)
        return 0

    errors, warnings = lint_templates(templates)
    for tid, msg in errors:
        print(f"[woc] ✗ {tid}: {msg}", file=sys.stderr)
    for tid, msg in warnings:
        print(f"[woc] ⚠ {tid}: {msg}", file=sys.stderr)

    print(f"[woc] 已检查 {len(templates)} 个模板: "
          f"{len(errors)} 个错误, {len(warnings)} 个提示")
    return 1 if errors else 0


def main():
    prog = os.path.basename(sys.argv[0])
    argv = sys.argv[1:]
    if prog == "wocc":
        wocc_main(argv)
    elif argv and argv[0] in ("-c", "--chat"):
        woc_chat_main(argv[1:])
    elif argv and argv[0] == "--templates":
        sys.exit(_cmd_list_templates())
    elif argv and argv[0] == "--check-templates":
        sys.exit(_cmd_lint_templates())
    else:
        woc_main(argv)


if __name__ == "__main__":
    main()
