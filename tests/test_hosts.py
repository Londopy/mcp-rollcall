"""Tests for the multi-host layer of mcprollcall.py: Codex config.toml, Cursor / Gemini /
Copilot / VS Code / Windsurf / OpenCode files, host detection, per-host clash rules and
the stdlib TOML fallback. Every file lives under a throwaway --home or project.

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from test_mcprollcall import Base, write, fake, PY, FAKE  # noqa: E402
import mcprollcall  # noqa: E402


class Hosts(Base):
    def setUp(self):
        super().setUp()
        self.fh = self.tmp / "fakehome"
        self.fh.mkdir()

    def servers(self, *args: str) -> dict[str, dict]:
        rep = self.report(*args)
        return {f"{s['agent']}:{s['name']}": s for s in rep["servers"]}

    # ---------------------------------------------------------------- codex
    def test_codex_config_toml_user_and_project(self):
        write(self.fh / ".codex" / "config.toml", f'''
model = "gpt-5-codex"

[mcp_servers.fake]
command = "{PY.replace(chr(92), "/")}"
args = ["{FAKE.replace(chr(92), "/")}", "ok"]
env = {{ TOKEN = "abc" }}
startup_timeout_sec = 1

[mcp_servers.docs]
url = "https://developers.openai.com/mcp"
bearer_token_env_var = "NOPE_NOT_SET_12345"
required = true

[mcp_servers.off]
command = "nonexistent-binary-xyz"
enabled = false
''')
        write(self.proj / ".codex" / "config.toml", '[mcp_servers.repo]\ncommand = "nonexistent-binary-xyz"\ncwd = "./missing-dir"\n')
        s = self.servers("--agent", "codex", "--no-net")
        self.assertEqual(s["codex:fake"]["scope"], "user")
        self.assertEqual(s["codex:fake"]["type"], "stdio")
        self.assertEqual(s["codex:fake"]["env"], {"TOKEN": "abc"})
        self.assertTrue(any(f["kind"] == "short-timeout" for f in s["codex:fake"]["findings"]))
        self.assertEqual(s["codex:docs"]["type"], "http")
        self.assertEqual(s["codex:docs"]["headers"], {"Authorization": "Bearer ${NOPE_NOT_SET_12345}"})
        kinds = {f["kind"] for f in s["codex:docs"]["findings"]}
        self.assertIn("var-unset", kinds)
        self.assertIn("required", kinds)
        self.assertEqual(s["codex:off"]["state"], "disabled")
        self.assertFalse(any(f["kind"] == "command-missing" for f in s["codex:off"]["findings"]))
        self.assertEqual(s["codex:repo"]["scope"], "project")
        kinds = {f["kind"] for f in s["codex:repo"]["findings"]}
        self.assertIn("command-missing", kinds)
        self.assertIn("cwd-missing", kinds)
        rep = self.report("--agent", "codex", "--no-net")
        self.assertTrue(any("trusted" in src for src in rep["sources"]))

    def test_codex_probe_uses_its_cwd(self):
        d = self.proj / "work"
        d.mkdir()
        write(self.fh / ".codex" / "config.toml",
              f'[mcp_servers.fake]\ncommand = "{PY.replace(chr(92), "/")}"\n'
              f'args = ["{FAKE.replace(chr(92), "/")}", "ok"]\ncwd = "{str(d).replace(chr(92), "/")}"\n')
        s = self.servers("--agent", "codex", "--no-net", "--probe")
        self.assertTrue(s["codex:fake"]["probe"]["ok"])

    def test_codex_toml_syntax_error_is_reported(self):
        write(self.fh / ".codex" / "config.toml", "[mcp_servers.x\ncommand = ")
        rep = self.report("--agent", "codex", "--no-net")
        self.assertTrue(any("config.toml" in e for e in rep["errors"]))
        self.assertEqual(rep["servers"], [])

    # ---------------------------------------------------------------- cursor
    def test_cursor_mcp_json_with_editor_variables(self):
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {
            "gh": {"url": "https://api.example.com/mcp", "headers": {"Authorization": "Bearer ${env:CURSOR_TEST_TOKEN}"}}}})
        write(self.proj / ".cursor" / "mcp.json", '''{
  // comments and trailing commas are fine for the editor
  "mcpServers": {
    "local": {"command": "nonexistent-binary-xyz", "args": ["${workspaceFolder}/server.py"], "envFile": ".env",},
  },
}''')
        with mock.patch.dict(os.environ, {"CURSOR_TEST_TOKEN": "t"}):
            s = self.servers("--agent", "cursor", "--no-net")
        self.assertEqual(s["cursor:gh"]["scope"], "user")
        self.assertFalse(any(f["kind"] == "var-unset" for f in s["cursor:gh"]["findings"]))
        loc = s["cursor:local"]
        self.assertEqual(loc["scope"], "project")
        kinds = {f["kind"] for f in loc["findings"]}
        self.assertIn("command-missing", kinds)
        self.assertIn("envfile-missing", kinds)
        # ${workspaceFolder} resolved to the project, so the arg-path check saw a real path
        self.assertTrue(any(f["kind"] == "arg-path-missing" and str(self.proj.name) in f["detail"] for f in loc["findings"]))

    def test_env_colon_unset_is_an_error(self):
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {"x": {"command": PY, "env": {"K": "${env:NOPE_NOT_SET_67890}"}}}})
        s = self.servers("--agent", "cursor", "--no-net")
        self.assertTrue(any(f["kind"] == "var-unset" and "NOPE_NOT_SET_67890" in f["detail"] for f in s["cursor:x"]["findings"]))

    # ---------------------------------------------------------------- gemini
    def test_gemini_settings_transports_and_allowlist(self):
        write(self.fh / ".gemini" / "settings.json", {
            "mcp": {"allowed": ["stdio", "stream"]},
            "mcpServers": {
                "stdio": {"command": PY, "args": [FAKE, "ok"], "env": {"K": "$GEMINI_TEST_VAR"}, "trust": True},
                "stream": {"httpUrl": "http://localhost:59999/mcp"},
                "sse": {"url": "http://localhost:59998/sse"},
            }})
        with mock.patch.dict(os.environ, {"GEMINI_TEST_VAR": "v"}):
            s = self.servers("--agent", "gemini", "--no-net")
        self.assertEqual(s["gemini:stdio"]["type"], "stdio")
        self.assertTrue(any(f["kind"] == "trusted" for f in s["gemini:stdio"]["findings"]))
        self.assertFalse(any(f["kind"] == "var-unset" for f in s["gemini:stdio"]["findings"]))
        self.assertEqual(s["gemini:stream"]["type"], "http")
        self.assertEqual(s["gemini:stream"]["url"], "http://localhost:59999/mcp")
        self.assertEqual(s["gemini:sse"]["type"], "sse")
        self.assertEqual(s["gemini:sse"]["state"], "denied")
        self.assertTrue(any(f["kind"] == "not-allowed" for f in s["gemini:sse"]["findings"]))

    def test_gemini_bare_dollar_unset_is_error_but_not_for_claude(self):
        write(self.fh / ".gemini" / "settings.json", {"mcpServers": {"g": {"command": PY, "env": {"K": "$NOPE_NOT_SET_555"}}}})
        self.user_servers["c"] = {"command": PY, "env": {"K": "$NOPE_NOT_SET_555"}}
        s = self.servers("--agent", "gemini,claude", "--no-net")
        self.assertTrue(any(f["kind"] == "var-unset" for f in s["gemini:g"]["findings"]))
        self.assertFalse(any(f["kind"] == "var-unset" for f in s["claude:c"]["findings"]))

    # ---------------------------------------------------------------- copilot
    def test_copilot_mcp_config_and_shared_project_file(self):
        write(self.fh / ".copilot" / "mcp-config.json", {"mcpServers": {
            "play": {"type": "local", "command": "nonexistent-binary-xyz", "args": [], "tools": ["*"]},
            "ctx": {"type": "http", "url": "https://mcp.context7.com/mcp", "tools": ["*"]}}})
        write(self.proj / ".mcp.json", {"mcpServers": {"shared": {"command": PY, "args": [FAKE, "ok"]}}})
        s = self.servers("--agent", "copilot", "--no-net")
        self.assertEqual(s["copilot:play"]["type"], "stdio")
        self.assertTrue(any(f["kind"] == "command-missing" for f in s["copilot:play"]["findings"]))
        self.assertEqual(s["copilot:ctx"]["type"], "http")
        self.assertEqual(s["copilot:shared"]["scope"], "project")
        rep = self.report("--agent", "copilot", "--no-net")
        self.assertTrue(any("same file Claude Code reads" in src for src in rep["sources"]))

    # ---------------------------------------------------------------- vscode
    def test_vscode_servers_and_inputs(self):
        write(self.proj / ".vscode" / "mcp.json", {
            "inputs": [{"id": "api-key", "type": "promptString", "password": True}],
            "servers": {
                "gh": {"type": "http", "url": "https://api.githubcopilot.com/mcp"},
                "pw": {"command": "nonexistent-binary-xyz", "args": ["-y", "x"], "env": {"KEY": "${input:api-key}"}},
                "bad": {"command": PY, "env": {"KEY": "${input:missing-input}"}},
            }})
        s = self.servers("--agent", "vscode", "--no-net")
        self.assertEqual(s["vscode:gh"]["type"], "http")
        self.assertTrue(any(f["kind"] == "prompted" for f in s["vscode:pw"]["findings"]))
        self.assertFalse(any(f["kind"] == "var-unset" for f in s["vscode:pw"]["findings"]))
        self.assertTrue(any(f["kind"] == "input-undefined" for f in s["vscode:bad"]["findings"]))

    # ---------------------------------------------------------------- windsurf / opencode
    def test_windsurf_server_url(self):
        write(self.fh / ".codeium" / "windsurf" / "mcp_config.json", {"mcpServers": {
            "remote": {"serverUrl": "https://example.com/mcp", "headers": {"API_KEY": "<YOUR_KEY>"}},
            "gh": {"command": "nonexistent-binary-xyz", "args": ["-y", "@modelcontextprotocol/server-github"]}}})
        s = self.servers("--agent", "windsurf", "--no-net")
        self.assertEqual(s["windsurf:remote"]["type"], "http")
        self.assertEqual(s["windsurf:remote"]["url"], "https://example.com/mcp")
        self.assertTrue(any(f["kind"] == "header-placeholder" for f in s["windsurf:remote"]["findings"]))
        self.assertTrue(any(f["kind"] == "command-missing" for f in s["windsurf:gh"]["findings"]))

    def test_opencode_list_command_and_jsonc(self):
        write(self.proj / "opencode.jsonc", '''{
  "$schema": "https://opencode.ai/config.json",
  "mcp": {
    // local server as a command list
    "everything": {"type": "local", "command": ["nonexistent-binary-xyz", "-y", "@modelcontextprotocol/server-everything"],
                   "environment": {"MY_VAR": "v"}, "enabled": true},
    "jira": {"type": "remote", "url": "https://jira.example.com/mcp", "enabled": false},
  },
}''')
        s = self.servers("--agent", "opencode", "--no-net")
        ev = s["opencode:everything"]
        self.assertEqual(ev["command"], "nonexistent-binary-xyz")
        self.assertEqual(ev["args"], ["-y", "@modelcontextprotocol/server-everything"])
        self.assertEqual(ev["env"], {"MY_VAR": "v"})
        self.assertTrue(any(f["kind"] == "command-missing" for f in ev["findings"]))
        self.assertEqual(s["opencode:jira"]["type"], "http")
        self.assertEqual(s["opencode:jira"]["state"], "disabled")

    # ---------------------------------------------------------------- cross-host rules
    def test_same_name_in_two_hosts_is_not_a_clash(self):
        self.user_servers["ctx"] = {"command": PY, "args": [FAKE, "ok"]}
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {"ctx": {"command": PY, "args": [FAKE, "ok"]}}})
        s = self.servers("--agent", "claude,cursor", "--no-net")
        self.assertEqual(s["claude:ctx"]["state"], "configured")
        self.assertEqual(s["cursor:ctx"]["state"], "configured")

    def test_same_name_twice_in_one_host_is_shadowed(self):
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {"ctx": {"command": PY}}})
        write(self.proj / ".cursor" / "mcp.json", {"mcpServers": {"ctx": {"command": PY}}})
        rep = self.report("--agent", "cursor", "--no-net")
        states = {s["scope"]: s["state"] for s in rep["servers"]}
        self.assertEqual(states, {"project": "configured", "user": "shadowed"})

    def test_claude_deny_list_does_not_touch_other_hosts(self):
        write(self.home / "settings.json", {"deniedMcpServers": ["ctx"]})
        self.user_servers["ctx"] = {"command": PY}
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {"ctx": {"command": PY}}})
        s = self.servers("--agent", "claude,cursor", "--no-net")
        self.assertEqual(s["claude:ctx"]["state"], "denied")
        self.assertEqual(s["cursor:ctx"]["state"], "configured")

    def test_all_reads_every_host_and_labels_scopes(self):
        self.user_servers["c"] = {"command": PY}
        write(self.fh / ".codex" / "config.toml", '[mcp_servers.x]\ncommand = "nonexistent-binary-xyz"\n')
        write(self.fh / ".cursor" / "mcp.json", {"mcpServers": {"y": {"command": PY}}})
        code, out = self.run_main("--agent", "all", "--no-net")
        self.assertIn("host          all (--agent all)", out)
        self.assertIn("claude user", out)
        self.assertIn("codex user", out)
        self.assertIn("cursor user", out)
        rep = self.report("--agent", "all", "--no-net")
        self.assertEqual({s["agent"] for s in rep["servers"]}, {"claude", "codex", "cursor"})

    def test_unknown_agent_is_an_error(self):
        with self.assertRaises(SystemExit):
            self.run_main("--agent", "clippy")

    def test_host_detected_from_environment(self):
        write(self.fh / ".codex" / "config.toml", '[mcp_servers.x]\ncommand = "nonexistent-binary-xyz"\n')
        self.user_servers["c"] = {"command": PY}
        keep = {k: v for k, v in os.environ.items() if k in ("HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "PATH", "SYSTEMROOT")}
        with mock.patch.dict(os.environ, {**keep, "CODEX_SANDBOX": "seatbelt"}, clear=True):
            code, out = self.run_main("--agent", "auto", "--no-net")
        self.assertIn("host          codex (CODEX_SANDBOX set)", out)
        self.assertNotIn("claude user", out)
        with mock.patch.dict(os.environ, keep, clear=True):
            code, out = self.run_main("--agent", "auto", "--no-net")
        self.assertIn("host          all (no host marker", out)

    def test_project_dir_stops_at_home_and_finds_host_markers(self):
        # a folder with only a .cursor/ is a project root; a gitless folder under home never
        # promotes the home directory itself
        nested = self.proj / "a" / "b"
        nested.mkdir(parents=True)
        (self.proj / ".cursor").mkdir()
        self.assertEqual(mcprollcall.project_dir(nested, {self.fh.resolve()}), self.proj.resolve())
        loose = self.fh / "loose"
        loose.mkdir()
        self.assertEqual(mcprollcall.project_dir(loose, {self.fh.resolve()}), loose.resolve())

    def test_json_rows_carry_agent_and_host(self):
        self.user_servers["c"] = {"command": PY}
        rep = self.report("--no-net")
        self.assertEqual(rep["host"], "claude")
        self.assertEqual(rep["servers"][0]["agent"], "claude")
        self.assertIn("cwd", rep["servers"][0])


# --------------------------------------------------------------------------- helpers

class Helpers(unittest.TestCase):
    def test_strip_jsonc(self):
        src = '{"a": 1, // c\n "b": "x // not a comment", /* block */ "c": [1, 2,],}'
        self.assertEqual(json.loads(mcprollcall.strip_jsonc(src)), {"a": 1, "b": "x // not a comment", "c": [1, 2]})

    def test_expand_editor_forms(self):
        mcprollcall.CTX.update({"workspaceFolder": "/ws", "userHome": "/home/u"})
        unset: set[str] = set()
        out = mcprollcall.expand("${workspaceFolder}/x ${userHome} ${env:EXP_TEST_A} ${input:key} ${EXP_TEST_B:-dflt}",
                                 {"EXP_TEST_A": "a"}, unset)
        self.assertEqual(out, "/ws/x /home/u a ${input:key} dflt")
        self.assertEqual(unset, set())
        out = mcprollcall.expand("$BARE ${env:GONE}", {}, unset, bare=True)
        self.assertEqual(out, " ")
        self.assertEqual(unset, {"BARE", "GONE"})

    def test_toml_fallback_matches_tomllib(self):
        src = ('[mcp_servers.a]\ncommand = "npx"\nargs = ["-y", "pkg"]  # c\nenv = { K = "v", "D.K" = \'l\' }\n'
               'enabled = false\nstartup_timeout_sec = 2.5\n\n[mcp_servers."b.c"]\nurl = "https://x/mcp"\n'
               'http_headers = { "X-A" = "1" }\n\n[[skills.config]]\npath = "p"\nenabled = true\n')
        got = mcprollcall._toml_fallback(src)
        self.assertEqual(got["mcp_servers"]["a"]["args"], ["-y", "pkg"])
        self.assertEqual(got["mcp_servers"]["b.c"]["http_headers"], {"X-A": "1"})
        self.assertIs(got["mcp_servers"]["a"]["enabled"], False)
        try:
            import tomllib
        except ImportError:
            self.skipTest("tomllib needs Python 3.11+")
        self.assertEqual(got, tomllib.loads(src))


if __name__ == "__main__":
    unittest.main()
