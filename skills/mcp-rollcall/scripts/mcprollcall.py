#!/usr/bin/env python3
"""mcp-rollcall: take a roll call of configured MCP servers - who fails, why, and what they cost.

Every agent - Claude Code, Codex, Cursor, Gemini CLI, Copilot, VS Code, Windsurf,
OpenCode - reports a server as "failed to connect" or "Connection closed" and keeps the
reason to itself. This reads every place a server can be configured for the host you
are in (or all of them), checks each one the way the host would try to start it, and -
on request - actually starts it, does the MCP handshake, and shows the stderr and the
tool list the host hides.

    python mcprollcall.py                     static roll call: sources, commands, paths, env, ports
    python mcprollcall.py --agent codex       another host's config files (or `all`)
    python mcprollcall.py --probe             spawn every stdio server; handshake, tools, cost, stderr
    python mcprollcall.py --probe a,b        only these
    python mcprollcall.py --project DIR       the project whose .mcp.json and local servers count
    python mcprollcall.py --timeout 30        seconds to wait for a handshake (default 20)
    python mcprollcall.py --no-net            skip the TCP reachability checks
    python mcprollcall.py --problems-only     findings only
    python mcprollcall.py --strict            exit 1 on any error-level finding
    python mcprollcall.py --json              everything, machine-readable

Stdlib only. Never writes a config file. --probe starts servers; nothing else does.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, asdict
from pathlib import Path
from urllib.parse import urlparse

RANK = {"managed": 0, "local": 1, "project": 2, "user": 3, "plugin": 4}   # who wins a name clash
VAR = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
# Cursor / VS Code forms: ${env:NAME}, ${workspaceFolder}, ${userHome}, ${input:id}
EDITOR_VAR = re.compile(r"\$\{(env|input):([A-Za-z_][A-Za-z0-9_-]*)\}|\$\{(workspaceFolder|workspaceFolderBasename|userHome)\}")
BARE_VAR = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")                     # Gemini CLI accepts $NAME too
CTX: dict[str, str] = {}                                                  # workspaceFolder / userHome for EDITOR_VAR
PLACEHOLDER = re.compile(r"^(?:<[^>]*>|\[[^\]]*\]|your[-_ ]|xxx+|todo|changeme|replace[-_ ]?me|sk-\.\.\.)", re.I)
ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|~/)")
PKG_RUNNERS = {"npx", "npm", "pnpm", "yarn", "bunx", "uvx", "uv", "pipx", "deno"}
SLOW_MS = 3000
CHARS_PER_TOKEN = 4
STDERR_KEEP = 40
MANAGED_DIRS = {
    "darwin": Path("/Library/Application Support/ClaudeCode"),
    "linux": Path("/etc/claude-code"),
    "win32": Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "ClaudeCode",
}

# --------------------------------------------------------------------------- hosts
# Where each host keeps MCP servers. `env` are variables a host sets in the shells it
# spawns; detection is best effort and --agent overrides it. Sources: each host's docs,
# 2026-09 (Codex config reference, Cursor mcp.json, Gemini CLI settings, Copilot CLI
# mcp-config.json, VS Code mcp.json, Windsurf mcp_config.json, OpenCode config).
AGENTS: dict[str, dict] = {
    "claude":   {"label": "Claude Code",    "env": ["CLAUDECODE"],
                 "files": "~/.claude.json, .mcp.json, managed-mcp.json, plugin .mcp.json"},
    "codex":    {"label": "Codex",          "env": ["CODEX_SANDBOX", "CODEX_SANDBOX_NETWORK_DISABLED"],
                 "files": "~/.codex/config.toml [mcp_servers], .codex/config.toml"},
    "cursor":   {"label": "Cursor",         "env": ["CURSOR_AGENT"],
                 "files": "~/.cursor/mcp.json, .cursor/mcp.json"},
    "gemini":   {"label": "Gemini CLI",     "env": ["GEMINI_CLI"],
                 "files": "~/.gemini/settings.json, .gemini/settings.json"},
    "copilot":  {"label": "Copilot CLI",    "env": [],
                 "files": "~/.copilot/mcp-config.json, .mcp.json"},
    "vscode":   {"label": "VS Code",        "env": [],
                 "files": "<user profile>/mcp.json, .vscode/mcp.json"},
    "windsurf": {"label": "Windsurf",       "env": [],
                 "files": "~/.codeium/windsurf/mcp_config.json"},
    "opencode": {"label": "OpenCode",       "env": [],
                 "files": "~/.config/opencode/opencode.json, opencode.json[c]"},
}


def detect_host() -> tuple[str | None, str | None]:
    for key, spec in AGENTS.items():
        for var in spec["env"]:
            if os.environ.get(var):
                return key, var
    return None, None


# --------------------------------------------------------------------------- model

@dataclass
class Server:
    name: str
    scope: str                       # user | local | project | managed | plugin
    source: str                      # file it came from
    type: str = "stdio"              # stdio | http | sse | ws
    command: str = ""
    args: list = field(default_factory=list)
    env: dict = field(default_factory=dict)
    url: str = ""
    headers: dict = field(default_factory=dict)
    cwd: str = ""                    # Codex / Gemini working directory for the process
    agent: str = "claude"            # which host's config it came from
    state: str = "configured"        # configured | shadowed | disabled | pending-approval | denied
    findings: list = field(default_factory=list)     # {severity, kind, detail, fix}
    probe: dict | None = None

    @property
    def where(self) -> str:
        return f"{self.agent} {self.scope}"

    def add(self, severity: str, kind: str, detail: str, fix: str = "") -> None:
        self.findings.append({"severity": severity, "kind": kind, "detail": detail, "fix": fix})

    @property
    def worst(self) -> str:
        sev = [f["severity"] for f in self.findings]
        return "error" if "error" in sev else "warn" if "warn" in sev else "ok"


@dataclass
class Report:
    project: str
    host: str = "claude"                                   # detected / requested host, or "all"
    host_why: str = ""
    servers: list[Server] = field(default_factory=list)
    connectors: list[dict] = field(default_factory=list)   # claude.ai connectors from the auth cache
    sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- loading

def read_json(p: Path) -> tuple[dict, str]:
    try:
        text = p.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return {}, ""
    except OSError as e:
        return {}, f"{p}: {e}"
    try:
        d = json.loads(text)
    except ValueError as e:
        # editors and OpenCode accept comments and trailing commas; Claude Code, Gemini
        # CLI and Copilot do not, so a stray comma there is reported as the host sees it
        tolerant = p.suffix.lower() == ".jsonc" or p.name == "opencode.json" or p.parent.name in (".vscode", ".cursor")
        if not tolerant:
            return {}, f"{p}: {e}"
        try:
            d = json.loads(strip_jsonc(text))
        except ValueError as e2:
            return {}, f"{p}: {e2}"
    return (d if isinstance(d, dict) else {}), ""


def strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas outside strings."""
    out, i, n = [], 0, len(text)
    while i < n:
        ch = text[i]
        if ch == '"':
            j = i + 1
            while j < n and text[j] != '"':
                j += 2 if text[j] == "\\" else 1
            out.append(text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            i = text.find("\n", i)
            i = n if i < 0 else i
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            i = n if j < 0 else j + 2
        else:
            out.append(ch)
            i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def read_toml(path: Path) -> tuple[dict, str]:
    """TOML via tomllib (3.11+) or a small stdlib fallback covering what config.toml
    files use: tables, arrays of tables, dotted and quoted keys, strings, numbers,
    booleans, arrays and inline tables."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}, ""
    except OSError as e:
        return {}, f"{path}: {e}"
    try:
        import tomllib  # type: ignore
        try:
            return tomllib.loads(text), ""
        except tomllib.TOMLDecodeError as e:
            return {}, f"{path}: {e}"
    except ImportError:
        try:
            return _toml_fallback(text), ""
        except ValueError as e:
            return {}, f"{path}: {e}"


def _toml_fallback(text: str) -> dict:
    root: dict = {}
    table: dict = root
    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = _toml_strip_comment(lines[i]).strip()
        i += 1
        if not line:
            continue
        if line.startswith("[["):
            keys = _toml_keys(line[2:line.index("]]")])
            parent = _toml_descend(root, keys[:-1])
            arr = parent.setdefault(keys[-1], [])
            if not isinstance(arr, list):
                raise ValueError("not an array of tables")
            table = {}
            arr.append(table)
            continue
        if line.startswith("["):
            table = _toml_descend(root, _toml_keys(line[1:line.index("]")]))
            continue
        if "=" not in line:
            raise ValueError(f"bad line: {line}")
        k, _, v = line.partition("=")
        v = v.strip()
        while v and v[0] in "[{" and _toml_unbalanced(v) and i < len(lines):
            v += " " + _toml_strip_comment(lines[i]).strip()
            i += 1
        keys = _toml_keys(k)
        _toml_descend(table, keys[:-1])[keys[-1]] = _toml_value(v)
    return root


def _toml_strip_comment(line: str) -> str:
    in_str = None
    j = 0
    while j < len(line):
        ch = line[j]
        if in_str:
            if ch == "\\" and in_str == '"':
                j += 2
                continue
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch == "#":
            return line[:j]
        j += 1
    return line


def _toml_keys(s: str) -> list[str]:
    return [k.strip().strip("\"'") for k in re.findall(r'"[^"]*"|\'[^\']*\'|[^.]+', s.strip())]


def _toml_descend(d: dict, keys: list[str]) -> dict:
    for k in keys:
        nxt = d.setdefault(k, {})
        if isinstance(nxt, list):
            nxt = nxt[-1]
        if not isinstance(nxt, dict):
            raise ValueError(f"{k} is not a table")
        d = nxt
    return d


def _toml_unbalanced(v: str) -> bool:
    depth, in_str = 0, None
    for ch in v:
        if in_str:
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
    return depth > 0 or in_str is not None


def _toml_split(body: str) -> list[str]:
    parts, buf, depth, in_str = [], "", 0, None
    for ch in body:
        if in_str:
            buf += ch
            if ch == in_str:
                in_str = None
        elif ch in "\"'":
            in_str = ch
            buf += ch
        elif ch in "[{":
            depth += 1
            buf += ch
        elif ch in "]}":
            depth -= 1
            buf += ch
        elif ch == "," and depth == 0:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    if buf.strip():
        parts.append(buf)
    return [p.strip() for p in parts if p.strip()]


def _toml_value(v: str):
    v = v.strip()
    if not v:
        raise ValueError("empty value")
    if v[0] == '"':
        end = _toml_str_end(v, '"')
        return bytes(v[1:end], "utf-8").decode("unicode_escape") if "\\" in v[1:end] else v[1:end]
    if v[0] == "'":
        return v[1:_toml_str_end(v, "'")]
    if v[0] == "[":
        return [_toml_value(p) for p in _toml_split(v[1:v.rindex("]")])]
    if v[0] == "{":
        out: dict = {}
        for p in _toml_split(v[1:v.rindex("}")]):
            k, _, val = p.partition("=")
            keys = _toml_keys(k)
            _toml_descend(out, keys[:-1])[keys[-1]] = _toml_value(val)
        return out
    if v in ("true", "false"):
        return v == "true"
    try:
        return int(v.replace("_", ""))
    except ValueError:
        pass
    try:
        return float(v.replace("_", ""))
    except ValueError:
        return v


def _toml_str_end(v: str, q: str) -> int:
    i = 1
    while i < len(v):
        if v[i] == "\\" and q == '"':
            i += 2
            continue
        if v[i] == q:
            return i
        i += 1
    raise ValueError("unterminated string")


def expand(value: str, env: dict, unset: set[str], bare: bool = False) -> str:
    """${NAME}, ${NAME:-default}; the editor forms ${env:NAME}, ${workspaceFolder},
    ${userHome} (${input:id} is left as-is: the editor prompts for it); and, when
    `bare` is set (Gemini CLI), plain $NAME."""
    def sub(m):
        name, default = m.group(1), m.group(2)
        if name in env:
            return env[name]
        if default is not None:
            return default
        unset.add(name)
        return ""

    def sub_editor(m):
        kind, name, token = m.group(1), m.group(2), m.group(3)
        if kind == "env":
            if name in env:
                return env[name]
            unset.add(name)
            return ""
        if kind == "input":
            return m.group(0)
        return CTX.get(token, m.group(0))

    out = VAR.sub(sub, EDITOR_VAR.sub(sub_editor, value))
    if bare:
        out = BARE_VAR.sub(lambda m: env.get(m.group(1)) if m.group(1) in env else (unset.add(m.group(1)) or ""), out)
    return out


def make_server(name: str, cfg: dict, scope: str, source: str, agent: str = "claude") -> Server:
    """Normalize one host's entry. Aliases: OpenCode `command` as a list and
    `environment`; Gemini `httpUrl`; Windsurf `serverUrl`; Copilot / OpenCode
    `type: local|remote`; Codex `bearer_token_env_var`, `env_http_headers`, `http_headers`."""
    s = Server(name=name, scope=scope, source=source, agent=agent)
    if not isinstance(cfg, dict):
        s.add("error", "config", "entry is not an object")
        return s
    command = cfg.get("command") or ""
    args = list(cfg.get("args") or [])
    if isinstance(command, list):                 # OpenCode: "command": ["npx", "-y", "pkg"]
        command, args = (command[0] if command else ""), [*command[1:], *args]
    url = cfg.get("url") or cfg.get("httpUrl") or cfg.get("serverUrl") or ""
    typ = str(cfg.get("type") or "").lower()
    typ = {"local": "stdio", "remote": "http", "streamable-http": "http", "streamable_http": "http"}.get(typ, typ)
    if not typ:
        typ = "http" if url and not command else "stdio"
        if cfg.get("url") and agent == "gemini" and not cfg.get("httpUrl") and not command:
            typ = "sse"                           # Gemini: `url` is SSE, `httpUrl` is streamable HTTP
    s.type = typ
    s.command = str(command)
    s.args = [str(a) for a in args]
    s.env = {str(k): str(v) for k, v in (cfg.get("env") or cfg.get("environment") or {}).items()}
    s.url = str(url)
    s.headers = {str(k): str(v) for k, v in (cfg.get("headers") or cfg.get("http_headers") or {}).items()}
    for k, var in (cfg.get("env_http_headers") or {}).items():        # Codex: header value from an env var
        s.headers[str(k)] = f"${{{var}}}"
    if cfg.get("bearer_token_env_var"):
        s.headers.setdefault("Authorization", f"Bearer ${{{cfg['bearer_token_env_var']}}}")
    s.cwd = str(cfg.get("cwd") or "")
    if cfg.get("enabled") is False:
        s.state = "disabled"
        s.add("info", "disabled", "enabled = false in its config; the host skips it")
    return s


PROJECT_MARKERS = (".mcp.json", ".claude", ".codex", ".cursor", ".gemini", ".vscode", ".agents",
                   "opencode.json", "opencode.jsonc")


def project_dir(start: Path, homes: set[Path] | None = None) -> Path:
    """Nearest of cwd and its parents with a project config folder or a git root, never
    climbing into a home directory (whose .claude/, .codex/, .cursor/ are user config)."""
    start = start.resolve()
    homes = homes or set()
    try:
        homes.add(Path.home().resolve())
    except RuntimeError:
        pass
    for p in [start, *start.parents]:
        if p in homes and p != start:
            break
        if any((p / m).exists() for m in PROJECT_MARKERS) or (p / ".git").exists():
            return p
    return start


def parse_agents(value: str) -> tuple[list[str], str, str]:
    if value == "auto":
        key, var = detect_host()
        if key:
            return [key], key, f"{var} set"
        return list(AGENTS), "all", "no host marker in the environment; pass --agent to narrow"
    if value == "all":
        return list(AGENTS), "all", "--agent all"
    keys = [k.strip() for k in value.split(",") if k.strip()]
    bad = [k for k in keys if k not in AGENTS]
    if bad:
        raise SystemExit(f"unknown agent {', '.join(bad)}; choose from {', '.join(AGENTS)} or all")
    return keys, ",".join(keys), "--agent"


def load_all(a) -> Report:
    home = Path(a.home)
    claude_home = Path(a.claude_home) if a.claude_home else home / ".claude"
    agents, host, why = parse_agents(a.agent)
    proj = project_dir(Path(a.project), {home.resolve()})
    rep = Report(project=str(proj), host=host, host_why=why)
    CTX.update({"workspaceFolder": str(proj), "workspaceFolderBasename": proj.name, "userHome": str(home)})
    loaders = {"claude": load_claude, "codex": load_codex, "cursor": load_cursor, "gemini": load_gemini,
               "copilot": load_copilot, "vscode": load_vscode, "windsurf": load_windsurf, "opencode": load_opencode}
    for key in agents:
        loaders[key](rep, a, home, claude_home, proj)
    resolve_clashes(rep.servers)
    return rep


def _add(rep: Report, agent: str, scope: str, path: Path, servers: dict, note: str = "") -> list[Server]:
    out = []
    for name, cfg in (servers or {}).items():
        out.append(make_server(name, cfg, scope, str(path), agent))
    if servers:
        rep.sources.append(f"{agent} {scope}: {path}" + (f"  ({note})" if note else ""))
    rep.servers.extend(out)
    return out


def load_codex(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    codex_home = Path(os.environ["CODEX_HOME"]) if os.environ.get("CODEX_HOME") and not a.home_given else home / ".codex"
    for scope, path, note in (("user", codex_home / "config.toml", ""),
                              ("project", proj / ".codex" / "config.toml", "loaded only when the project is trusted")):
        cfg, err = read_toml(path)
        if err:
            rep.errors.append(err)
        servers = cfg.get("mcp_servers") if isinstance(cfg.get("mcp_servers"), dict) else {}
        for s in _add(rep, "codex", scope, path, servers, note):
            src = servers.get(s.name) or {}
            if src.get("required") is True:
                s.add("info", "required", "required = true: Codex refuses to start if this server fails")
            if src.get("startup_timeout_sec") not in (None, "") and float(src["startup_timeout_sec"]) < 3:
                s.add("warn", "short-timeout", f"startup_timeout_sec = {src['startup_timeout_sec']}; npx / uvx servers "
                      "usually need longer on a cold start", "raise it, or pre-install the package")


def load_cursor(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    for scope, path in (("user", home / ".cursor" / "mcp.json"), ("project", proj / ".cursor" / "mcp.json")):
        d, err = read_json(path)
        if err:
            rep.errors.append(err)
        for s in _add(rep, "cursor", scope, path, d.get("mcpServers")):
            ef = (d.get("mcpServers") or {}).get(s.name, {}).get("envFile")
            if ef:
                p = Path(expand(str(ef), dict(os.environ), set()))
                if not p.is_absolute():
                    p = proj / p
                if not p.exists():
                    s.add("error", "envfile-missing", f"envFile does not exist: {ef}")


def load_gemini(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    allowed = excluded = None
    servers: list[Server] = []
    for scope, path in (("user", home / ".gemini" / "settings.json"), ("project", proj / ".gemini" / "settings.json")):
        d, err = read_json(path)
        if err:
            rep.errors.append(err)
        servers += _add(rep, "gemini", scope, path, d.get("mcpServers"))
        mcp = d.get("mcp") if isinstance(d.get("mcp"), dict) else {}
        if isinstance(mcp.get("allowed"), list):
            allowed = set(mcp["allowed"])
        if isinstance(mcp.get("excluded"), list):
            excluded = (excluded or set()) | set(mcp["excluded"])
        for s in servers:
            src = (d.get("mcpServers") or {}).get(s.name) or {}
            if src.get("trust") is True:
                s.add("info", "trusted", "trust = true: tool calls run without confirmation")
    for s in servers:
        if excluded and s.name in excluded:
            s.state = "denied"
            s.add("error", "excluded", "listed in mcp.excluded; Gemini CLI will not connect to it")
        elif allowed is not None and s.name not in allowed:
            s.state = "denied"
            s.add("error", "not-allowed", "mcp.allowed is set and does not include it")


def load_copilot(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    chome = Path(os.environ["COPILOT_HOME"]) if os.environ.get("COPILOT_HOME") and not a.home_given else home / ".copilot"
    d, err = read_json(chome / "mcp-config.json")
    if err:
        rep.errors.append(err)
    _add(rep, "copilot", "user", chome / "mcp-config.json", d.get("mcpServers"))
    d, err = read_json(proj / ".mcp.json")
    if err and "claude" not in rep.host and rep.host != "all":
        rep.errors.append(err)
    _add(rep, "copilot", "project", proj / ".mcp.json", d.get("mcpServers"), "the same file Claude Code reads")


def load_vscode(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    if sys.platform == "win32":
        user = home / "AppData" / "Roaming" / "Code" / "User" / "mcp.json"
    elif sys.platform == "darwin":
        user = home / "Library" / "Application Support" / "Code" / "User" / "mcp.json"
    else:
        user = home / ".config" / "Code" / "User" / "mcp.json"
    for scope, path in (("user", user), ("project", proj / ".vscode" / "mcp.json")):
        d, err = read_json(path)
        if err:
            rep.errors.append(err)
        inputs = {i.get("id") for i in (d.get("inputs") or []) if isinstance(i, dict)}
        for s in _add(rep, "vscode", scope, path, d.get("servers")):
            used = set(re.findall(r"\$\{input:([^}]+)\}", json.dumps((d.get("servers") or {}).get(s.name) or {})))
            for i in sorted(used - inputs):
                s.add("error", "input-undefined", f"${{input:{i}}} has no matching entry in \"inputs\"")
            if used & inputs:
                s.add("info", "prompted", f"VS Code prompts for {', '.join(sorted(used & inputs))} on first start")


def load_windsurf(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    path = home / ".codeium" / "windsurf" / "mcp_config.json"
    d, err = read_json(path)
    if err:
        rep.errors.append(err)
    _add(rep, "windsurf", "user", path, d.get("mcpServers"))


def load_opencode(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    files = [("user", home / ".config" / "opencode" / "opencode.json"),
             ("user", home / ".config" / "opencode" / "opencode.jsonc"),
             ("project", proj / "opencode.json"), ("project", proj / "opencode.jsonc")]
    for scope, path in files:
        d, err = read_json(path)
        if err:
            rep.errors.append(err)
        _add(rep, "opencode", scope, path, d.get("mcp") if isinstance(d.get("mcp"), dict) else {})


def load_claude(rep: Report, a, home: Path, claude_home: Path, proj: Path) -> None:
    claude_json = Path(a.claude_json) if a.claude_json else home / ".claude.json"
    cj, err = read_json(claude_json)
    if err:
        rep.errors.append(err)

    # user scope
    for name, cfg in (cj.get("mcpServers") or {}).items():
        rep.servers.append(make_server(name, cfg, "user", str(claude_json)))
    if cj.get("mcpServers"):
        rep.sources.append(f"claude user: {claude_json}")

    # local scope (per project, inside ~/.claude.json)
    pentry = {}
    for k, v in (cj.get("projects") or {}).items():
        try:
            if Path(k).resolve() == proj:
                pentry = v or {}
                break
        except OSError:
            pass
    for name, cfg in (pentry.get("mcpServers") or {}).items():
        rep.servers.append(make_server(name, cfg, "local", f"{claude_json} projects[{proj}]"))
    if pentry.get("mcpServers"):
        rep.sources.append(f"claude local: {claude_json} projects[...]")

    # settings that gate project servers and allow/deny lists
    settings = merged_settings(proj, claude_home, Path(a.managed_dir) if a.managed_dir else MANAGED_DIRS.get(sys.platform, MANAGED_DIRS["linux"]))
    enabled_json = set(pentry.get("enabledMcpjsonServers") or []) | set(settings.get("enabledMcpjsonServers") or [])
    disabled_json = set(pentry.get("disabledMcpjsonServers") or []) | set(settings.get("disabledMcpjsonServers") or [])
    enable_all = bool(pentry.get("enableAllProjectMcpServers") or settings.get("enableAllProjectMcpServers"))

    # project scope: .mcp.json
    mcp_json = proj / ".mcp.json"
    pj, err = read_json(mcp_json)
    if err:
        rep.errors.append(err)
    for name, cfg in (pj.get("mcpServers") or {}).items():
        s = make_server(name, cfg, "project", str(mcp_json))
        if name in disabled_json:
            s.state = "disabled"
            s.add("info", "disabled", "listed in disabledMcpjsonServers; the harness skips it")
        elif not (enable_all or name in enabled_json):
            s.state = "pending-approval"
            s.add("warn", "approval", ".mcp.json servers load only after you approve them in the prompt Claude Code shows",
                  "answer the prompt, or set enableAllProjectMcpServers / enabledMcpjsonServers")
        rep.servers.append(s)
    if pj.get("mcpServers"):
        rep.sources.append(f"claude project: {mcp_json}")

    # managed
    mdir = Path(a.managed_dir) if a.managed_dir else MANAGED_DIRS.get(sys.platform, MANAGED_DIRS["linux"])
    mm, err = read_json(mdir / "managed-mcp.json")
    if err:
        rep.errors.append(err)
    for name, cfg in (mm.get("mcpServers") or {}).items():
        rep.servers.append(make_server(name, cfg, "managed", str(mdir / "managed-mcp.json")))
    for name, cfg in (settings.get("managedMcpServers") or {}).items():
        rep.servers.append(make_server(name, cfg, "managed", "managed settings managedMcpServers"))
    if mm.get("mcpServers") or settings.get("managedMcpServers"):
        rep.sources.append("claude managed")

    # plugins (best effort: any .mcp.json under the plugin cache)
    if not a.no_plugins:
        proot = claude_home / "plugins"
        if proot.is_dir():
            for p in sorted(proot.rglob(".mcp.json")):
                if len(p.relative_to(proot).parts) > 6:
                    continue
                d, err = read_json(p)
                if err:
                    rep.errors.append(err)
                    continue
                for name, cfg in (d.get("mcpServers") or {}).items():
                    rep.servers.append(make_server(name, cfg, "plugin", str(p)))
                if d.get("mcpServers"):
                    rep.sources.append(f"claude plugin: {p}")

    # allow / deny lists
    allowed = settings.get("allowedMcpServers")
    denied = settings.get("deniedMcpServers") or []
    for s in rep.servers:
        if s.scope == "managed" or s.agent != "claude":
            continue
        if any(_rule_matches(r, s) for r in denied):
            s.state = "denied"
            s.add("error", "denied", "matches deniedMcpServers; the harness will not load it")
        elif allowed is not None and not any(_rule_matches(r, s) for r in allowed):
            s.state = "denied"
            s.add("error", "not-allowed", "allowedMcpServers is set and does not include it")

    # claude.ai connectors
    cache, _ = read_json(claude_home / "mcp-needs-auth-cache.json")
    for k, v in cache.items():
        rep.connectors.append({"name": k, "needs_auth": True, "since": (v or {}).get("timestamp")})


def _rule_matches(rule, s: Server) -> bool:
    if isinstance(rule, str):
        return rule == s.name
    if isinstance(rule, dict):
        if "serverName" in rule and rule["serverName"] != s.name:
            return False
        if "serverCommand" in rule and rule["serverCommand"] != [s.command, *s.args]:
            return False
        if "serverUrl" in rule and rule["serverUrl"] != s.url:
            return False
        return True
    return False


def merged_settings(proj: Path, home: Path, managed_dir: Path) -> dict:
    """Only the MCP-relevant keys; lists union, scalars highest-wins (managed > local > project > user)."""
    out: dict = {}
    files = [home / "settings.json", proj / ".claude" / "settings.json", proj / ".claude" / "settings.local.json",
             managed_dir / "managed-settings.json"]
    for f in files:
        d, _ = read_json(f)
        for k in ("enabledMcpjsonServers", "disabledMcpjsonServers", "deniedMcpServers"):
            if isinstance(d.get(k), list):
                out[k] = out.get(k, []) + [x for x in d[k] if x not in out.get(k, [])]
        if isinstance(d.get("allowedMcpServers"), list):
            out["allowedMcpServers"] = out.get("allowedMcpServers", []) + d["allowedMcpServers"]
        for k in ("enableAllProjectMcpServers",):
            if k in d:
                out[k] = d[k]
        if f.name == "managed-settings.json" and isinstance(d.get("managedMcpServers"), dict):
            out["managedMcpServers"] = d["managedMcpServers"]
    return out


def resolve_clashes(servers: list[Server]) -> None:
    """Same name twice inside one host: the higher-ranked scope wins. The same name in
    two hosts' files is two servers, not a clash."""
    by_name: dict[tuple[str, str], list[Server]] = {}
    for s in servers:
        by_name.setdefault((s.agent, s.name), []).append(s)
    for (_, name), group in by_name.items():
        if len(group) < 2:
            continue
        group.sort(key=lambda s: RANK[s.scope])
        winner = group[0]
        for loser in group[1:]:
            if loser.state == "configured":
                loser.state = "shadowed"
            loser.add("warn", "shadowed", f"same name in {winner.scope} scope wins ({winner.source})",
                      "remove one, or rename it")


# --------------------------------------------------------------------------- static checks

def which(cmd: str) -> str | None:
    if ABS_PATH.match(cmd) or os.sep in cmd or "/" in cmd:
        p = Path(os.path.expanduser(cmd))
        return str(p) if p.exists() else None
    return shutil.which(cmd)


def check_static(s: Server, do_net: bool, project: Path | None = None) -> None:
    if s.state in ("shadowed", "disabled", "denied"):
        return
    unset: set[str] = set()
    env = dict(os.environ)
    bare = s.agent == "gemini"
    for k, v in s.env.items():
        ev = expand(v, env, unset, bare)
        if not ev.strip():
            s.add("warn", "env-empty", f"env {k} is empty", "set it, or remove the key")
        elif PLACEHOLDER.match(ev.strip()):
            s.add("warn", "env-placeholder", f"env {k} looks like a placeholder: {ev[:40]!r}", "put the real value in")
        if k.upper() == "PATH":
            s.add("info", "env-path", "env overrides PATH for the server; anything not in this value is invisible to it")
    cmd = expand(s.command, env, unset, bare)
    args = [expand(x, env, unset, bare) for x in s.args]
    url = expand(s.url, env, unset, bare)
    headers = {k: expand(v, env, unset, bare) for k, v in s.headers.items()}
    for name in sorted(unset):
        s.add("error", "var-unset", f"${{{name}}} is not set in your environment and has no default",
              f"export {name}, or write ${{{name}:-default}}")
    if s.cwd:
        cwd = (project or Path.cwd()) / os.path.expanduser(expand(s.cwd, env, set(), bare))
        if not cwd.is_dir():
            s.add("error", "cwd-missing", f"cwd does not exist: {s.cwd}")

    if s.type == "stdio":
        if not cmd:
            s.add("error", "no-command", "stdio server has no command")
            return
        resolved = which(cmd)
        base = Path(cmd).name.lower()
        stem = base.split(".")[0]
        if resolved is None:
            hint = ""
            if stem in ("npx", "npm", "node"):
                hint = "install Node.js, or use the full path to npx.cmd"
            elif stem in ("uvx", "uv"):
                hint = "install uv (pip install uv), or use the full path to uvx"
            elif stem in ("python", "python3"):
                hint = "use the full path to the interpreter that has the package"
            s.add("error", "command-missing", f"command not found: {cmd}", hint)
        elif sys.platform == "win32" and stem in PKG_RUNNERS and "." not in base and stem != "cmd":
            # documented for Claude Code and Cursor, which spawn without a shell; other hosts may resolve the .cmd shim
            s.add("warn" if s.agent in ("claude", "cursor") else "info", "windows-shim",
                  f"bare '{cmd}' on Windows: a host that spawns without a shell needs 'cmd /c {cmd} ...' or the full path to {stem}.cmd",
                  f'"command": "cmd", "args": ["/c", "{cmd}", ...]')
        if resolved and " " in resolved and stem == "cmd":
            pass
        for i, x in enumerate(args):
            if ABS_PATH.match(x) and not any(ch in x for ch in "*?") and "://" not in x:
                p = Path(os.path.expanduser(x))
                if not p.exists():
                    s.add("error", "arg-path-missing", f"args[{i}] path does not exist: {x}")
        # mcp-remote / bridged URLs
        for x in args:
            if "://" in x:
                check_url(s, x, do_net, via="args")
        if stem == "python" and "-m" not in args and args and args[0].endswith(".py"):
            pass
    else:
        if not url:
            s.add("error", "no-url", f"{s.type} server has no url")
        else:
            check_url(s, url, do_net, via="url")
        for k, hv in headers.items():
            if "${" in s.headers[k] and unset:
                continue                      # already reported as var-unset
            if not hv.strip() or PLACEHOLDER.match(hv.strip()):
                s.add("warn", "header-placeholder", f"header {k} is empty or a placeholder")


def check_url(s: Server, url: str, do_net: bool, via: str) -> None:
    try:
        u = urlparse(url)
    except ValueError:
        s.add("error", "bad-url", f"cannot parse {url}")
        return
    if u.scheme not in ("http", "https", "ws", "wss"):
        s.add("warn", "bad-url", f"unexpected scheme in {url}")
        return
    host = u.hostname or ""
    port = u.port or (443 if u.scheme in ("https", "wss") else 80)
    if not do_net or not host:
        return
    local = host in ("localhost", "127.0.0.1", "::1", "0.0.0.0")
    try:
        with socket.create_connection((host, port), timeout=0.8 if local else 3.0):
            pass
    except socket.gaierror:
        s.add("error", "dns", f"cannot resolve {host}")
    except (OSError, socket.timeout):
        if local:
            s.add("error", "port-closed", f"nothing listening on {host}:{port}",
                  "start the application that hosts this server, then reconnect")
        else:
            s.add("warn", "unreachable", f"{host}:{port} did not accept a TCP connection (network, VPN, or the service is down)")


# --------------------------------------------------------------------------- probe

def _reader(stream, sink: list, lock: threading.Lock, keep: int | None) -> None:
    try:
        for raw in iter(stream.readline, b""):
            with lock:
                sink.append(raw)
                if keep and len(sink) > keep:
                    del sink[0]
    except (OSError, ValueError):
        pass


def probe(s: Server, timeout: float, cwd: Path) -> dict:
    """Spawn, initialize, tools/list, terminate. Everything the harness sees and doesn't show."""
    out: dict = {"ok": False, "ms_start": None, "ms_tools": None, "tools": None, "tokens": None,
                 "server": None, "error": None, "stderr": [], "exit": None}
    if s.type != "stdio":
        out["error"] = f"{s.type} servers are not spawned; see the reachability check"
        return out
    unset: set[str] = set()
    env = dict(os.environ)
    bare = s.agent == "gemini"
    env.update({k: expand(v, env, unset, bare) for k, v in s.env.items()})
    cmd = [expand(s.command, env, unset, bare), *[expand(x, env, unset, bare) for x in s.args]]
    if s.cwd:
        cwd = cwd / os.path.expanduser(expand(s.cwd, env, unset, bare))
    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(cmd, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except OSError as e:
        out["error"] = f"spawn failed: {e.strerror or e}"
        return out
    lines: list[bytes] = []
    errs: list[bytes] = []
    lock = threading.Lock()
    threading.Thread(target=_reader, args=(proc.stdout, lines, lock, None), daemon=True).start()
    threading.Thread(target=_reader, args=(proc.stderr, errs, lock, STDERR_KEEP), daemon=True).start()

    def send(obj: dict) -> bool:
        try:
            proc.stdin.write((json.dumps(obj) + "\n").encode("utf-8"))
            proc.stdin.flush()
            return True
        except (OSError, ValueError):
            return False

    def wait_for(rid: int, deadline: float) -> dict | None:
        seen = 0
        while time.monotonic() < deadline:
            with lock:
                pending = lines[seen:]
                seen = len(lines)
            for raw in pending:
                try:
                    msg = json.loads(raw.decode("utf-8", errors="replace"))
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == rid:
                    return msg
            if proc.poll() is not None and not pending:
                return None
            time.sleep(0.02)
        return None

    try:
        deadline = t0 + timeout
        send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
              "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                         "clientInfo": {"name": "mcp-rollcall", "version": "1.0"}}})
        init = wait_for(1, deadline)
        if init is None:
            out["error"] = ("exited before answering initialize" if proc.poll() is not None
                            else f"no initialize response within {timeout:.0f}s")
            return out
        out["ms_start"] = int((time.monotonic() - t0) * 1000)
        if "error" in init:
            out["error"] = f"initialize error: {json.dumps(init['error'])[:200]}"
            return out
        info = (init.get("result") or {}).get("serverInfo") or {}
        out["server"] = f"{info.get('name', '?')} {info.get('version', '')}".strip()
        send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        send({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        t1 = time.monotonic()
        tl = wait_for(2, deadline)
        if tl is None:
            out["error"] = "initialized, but no tools/list response" + (" (exited)" if proc.poll() is not None else " (timeout)")
            out["ok"] = True          # it did handshake; the harness would show it as connected
            return out
        out["ms_tools"] = int((time.monotonic() - t1) * 1000)
        tools = ((tl.get("result") or {}).get("tools") or []) if "error" not in tl else []
        out["tools"] = [t.get("name", "?") for t in tools]
        out["tokens"] = len(json.dumps(tools)) // CHARS_PER_TOKEN
        out["ok"] = True
        return out
    finally:
        try:
            proc.stdin.close()
        except OSError:
            pass
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            try:
                proc.kill()
                proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                pass
        out["exit"] = proc.returncode
        time.sleep(0.05)
        with lock:
            out["stderr"] = [e.decode("utf-8", errors="replace").rstrip() for e in errs][-STDERR_KEEP:]
        for stream in (proc.stdout, proc.stderr):
            try:
                stream.close()
            except OSError:
                pass


def apply_probe_findings(s: Server) -> None:
    p = s.probe
    if not p:
        return
    if not p["ok"]:
        tail = next((l for l in reversed(p["stderr"]) if l.strip()), "")
        s.add("error", "probe-failed", p["error"] + (f" - stderr: {tail[:160]}" if tail else " - no stderr"),
              "run --probe with --full to see the whole stderr tail")
    else:
        if p["ms_start"] and p["ms_start"] > SLOW_MS:
            s.add("warn", "slow-start", f"{p['ms_start']} ms to initialize; this delays every session start",
                  "pre-install the package instead of npx -y / uvx, or move it to a project scope")
        if p["tools"] is not None and len(p["tools"]) == 0:
            s.add("warn", "no-tools", "connected but exposes no tools")
        if p["tokens"] and p["tokens"] > 8000:
            s.add("info", "heavy", f"~{p['tokens']} tokens of tool definitions")


# --------------------------------------------------------------------------- report

def short(v: str, n: int) -> str:
    return v if len(v) <= n else v[: n - 1] + "…"


def cmdline(s: Server) -> str:
    if s.type != "stdio":
        return s.url
    return " ".join([Path(s.command).name, *s.args]) if s.command else "(no command)"


def render(rep: Report, a) -> None:
    W = 200 if a.full else 46
    multi = len({s.agent for s in rep.servers}) > 1 or rep.host == "all"
    SW = 16 if multi else 8
    print(f"mcp-rollcall  {rep.project}")
    print(f"host          {rep.host} ({rep.host_why})")
    print()
    counts = {}
    for s in rep.servers:
        k = s.where if multi else s.scope
        counts[k] = counts.get(k, 0) + 1
    scopes = ", ".join(f"{n} {k}" for k, n in sorted(counts.items(), key=lambda kv: (kv[0].split()[0], RANK[kv[0].split()[-1]])))
    line = f"  {len(rep.servers)} server{'s' if len(rep.servers) != 1 else ''}"
    if scopes:
        line += f": {scopes}"
    if rep.connectors:
        line += f"   +{len(rep.connectors)} claude.ai connector{'s' if len(rep.connectors) != 1 else ''} needing auth"
    print(line)
    for src in rep.sources:
        print(f"  {src}")
    for e in rep.errors:
        print(f"  SKIPPED {e}")

    if not a.problems_only:
        print()
        probed = any(s.probe for s in rep.servers)
        hdr = f"  {'name':14} {'scope':{SW}} {'type':6} {'command / url':{W}}"
        hdr += f"  {'start':>7} {'tools':>5} {'~tokens':>7}  " if probed else "  "
        print(hdr + "status")
        for s in rep.servers:
            st = s.state if s.state != "configured" else s.worst
            if s.probe and s.state == "configured":
                st = ("ok" if s.probe["ok"] else "FAILED") if s.worst != "error" or s.probe["ok"] else "FAILED"
            row = f"  {s.name:14} {(s.where if multi else s.scope):{SW}} {s.type:6} {short(cmdline(s), W):{W}}"
            if probed:
                p = s.probe or {}
                ms = f"{p['ms_start']}ms" if p.get("ms_start") is not None else "-"
                tl = str(len(p["tools"])) if p.get("tools") is not None else "-"
                tk = str(p["tokens"]) if p.get("tokens") is not None else "-"
                row += f"  {ms:>7} {tl:>5} {tk:>7}  "
            else:
                row += "  "
            tag = {"ok": "ok", "warn": "warn", "error": "ERROR", "shadowed": "shadowed", "disabled": "disabled",
                   "pending-approval": "pending", "denied": "DENIED", "FAILED": "FAILED"}.get(st, st)
            first = next((f["detail"] for f in s.findings if f["severity"] == "error"), None) or \
                    next((f["detail"] for f in s.findings if f["severity"] == "warn"), None)
            if s.probe and s.probe.get("server") and tag == "ok":
                first = s.probe["server"]
            print(row + tag + (f"  {short(first, 70 if not a.full else 400)}" if first else ""))
        if a.full and probed:
            for s in rep.servers:
                if s.probe and (s.probe["tools"] or s.probe["stderr"]):
                    print()
                    print(f"  {s.name}")
                    if s.probe["tools"]:
                        print(f"    tools   {', '.join(s.probe['tools'])}")
                    if s.probe["stderr"]:
                        print("    stderr  " + "\n            ".join(s.probe["stderr"]))

    fs = [(s, f) for s in rep.servers for f in s.findings]
    n_err = sum(f["severity"] == "error" for _, f in fs)
    n_warn = sum(f["severity"] == "warn" for _, f in fs)
    print()
    print(f"findings  {n_err} errors, {n_warn} warnings, {len(fs) - n_err - n_warn} notes")
    for s, f in sorted(fs, key=lambda x: ({"error": 0, "warn": 1, "info": 2}[x[1]["severity"]], x[0].name)):
        tag = {"error": "ERROR", "warn": "warn", "info": "note"}[f["severity"]]
        print(f"  {tag:6} {s.name:14} {f['kind']:16} {f['detail']}")
        if f.get("fix"):
            print(f"  {'':6} {'':14} {'':16} -> {f['fix']}")
    if rep.connectors and not a.problems_only:
        print()
        print(f"connectors  {len(rep.connectors)} claude.ai connector{'s' if len(rep.connectors) != 1 else ''} need authorization "
              "(claude.ai > Settings > Connectors; not fixable from a file)")
        if a.full:
            for c in rep.connectors:
                print(f"  {c['name']}")
    if not any(s.probe for s in rep.servers) and rep.servers and not a.problems_only:
        print()
        print("next      --probe to start each server and see its handshake, tool count and stderr")


# --------------------------------------------------------------------------- main

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", default=os.getcwd(), help="project directory (default: cwd)")
    ap.add_argument("--probe", nargs="?", const="all", default=None, metavar="NAMES",
                    help="spawn stdio servers and handshake; 'all' or a comma-separated list")
    ap.add_argument("--timeout", type=float, default=20.0, help="seconds to wait for a handshake (default 20)")
    ap.add_argument("--jobs", type=int, default=4, help="parallel probes (default 4)")
    ap.add_argument("--no-net", action="store_true", help="skip TCP reachability checks")
    ap.add_argument("--no-plugins", action="store_true", help="skip plugin .mcp.json files")
    ap.add_argument("--problems-only", action="store_true", help="findings only")
    ap.add_argument("--full", action="store_true", help="untruncated commands, tool names, whole stderr tail")
    ap.add_argument("--strict", action="store_true", help="exit 1 on any error-level finding")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--agent", default="auto",
                    help="which host's config to read: " + ", ".join(AGENTS)
                         + ", a comma list, or all (default: detect from the environment, else all)")
    ap.add_argument("--home", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--claude-home", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--claude-json", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--managed-dir", default=None, help=argparse.SUPPRESS)
    a = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    a.home_given = a.home is not None
    a.home = a.home or str(Path.home())
    rep = load_all(a)
    for s in rep.servers:
        check_static(s, not a.no_net, Path(rep.project))

    if a.probe is not None:
        wanted = None if a.probe == "all" else {x.strip() for x in a.probe.split(",") if x.strip()}
        targets = [s for s in rep.servers if s.state == "configured" and s.type == "stdio"
                   and (wanted is None or s.name in wanted)
                   and not any(f["kind"] in ("command-missing", "var-unset") for f in s.findings)]
        if wanted:
            missing = wanted - {s.name for s in rep.servers}
            for m in sorted(missing):
                rep.errors.append(f"--probe {m}: no such server")
        with ThreadPoolExecutor(max_workers=max(1, a.jobs)) as ex:
            for s, res in zip(targets, ex.map(lambda s: probe(s, a.timeout, Path(rep.project)), targets)):
                s.probe = res
                apply_probe_findings(s)

    if a.json:
        print(json.dumps(asdict(rep), indent=2, default=str))
    else:
        render(rep, a)
    if a.strict and (rep.errors or any(f["severity"] == "error" for s in rep.servers for f in s.findings)):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
