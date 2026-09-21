---
name: mcp-rollcall
description: Take a roll call of configured MCP servers - which are configured where, which will fail to connect and exactly why (missing command, dead path, unset variable, nothing listening on the port, the server's own stderr), and what each one costs in tools and context. Reads Claude Code, Codex, Cursor, Gemini CLI, Copilot CLI, VS Code, Windsurf and OpenCode config files. Use this whenever the user says an MCP server failed to connect, shows "Connection closed" or "still connecting", asks why a server or its tools are missing, asks what MCP servers they have or where one is configured, asks which servers are slow or heavy, wants MCP config checked before committing a .mcp.json, config.toml or mcp.json, asks which agent a server is configured for, or after adding or editing a server. Also use it when your own system context lists a server as failed and the user asks about it.
license: MIT
compatibility: Requires Python 3.10+. Never writes a config file; only --probe starts servers.
metadata:
  author: Londopy
  version: "1.1.0"
---

# mcp-rollcall

Every agent starts its configured MCP servers at session start and, when one fails,
says `failed to connect` or `Connection closed` and nothing else. The server's stderr,
the exit code, the missing directory, the port nobody is listening on - all of that is
discarded, in Claude Code, Codex, Cursor, Gemini CLI, Copilot, VS Code, Windsurf and
OpenCode alike. Each also keeps its servers in a different file. This skill reads
every place a server can be configured for the host you are in, checks each one the
way the host would try to start it, and on request actually starts it, does the MCP
handshake and reports what came back.

The script is `scripts/mcprollcall.py` next to this file - run it from wherever this
skill was installed (`~/.claude/skills/mcp-rollcall/`, `~/.agents/skills/mcp-rollcall/`,
`~/.cursor/skills/...`, or a project's `.agents/skills/`). Stdlib-only Python 3.10+. It
never writes a config file. Only `--probe` starts servers; everything else is static.

## Which host

The script detects the host from the environment (`CLAUDECODE`, `CODEX_SANDBOX`,
`CURSOR_AGENT`, `GEMINI_CLI`) and reads that host's files. If nothing identifies the
host it reads every host's files and labels each row `<host> <scope>`. Pass `--agent`
when you know better: `--agent codex`, `--agent cursor,vscode`, `--agent all`.

| Host | Files read |
|---|---|
| `claude` | `~/.claude.json` (user + per-project local), `.mcp.json`, managed `managed-mcp.json`, plugin `.mcp.json` |
| `codex` | `~/.codex/config.toml` `[mcp_servers.<id>]` (or `$CODEX_HOME`), `.codex/config.toml` (only when the project is trusted) |
| `cursor` | `~/.cursor/mcp.json`, `.cursor/mcp.json` - `${env:X}`, `${workspaceFolder}`, `envFile` understood |
| `gemini` | `~/.gemini/settings.json`, `.gemini/settings.json` - `httpUrl` is streamable HTTP, `url` is SSE, `$VAR` and `${VAR}` both expand, `mcp.allowed` / `mcp.excluded` applied |
| `copilot` | `~/.copilot/mcp-config.json` (or `$COPILOT_HOME`), and the project's `.mcp.json` - the same file Claude Code reads |
| `vscode` | the user profile `mcp.json`, `.vscode/mcp.json` - `servers` + `inputs`; `${input:id}` is prompted, not a missing variable |
| `windsurf` | `~/.codeium/windsurf/mcp_config.json` - `serverUrl` for remote servers |
| `opencode` | `~/.config/opencode/opencode.json[c]`, `opencode.json[c]` - `command` is a list, `environment`, `type: local|remote`, comments allowed |

A name that appears in two hosts' files is two servers, not a clash; `shadowed` only
means the same name twice inside one host. Claude Code's allow / deny lists apply to
Claude Code's servers only, Gemini's `mcp.allowed` to Gemini's.

## Pick the mode from what the user asked

| The user says | Run |
|---|---|
| "what MCP servers do I have", "where is X configured" | default |
| "X failed to connect", "Connection closed", "X isn't showing up" | default, then `--probe X --full` |
| "check all of them", after a bulk install or a machine move | `--probe` |
| "which ones are slow", "how much context do they eat" | `--probe` and read the start / tools / ~tokens columns |
| "is my .mcp.json / config.toml / mcp.json OK to commit" / CI | `--strict --no-net` from the repo, with `--agent` for the file's host |
| "does Cursor have the same servers as Claude", "what does Codex see" | `--agent all` or `--agent codex` |
| a server is remote and the network is off | `--no-net` |

`--project DIR` when they mean another project. `--timeout N` for servers that take
long to start (first `npx -y` / `uvx` runs download). `--json` for processing.

## Steps

1. Run the static roll call first. It is fast and touches nothing. For each server the
   status column is `ok`, `warn`, `ERROR`, `shadowed`, `pending`, `disabled` or
   `DENIED`; the first finding is printed beside it and the full list below. The
   static checks catch most failures on their own:
   - **command-missing** - the command is not on PATH or the path does not exist.
     The fix line says what to install or which full path to use.
   - **windows-shim** - a bare `npx` / `uvx` on Windows; a host that spawns without a
     shell (Claude Code and Cursor document this) needs `cmd /c npx ...` or the
     `.cmd` path. For other hosts it is a note, not a warning.
   - **arg-path-missing** / **cwd-missing** - an absolute path in `args` (a script, a
     directory to serve) or a Codex / Gemini `cwd` that is not there.
   - **var-unset** - `${VAR}`, `${env:VAR}`, a Codex `bearer_token_env_var` or
     `env_http_headers` variable, or a Gemini `$VAR`, with no value and no default.
   - **env-empty** / **env-placeholder** / **header-placeholder** - a token that was
     never filled in.
   - **port-closed** - the server (or an `mcp-remote` bridge in its args) points at
     `localhost:PORT` and nothing is listening. This is the usual reason for
     "Connection closed": the desktop app that hosts the server is not running.
   - **shadowed** - the same name in two scopes of one host; local beats project
     beats user.
   - **approval** - a Claude Code `.mcp.json` server the user has not approved yet.
   - **denied** / **not-allowed** / **excluded** - blocked by Claude Code's
     `deniedMcpServers` / `allowedMcpServers` or Gemini's `mcp.excluded` / `mcp.allowed`.
   - **disabled** - `enabled = false` (Codex, OpenCode) or `disabledMcpjsonServers`
     (Claude Code); the host skips it on purpose.
   - **envfile-missing** (Cursor), **input-undefined** (VS Code), **short-timeout** and
     **required** (Codex): host-specific facts printed as the host would act on them.

2. If the static pass is clean for the server they asked about, run
   `--probe <name> --full`. It spawns the server with its configured env and `cwd`,
   sends `initialize`, then `tools/list`, then terminates it. Read the result:
   - **FAILED, exited before answering initialize** - the stderr tail is printed;
     the last non-empty line is usually the exception. Quote it. That is the answer
     the host withheld.
   - **FAILED, no initialize response within Ns** - it started but never spoke MCP.
     Wrong command (a CLI that is not a server), wrong transport (an HTTP server
     configured as stdio), or a first-run download still going: rerun with a longer
     `--timeout` before concluding.
   - **ok** with `start`, `tools` and `~tokens` - it works. `slow-start` (over 3 s)
     delays every session; suggest pre-installing the package instead of `npx -y` /
     `uvx`. `heavy` is thousands of tokens of tool definitions loaded per session;
     that is a cost the user may want to know about, not a fault.

3. Answer the question first. If they asked about one server, lead with its line and
   its finding, then the fix. Do not paste the whole table for twenty servers when
   one matters; give the count and the exceptions. Under `--agent all`, say which
   host each finding belongs to - the same package configured for two hosts fails or
   succeeds independently.

4. Probing has side effects the host also has: a server that talks to a desktop
   application (Blender, Photoshop, SolidWorks) will try to reach it, log that it
   could not, and still handshake. That is normal and not a failure. A server that
   *refuses to start* without its application is the one to report.

5. The claude.ai connectors (`plugin:*`) are not files; the tool lists the ones Claude
   Code has cached as needing authorization. The fix is in claude.ai's connector
   settings, not on disk, and you cannot do it from here - say so. Codex and Cursor
   OAuth servers get a TCP reachability check only.

6. Fixing is by hand, in the host's own file. Claude Code: `claude mcp remove X` /
   `claude mcp add X ...` rewrites the entry cleanly; editing `~/.claude.json` directly
   while a session is open risks the app overwriting it. Codex: edit `config.toml` and
   restart. Cursor and VS Code: edit `mcp.json`; the editor reloads it. Gemini:
   `gemini mcp add` or edit `settings.json`. After the change, reconnect (`/mcp` in
   Claude Code, restart for Codex) and rerun the roll call to confirm.

## Related, not overlapping

`claude mcp list`, `codex mcp list` and `gemini mcp list` print configured or
connected / failed per server with no reason. `/mcp` in a Claude Code session
reconnects and shows auth state. None shows stderr, checks paths and env before
starting, or counts tools. `skill-rollcall` and `settings-effective` do the same job
for skills and settings.

## What this cannot do

It does not know a server's own conventions: a directory list a server splits on
commas while the config uses semicolons looks fine statically and only the probe
catches it. It does not authenticate: an HTTP server behind OAuth gets a TCP check,
not a handshake. Host detection is best effort - a host that sets no environment
marker is reported as `all`; pass `--agent`. It reads config files, not plugin
bundles: Claude Code plugin servers are found by scanning the plugin cache for
`.mcp.json` (best-effort), and Codex / Cursor / VS Code marketplace plugins are not
read at all. Codex's `.codex/config.toml` is listed even when the project is not
trusted, because trust state is not on disk. And a probe that succeeds here can still
fail in the host if the host's PATH or environment differs from the shell you ran this
from; the report prints the command it ran so the difference can be found.
