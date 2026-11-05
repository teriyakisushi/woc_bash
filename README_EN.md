# woc

[![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies](https://img.shields.io/badge/dependencies-0-success)](woc.py)
[![Tests](https://img.shields.io/badge/tests-332%20passing-brightgreen)](.github/workflows/test.yml)
[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

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

The installer puts the commands in `~/bin` by default and tries to add that directory to your current Shell's config file. It asks for the log alias and three command names (press Enter for the defaults, see `[prefix]` in [docs/CONFIG.md](docs/CONFIG.md)). It also installs a file read/write command `<woc command name>-fs` (default `woc-fs`): the name is derived from `woc_prefix` and does not take an extra config key.
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

## Quick start

```bash
woc three largest files here          # single turn: one command, confirm, run
dmesg | woc "anything unusual"        # pipe mode: stdin as data, answer only, never execute
wocc --loop                           # multi-turn + agentic loop (each step confirmed, :steps audits)
woc -c "what is systemd"              # chat mode: Markdown, streaming, optional web search
```

Confirm keys are `Enter` to run / `n` to skip / `e` to edit; no other key ever runs anything. `:q` leaves the session.

## Configuration

Every key, default and environment override lives in [docs/CONFIG.md](docs/CONFIG.md) (Chinese). The minimum is one variable:

```bash
export WOC_API_KEY=sk-xxxx
```

## Documentation

| Document | Contents |
|---|---|
| [docs/USAGE_EN.md](docs/USAGE_EN.md) | Pipe mode, `woc-fs`, multi-turn sessions, restore, chat, safety, sudo, completion, uninstall |
| [docs/TEMPLATES_EN.md](docs/TEMPLATES_EN.md) | Template format, two-layer injection, installing globally, prefix-cache rules |
| [docs/CONFIG.md](docs/CONFIG.md) | Meaning, default and environment override of every config key |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Seven invariants, three boundaries, stable prefix, loop state machine |
| [docs/DESIGN-agentic-loop.md](docs/DESIGN-agentic-loop.md) | Loop design trade-offs and pitfalls |
| [docs/DESIGN-web-search.md](docs/DESIGN-web-search.md) | Search cost model, injection point, open items |

## Safety

Every command waits for one human confirmation; dangerous commands are matched at **command position**, not as a substring,
so `printf '... reboot ...'` and `grep reboot` are not false positives. `auto_safe` in `--loop` only auto-runs read-only single commands.
The full boundary (including "read-only but leaks" forms such as `ps e` and `cat /proc/self/environ`) is in
[docs/USAGE_EN.md](docs/USAGE_EN.md#safety).

## License

[MIT](LICENSE)
