# woc

`woc` is a natural-language command assistant that runs in your terminal. It turns everyday descriptions into Shell commands, answers questions, and can execute commands while carrying the results forward across multi-turn sessions.

> Want to change the code? Read **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** first: the seven invariants, the three boundaries, why the stable prefix must not move, the loop state machine, and a "where to look before you change what" cheat sheet.
> 中文版：[README.md](README.md)

## Features

- `woc`: single-turn generation and execution of Shell commands.
- `wocc`: multi-turn interactive session; each turn's command output is fed back to the model.
- `woc -c` / `woc_chat`: multi-turn chat mode with Markdown output.
- After a command is generated, it waits for confirmation; press `Enter` to execute.
- High-risk commands are blocked, e.g. deleting the root directory, formatting disks, and shutting down.
- Backspace editing of input; the colored prompt is not treated as editable text by readline.
- A one-line loading indicator while waiting for the model / running a command; terminal only, disable with `[ui] spinner`.
- Pipe mode: `dmesg | woc "analyze this"` treats stdin as data and only answers, never executes.
- `WOC_DEBUG=1` exposes usage, prefix-cache hits, and the size of each request.
- In-session `:steps` audits this turn's steps, `:explain` explains the previous command; `wocc --last-session` reattaches to the previous session.
- Chat mode (`woc -c`) searches the web automatically when needed (disable with `--no-search`); both `woc -c` and `wocc` accept `:search <keywords>` to search manually.

## Requirements

- Python 3.10 or newer
- Bash
- A service endpoint compatible with the OpenAI Chat Completions API
- An API Key for that service

The project uses only the Python standard library; no third-party dependencies are needed.

## Installation

Run this in the project root:

```bash
python3 compile.py --key sk-xxxx
```

The installer puts the commands in `~/bin` by default and tries to add that directory to your current Shell's config file. It asks for the log alias and three command names (press Enter for the defaults, see `[prefix]` below). It also installs a file read/write command `<woc command name>-fs` (default `woc-fs`): the name is derived from `woc_prefix` and does not take an extra config key.
You can also skip API Key configuration and set it later:

```bash
python3 compile.py --no-key
```

Specify the install directory:

```bash
python3 compile.py --prefix ~/.local/bin --key sk-xxxx
```

The installer also writes the full config template to `~/.config/woc/config.toml`: **only missing entries are added**, existing keys and values are never touched. Upgrading the installer never loses config, and new config items (e.g. `[search]`) land after one install.

After installation, if the current terminal still cannot find the commands, run the `source` command the installer prints, or open a new terminal.

## Configuration

Defaults, environment variables, and validation rules for every key are in **[docs/CONFIG.md](docs/CONFIG.md)**; below is a quick reference for the common parts.

### Environment variables

```bash
export WOC_API_KEY="sk-xxxx"
export WOC_API_BASE="https://api.deepseek.com"
export WOC_MODEL="deepseek-flash"
export WOC_TIMEOUT="30"
export WOC_MAX_OUTPUT_TOKENS="4096"   # same as [budget] max_output_tokens
export WOC_CAPTURE_SUDO="1"   # same as [sudo] capture_output = true
export WOC_CONTEXT_LS="1"     # same as [context] ls_on_start = true
export WOC_LOOP="1"           # same as [loop] enabled = true
```
export WOC_SYNTAX_CHECK="0"   # same as [exec] syntax_check = false
export WOC_SPINNER="0"        # same as [ui] spinner = false
export WOC_PIPE="0"           # same as [pipe] enabled = false
export WOC_PIPE_MAX_CHARS="32000"   # same as [pipe] max_chars
export WOC_MAX_INPUT_CHARS="32000"  # same as [budget] max_input_chars
export WOC_CONTEXT_TOKENS="64000"   # same as [budget] context_tokens
export WOC_FOLD_LINES="25"          # same as [ui] fold_lines
export WOC_THINKING="1"             # same as [thinking] enabled = true (off by default)
export WOC_THINKING_EFFORT="low"    # same as [thinking] effort (low/high/max)
export WOC_DEBUG="1"                # same as [debug] enabled = true
export WOC_SESSION_PERSIST="0"      # same as [session] persist = false
export WOC_SESSION_MAX_MESSAGES="40"
export WOC_SESSION_MAX_CHARS="60000"
export WOC_SEARCH="0"               # disable model-initiated search (on by default)
export WOC_SEARCH_COUNT="5"
export WOC_SEARCH_MAX="1"           # max searches per turn
```

### Config file

You can also create `~/.config/woc/config.toml`:

```toml
api_key = "sk-xxxx"
api_base = "https://api.deepseek.com"
model = "deepseek-flash"
timeout = 30

