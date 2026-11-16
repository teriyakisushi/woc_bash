# AGENTS.md

Notes for AI agents working in this repository. Humans should read [README.md](README.md);
design trade-offs and hard-won lessons live in `docs/DESIGN-agentic-loop.md` (`docs/HANDOFF-*.md`
are local and gitignored). For the big picture see `docs/ARCHITECTURE.md`: seven invariants,
three boundaries, dependency direction, and a "where to look before changing what" cheat sheet —
skim that table before touching code.

## One sentence

`woc` turns natural language into shell commands: `woc` (single turn), `wocc` (multi-turn +
optional agentic loop), `woc -c` / `woc_chat` (chat). Pure Python 3.10+ standard library, no
third-party dependencies.

## Hard constraints (read before changing code)

1. **Zero dependencies**: standard library only. Do not add PyYAML/requests and friends.
2. **Single turn cannot loop**: `woc` hands the process over with `os.execvp`
   (`woc/cli/single.py`); the loop exists only in `wocc`.
3. **Model output is a single-line command**: `sanitize()` keeps only the first valid line and
   silently drops the rest; tool-call/XML residue (DSML, `<exec_result>`, `</br>`) is rejected,
   never executed.
4. **Human confirmation is the boundary**: every command needs one confirmation before it runs.
   The loop only skips it for a "read-only single command" when `auto_safe=true`; everything else
   is still confirmed one by one. Non-tty input has no confirmation to give: `woc` falls into pipe
   mode (answers only, never executes), `wocc` / `woc -c` refuse to start.
5. **Prefix cache**: the stable segments (SYSTEM_PROMPT / MULTI_TURN_HINT(+LOOP_HINT) /
   `<templates>` / `<environment>`) must stay byte-identical within a session. Never put time,
   git state, or live load in there.
6. **Templates are just text**: commands inside `<templates>` have the same standing as typed
   commands and must pass the same danger checks.
7. **History format is contagious**: the `#CHAT:` / `#STEP:` markers in assistant history must not
   be stripped (`docs/DESIGN §8.1`). Once the loop is on, steps are "sticky": a turn that ever
   produced `#STEP:` keeps taking bare commands, and only `#CHAT:` / `#ERROR:` end it.
8. **Search stays off the default path**: `woc/search.py` is imported lazily, only when a search
   really happens (`#SEARCH:` / `:search` in `woc -c`); the default startup path must not load
   it. Search results are appended at the end of messages, never into the stable prefix; the hit
   list is printed only under `WOC_DEBUG=1`, but **the sources line after an answer must always be
   printed** (the process may be quiet, auditability may not).

## Common commands

```bash
python3 woc.py "list the 3 biggest files here"   # single turn (execvp — do not run directly in tests)
python3 wocc.py --loop                           # multi-turn + agentic loop
python3 woc.py --templates                       # template index and two-layer injection
python3 woc.py --check-templates                 # template self-check (should be 0 error / 0 warning)
WOC_DEBUG=1 python3 woc.py "..."                 # observe usage/cache/prefix size (stderr)

python3 -m compileall -q woc tests install.py   # syntax
python3 -m unittest discover -s tests           # offline regression (353 cases, 1 live skip, ~4s; ~3s of that is the pty editor test)
WOC_LIVE_TEST=1 python3 -m unittest discover -s tests   # real API calls, skipped by default, costs money
```

CI lives in `.github/workflows/test.yml` and runs the same two checks on Python 3.10 / 3.13.

## Code map

