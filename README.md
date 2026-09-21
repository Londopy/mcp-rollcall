<div align="center">

# 🔌 mcp-rollcall

**Take a roll call of your MCP servers — which will fail to connect, exactly why, and what each one costs. Claude Code, Codex, Cursor, Gemini CLI, Copilot, VS Code, Windsurf, OpenCode.**

[![CI](https://github.com/Londopy/mcp-rollcall/actions/workflows/ci.yml/badge.svg)](https://github.com/Londopy/mcp-rollcall/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)](skills/mcp-rollcall/scripts/mcprollcall.py)
[![Agent Skills](https://img.shields.io/badge/Agent_Skills-spec-111)](https://agentskills.io)
[![Works with](https://img.shields.io/badge/works_with-Claude_Code_%7C_Codex_%7C_Cursor_%7C_Gemini_CLI_%7C_Copilot_%7C_VS_Code_%7C_OpenCode-D97757)](#install)
[![Platform](https://img.shields.io/badge/platform-windows%20%7C%20macos%20%7C%20linux-lightgrey)](#install)

<img src="docs/demo.png" alt="mcp-rollcall output: servers by host and scope, a probe with start time, tool count and token cost, and the stderr of the one that failed" width="900">

<sub>Part of the rollcall family — tools that make what your coding agent does silently legible: [skill-rollcall](https://github.com/Londopy/skill-rollcall) · **mcp-rollcall** · [settings-effective](https://github.com/Londopy/settings-effective) · [git-attribution](https://github.com/Londopy/git-attribution) · all four: [agent-skills](https://github.com/Londopy/agent-skills)</sub>

</div>

---

Claude Code says `anki (CONNECTION_CLOSED)` and moves on. Codex says the server "failed to initialize". Cursor shows a red dot. Was it the command? A path? An env var? The app it bridges to isn't running? The server threw on startup? The host saw the answer — it read the stderr, it got the exit code — and discarded it. And each host keeps its servers in a different file, so "what do I have configured" is already five files deep.

`mcp-rollcall` reads every place a server can be configured for the host you're in (or all of them), checks each one the way the host would try to start it, and on request actually starts it, does the MCP handshake, and shows you what came back: the tool list, the startup time, the context cost, or the traceback. It runs as a skill (`/mcp-rollcall` in Claude Code, `$mcp-rollcall` in Codex, or just say "why did anki fail to connect") and as a plain CLI.

## What it does

| Mode | Question it answers |
|---|---|
| default (static, fast, touches nothing) | What's configured, where, and which of it can't work: command not on PATH, `.js` or directory in `args` that doesn't exist, `cwd` missing, `${VAR}` / `${env:VAR}` / Codex `bearer_token_env_var` unset, empty or placeholder token, bare `npx` on Windows, same name in two scopes of one host (which wins), `.mcp.json` server awaiting approval, blocked by an allow/deny list, `enabled = false`. And for every URL — `type: http`, Gemini `httpUrl`, Windsurf `serverUrl`, or an `mcp-remote` bridge in `args` — a TCP check: **nothing listening on 127.0.0.1:3141**. |
| `--agent codex` / `--agent all` | The same, for **that** host's files — or every host side by side, each row labelled `codex user`, `cursor project`, … |
| `--probe [names]` | Spawns each stdio server with its env and `cwd`, sends `initialize` and `tools/list`, terminates it. Reports start ms, tool count, **~tokens of tool definitions loaded every session**, server name/version — or the exit code and **the last 40 lines of stderr**. |
| `--strict --no-net` | Is this `.mcp.json` / `config.toml` / `mcp.json` sane before I commit it? Non-zero exit for CI. |

Only `--probe` starts anything. It never writes a config file.

## Install

One layout — `skills/mcp-rollcall/SKILL.md` + `scripts/mcprollcall.py` — is the [Agent Skills](https://agentskills.io) standard, so the same folder works everywhere.

**`skills` CLI** — any of 79 agents (global; `--copy` because symlinks need Developer Mode on Windows):

```bash
npx skills add Londopy/mcp-rollcall -g --copy                  # picks the agents it finds
npx skills add Londopy/mcp-rollcall -g --copy -a codex -a cursor
npx skills add Londopy/mcp-rollcall -g --copy --all            # every agent, no prompts
```

**Claude Code plugin** (in an interactive `claude` session):

```
/plugin marketplace add Londopy/mcp-rollcall
/plugin install mcp-rollcall@mcp-rollcall
```

**By hand** — copy the folder into the host's skills directory:

```bash
git clone https://github.com/Londopy/mcp-rollcall
cp -r mcp-rollcall/skills/mcp-rollcall ~/.claude/skills/          # Claude Code
cp -r mcp-rollcall/skills/mcp-rollcall ~/.agents/skills/          # Codex, Cline, Zed, Warp (universal)
cp -r mcp-rollcall/skills/mcp-rollcall ~/.cursor/skills/          # Cursor
cp -r mcp-rollcall/skills/mcp-rollcall ~/.gemini/skills/          # Gemini CLI
cp -r mcp-rollcall/skills/mcp-rollcall ~/.copilot/skills/         # GitHub Copilot
cp -r mcp-rollcall/skills/mcp-rollcall ~/.config/opencode/skills/ # OpenCode
```

## Usage

### From your agent

Say what you'd naturally say — "why did wireshark fail to connect?", "what MCP servers do I have?", "which servers are eating my context?", "is this .mcp.json OK?", "does Cursor have the same servers as Claude?" — or invoke the skill by name. The agent runs the static pass for the host you're in, probes the server you asked about if it's still unexplained, quotes the stderr line that matters, and tells you the fix. It won't edit config for you (Claude Code's running app rewrites `~/.claude.json`); it gives you the `claude mcp add` line, or the `config.toml` / `mcp.json` entry to change.

### As a CLI

Stdlib-only Python, nothing in it depends on any particular agent:

```bash
python ~/.claude/skills/mcp-rollcall/scripts/mcprollcall.py                       # host detected from the environment
python ~/.agents/skills/mcp-rollcall/scripts/mcprollcall.py --agent codex          # Codex's config.toml
python ~/.claude/skills/mcp-rollcall/scripts/mcprollcall.py --agent all --probe    # everything on this machine, probed
python ~/.claude/skills/mcp-rollcall/scripts/mcprollcall.py --probe wireshark --full
```

| Flag | Effect |
|---|---|
| `--agent X` | Host whose files to read: `claude`, `codex`, `cursor`, `gemini`, `copilot`, `vscode`, `windsurf`, `opencode`, a comma list, or `all`. Default: detect from the environment, else `all` |
| `--probe [a,b]` | Spawn and handshake every stdio server, or just these |
| `--timeout N` | Seconds to wait for `initialize` (default 20; first `npx -y` / `uvx` runs download) |
| `--jobs N` | Parallel probes (default 4) |
| `--project DIR` | Project whose files count (default: cwd, walking up to a `.mcp.json` / `.claude` / `.codex` / `.cursor` / `.gemini` / `.vscode` / `opencode.json` / git root, never into `$HOME`) |
| `--no-net` | Skip TCP reachability checks |
| `--no-plugins` | Skip Claude Code plugin `.mcp.json` files |
| `--problems-only` | Findings only |
| `--full` | Untruncated commands, every tool name, the whole stderr tail |
| `--strict` | Exit 1 on any error |
| `--json` | Machine-readable; each server carries `agent`, `scope`, `source` |

### Where each host keeps its servers

| Host | User scope | Project scope | Shape notes |
|---|---|---|---|
| Claude Code | `~/.claude.json` → `mcpServers`, `projects[<dir>].mcpServers` (local) | `.mcp.json` (+ `enabledMcpjsonServers` / `disabledMcpjsonServers` / `enableAllProjectMcpServers`), managed `managed-mcp.json`, plugin `.mcp.json` | `allowedMcpServers` / `deniedMcpServers`; claude.ai connectors from `mcp-needs-auth-cache.json` |
| Codex | `~/.codex/config.toml` (`$CODEX_HOME`) → `[mcp_servers.<id>]` | `.codex/config.toml` (only when the project is trusted) | `command`/`args`/`env`/`cwd`, `url` + `bearer_token_env_var` / `http_headers` / `env_http_headers`, `enabled`, `required`, `startup_timeout_sec` |
| Cursor | `~/.cursor/mcp.json` | `.cursor/mcp.json` | `${env:X}`, `${workspaceFolder}`, `${userHome}`, `envFile`; comments tolerated |
| Gemini CLI | `~/.gemini/settings.json` | `.gemini/settings.json` | `httpUrl` = streamable HTTP, `url` = SSE, `cwd`, `trust`, `$VAR` or `${VAR}`; `mcp.allowed` / `mcp.excluded` |
| Copilot CLI | `~/.copilot/mcp-config.json` (`$COPILOT_HOME`) | `.mcp.json` — the same file Claude Code reads | `type: local \| http`, `tools` |
| VS Code | user profile `mcp.json` | `.vscode/mcp.json` | `servers` + `inputs`; `${input:id}` is prompted, `${workspaceFolder}` |
| Windsurf | `~/.codeium/windsurf/mcp_config.json` | — | `serverUrl` for remote servers |
| OpenCode | `~/.config/opencode/opencode.json[c]` | `opencode.json[c]` | `mcp` key; `type: local \| remote`, `command` as a list, `environment`, `enabled`; JSONC |

Same name in two hosts' files = two servers (they start independently). Same name twice inside one host = `shadowed`, local beats project beats user. Host detection reads `CLAUDECODE`, `CODEX_SANDBOX`, `CURSOR_AGENT`, `GEMINI_CLI`; anything else reports `host: all` and you narrow with `--agent`. Codex's `config.toml` is parsed with `tomllib` on Python 3.11+ and a small stdlib fallback on 3.10.

## What it catches

| Finding | Severity | Example | Fix it prints |
|---|---|---|---|
| `command-missing` | error | `uvx` not installed | install uv, or use the full path |
| `arg-path-missing` / `cwd-missing` | error | `dist/index.js` moved; Codex `cwd = "./tools"` gone | — |
| `var-unset` | error | `${OBSIDIAN_VAULT}`, `${env:TOKEN}`, `bearer_token_env_var = "X"`, Gemini `$VAR` — no value, no default | `export` it, or `${VAR:-default}` |
| `port-closed` | error | `mcp-remote http://127.0.0.1:3141` and Anki isn't open | start the app that hosts it |
| `probe-failed` | error | `ValueError: Allowed directories must already exist` | the stderr tail |
| `denied` / `not-allowed` / `excluded` | error | Claude `deniedMcpServers`, Gemini `mcp.excluded` | — |
| `envfile-missing` (Cursor) / `input-undefined` (VS Code) | error | `"envFile": ".env"` absent; `${input:key}` with no `inputs` entry | — |
| `windows-shim` | warn (Claude, Cursor) / note | `"command": "npx"` on Windows | `cmd /c npx …` or `npx.cmd` |
| `env-empty` / `env-placeholder` / `header-placeholder` | warn | `"TOKEN": "<your-token>"` | — |
| `shadowed` | warn | `filesystem` in both user and local scope of one host | remove or rename one |
| `approval` | warn | Claude `.mcp.json` server not yet approved | answer the prompt, or `enableAllProjectMcpServers` |
| `short-timeout` (Codex) | warn | `startup_timeout_sec = 1` on an `npx -y` server | raise it, or pre-install |
| `slow-start` | warn | 6 s to initialize, every session | pre-install instead of `npx -y` |
| `no-tools` | warn | connected, exposes nothing | — |
| `disabled` / `required` / `trusted` / `prompted` | note | `enabled = false`; Codex `required = true`; Gemini `trust = true`; VS Code `${input:…}` | your call |
| `heavy` | note | 132 tools, ~36 k tokens per session | your call |
| `env-path` | note | `env.PATH` overrides the server's PATH | — |

## How the probe works

Same thing every host does at session start, with the output kept: spawn `command args` with `env` merged over yours, in the configured `cwd`, write `{"method":"initialize"}` then `notifications/initialized` then `{"method":"tools/list"}` as newline-delimited JSON-RPC on stdin, read stdout until each reply arrives, then `terminate()`. Time-to-`initialize` is the start column; `len(json.dumps(tools)) / 4` is the token estimate. Stderr is collected the whole time and the last 40 lines are kept.

A server that talks to a desktop app (Blender, Photoshop, SolidWorks) will log that it couldn't reach the app and *still* handshake — that's `ok`, and it's also what happens in the host. The ones that refuse to start without their app are the ones that show as `FAILED` there too.

## Related, not overlapping

`claude mcp list`, `codex mcp list` and `gemini mcp list` print configured or ✔ / ✘ per server with no reason; `/mcp` in a Claude Code session reconnects and shows auth state. [`mcp-doctor`](https://github.com/frankxai/mcp-doctor) (npm) audits config across several agents and spawns servers for a health score, but doesn't surface stderr or count tools. [`skill-rollcall`](https://github.com/Londopy/skill-rollcall) and [`settings-effective`](https://github.com/Londopy/settings-effective) do this job for skills and settings.

## What it cannot do

- **It doesn't know each server's conventions.** A directory list a server splits on commas while your config uses semicolons passes every static check; only the probe catches it (that one was real).
- **It doesn't authenticate.** An HTTP server behind OAuth gets a TCP check, not a handshake; the claude.ai connectors are listed from Claude Code's cache, and their fix is in claude.ai's connector settings.
- **Host detection is best effort.** It reads environment markers; a host that sets none is reported as `all`. Pass `--agent`.
- **It reads config files, not plugin bundles.** Claude Code plugin servers are found by scanning the plugin cache for `.mcp.json` (best-effort); Codex, Cursor and VS Code marketplace plugins aren't read. Codex's `.codex/config.toml` is listed whether or not the project is trusted, because trust state isn't on disk.
- **A probe that passes here can still fail in the host** if the host runs with a different PATH or environment than your shell. The report prints the exact command so you can compare.

## Development

```bash
python -m unittest discover -s tests -v          # 50 tests, stdlib only; probes spawn tests/fake_server.py
python skills/mcp-rollcall/scripts/mcprollcall.py --agent all --strict --no-net
python docs/make_demo.py                         # re-render docs/demo.png (needs Pillow)
```

CI runs the tests on Windows, macOS and Linux across Python 3.10 and 3.13 (3.10 exercises the stdlib TOML fallback; 3.13 checks it against `tomllib`), then runs the static roll call for every host on an empty machine in strict mode.

## Layout

```
mcp-rollcall/
├── .claude-plugin/          # Claude Code plugin manifest + single-plugin marketplace
├── skills/mcp-rollcall/
│   ├── SKILL.md             # what the agent reads (Agent Skills spec frontmatter)
│   ├── agents/openai.yaml   # Codex / ChatGPT UI metadata (optional, ignored elsewhere)
│   └── scripts/mcprollcall.py
├── tests/                   # test_mcprollcall.py + test_hosts.py + fake_server.py
├── docs/                    # demo image + generator
└── .github/workflows/ci.yml
```

## License

[MIT](LICENSE) © 2026 Londopy
