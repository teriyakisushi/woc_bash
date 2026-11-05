# Usage and safety

How to use `woc` and where its boundaries are. Project overview and installation: [README_EN.md](../README_EN.md).

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
