# Changelog

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
