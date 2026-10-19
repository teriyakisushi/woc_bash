"""给 LLM 的上下文：template 参考库 + 运行环境。

设计约束（重要，改动前先读）：

1. 注入内容分为 **stable** 与 **dynamic** 两段。
   - stable：template 索引 + 正文、通用规则。前缀逐字节相同时命中服务端
     前缀缓存（DeepSeek/OpenAI 的 prompt_cache_hit_tokens）。
   - dynamic：工作目录、平台、发行版、uid/groups、容器类型。只有这些会变，
     放在 stable 之后。

   往 stable 段里塞任何"每次都不一样"的东西（时间戳、随机数、git 状态、
   实时负载），都会让缓存永久 miss。这是隐性成本，不要这么做。

2. template 分两层注入，**两种预算互不挤占**：
   - 索引层（恒定，永远全量）：`id | desc | 适用`，让模型知道"有什么"。
   - 正文层（按预算）：完整正文，让模型知道"怎么写"。
   **不要**用检索结果去砍索引 —— 那会让模型不知道未命中的模板存在。
   检索只决定正文给谁。

3. template 只是**文本**，注入给模型参考，绝不直接执行。
   执行路径保持唯一：LLM 生成 → sanitize → is_dangerous → 用户确认。

4. 目录快照（render_listing）默认关闭，由 [context] ls_on_start 控制。
   `wocc` 只在启动时生成一次，并作为 stable 前缀的一部分复用；
   `woc` 单轮每次调用生成。它属于 dynamic 事实，不要写进模板或 system prompt。
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from . import debug

# 索引层：模板数上限（超过就只保留前 N 个的索引，防极端情况撑爆上下文）
INDEX_LIMIT = 100
# 正文层：注入正文的字符预算。模板少时等于全量，多时自动收敛。
BODY_BUDGET = 4000

TEMPLATE_DIRNAME = "templates"
USER_TEMPLATE_DIR = (".config", "woc", TEMPLATE_DIRNAME)
PROJECT_MARKERS = (".git", ".hg", "pyproject.toml", "go.mod", "package.json", "Cargo.toml")

FENCE_RE = re.compile(r"^\s*```")
ID_OK_RE = re.compile(r"^[A-Za-z0-9_.\-\u4e00-\u9fff]+$")
# 形如 `NAME=value` 的纯赋值行（不是 `NAME=value cmd`）
ASSIGN_ONLY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=(?:[^;|&]*|\$\([^)]*\)|\"[^\"]*\"|'[^']*')$")

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
    from .config import load_config, prefix
    name = prefix(load_config(), "alias")
    sys.stderr.write(f"[{name}] {msg}\n")
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
        dirs.append(user_template_dir())
    return dirs


def _same_content(path_a, path_b):
    """两个模板文件内容是否逐字节相同。

    用于 `--install-templates` 之后的常态：用户级是项目级的副本，此时覆盖是
    预期的，不该每次都刷告警。读失败时按"不同"处理，宁可多提示。
    """
    try:
        return Path(path_a).read_bytes() == Path(path_b).read_bytes()
    except OSError:
        return False


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
                old = merged[tpl.id]
                if not _same_content(old.path, str(path)):
                    warn(f"模板 {tpl.id!r} 被覆盖: {old.path} -> {path}")
            else:
                order.append(tpl.id)
            merged[tpl.id] = tpl

    return [merged[tid] for tid in order], sources


# ---------------- 检索接口（本轮为全量） ----------------

def select_index(templates, limit=INDEX_LIMIT):
    """索引层：恒定全量（按 id 排序，保证前缀稳定）。

    刻意**不**接受 query —— 索引是"有什么"的清单，任何按查询裁剪索引的做法都会
    让模型不知道未命中模板的存在。裁剪只发生在正文层。
    """
    ordered = sorted(templates, key=lambda t: t.id)
    return ordered[:limit]


def query_terms(query):
    """切词。中文连续书写，所以除了按标点空白切，还要保留 2 字滑窗。

    `'看看日志'` 按标点切只能得到整串，匹配不到 tag `'日志'`；补上滑窗后才能命中。
    """
    lowered = (query or "").lower()
    terms = set()
    for chunk in re.split(r"[\s,;:!?/、()（）\[\]【】。，；：！？]+", lowered):
        if not chunk:
            continue
        terms.add(chunk)
        if all(ord(ch) > 0x2E80 for ch in chunk) and len(chunk) > 2:
            # 纯 CJK 且没有分隔符：加 2 字滑窗，避免整串当一个词
            terms.update(chunk[i:i + 2] for i in range(len(chunk) - 1))
    return {t for t in terms if len(t) >= 2}


def score_template(tpl, query):
    """给模板打分。标签命中权重最高 —— tags 是用户为检索专门写的字段。

    用"标签是查询的子串"而不是"查询词命中标签"，因为中文查询里标签总是更短的一侧：
    `'看看日志'` 含 `'日志'`。
    """
    lowered = (query or "").lower()
    score = 0
    for tag in tpl.tags:
        tag_l = tag.lower().strip()
        if tag_l and tag_l in lowered:
            score += 10
    if tpl.id.lower() in lowered:
        score += 20
    haystack = tpl.haystack()
    for term in query_terms(query):
        if term in haystack:
            score += 1
    return score


def select_bodies(templates, query="", budget=BODY_BUDGET):
    """正文层：按查询相关度 + 字符预算挑选。

    返回 (selected, strategy, scores)。strategy ∈ {"full", "ranked"}。
    - 全部正文装得下预算 → full（行为与"全量注入"完全一致）
    - 装不下 → ranked，按分数降序填充，同分按 id 保证确定性
    """
    ordered = sorted(templates, key=lambda t: t.id)
    scores = {t.id: score_template(t, query) for t in ordered}
    if sum(len(t.as_unit()) for t in ordered) <= budget:
        return ordered, "full", scores

    ranked = sorted(ordered, key=lambda t: (-scores[t.id], t.id))
    picked, used = [], 0
    for tpl in ranked:
        size = len(tpl.as_unit())
        if picked and used + size > budget:
            continue
        picked.append(tpl)
        used += size
    # 保持 id 顺序，让前缀在相同选择下逐字节稳定
    picked.sort(key=lambda t: t.id)
    return picked, "ranked", scores


def select_templates(templates, query="", limit=None, budget=None):
    """兼容旧接口：返回 (正文层选中, 策略)。新代码请用 select_index/select_bodies。"""
    if budget is None:
        budget = BODY_BUDGET
        if limit is not None:
            ordered = sorted(templates, key=lambda t: t.id)
            if ordered:
                budget = max(len(t.as_unit()) for t in ordered) * limit
    selected, strategy, _scores = select_bodies(templates, query=query, budget=budget)
    return selected, strategy


def render_index(templates):
    """索引层文本：id | desc | 适用 | tags。紧凑，永远全量。"""
    lines = []
    for tpl in templates:
        parts = [f"- {tpl.id}: {tpl.desc or '(未写 desc)'}"]
        if tpl.when:
            parts.append(f"适用：{tpl.when}")
        if tpl.tags:
            parts.append(f"tags: {', '.join(tpl.tags)}")
        lines.append(" | ".join(parts))
    return "可用模板索引：\n" + "\n".join(lines)


def render_templates(templates, index=None, strategy="full"):
    """把模板渲染成注入文本。逐字节确定 —— 不要在这里掺入时间等易变内容。

    index 非空时先给索引，再给正文；正文只覆盖索引的一个子集。
    """
    head = [
        "<templates>",
        "以下是你在这个环境里积累的命令参考。它们是**用户自己维护的笔记，没有经过"
        "任何验证**，也不是必须照抄：可以改写以适应本次请求的具体参数。",
        "**这些模板不享有任何权限豁免。** 由它派生出的命令与其它命令完全同等对待，"
        "同样要经过安全检查与人工确认。不要因为它是模板就默认它安全。",
        "（模板正文里的「已验证」只是作者自己的标注，不代表任何保证；以实际环境为准。）",
        "冲突时的优先级：用户本次的明确要求 > <environment> 里的环境事实 > 这些模板 >"
        " 上面的通用规则。",
        "需要同时满足的多个子问题，请合并成**一条**命令，一次答完。",
        "（用户用 @id 提及时，表示他指定要用那个模板。）",
    ]
    sections = []
    if index:
        head.append(
            "下面是全部可用模板的索引。附有正文的模板标注在正文区；只有索引的模板"
            "需要时可按 id 向用户确认。")
        sections.append(render_index(index))
    if templates:
        units = "\n\n".join(t.as_unit() for t in templates)
        sections.append(("以下是相关模板的完整正文：\n\n" + units) if index else units)
    return "\n".join(head) + "\n\n" + "\n\n".join(sections) + "\n</templates>"


_STATIC_FACTS = None


def _read_os_pretty_name():
    try:
        with open("/etc/os-release", encoding="utf-8") as handle:
            for line in handle:
                key, _, value = line.strip().partition("=")
                if key == "PRETTY_NAME" and value:
                    return value.strip().strip("'\"")
    except OSError:
        pass
    return ""


def _detect_container():
    if os.path.exists("/.dockerenv"):
        return "docker"
    if os.path.exists("/run/.containerenv"):
        return "podman"
    try:
        with open("/proc/version", encoding="utf-8") as handle:
            if "microsoft" in handle.read().lower():
                return "wsl"
    except OSError:
        pass
    return ""


def _identity_facts():
    facts = []
    getuid = getattr(os, "getuid", None)
    getgid = getattr(os, "getgid", None)
    if getuid is None or getgid is None:
        return facts
    facts.append(f"uid: {getuid()} (gid {getgid()})")
    try:
        import grp
        names = []
        for gid in [getgid(), *os.getgroups()]:
            try:
                name = grp.getgrgid(gid).gr_name
            except KeyError:
                continue
            if name not in names:
                names.append(name)
    except (ImportError, OSError, AttributeError):
        return facts
    if names:
        facts.append("groups: " + ", ".join(names[:12]))
    return facts


def _static_facts():
    """进程内常量事实：发行版、uid/groups、容器类型。

    只算一次，保证多次 build_messages 的 <environment> 逐字节稳定。
    """
    global _STATIC_FACTS
    if _STATIC_FACTS is None:
        facts = []
        name = _read_os_pretty_name()
        if name:
            facts.append(f"os: {name}")
        facts.extend(_identity_facts())
        container = _detect_container()
        if container:
            facts.append(f"container: {container}")
        _STATIC_FACTS = facts
    return _STATIC_FACTS


LS_MAX_LINES = 200
LS_MAX_CHARS = 4000


def _run_ls(cwd):
    try:
        proc = subprocess.run(
            ["ls", "-alF", "--color=never", cwd],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", "replace").rstrip("\n")


def render_listing(cwd=None, max_lines=LS_MAX_LINES, max_chars=LS_MAX_CHARS):
    """启动时对当前目录做一次 `ls -alF` 快照，供模型参考。

    只在调用方要求时生成（见 [context] ls_on_start）；内容可能很快过期，
    所以提示里明确要求模型不要硬编码文件名。
    """
    cwd = cwd or os.getcwd()
    text = _run_ls(cwd)
    if not text:
        return ""
    lines = text.splitlines()
    truncated = False
    if len(lines) > max_lines:
        lines = lines[:max_lines]
        truncated = True
    body = "\n".join(lines)
    if len(body) > max_chars:
        body = body[:max_chars]
        truncated = True
    header = (
        "启动时对当前目录执行 `ls -alF` 的快照。它可能已经过期：不要硬编码这里的"
        "文件名，需要准确结果时请生成新的命令去读取。"
    )
    if truncated:
        header += "（输出已截断）"
    return "\n".join([
        "<directory_listing>",
        header,
        f"directory: {cwd}",
        body,
        "</directory_listing>",
    ])


def render_environment(cwd=None, platform=None, facts=None, confirm_hint=True):
    """dynamic 段：只放会变的环境事实，必须排在 stable 段之后。

    facts 不传时使用进程内缓存的事实（发行版 / uid / groups / 容器类型）。
    confirm_hint=False 用于管道模式：那里没有命令执行，也就没有“人工确认”。
    """
    cwd = cwd or os.getcwd()
    if platform is None:
        uname = getattr(os, "uname", None)
        platform = uname().sysname if uname else "Linux"
    lines = [
        "<environment>",
        f"current working directory: {cwd}",
        f"platform: {platform} (shell: bash)",
    ]
    lines.extend(facts if facts is not None else _static_facts())
    if confirm_hint:
        lines.append("用户在终端里，命令执行前会有人工确认。")
    lines.append("</environment>")
    return "\n".join(lines)


def stable_context(query="", cwd=None, templates=None, warn=None, detail=None):
    """组装 stable 段：索引层（恒定）+ 正文层（按预算）。

    detail 传入 dict 时会把选择结果写回去，供 --templates 展示。
    """
    if templates is None:
        if not _env_flag("WOC_TEMPLATES", True):
            return ""
        templates, _ = load_templates(cwd=cwd, warn=warn)
    if not templates:
        return ""
    index = select_index(templates)
    bodies, strategy, scores = select_bodies(templates, query=query)
    if debug.enabled():
        debug.log(
            "templates: %d total, strategy=%s, bodies=%d, index=%d chars, body=%d chars"
            % (len(templates), strategy, len(bodies),
               len(render_index(index)),
               sum(len(t.as_unit()) for t in bodies)))
    if detail is not None:
        detail.update(
            total=len(templates),
            indexed=len(index),
            bodies=[t.id for t in bodies],
            strategy=strategy,
            scores=scores,
            index_chars=len(render_index(index)),
            body_chars=sum(len(t.as_unit()) for t in bodies),
        )
    return render_templates(bodies, index=index, strategy=strategy)


# ---------------- 消息组装 ----------------

def build_messages(user_input, system_prompt, cwd=None, multi_turn_hint=None,
                   query=None, with_templates=True, with_environment=True,
                   templates=None, with_listing=False):
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
    if with_listing:
        listing = render_listing(cwd=cwd)
        if listing:
            messages.append({"role": "system", "content": listing})
    messages.append({"role": "user", "content": user_input})
    return messages


def messages_prefix(multi_turn=False, system_prompt="", multi_turn_hint=None,
                   cwd=None, templates=None, with_listing=False):
    """只要 system 前缀（多轮会话每轮复用的部分），不含 user 消息。"""
    stable = [{"role": "system", "content": system_prompt}]
    if multi_turn and multi_turn_hint:
        stable.append({"role": "system", "content": multi_turn_hint})
    block = stable_context(cwd=cwd, templates=templates)
    if block:
        stable.append({"role": "system", "content": block})
    stable.append({"role": "system", "content": render_environment(cwd=cwd)})
    if with_listing:
        listing = render_listing(cwd=cwd)
        if listing:
            stable.append({"role": "system", "content": listing})
    return stable


# ---------------- 安装到全局 ----------------

def user_template_dir():
    """用户级模板目录。遵循 XDG_CONFIG_HOME，默认 ~/.config/woc/templates。"""
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg).expanduser() / "woc" / TEMPLATE_DIRNAME
    return Path(os.path.expanduser("~")).joinpath(*USER_TEMPLATE_DIR)


def project_template_dir(cwd=None):
    """当前项目级模板目录；不构成项目时返回 None。"""
    project = find_project_dir(cwd)
    if project is None:
        return None
    return project / ".woc" / TEMPLATE_DIRNAME


def install_templates(ids=None, cwd=None, dry_run=False, warn=None, project_dir=None):
    """把项目级模板复制到用户级，使其在所有项目里可用。

    ids 为空表示全部。project_dir 显式指定来源目录（不指定则按 cwd 向上查找项目根）。

    返回 (copied, skipped, missing, failed)：
    - copied：本次安装/覆盖的 id
    - skipped：已是最新（内容相同），未改动
    - missing：指定的 id 在项目级里不存在
    - failed：写入失败的 (id, 原因)

    写失败不抛异常：模板是用户手写的附属内容，装不上不该让整个 woc 崩掉。
    """
    warn = warn or _warn
    if project_dir is not None:
        src_dir = Path(project_dir) / ".woc" / TEMPLATE_DIRNAME
    else:
        src_dir = project_template_dir(cwd)
    if src_dir is None or not src_dir.is_dir():
        return [], [], list(ids or []), []

    available = {}
    for path in sorted(src_dir.glob("*.md")):
        try:
            tpl = parse_template_file(path)
        except ValueError as exc:
            warn(f"模板已跳过 {path}: {exc}")
            continue
        available[tpl.id] = (tpl, path)

    wanted = list(ids) if ids else sorted(available)
    missing = [i for i in wanted if i not in available]
    dest_dir = user_template_dir()
    copied, skipped, failed = [], [], []

    for tid in wanted:
        if tid not in available:
            continue
        _tpl, path = available[tid]
        dest = dest_dir / path.name
        try:
            same = dest.is_file() and dest.read_bytes() == path.read_bytes()
        except OSError:
            same = False
        if same:
            skipped.append(tid)
            continue
        if dry_run:
            # 会复制，但不落盘 —— 仍计入 copied，让调用方能列出"将要安装"的清单
            copied.append(tid)
            continue
        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, dest)
            # 全局模板是给人看/给模型读的，不该继承项目里可能有的 600 权限
            dest.chmod(0o644)
        except OSError as exc:
            reason = exc.strerror or str(exc)
            failed.append((tid, f"{reason}: {dest}"))
            continue
        copied.append(tid)
    return copied, skipped, missing, failed


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
    - 参考命令会被 `sanitize` 截断（多行）或被 `is_dangerous` 拦截（安全策略）；
    - 已知的可移植性坑。
    """
    from .sanitize import is_assignment_only, is_dangerous

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
                note = (f"代码块含 {len(candidates)} 行命令；`sanitize` 只取第一行，"
                        "其余会被截断。建议合并成单行（用 && 或 ;）")
                # 第一行是纯赋值时后果最严重：截断后既不产出输出，变量也不会被用到
                if is_assignment_only(candidates[0]):
                    errors.append((
                        tpl.id,
                        "第一行是纯变量赋值且后面还有命令 → `sanitize` 会报错拒绝执行。"
                        "必须合并成单行：" + candidates[0][:50] + " ; ...",
                    ))
                else:
                    warnings.append((tpl.id, note))
            for line in candidates:
                if is_dangerous(line):
                    errors.append((
                        tpl.id,
                        f"参考命令会被 is_dangerous 拦截（命令位置规则）: {line[:70]}",
                    ))
                for pattern, note in PORTABILITY_NOTES:
                    if pattern.search(line):
                        warnings.append((tpl.id, f"可移植性: {note} → {line[:60]}"))
    return errors, warnings