[prefix]
alias = "woc"
woc_prefix = "woc"
wocc_prefix = "wocc"
woc_chat_prefix = "woc_chat"

[sudo]
capture_output = false

[context]
ls_on_start = false

[loop]
enabled = false
max_steps = 5
total_chars = 24000
auto_safe = false

[exec]
syntax_check = true

[ui]
spinner = true

[thinking]
enabled = false            # DeepSeek enables it server-side; off here by default
effort = "high"            # low/high/max, only sent when enabled

[pipe]
enabled = true
# max_chars = 32000        # optional: defaults to budget.max_input_chars

[search]
enabled = true
count = 5
max_searches = 1
max_chars = 6000
timeout = 10
query_by_llm = true

[budget]
context_tokens = 64000     # model context window
max_output_tokens = 4096   # per-reply cap
max_input_chars = 32000    # per-request input (pipe data) cap

[debug]
enabled = false

[session]
persist = true
max_messages = 40
max_chars = 60000
```

Environment variables take precedence over the config file. The default API Base is `https://api.deepseek.com`, and the default model is `deepseek-flash`.

### Budget (`[budget]`)

Three numbers together decide "how big this request can be". Configuring them separately easily produces contradictory values (e.g. an input cap larger than the model window), so they live in one place:

| Key | Default | Purpose |
|---|---|---|
| `context_tokens` | 64000 | Model context window |
| `max_output_tokens` | 4096 | Per-reply cap (too small truncates commands/answers) |
| `max_input_chars` | 32000 | Per-request input (pipe data) cap, in characters |

Conversion uses a conservative 2 chars/token: effective input cap = `min(max_input_chars, (context_tokens - max_output_tokens - system segment) × 2)`, so setting `max_input_chars` very large still cannot overflow the context.
The legacy root-level `output_maxtokens` and `WOC_OUTPUT_MAXTOKENS` still work (equivalent to `[budget] max_output_tokens`); new config should go under `[budget]`.

### Thinking mode (`[thinking]`)

DeepSeek's thinking mode is **on by default server-side** (effort defaults to `high`); here it is **off by default**:

