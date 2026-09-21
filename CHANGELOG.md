# Changelog

## 1.1.0 - 2026-09-21

- Multi-host: reads MCP config for every agent, not just Claude Code. `--agent` picks
  `claude`, `codex`, `cursor`, `gemini`, `copilot`, `vscode`, `windsurf`, `opencode`, a
  comma list, or `all`; with no flag the host is detected from `CLAUDECODE`,
  `CODEX_SANDBOX`, `CURSOR_AGENT` or `GEMINI_CLI` and falls back to `all`. Rows and
  sources are labelled `<host> <scope>` when more than one host is shown; the report
  header names the host and why.
- Codex: `~/.codex/config.toml` (`$CODEX_HOME`) and `.codex/config.toml`
  `[mcp_servers.<id>]` - `command`/`args`/`env`/`cwd`, `url` with
  `bearer_token_env_var`, `http_headers`, `env_http_headers`, `enabled`, `required`
  (note), `startup_timeout_sec` under 3 s (`short-timeout`). TOML via `tomllib` on
  3.11+ with a stdlib fallback on 3.10.
- Cursor: `~/.cursor/mcp.json`, `.cursor/mcp.json` with `${env:X}`,
  `${workspaceFolder}`, `${userHome}` expansion, `envFile` existence
  (`envfile-missing`), comments and trailing commas tolerated.
- Gemini CLI: `~/.gemini/settings.json`, `.gemini/settings.json` - `httpUrl` (HTTP)
  vs `url` (SSE), `cwd`, `trust` (note), bare `$VAR` expansion, `mcp.allowed` /
  `mcp.excluded` as `not-allowed` / `excluded`.
- Copilot CLI: `~/.copilot/mcp-config.json` (`$COPILOT_HOME`) with `type: local|http`,
  plus the project's `.mcp.json`, flagged as the same file Claude Code reads.
- VS Code: the user profile `mcp.json` and `.vscode/mcp.json` - `servers` + `inputs`;
  `${input:id}` is a `prompted` note, an undefined id is `input-undefined`.
- Windsurf `mcp_config.json` (`serverUrl`) and OpenCode `opencode.json[c]` (`mcp` key,
  `command` as a list, `environment`, `type: local|remote`, `enabled`, JSONC).
- New static checks for every host: `cwd-missing`, header variables reported as
  `var-unset`, `disabled` for `enabled = false`. `windows-shim` is a warning for
  Claude Code and Cursor (documented) and a note elsewhere.
- Same name in two hosts' files is two servers; `shadowed` and Claude Code's allow /
  deny lists apply within one host only. Probes honour the server's `cwd`.
- `project_dir` recognises `.codex`, `.cursor`, `.gemini`, `.vscode`, `.agents` and
  `opencode.json` as project roots and never climbs into a home directory.
- JSON: `host`, `host_why` on the report; `agent`, `cwd` on each server.
- SKILL.md rewritten for any host with spec `license`, `compatibility` and `metadata`
  frontmatter; `agents/openai.yaml` for Codex / ChatGPT UI metadata.
- 22 new tests (50 total), including a fallback-vs-`tomllib` equivalence check.

## 1.0.0 - 2026-09-19

Initial release.

- `mcprollcall.py`: reads every MCP source - `~/.claude.json` user and per-project
  entries, `.mcp.json` with its approval state, `managed-mcp.json` and
  `managedMcpServers`, plugin `.mcp.json` files, and the claude.ai connectors the
  harness has cached as needing auth - and resolves name clashes the way the harness
  does (managed > local > project > user).
- Static checks, no spawning: command on PATH or at its path (with install hints for
  npx / uvx / python), bare `npx` on Windows, absolute paths in `args` that do not
  exist, `${VAR}` without a value, empty or placeholder env and headers, `PATH`
  overrides, `allowedMcpServers` / `deniedMcpServers`, and a TCP check on every URL
  including an `mcp-remote` bridge - "nothing listening on 127.0.0.1:3141".
- `--probe [names]`: spawns each stdio server with its env, performs `initialize`
  and `tools/list` over newline-delimited JSON-RPC, terminates it, and reports start
  time, tool count, ~tokens of tool definitions, server name/version - or on failure
  the exit code and the last lines of stderr. Parallel, per-server timeout,
  `slow-start` and `heavy` findings.
- `--strict`, `--problems-only`, `--full`, `--no-net`, `--no-plugins`, `--json`.
- 28-test suite with a fake MCP server (ok / crash / hang / slow / no-tools / env) and
  a CI matrix (Windows / macOS / Linux, Python 3.10 / 3.13).
