"""给 LLM 的上下文：template 参考库 + 运行环境。

设计约束（重要，改动前先读）：

1. 注入内容分为 **stable** 与 **dynamic** 两段。
   - stable：template 全文、工具约定。每次调用逐字节相同，用于命中服务端的
     前缀缓存（DeepSeek/OpenAI 的 prompt_cache_hit_tokens）。
   - dynamic：工作目录、操作系统。只有这些会变，放在 stable 之后，
     因此缓存前缀不会被破坏。

   往 stable 段里塞任何"每次都不一样"的东西（时间戳、随机数、git 状态、
   实时负载），都会让缓存永久 miss。这是隐性成本，不要这么做。

2. template 只是**文本**，注入给模型参考，绝不直接执行。
   执行路径保持唯一：LLM 生成 → sanitize → is_dangerous → 用户确认。

3. 本轮为全量注入（模板 ≤ FULL_INJECTION_LIMIT 时）。
   模板变多后改写 `select_templates()` 为关键词索引即可，接口已就位。
"""

import os
import re
import sys
from pathlib import Path

# stable 段保持逐字节稳定所需：目录遍历与渲染顺序必须确定
FULL_INJECTION_LIMIT = 24

TEMPLATE_DIRNAME = "templates"
USER_TEMPLATE_DIR = (".config", "woc", TEMPLATE_DIRNAME)
PROJECT_MARKERS = (".git", ".hg", "pyproject.toml", "go.mod", "package.json", "Cargo.toml")

FENCE_RE = re.compile(r"^\s*```")
ID_OK_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")

# 已知的可移植性坑：某些命令在非 GNU 用户态下会失败或行为不同。
# 这里只做提示，不阻断。
PORTABILITY_NOTES = (
    (re.compile(r"\bfind\b[^|;]*\s-printf\b"),
     "`find -printf` 是 GNU 扩展；BSD/macOS 上会直接报错。"),
    (re.compile(r"\bsed\s+-i\s"),
     "`sed -i` 的备份参数语义在 GNU 与 BSD 上不同（BSD 需 `-i ''`）。"),
    (re.compile(r"\bgrep\b[^|;]*\s-P\b"),
     "`grep -P` 依赖 PCRE，BSD/macOS 上不可用；改用 `-E`。"),
    (re.compile(r"\breadlink\b|\bstat\s+-c\b"),
     "`readlink -f` / `stat -c` 是 GNU 行为，BSD 上参数不同。"),
)


def _warn(msg):
    sys.stderr.write(f"[woc] {msg}\n")
    sys.stderr.flush()


def _env_flag(name, default=True):
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() not in ("", "0", "false", "no", "off")


class Template:
    """一个参考模板：front matter 元数据 + 原样正文。"""

    __slots__ = ("id", "desc", "tags", "when", "body", "path")

    def __init__(self, id, desc="", tags=(), when="", body="", path=None):
        self.id = id
        self.desc = desc
        self.tags = tuple(tags)
        self.when = when
        self.body = body
        self.path = path

    def as_unit(self):
        """渲染成注入给模型的一个单元。正文原样保留（含"不要这样做"等章节）。"""
        head = [f"### {self.id}"]
        if self.desc:
            head.append(f"用途：{self.desc}")
        if self.tags:
            head.append(f"标签：{', '.join(self.tags)}")
        if self.when:
            head.append(f"适用：{self.when}")
        return "\n".join(head) + "\n\n" + self.body.strip()

    def haystack(self):
        return " ".join((self.id, self.desc, self.when, " ".join(self.tags))).lower()


# ---------------- front matter 解析 ----------------