- In thinking mode the server **ignores `temperature`** ([official docs](https://api-docs.deepseek.com/guides/thinking_mode/)), while this tool relies on `temperature = 0` for determinism — with thinking on, that 0 is effectively not written;
- `woc` is a "type a sentence, get a command" interactive tool; the first-token latency from thinking lands right on the critical path;
- Thinking tokens count against `[budget] max_output_tokens` (default 4096) and may squeeze out the answer.

If you want a smarter model and are willing to wait, turn it on; pay the cost as needed:

```toml
# root level
timeout = 90                 # first token gets noticeably slower; 30s often times out

[thinking]
enabled = true
effort = "low"               # low / high / max; default high

[budget]
max_output_tokens = 16384    # thinking + answer share this cap
```

When disabled, the request explicitly carries `{"thinking": {"type": "disabled"}}` rather than omitting the field — omitting it means following the server default, and behavior would drift if that default ever changes. The chain of thought (`reasoning_content`) is neither displayed nor added to `wocc`'s session history; only the final answer counts.

### Debugging and observability (`[debug]`)

`WOC_DEBUG=1` (or `[debug] enabled = true`) prints observability info to **stderr**; stdout is unaffected, so pipe mode can still `| less`:

```text
[woc] debug: pipe: max_chars=32000 reserved=1140 chars
[woc] debug: pipe: stdin=1842 chars truncated=False
[woc] debug: request: model=deepseek-flash messages=3 chars=2900 max_tokens=4096 timeout=30s
[woc] debug: budget: context=64000tok output=4096tok input<=32000chars effective_input<=32000chars
[woc] debug: templates: 4 total, strategy=full, bodies=4, index=420 chars, body=2810 chars
[woc] debug: step 2/5: tier=confirm cmd='du -sh /var/log'
[woc] debug: llm: 2.31s first_token=0.84s
[woc] debug: usage: prompt=3456 completion=128 total=3584 cache_hit=3200 cache_miss=256
```

`cache_hit` / `cache_miss` in `usage` are the server-side prefix-cache hits — after changing the stable part of the prompt, these two numbers should stay stable (see `docs/DESIGN-agentic-loop.md` §7).

### Command names and prompt prefix (`[prefix]`)

`[prefix]` renames `woc` / `wocc` / `woc_chat`:

| Key | Purpose |
|---|---|
| `alias` | Prefix of every log line and prompt, and the name the assistant uses in the system prompt |
| `woc_prefix` | Command name of `woc` (what help/usage prints) |
| `wocc_prefix` | Command name of `wocc` |
| `woc_chat_prefix` | Command name of `woc_chat` |

`woc -c` follows `woc_prefix` (written `woc_prefix -c`) and cannot be renamed on its own. For example:

```toml
[prefix]
alias = "qwe"
woc_prefix = "qwe"
wocc_prefix = "qwee"
woc_chat_prefix = "qwe_chat"
```

Every prompt then carries the `[qwe]` prefix regardless of which command you ran, and the system prompt says `You are qwe, ...`; help and usage print the individual names, e.g. `qwee --help`. `compile.py` asks for these names during installation; press Enter to keep the current value.

### sudo output capture (`[sudo]`)

Off by default. When enabled, `wocc` runs a command through a pty (`script`) if it needs a sudo password and no ticket is cached: you still type the password into your terminal, but the command output is captured and fed back to the model as `<exec_result>`. You can also enable it for one session only:

```bash
wocc --capture-sudo
```

The matching environment variable is `WOC_CAPTURE_SUDO=1`. Anything root can read may contain secrets, so decide for yourself before enabling it. Captured text is redacted for fields like `password=`, `token=`, `api_key=`; the password itself is suppressed by sudo's own echo-off and never reaches the output. Single-turn `woc` never captures — it prints straight to your terminal.

### Directory snapshot at startup (`[context]`)

Off by default. When enabled:

- `woc` (single turn) injects an `ls -alF` snapshot of the current directory on every call;
- `wocc` builds it **once at startup** and reuses it as part of the session prefix (it does not re-run `ls` each turn);
- `woc_chat` never injects it.

`<environment>` also carries your interactive shell (`$SHELL`); commands are always executed by bash, and the model is responsible for translating zsh/fish-only syntax. A snapshot can go stale, so the injected text explicitly tells the model not to hard-code file names and to re-run a command when it needs an exact result. Output is capped at 200 lines / 4000 chars, with an explicit "truncated" marker. Environment variable: `WOC_CONTEXT_LS=1`.

### Agentic loop (`[loop]`)

Off by default. When enabled, `wocc` lets the model take several steps **inside one turn**: the model continues explicitly with `#STEP: `. Once a turn has seen `#STEP:`, it enters "sticky" mode — plain commands keep going until `#CHAT:` gives a conclusion, or the step/budget cap is hit.

```bash
wocc --loop        # this session only
```

```toml
[loop]
enabled = false        # same as --loop / WOC_LOOP=1
max_steps = 5          # steps per turn; config wins, hard cap 50
total_chars = 24000    # accumulated output budget per turn
auto_safe = false      # run read-only single commands without confirmation
```

- A turn that never used `#STEP:` is still a single-command turn: one request, one execution.
- After `#STEP:` appears, plain commands no longer end the turn, so the model does not stop mid-investigation just because it forgot a marker, and you do not have to type "continue". Only `#CHAT:` / `#ERROR:` end it.
- `#STEP:` stays in the assistant history (same reason as `#CHAT:`), otherwise the model drifts back to bare commands.
- With `auto_safe=false` (the default) every step still waits for Enter. Setting it to `true` only auto-runs **read-only single commands** such as `ls`, `cat`, `grep`, `wc`. Anything with a pipe, redirection, `$(...)` or `;` still needs confirmation, and `find`, `sort`, `awk`, `sed` are not on the list (they can write via `-exec` / `-o`, or run code).
- Each step shows `[step i/N]` in the spinner and the run label; hitting the step or output cap is reported, never silent.

### Web search (`[search]`)

**On by default, chat mode only (`woc -c`).** When the model judges that it needs live information (news, versions, prices, docs that may be stale) it searches once before answering. One search is a **second HTTP request** (DeepSeek Search API): extra latency, extra billing. The search itself is quiet, but every answer is followed by a sources line.

```bash
woc -c "latest DeepSeek version"              # searches when needed
woc -c --no-search "latest DeepSeek version"  # no automatic search this session
```

Inside a session you can also drive it manually:

```text
[user] :search gitea latest release
[woc] query: gitea latest release (from: gitea latest release)
[woc] searched "gitea latest release", 5 results
[assistant] According to the results, the latest stable Gitea CE is v28.2.0 [4][5].
[woc] sources: [4] gitea.com · [5] github.com

[user] :search off                 # stop autonomous search; :search on re-enables it
```

```toml
[search]
enabled = true         # the model may emit #SEARCH:; false or --no-search disables it
count = 5              # results per search (1-50)
max_searches = 1       # searches per turn
max_chars = 6000       # total characters injected into the context
timeout = 10           # independent of the root-level timeout=30
query_by_llm = true    # :search query: true = model distills keywords, false = verbatim
# base_url = ""        # empty = follow api_base, /v1 appended automatically
```

Three ways to use it:

- **Automatic**: the model replies `#SEARCH: <query>`, woc searches, appends `<search_results>` to the context, then asks the model again. The marker never reaches the screen; success prints nothing, failure prints one line and tells the model plainly that it cannot search and must not invent links.
- **Manual**: `:search <query>` searches once and then **immediately asks the model to answer from the results** (no extra "go ahead" needed). It ignores `--no-search` / `enabled` — that switch governs the model, not you. By default the model distills the query from your sentence (`[search] query_by_llm`); an empty `:search` infers it from the conversation so far. When the query differs from your words it prints `query: ... (from: ...)` so you can see what was searched.
- **Switch**: `:search off` / `:search on` toggles autonomous search for this session only, no restart. It edits the system prompt, so the prefix cache rebuilds once. `wocc` has no autonomous search, so the command only prints a hint there instead of searching for the word "off".

A few boundaries:

- Only a `summary` (roughly 600-800 words) is fetched, never the full page; past `max_chars` results are truncated by relevance;
- **There is always a sources line after the answer** (`[n] domain`, up to 5): what is quiet is the search, not the audit trail;
- The hit list (titles, URLs, count, latency) still only prints with `WOC_DEBUG=1`;
- Results are appended at the end of the messages and **never enter the stable prefix**, so the prefix cache is unaffected;
- Search results are **third-party untrusted text**; the prompt says so explicitly. Chat mode executes nothing in the first place, so this path does not widen the execution surface;
- `#SEARCH:` exists only in `woc -c`: single-turn `woc` rejects it (a comment line is not a command), and `wocc` never lets the model search on its own — manual `:search` only (also with a sources line). Design notes and next steps: [docs/DESIGN-web-search.md](docs/DESIGN-web-search.md).

### Command syntax check (`[exec]`)

On by default. Before showing a command and asking for confirmation, it runs `bash -n -c` — **parse only, never execute**. A syntax error never reaches the confirmation prompt and never runs; in `wocc --loop` the error is fed back to the model and the loop continues, while single-turn `woc` exits with an error. Stray `</br>` / `</exec_result>` artifacts from the model are caught here. Environment variable `WOC_SYNTAX_CHECK=0` disables it.

```toml
[exec]
syntax_check = true
```

### Spinner and folding (`[ui]`)

On by default, only when `stderr` is a terminal. While waiting for the model it shows `[woc] Thinking` with a spinner and elapsed time; when `wocc` runs a captured command it shows `[woc] Step 2/10 - Running`. The line is cleared as soon as output starts or the command finishes. Shared by `woc`, `wocc` and `woc -c`; silent when piped or redirected.

```toml
[ui]
spinner = true
fold_lines = 25   # fold stdout longer than this; 0 disables folding
```

Environment variable `WOC_SPINNER=0`; `WOC_NO_SPINNER=1` also turns it off for one run.

Never commit a config file that contains your API key, and do not share it.

## Template library

Templates are **reference text written for the model**, used to pin down the commands you have accumulated in this environment and to cut down on guessing and back-and-forth confirmation. They are text only: injected as reference, **never executed**.

**Templates enjoy no privilege whatsoever.** A command derived from a template is treated exactly like any other command and goes through the same dangerous-pattern check and human confirmation. Templates are notes you maintain yourself, not a trusted source — doubly so for templates from a cloned repository.

### Where templates live

```
<project>/.woc/templates/*.md      # project level
~/.config/woc/templates/*.md       # user level; same id overrides the project one
```

Command-generating entry points (`woc`, `wocc`) load and inject them automatically; chat mode (`woc -c`) does not.

### Template format

```markdown
---
id: top-ip-from-log
desc: Top N client IPs by request count
tags: [nginx, log, statistics, awk]
when: user asks "which IP gets the most requests"
---

# Scenario
Analyse a web access log and find the busiest client.

# Reference command (annotated by the author, not verified)
awk '{print $1}' /var/log/nginx/access.log | sort | uniq -c | sort -rn | head -n 10

# Conventions
- In combined format the IP is always column 1.

# Do not do this
- Do not use `cut -d' ' -f1`: the request line contains spaces and the column shifts in some variants.
```

`id` is optional and defaults to the file name. `desc` / `tags` / `when` drive retrieval, so fill in all three. The body is injected **verbatim**, so sections like "Do not do this" are read as written.

### Management commands

```bash
woc --templates                                   # list templates, sources, and the two-layer policy
woc --templates --query "which IP hits us most"   # preview which bodies a query would select
woc --check-templates                             # self-check: portability traps, multi-line truncation
```

### Two-layer injection: constant index, budgeted bodies

Templates are injected in two layers with **separate budgets that do not squeeze each other**:

| Layer | Content | Policy |
|---|---|---|
| Index | `id \| desc \| when \| tags` | **Always complete**, so the model knows what exists |
| Body | The full template body | Query relevance plus a character budget (`BODY_BUDGET`, default 4000) |

The key rule: **retrieval only decides whose body is sent, never trims the index**. If the index were filtered by query, the model would not know the other templates exist. When the budget fits everything the policy is `full`, identical to injecting all bodies; otherwise it switches to `ranked`.

Relevance scoring (`select_bodies`):

- `tags` hits weigh 10, an `id` appearing verbatim in the query weighs 20;
- other terms are down-weighted by **inverse document frequency**: a word in one template weighs 1.0, a word in nearly every template approaches 0 (the current template set is the corpus);
- the same word in `when` counts 2.5, in `desc` 1.5, in the body 1.0;
- ASCII tags match on **word boundaries** (`log` does not hit `catalog`), while Chinese tags stay substring-based (with a 2-character sliding window, so "看看日志" still hits the tag `日志`);
- when the budget does not fit, templates scoring 0 get no body (pure noise); the index is still complete.

**So `tags` and `when` are not decoration** — how well you write them decides whether a template is selected at all.

### Installing globally

Project templates only apply inside this repository. To reuse them everywhere:

```bash
woc --install-templates                      # install every template of this project to the user level
woc --install-templates git-recent-changes    # only the listed ones
python3 compile.py --global-templates [ID...]  # equivalent entry point via the installer
python3 compile.py --list-templates            # see what is there first
```

Templates with identical content are skipped (shown as "up to date"), so running it repeatedly is safe. The user-level directory follows `XDG_CONFIG_HOME`, defaulting to `~/.config/woc/templates`.

### Injection order and caching

The injected message order is fixed:

```
system: general rules → multi-turn hint → <templates>(index + bodies) → <environment> → user: your question
```

The first four segments are byte-identical for the whole session, which is what keeps hitting the server-side prefix cache (`prompt_cache_hit_tokens`). **Never put anything that changes every run (time, git status, live load) into a template** — that would invalidate the cache permanently.

### Environment variables

```bash
export WOC_TEMPLATES=0            # disable template injection entirely
export WOC_TEMPLATES_USER=0       # disable user-level templates only
export WOC_TEMPLATES_PROJECT=0    # disable project-level templates only
export XDG_CONFIG_HOME=...        # relocate the user-level template directory
```

## Usage

### Single turn

```bash
woc 10 largest files in this directory
woc python files modified in the last day
```

When the model returns a command, the terminal shows it and waits: press `Enter` to run, `Ctrl+C` to cancel. If the request is read as an explanation request, `woc` prints the answer instead.

### Pipe mode (treat command output as data)

```bash
dmesg | woc "anything unusual lately"
journalctl -p err -n 200 | woc "summarise the root cause"
git diff | woc "review this change"
kubectl get pods | woc "which pods are unhealthy"
cat err.log | woc              # no question: uses the built-in triage/summarise instruction
git diff | woc --max-chars 8000 "what is risky here"   # one-off input cap
```

When stdin is not a terminal, pipe mode is automatic: stdin is wrapped into a `<stdin>` block and sent to the model, which **only answers and never executes anything**. There is no command execution on this path, so the "every command needs confirmation" boundary is not relaxed — non-terminal input never had a confirmation to give. Answers go to stdout, prompts to stderr, so you can keep piping: `| less` or `> out.md`.

- `<templates>` is not injected: command references are noise for "analyse this log";
- The input cap follows `[budget] max_input_chars` (default 32000 characters) and can be overridden by `[pipe] max_chars` or a one-off `--max-chars N`; any override is still clamped by the context budget. Over the cap, the head 60% and tail 40% are kept and the model is told explicitly that it was truncated;
- Unified diffs and JSON are detected and tagged `format="diff"` / `format="json"` on `<stdin>`, so the model does not have to guess; when detection fails nothing is tagged rather than guessing wrong;
- Binary input containing NUL bytes is refused outright; ANSI colours are stripped;
- Like `grep`, it waits for EOF before sending: commands that never end (`tail -f`) need `Ctrl+C`;
- With `[pipe] enabled = false` the mode is off and non-terminal input falls back to a refusal ("cannot confirm execution").

```toml
[pipe]
enabled = true
# max_chars = 32000     # optional: defaults to budget.max_input_chars
```

Environment variables: `WOC_PIPE=0` / `WOC_PIPE_MAX_CHARS=...`.

### File read/write (`woc-fs`)

`woc-fs` is the file read/write command for the model (and for you): one command, one action, one summary line plus the data — never the whole file dumped into the conversation.

```bash
woc-fs read PATH --lines 40:80                 # numbered slice, first 60 lines by default
woc-fs read PATH --tail 20                     # last 20 lines
woc-fs write PATH --content @/tmp/new.py --mkdir --backup
woc-fs edit PATH --old 'return None' --new 'return []' --backup
```

- `read` refuses binary files and truncates past 20000 characters with a note.
- `write` writes a temp file and `os.replace`s it, preserving the original mode; `--dry-run` reports without touching the disk.
- `edit` is a literal replacement: zero hits or two-or-more hits both fail loudly and tell you which lines matched.
- Content arguments accept `@file` (read from a file) and `-` (read from stdin), which sidesteps quoting hell.
- Exit codes: 0 success / 1 failure / 2 bad arguments; on failure stderr carries a single `error: ...` line.

It goes through the same dangerous check and confirmation as any other command: it is **not** on the read-only list, and `auto_safe` never auto-runs it. When the model should reach for it is documented in `.woc/templates/file-ops.md`.

### Multi-turn session

```bash
wocc
```

Example:

```text
[user] 10 largest files in this directory
[assistant] ...
[user] python files only
```

Type `:q`, `exit` or `quit` to leave. Command output is added to the next turn's context automatically. In-session commands (type `:help` any time):

- `:steps` prints each step's command, exit code and duration for this turn;
- `:explain` sends the last command and its output to the model for an explanation (one request, executes nothing, does not enter the history);
- `:again` re-runs the last command (still checked for danger, still confirmed, result written back to the history);
- `:search [keywords]` searches the web once and asks the model to answer from the results, printing a sources line afterwards; empty means "infer from the conversation";
- `:more` / `v` expands the output, `:last` shows the last command again.

After a command is generated:

- `Enter` / `y` — run it
- `n` — skip it (nothing runs; the model is told you skipped)
- `e` — edit the command (Enter runs the edited version, which goes through the dangerous check again)
- `Ctrl+C` — end the session
- Any other key never runs anything: it prints "Enter/y · n · e only" once. Arrow and function keys start with `ESC` and are never mistaken for "run".

When a command is wider than the screen, it is re-wrapped on word boundaries and indented before the prompt, so you can still read what will run; `e` edits the original single command (pipes and `;` included).

In the editor (while typing or editing a command):

- `Ctrl+A` / `Ctrl+E` start / end of line, `Ctrl+U` / `Ctrl+K` delete to start / end, `Ctrl+W` delete the previous word, `Ctrl+L` clear the screen;
- `↑` / `↓` walk the in-process history (200 entries, not persisted across sessions);
- Long lines scroll horizontally with `~` marking hidden content on either side; `Ctrl+G` prints the full input above;
- Multi-line pastes keep only the first line; the remaining bytes are dropped with a one-line notice — otherwise they would sit in the tty buffer and be read as a confirmation key.

Output longer than `[ui] fold_lines` is folded to the first 15 + last 8 lines; `stderr` and failed commands are always shown in full. `:more` (or `v`) reprints the whole output, `:last` shows the command again.

You can also put the first input on the command line:

```bash
wocc check the processes running right now
```

Add `--loop` when the model has to chain steps (each depending on the real output of the previous one).

To continue the previous session:

```bash
wocc --last-session
```

To list saved sessions, or resume one of them:

```bash
wocc --sessions
wocc --resume session-20261019-121500
```

### Session restore

After every turn `wocc` writes the conversation to `$XDG_STATE_HOME/woc/sessions/last.json` (default `~/.local/state/woc/sessions/last.json`, mode 0600) and keeps one `session-<timestamp>.json` archive (the last 20). Next time, `wocc --last-session` reattaches to the latest one, or use `wocc --sessions` to list and `wocc --resume <name>` to pick one.

- **Only user/assistant messages are replayed**: the system prompt, templates and `<environment>` are rebuilt from the current environment, so changing directory, config or templates stays consistent;
- Loading walks back from the most recent message, taking at most 40 messages / 60000 characters by default, and guarantees the first one is a user message;
- A `<session_context>` note is prepended, telling the model this is an old session that may be stale and that it should re-run commands when it needs exact results;
- Text is redacted before it hits the disk (`password=` / `token=` / `api_key=` style fields), and a single message is capped at 12000 characters;
- On by default; `[session] persist = false` (or `WOC_SESSION_PERSIST=0`) stops writing. With it off, `--last-session` still reads old files but no new ones are produced.

```toml
[session]
persist = true
max_messages = 40
max_chars = 60000
```

What it solves is **crash recovery** (a long session killed by Ctrl+C or a closed terminal), not multi-session management: there is exactly one `last.json`, and running two `wocc` instances means the later save overwrites the earlier one.

### Chat mode

```bash
woc -c what is systemd?
# or
woc_chat what is systemd?
```

Chat mode is for questions and answers; it never executes what it generates. It searches the web by default: when the model needs live information it searches once and answers, quietly, followed by a sources line. Turn it off with `woc -c --no-search` or `:search off`; drive it manually with `:search <keywords>`. See the `[search]` section above.

Answers are **streamed**: SSE deltas are rendered line by line as they arrive, without waiting for the whole answer; tables appear as one block a little later, because column widths need the whole table.

## Safety

LLM output should not be treated as fully trustworthy. Check the command, the paths and the arguments before running anything, especially anything touching deletion, permissions, network requests or system configuration.

The built-in dangerous-command check is not "substring matching over the whole line"; it parses shell syntax and looks at **command position**:

- Allowed: `printf '... reboot ...' > file`, `grep reboot`, `man reboot`, `journalctl -u reboot.target`, `grep mkfs`;
- Blocked: `systemctl reboot`, `sudo reboot`, `bash -c 'reboot'`, `find -exec reboot`, `rm -rf /`, `mkfs`, `dd of=/dev/sd*`, `> /dev/sd*`, `chmod -R 777 /`.

"Read-only" does not mean "no leak": `cat /proc/self/environ`, `ps e` and `ps auxe` print a process's environment (tokens, keys) verbatim. Those forms need confirmation even with `auto_safe=true`, and anything fed back to the model or written to disk also passes `redact_secrets()` (values under names containing `secret`/`token`/`password`, `Bearer` credentials and PEM private-key blocks become `***`; bare tokens are still not covered).

`sh -c` / `eval` / `find -exec` / `xargs` are checked recursively; if parsing fails, or stdin is fed to a shell, it falls back to the older substring rules (fail-closed). It is still **not a sandbox**: script file contents, dynamic assembly and encoded obfuscation can get through; hosts that execute code (`awk` / `sed` / `python`) only fall back conservatively when an execution construct is detected (`system(`, `-c`). Be careful in environments with important data, and take a backup first.

`--loop` moves the "every command needs confirmation" boundary: by default every step still needs confirmation, and only with `auto_safe=true` are read-only single commands auto-run. Sticky continuation changes **when a turn ends**, never **whether a command is confirmed**; steps and accumulated output have hard caps and going past them is reported.

When stdin is not a terminal: `woc` enters pipe mode (answer only, execute nothing); `wocc` and `woc -c` (without a first question) refuse to start — interactive confirmation needs a terminal, and an arbitrary line from a pipe must not stand in for pressing Enter.

### sudo and privileges

- In single-turn `woc`, sudo takes over the terminal: the password prompt and the ticket cache (`timestamp_timeout`) belong to sudo. woc never touches or stores the password.
- Multi-turn `wocc` probes `sudo -n` first: with a valid ticket it captures output as usual; otherwise it runs in the foreground in your terminal and does not feed the output back to the model (what root can read should not land in the prompt).
- Commands that open an interactive root shell (`sudo -i`, `sudo -s`, `su`) are refused outright.
- If you need several sudo commands in a row, run `sudo -v` in the same terminal first.
- Environment injection carries the working directory, distro, uid/groups and container type only — **no environment variables at all**.

### Shell completion

Generate a bash / zsh completion script (command names and flags only; no execution semantics):

```bash
source <(woc --completion bash)    # enable for this shell
source <(woc --completion zsh)
```

For a permanent setup, add that line to `~/.bashrc` / `~/.zshrc` (or redirect the output into your completion directory). Command names follow `[prefix]`, and `<woc_prefix>-fs` is completed too.

## Uninstall

Remove the commands and the PATH initialisation from your shell config:

```bash
python3 compile.py --uninstall
```

To also delete `~/.config/woc/config.toml`:

```bash
python3 compile.py --uninstall --purge-config
```

## Development checks

```bash
python3 -m compileall -q woc           # syntax
python3 -m unittest discover -s tests  # offline regression (loop / gate / syntax / editor / Markdown)
```

The live-API `#CHAT:` marker compliance test is **skipped by default**; enabling it calls the model and costs money:

```bash
WOC_LIVE_TEST=1 python3 -m unittest discover -s tests
```

CI runs the same two checks on Python 3.10 / 3.13 via GitHub Actions (see `.github/workflows/test.yml`).
