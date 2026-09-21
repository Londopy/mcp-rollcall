"""Tests for mcprollcall.py. Stdlib only: python -m unittest discover -s tests -v

Every test builds a throwaway ~/.claude.json, --claude-home, --managed-dir and project in a
temp dir. Probes spawn tests/fake_server.py with the running interpreter, never a real
MCP server.
"""
from __future__ import annotations

import io
import json
import os
import socket
import sys
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "skills" / "mcp-rollcall" / "scripts"))
import mcprollcall  # noqa: E402

FAKE = str(HERE / "fake_server.py")
PY = sys.executable


def write(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def fake(mode: str = "ok", *extra: str, env: dict | None = None) -> dict:
    cfg = {"command": PY, "args": [FAKE, mode, *extra]}
    if env:
        cfg["env"] = env
    return cfg


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.home = self.tmp / "home"
        self.managed = self.tmp / "managed"
        self.proj = self.tmp / "proj"
        self.cj = self.tmp / "claude.json"
        for d in (self.home, self.managed, self.proj):
            d.mkdir(parents=True)
        self.user_servers: dict = {}
        self.local_servers: dict = {}
        self.pentry: dict = {}

    def tearDown(self):
        self._tmp.cleanup()

    def save(self):
        entry = dict(self.pentry)
        if self.local_servers:
            entry["mcpServers"] = self.local_servers
        write(self.cj, {"mcpServers": self.user_servers, "projects": {str(self.proj): entry} if entry else {}})

    def run_main(self, *args: str) -> tuple[int, str]:
        self.save()
        # --home is a throwaway so no other host's real config is read; tests pin
        # --agent claude unless they pass their own
        agent = [] if "--agent" in args else ["--agent", "claude"]
        argv = ["--project", str(self.proj), "--home", str(self.tmp / "fakehome"), "--claude-home", str(self.home),
                "--claude-json", str(self.cj), "--managed-dir", str(self.managed), "--no-plugins", *agent, *args]
        out = io.StringIO()
        with redirect_stdout(out):
            code = mcprollcall.main(argv)
        return code, out.getvalue()

    def report(self, *args: str) -> dict:
        code, out = self.run_main("--json", *args)
        return json.loads(out)

    def server(self, rep: dict, name: str, scope: str | None = None) -> dict:
        return next(s for s in rep["servers"] if s["name"] == name and (scope is None or s["scope"] == scope))

    def kinds(self, s: dict) -> list[str]:
        return [f["kind"] for f in s["findings"]]


# --------------------------------------------------------------------------- sources

class Sources(Base):
    def test_user_local_project_managed(self):
        self.user_servers["u"] = fake()
        self.local_servers["l"] = fake()
        write(self.proj / ".mcp.json", {"mcpServers": {"p": fake()}})
        write(self.managed / "managed-mcp.json", {"mcpServers": {"m": fake()}})
        rep = self.report()
        self.assertEqual({(s["name"], s["scope"]) for s in rep["servers"]},
                         {("u", "user"), ("l", "local"), ("p", "project"), ("m", "managed")})
        self.assertEqual(len(rep["sources"]), 4)

    def test_project_mcp_json_approval_states(self):
        write(self.proj / ".mcp.json", {"mcpServers": {"pending": fake(), "on": fake(), "off": fake()}})
        self.pentry = {"enabledMcpjsonServers": ["on"], "disabledMcpjsonServers": ["off"]}
        rep = self.report()
        self.assertEqual(self.server(rep, "pending")["state"], "pending-approval")
        self.assertEqual(self.server(rep, "on")["state"], "configured")
        self.assertEqual(self.server(rep, "off")["state"], "disabled")
        self.assertIn("approval", self.kinds(self.server(rep, "pending")))

    def test_enable_all_project_servers_from_settings(self):
        write(self.proj / ".mcp.json", {"mcpServers": {"p": fake()}})
        write(self.proj / ".claude" / "settings.json", {"enableAllProjectMcpServers": True})
        self.assertEqual(self.server(self.report(), "p")["state"], "configured")

    def test_name_clash_local_beats_user(self):
        self.user_servers["dup"] = fake()
        self.local_servers["dup"] = fake("ok", "5")
        rep = self.report()
        self.assertEqual(self.server(rep, "dup", "user")["state"], "shadowed")
        self.assertEqual(self.server(rep, "dup", "local")["state"], "configured")

    def test_denied_and_allowed_lists(self):
        self.user_servers["a"] = fake()
        self.user_servers["b"] = fake()
        write(self.home / "settings.json", {"deniedMcpServers": ["a"]})
        rep = self.report()
        self.assertEqual(self.server(rep, "a")["state"], "denied")
        self.assertEqual(self.server(rep, "b")["state"], "configured")
        write(self.home / "settings.json", {"allowedMcpServers": [{"serverName": "a"}]})
        rep = self.report()
        self.assertEqual(self.server(rep, "a")["state"], "configured")
        self.assertIn("not-allowed", self.kinds(self.server(rep, "b")))

    def test_connectors_from_auth_cache(self):
        write(self.home / "mcp-needs-auth-cache.json", {"plugin:x:slack": {"timestamp": 1}, "plugin:y:notion": {"timestamp": 2}})
        rep = self.report()
        self.assertEqual({c["name"] for c in rep["connectors"]}, {"plugin:x:slack", "plugin:y:notion"})

    def test_bad_json_reported_not_fatal(self):
        write(self.proj / ".mcp.json", "{oops")
        self.user_servers["u"] = fake()
        code, rep = 0, self.report()
        self.assertEqual(len(rep["servers"]), 1)
        self.assertTrue(any(".mcp.json" in e for e in rep["errors"]))
        self.assertEqual(self.run_main("--strict")[0], 1)

    def test_project_walks_up(self):
        write(self.proj / ".mcp.json", {"mcpServers": {"p": fake()}})
        sub = self.proj / "a" / "b"
        sub.mkdir(parents=True)
        code, out = self.run_main("--project", str(sub), "--json")
        rep = json.loads(out)
        self.assertEqual(Path(rep["project"]), self.proj.resolve())
        self.assertEqual(len(rep["servers"]), 1)


# --------------------------------------------------------------------------- static checks

class Static(Base):
    def test_command_missing_with_hint(self):
        self.user_servers["x"] = {"command": "npx-definitely-not-installed", "args": ["-y", "foo"]}
        self.user_servers["y"] = {"command": str(self.tmp / "nope" / "uvx"), "args": ["bar"]}
        rep = self.report()
        fx = next(f for f in self.server(rep, "x")["findings"] if f["kind"] == "command-missing")
        self.assertEqual(fx["severity"], "error")
        fy = next(f for f in self.server(rep, "y")["findings"] if f["kind"] == "command-missing")
        self.assertIn("uv", fy["fix"])

    def test_arg_path_missing(self):
        self.user_servers["x"] = {"command": PY, "args": [str(self.tmp / "missing" / "server.js")]}
        self.assertIn("arg-path-missing", self.kinds(self.server(self.report(), "x")))
        self.user_servers["x"] = {"command": PY, "args": [FAKE]}
        self.assertNotIn("arg-path-missing", self.kinds(self.server(self.report(), "x")))

    def test_env_checks(self):
        self.user_servers["x"] = fake(env={"TOKEN": "", "KEY": "<your-api-key>", "PATH": "C:/x", "OK": "value"})
        k = self.kinds(self.server(self.report(), "x"))
        self.assertIn("env-empty", k)
        self.assertIn("env-placeholder", k)
        self.assertIn("env-path", k)
        self.assertEqual(k.count("env-empty") + k.count("env-placeholder"), 2)

    def test_unset_variable(self):
        self.user_servers["x"] = {"command": PY, "args": [FAKE], "env": {"A": "${MCP_ROLLCALL_NOPE}", "B": "${MCP_ROLLCALL_NOPE:-dflt}"}}
        s = self.server(self.report(), "x")
        f = [f for f in s["findings"] if f["kind"] == "var-unset"]
        self.assertEqual(len(f), 1)
        self.assertIn("MCP_ROLLCALL_NOPE", f[0]["detail"])

    def test_local_port_closed_via_mcp_remote(self):
        # find a port nobody listens on
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        self.user_servers["anki"] = {"command": PY, "args": [FAKE, "ok", f"http://127.0.0.1:{port}"]}
        s = self.server(self.report(), "anki")
        self.assertIn("port-closed", self.kinds(s))
        self.assertIn(f"127.0.0.1:{port}", next(f["detail"] for f in s["findings"] if f["kind"] == "port-closed"))
        self.assertNotIn("port-closed", self.kinds(self.server(self.report("--no-net"), "anki")))

    def test_local_port_open(self):
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        t = threading.Thread(target=lambda: (srv.accept()[0].close() if True else None), daemon=True)
        t.start()
        try:
            self.user_servers["h"] = {"type": "http", "url": f"http://127.0.0.1:{port}/mcp"}
            s = self.server(self.report(), "h")
            self.assertEqual(s["type"], "http")
            self.assertEqual(self.kinds(s), [])
        finally:
            srv.close()

    def test_http_without_url(self):
        self.user_servers["h"] = {"type": "http"}
        self.assertIn("no-url", self.kinds(self.server(self.report(), "h")))

    def test_dns_failure(self):
        self.user_servers["h"] = {"type": "http", "url": "https://no-such-host.invalid/mcp"}
        self.assertIn("dns", self.kinds(self.server(self.report(), "h")))

    @unittest.skipUnless(sys.platform == "win32", "Windows-only shim check")
    def test_windows_bare_npx(self):
        self.user_servers["x"] = {"command": "npx", "args": ["-y", "foo"]}
        k = self.kinds(self.server(self.report(), "x"))
        self.assertTrue("windows-shim" in k or "command-missing" in k)

    def test_shadowed_and_disabled_skip_static(self):
        self.user_servers["dup"] = {"command": "nope-nope", "args": []}
        self.local_servers["dup"] = fake()
        s = self.server(self.report(), "dup", "user")
        self.assertEqual(self.kinds(s), ["shadowed"])


# --------------------------------------------------------------------------- probe

class Probe(Base):
    def test_ok_server(self):
        self.user_servers["ok"] = fake("ok", "4")
        s = self.server(self.report("--probe"), "ok")
        p = s["probe"]
        self.assertTrue(p["ok"], p)
        self.assertEqual(p["tools"], ["tool_0", "tool_1", "tool_2", "tool_3"])
        self.assertEqual(p["server"], "fake 0.1")
        self.assertGreater(p["tokens"], 0)
        self.assertIsNotNone(p["ms_start"])
        self.assertIn("fake server starting", p["stderr"])
        self.assertEqual(self.kinds(s), [])

    def test_crash_shows_stderr(self):
        self.user_servers["bad"] = fake("crash")
        s = self.server(self.report("--probe"), "bad")
        self.assertFalse(s["probe"]["ok"])
        self.assertEqual(s["probe"]["exit"], 3)
        self.assertIn("exited before answering initialize", s["probe"]["error"])
        f = next(f for f in s["findings"] if f["kind"] == "probe-failed")
        self.assertIn("Allowed directories must already exist", f["detail"])

    def test_hang_times_out(self):
        self.user_servers["h"] = fake("hang")
        s = self.server(self.report("--probe", "--timeout", "1.5"), "h")
        self.assertFalse(s["probe"]["ok"])
        self.assertIn("no initialize response within", s["probe"]["error"])

    def test_notools(self):
        self.user_servers["n"] = fake("notools")
        s = self.server(self.report("--probe", "--timeout", "1.5"), "n")
        self.assertTrue(s["probe"]["ok"])
        self.assertIsNone(s["probe"]["tools"])
        self.assertIn("no tools/list response", s["probe"]["error"])

    def test_zero_tools_warns(self):
        self.user_servers["z"] = fake("ok", "0")
        self.assertIn("no-tools", self.kinds(self.server(self.report("--probe"), "z")))

    def test_env_reaches_child(self):
        self.user_servers["e"] = fake("env", env={"FAKE_ENV": "hello-${MCP_RC_TEST:-there}"})
        s = self.server(self.report("--probe"), "e")
        self.assertEqual(s["probe"]["tools"], ["env_tool"])
        # the description carried the expanded env value; prove it via a direct probe
        srv = mcprollcall.Server(name="e", scope="user", source="t", command=PY, args=[FAKE, "env"],
                                 env={"FAKE_ENV": "hello-${MCP_RC_TEST:-there}"})
        p = mcprollcall.probe(srv, 10, self.proj)
        self.assertTrue(p["ok"])

    def test_probe_subset_and_unknown_name(self):
        self.user_servers["a"] = fake()
        self.user_servers["b"] = fake()
        rep = self.report("--probe", "a,zzz")
        self.assertIsNotNone(self.server(rep, "a")["probe"])
        self.assertIsNone(self.server(rep, "b")["probe"])
        self.assertTrue(any("zzz" in e for e in rep["errors"]))

    def test_probe_skips_broken_and_non_stdio(self):
        self.user_servers["missing"] = {"command": "nope-nope-nope", "args": []}
        self.user_servers["http"] = {"type": "http", "url": "http://127.0.0.1:1/"}
        rep = self.report("--probe", "--no-net")
        self.assertIsNone(self.server(rep, "missing")["probe"])
        self.assertIsNone(self.server(rep, "http")["probe"])

    def test_slow_start_flagged(self):
        old = mcprollcall.SLOW_MS
        mcprollcall.SLOW_MS = 500
        try:
            self.user_servers["s"] = fake("slow")
            self.assertIn("slow-start", self.kinds(self.server(self.report("--probe"), "s")))
        finally:
            mcprollcall.SLOW_MS = old

    def test_report_text(self):
        self.user_servers["ok"] = fake("ok", "2")
        self.user_servers["bad"] = fake("crash")
        code, out = self.run_main("--probe", "--full")
        self.assertIn("fake 0.1", out)
        self.assertIn("FAILED", out)
        self.assertIn("tools   tool_0, tool_1", out)
        self.assertIn("ValueError: Allowed directories", out)
        self.assertIn("findings  1 errors", out)
        self.assertEqual(self.run_main("--probe", "--strict")[0], 1)


if __name__ == "__main__":
    unittest.main()
