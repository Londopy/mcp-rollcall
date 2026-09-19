---
name: mcp-rollcall
description: Take a roll call of configured MCP servers - which are configured where, which will fail to connect and exactly why (missing command, dead path, unset variable, nothing listening on the port, the server's own stderr), and what each one costs in tools and context. Use this whenever the user says an MCP server failed to connect, shows "Connection closed" or "still connecting", asks why a server or its tools are missing, asks what MCP servers they have or where one is configured, asks which servers are slow or heavy, wants MCP config checked before committing a .mcp.json, or after adding or editing a server. Also use it when your own system context lists a server as failed and the user asks about it.
---

# mcp-rollcall

Claude Code starts every configured MCP server at session start and, when one fails,
says `failed to connect` or `Connection closed` and nothing else. The server's stderr,
the exit code, the missing directory, the port nobody is listening on - all of that is
discarded. This skill reads every place a server can be configured, checks each one
the way the harness would try to start it, and on request actually starts it, does the
MCP handshake and reports what came back.

The script is at `scripts/mcprollcall.py` next to this file (installed as a skill that
is `~/.claude/skills/mcp-rollcall/scripts/mcprollcall.py`). Stdlib-only. It never
writes a config file. Only `--probe` starts servers; everything else is static.

## Pick the mode from what the user asked

| The user says | Run |
|---|---|
| "what MCP servers do I have", "where is X configured" | default |
| "X failed to connect", "Connection closed", "X isn't showing up" | default, then `--probe X --full` |
| "check all of them", after a bulk install or a machine move | `--probe` |
| "which ones are slow", "how much context do they eat" | `--probe` and read the start / tools / ~tokens columns |
| "is my .mcp.json OK to commit" / CI | `--strict --no-net` from the repo |
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
   - **windows-shim** - a bare `npx` / `uvx` on Windows; the harness needs
     `cmd /c npx ...` or the `.cmd` path.
   - **arg-path-missing** - an absolute path in `args` (a script, a directory to
     serve) that is not there.
   - **var-unset** - `${VAR}` with no value and no default.
   - **env-empty** / **env-placeholder** - a token that was never filled in.
   - **port-closed** - the server (or an `mcp-remote` bridge in its args) points at
     `localhost:PORT` and nothing is listening. This is the usual reason for
     "Connection closed": the desktop app that hosts the server is not running.
   - **shadowed** - the same name in two scopes; local beats project beats user.
   - **approval** - a `.mcp.json` server the user has not approved yet.
   - **denied** / **not-allowed** - blocked by `deniedMcpServers` / `allowedMcpServers`.

2. If the static pass is clean for the server they asked about, run
   `--probe <name> --full`. It spawns the server with its configured env, sends
   `initialize`, then `tools/list`, then terminates it. Read the result:
   - **FAILED, exited before answering initialize** - the stderr tail is printed;
     the last non-empty line is usually the exception. Quote it. That is the answer
     the harness withheld.
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
   one matters; give the count and the exceptions.

4. Probing has side effects the harness also has: a server that talks to a desktop
   application (Blender, Photoshop, SolidWorks) will try to reach it, log that it
   could not, and still handshake. That is normal and not a failure. A server that
   *refuses to start* without its application is the one to report.

5. The claude.ai connectors (`plugin:*`) are not files; the tool lists the ones the
   harness has cached as needing authorization. The fix is in claude.ai's connector
   settings, not on disk, and you cannot do it from here - say so.

6. Fixing is by hand. `claude mcp remove X` / `claude mcp add X ...` rewrites the
   entry cleanly; editing `~/.claude.json` directly while a session is open risks
   the app overwriting it. For a `.mcp.json`, edit the file. After the change,
   `/mcp` in the session reconnects; rerun the roll call to confirm.

## Related, not overlapping

`claude mcp list` prints connected / failed per server with no reason. `/mcp` in a
session reconnects and shows auth state. Neither shows stderr, checks paths and env
before starting, or counts tools. `skill-rollcall` and `settings-effective` do the
same job for skills and settings.

## What this cannot do

It does not know a server's own conventions: a directory list a server splits on
commas while the config uses semicolons looks fine statically and only the probe
catches it. It does not authenticate: an HTTP server behind OAuth gets a TCP check,
not a handshake. Plugin servers are found by scanning the plugin cache for
`.mcp.json`, which is best-effort. And a probe that succeeds here can still fail in
the harness if the harness's PATH or environment differs from the shell you ran this
from; the report prints the command it ran so the difference can be found.