def parse_front_matter(text):
    """解析简单的 `key: value` front matter。

    只支持本项目需要的子集：裸值、引号值、行内列表 `[a, b]`。
    不引入 PyYAML —— 这是纯标准库项目。
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text

    meta = {}
    index = 1
    closed = False
    while index < len(lines):
        raw = lines[index]
        if raw.strip() == "---":
            closed = True
            index += 1
            break
        line = raw.strip()
        if line and not line.startswith("#") and ":" in line:
            key, value = line.split(":", 1)
            key = key.strip().lower()
            value = value.strip()
            if value.startswith("[") and value.endswith("]"):
                items = [item.strip().strip("'\"") for item in value[1:-1].split(",")]
                meta[key] = tuple(item for item in items if item)
            else:
                meta[key] = value.strip("'\"")
        index += 1

    if not closed:
        raise ValueError("front matter 未闭合（缺少结尾的 --- 行）")
    return meta, "\n".join(lines[index:])


def parse_template_file(path):
    """读取并解析单个模板文件。"""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ValueError(f"读取失败: {exc}") from exc

    meta, body = parse_front_matter(text)
    tid = str(meta.get("id") or path.stem).strip()
    if not ID_OK_RE.match(tid):
        raise ValueError(f"id 含非法字符: {tid!r}（用字母/数字/下划线/连字符/中文）")

    tags = meta.get("tags") or ()
    if isinstance(tags, str):
        tags = tuple(t.strip() for t in tags.split(",") if t.strip())

    return Template(
        id=tid,
        desc=str(meta.get("desc", "")).strip(),
        tags=tags,
        when=str(meta.get("when", "")).strip(),
        body=body.strip(),
        path=str(path),
    )


# ---------------- 定位与加载 ----------------

def find_project_dir(start=None):
    """向上查找最近的项目根目录（含 .git 等标记）。找不到返回 None。"""
    try:
        current = Path(start or os.getcwd()).resolve()
    except OSError:
        return None
    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return None


def template_dirs(cwd=None):
    """返回模板目录列表，顺序即优先级（后者覆盖前者）。

    项目级放前、用户级放后，这样同名 id 时用户级会覆盖项目级 —— 与配置文件的
    "env 优先"惯例相反，这里刻意让**用户级**能兜底覆盖项目级，因为
    ~/.config/woc 是用户自己的、更可信的来源。
    """
    dirs = []
    if _env_flag("WOC_TEMPLATES_PROJECT", True):
        project = find_project_dir(cwd)
        if project is not None:
            dirs.append(project / ".woc" / TEMPLATE_DIRNAME)
    if _env_flag("WOC_TEMPLATES_USER", True):
        home = Path(os.path.expanduser("~"))
        dirs.append(home.joinpath(*USER_TEMPLATE_DIR))
    return dirs


def load_templates(cwd=None, warn=None):
    """加载所有模板。返回 (templates, sources)。

    单个文件出错只告警并跳过，绝不让整个 woc 挂掉 —— 模板是用户手写的，
    格式错误是常态，不能因此阻断命令生成。
    """
    warn = warn or _warn
    sources = []
    merged = {}
    order = []

    for directory in template_dirs(cwd):
        sources.append((str(directory), directory.is_dir()))
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.md")):
            try:
                tpl = parse_template_file(path)
            except ValueError as exc:
                warn(f"模板已跳过 {path}: {exc}")
                continue
            if tpl.id in merged:
                warn(f"模板 {tpl.id!r} 被覆盖: {merged[tpl.id].path} -> {path}")
            else:
                order.append(tpl.id)
            merged[tpl.id] = tpl

    return [merged[tid] for tid in order], sources


# ---------------- 检索接口（本轮为全量） ----------------

def select_templates(templates, query="", limit=FULL_INJECTION_LIMIT):
    """选择要注入的模板。当前策略：全量（按 id 排序，保证前缀稳定）。

    模板数量超过 limit 后，改写这里为关键词打分即可 —— 调用方无需改动。
    返回 (selected, strategy_name)。
    """
    ordered = sorted(templates, key=lambda t: t.id)
    if len(ordered) <= limit:
        return ordered, "full"

    terms = [t for t in re.split(r"[\s,，。;；:：!！?？/]+", query.lower()) if len(t) >= 2]
    scored = []
    for tpl in ordered:
        haystack = tpl.haystack()
        score = sum(1 for term in terms if term in haystack)
        scored.append((score, tpl.id, tpl))
    scored.sort(key=lambda item: (-item[0], item[1]))
    picked = [tpl for score, _, tpl in scored if score > 0][:limit]
    if not picked:
        picked = ordered[:limit]
    return picked, "keyword"


def render_templates(templates):
    """把模板渲染成注入文本。逐字节确定 —— 不要在这里掺入时间等易变内容。"""
    units = "\n\n".join(tpl.as_unit() for tpl in templates)
    return (
        "<templates>\n"
        "以下是你在这个环境里积累的、已经验证过的命令参考。它们是**参考**，不是必须"
        "照抄：可以改写以适应本次请求的具体参数。\n"
        "冲突时的优先级：用户本次的明确要求 > <environment> 里的环境事实 > 这些模板 >"
        " 上面的通用规则。\n"
        "需要同时满足的多个子问题，请合并成**一条**命令，一次答完。\n"
        "（用户用 @id 提及时，表示他指定要用那个模板。）\n\n"
        f"{units}\n"
        "</templates>"
    )


def render_environment(cwd=None, platform=None):
    """dynamic 段：只放会变的环境事实，必须排在 stable 段之后。"""
    cwd = cwd or os.getcwd()
    platform = platform or os.uname().sysname
    return (
        "<environment>\n"
        f"current working directory: {cwd}\n"
        f"platform: {platform} (shell: bash)\n"
        "用户在终端里，命令执行前会有人工确认。\n"
        "</environment>"
    )


def stable_context(query="", cwd=None, templates=None, warn=None):
    """组装 stable 段：template 参考库。无模板时返回 ""。"""
    if templates is None:
        if not _env_flag("WOC_TEMPLATES", True):
            return ""
        templates, _ = load_templates(cwd=cwd, warn=warn)
    if not templates:
        return ""
    selected, _strategy = select_templates(templates, query=query)
    if not selected:
        return ""
    return render_templates(selected)


# ---------------- 消息组装 ----------------

def build_messages(user_input, system_prompt, cwd=None, multi_turn_hint=None,
                   query=None, with_templates=True, with_environment=True,
                   templates=None):
    """组装一次请求的 messages。

    顺序刻意如此：stable 内容在前、dynamic 内容在后、用户输入最后。
    前缀缓存命中的就是 `system_prompt (+ multi_turn_hint) + <templates>` 这段。
    """
    messages = [{"role": "system", "content": system_prompt}]
    if multi_turn_hint:
        messages.append({"role": "system", "content": multi_turn_hint})
    if with_templates:
        block = stable_context(query=query if query is not None else user_input,
                               cwd=cwd, templates=templates)
        if block:
            messages.append({"role": "system", "content": block})
    if with_environment:
        messages.append({"role": "system", "content": render_environment(cwd=cwd)})
    messages.append({"role": "user", "content": user_input})
    return messages


def messages_prefix(multi_turn=False, system_prompt="", multi_turn_hint=None,
                   cwd=None, templates=None):
    """只要 system 前缀（多轮会话每轮复用的部分），不含 user 消息。"""
    stable = [{"role": "system", "content": system_prompt}]
    if multi_turn and multi_turn_hint:
        stable.append({"role": "system", "content": multi_turn_hint})
    block = stable_context(cwd=cwd, templates=templates)
    if block:
        stable.append({"role": "system", "content": block})
    stable.append({"role": "system", "content": render_environment(cwd=cwd)})
    return stable


# ---------------- 模板自检 ----------------

def extract_code_blocks(body):
    """抽取 markdown 围栏代码块，返回 [(lang, [line, ...]), ...]。"""
    blocks = []
    lang = None
    pending = None
    for line in body.splitlines():
        if FENCE_RE.match(line):
            if pending is None:
                lang = line.strip().strip("`").strip()
                pending = []
            else:
                blocks.append((lang, pending))
                lang, pending = None, None
            continue
        if pending is not None:
            pending.append(line)
    if pending is not None:
        blocks.append((lang, pending))
    return blocks


def command_lines(lines):
    """从代码块里挑出"看起来是命令"的行，忽略空行与 # 注释。

    行首的 "$ " 是 shell 提示符，要去掉；但行首单独的 "$" 可能是字段符号，
    所以只认 "$ "（带空格）这种形态。
    """
    out = []
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("$ "):
            line = line[2:].strip()
        if line:
            out.append(line)
    return out


def lint_templates(templates):
    """模板自检：返回 (errors, warnings)，均为 [(template_id, message), ...]。

    重点抓两类问题：
    - 参考命令会被 `sanitize` 截断（多行）或被 `is_dangerous` 拦截（子串误报）；
    - 已知的可移植性坑。
    """
    from .sanitize import is_dangerous

    errors = []
    warnings = []
    for tpl in templates:
        blocks = extract_code_blocks(tpl.body)
        if not blocks:
            warnings.append((tpl.id, "没有任何 ``` 代码块，模型无从参考"))
        if not tpl.desc:
            warnings.append((tpl.id, "front matter 缺少 desc，影响后续检索"))
        if not tpl.tags:
            warnings.append((tpl.id, "front matter 缺少 tags，影响后续检索"))

        for _lang, lines in blocks:
            candidates = command_lines(lines)
            if len(candidates) > 1:
                warnings.append((
                    tpl.id,
                    f"代码块含 {len(candidates)} 行命令；`sanitize` 只取第一行，"
                    "其余会被截断。建议合并成单行（用 && 或 ;）",
                ))
            for line in candidates:
                if is_dangerous(line):
                    errors.append((
                        tpl.id,
                        f"参考命令会被 is_dangerous 拦截（子串匹配误报）: {line[:70]}",
                    ))
                for pattern, note in PORTABILITY_NOTES:
                    if pattern.search(line):
                        warnings.append((tpl.id, f"可移植性: {note} → {line[:60]}"))
    return errors, warnings