| File | Responsibility |
|---|---|
| `woc/loop.py` | agentic loop: `risk_tier`, `#STEP:` sticky state machine, syntax pre-check, output folding, `:more` |
| `woc/cli/multi.py` | `wocc` session loop (user input, `:q` / `:more` / `:last`) |
| `woc/cli/single.py` | single-turn `woc` (`os.execvp` after confirmation) |
| `woc/cli/chat.py` | `woc -c` chat (Markdown rendering, SSE deltas rendered by safe prefix) |
| `woc/markdown.py` | terminal Markdown rendering: one-shot `render_markdown()` and streaming `MarkdownStream` |
| `woc/cli/completion.py` | `--completion bash\|zsh` script generation |
| `woc/context.py` | two-layer template injection, `<environment>`, directory snapshot, `messages_prefix()` |
| `woc/llm.py` | SSE streaming + `StreamFilter` (`#CHAT:` / `#STEP:` detection) |
| `woc/sanitize.py` | command cleanup, DSML stripping, tool-call guard, root-shell detection |
| `woc/gate.py` | structured danger checks: command position, wrappers, recursion, conservative fallback |
| `woc/exec.py` | command execution, `bash -n` syntax pre-check, sudo/pty capture, `format_exec_result` |
| `woc/ui.py` | `Spinner`: one-line loading while waiting for the model / running a command (tty only) |
| `woc/pipe.py` | pipe mode: stdin as data, answers only; binary rejection, head/tail truncation |
| `woc/search.py` | web search: Search API call, result rendering and character budget (`woc -c` only) |
| `woc/budget.py` | budget math: context / output / input constrain each other |
| `woc/session.py` | `wocc` session persist/restore: conversation only, redacted, size-capped, 0600 |
| `woc/debug.py` | `WOC_DEBUG=1` observation: usage, prefix, input size, loop steps (stderr only) |
| `woc/config.py` | sectioned config with environment-variable precedence |
| `woc/colors.py` | hand-rolled line editor + colored output (**highest change risk**); run `tests/test_colors.py` first |
| `woc/fs.py` | `woc-fs`: file read/write `read` / `write` / `edit` (standalone command, not in the loop) |
| `install.py` | installer (command renaming, global templates, uninstall) |

## Code style

- Documentation and comments are in Chinese; identifiers are English.
- The log prefix always comes from `[prefix].alias`; **never hardcode `[woc]`**.
- Changing security logic (`is_dangerous` / `is_root_shell` / `risk_tier` in `woc/gate.py`)
  requires updating `GateTest` in `tests/test_gate.py`.
- New config items go into the matching section of `woc/config.py` and must support an
  environment-variable override; also update `CONFIG_TEMPLATE` in `install.py`
  (`tests/test_install.py` compares both sides: a missing key, an extra key, or a different
  value all fail).
- **User-visible copy is English only**: every line we print to the terminal or feed back to the
  model (help, prompts, hints, errors, notes) is written as the final English string at the call
  site — no translation layer, no language switch. `tests/test_output_text.py` pins the main
  entry points.
- Do not loosen the `risk_tier` whitelist just to save one Enter press.

## Commit conventions

- Use `feat:` / `fix:` / `docs:` / `test:` plus a Chinese summary; the body explains "why".
- Before committing: `compileall` + `unittest discover -s tests` must be green.
- Temporary scripts get a `_` prefix and are deleted afterwards; never commit `_*.py` / `_*.txt`.

## Security notes

- `is_dangerous()` now matches on **command position** (`woc/gate.py`): `printf '... reboot ...'`,
  `grep reboot`, `man reboot` no longer trip it; `systemctl reboot`, `sudo reboot`,
  `bash -c 'reboot'`, `find -exec reboot`, `rm -rf /` are still blocked.
- It is not a sandbox: when parsing fails, input is piped to a shell, or a script host such as
  `awk`/`sed`/`python` is involved, it falls back to the old substring rules (fail-closed);
  dynamic assembly and script file contents can still slip through.
- `is_root_shell()` has a deliberate hole: `sudo -u root bash` is not caught.
- `risk_tier()` applies an extra argument review before saying `safe` (`read_only_reason()`,
  `woc/loop.py:87`): `cat|head|tail|grep` reading `/proc`, `/sys`, `environ`, and `ps` with the
  BSD `e` flag (`ps e`, `ps auxe`) all drop to `confirm`. Before adding a command to
  `READ_ONLY`, think it through: could it print another process's environment?
- `redact_secrets()` (`woc/exec.py:110`) matches on **keywords in field names** (`*SECRET*`,
  `*TOKEN*`, `AWS_*`, `*PASSWORD*`, `Environment` lines) and on credential shapes
  (`Bearer ...`, PEM private-key blocks). Bare tokens (no field name) are still not covered —
  there is no signal to match on, which is why the argument review above keeps them from being
  executed at all.
- `bash -n` syntax pre-check only parses, never executes; it catches garbage characters and
  typos, and is not a security boundary.
- When sudo needs a password and there is no ticket, `wocc` runs in the foreground and **does not
  feed the output back** (so root-readable content cannot reach the prompt).
- The model occasionally treats a Chinese answer as a command (missing `#CHAT:`); there is an
  exit-127 hint for that — do not swallow it silently.
- `wocc` persists sessions to `$XDG_STATE_HOME/woc/sessions/last.json` by default (redacted, 0600);
  changes there must update `tests/test_session.py`, and nothing may be written into the project
  directory.
