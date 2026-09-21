"""Render docs/demo.png: real mcprollcall output on a fixture, styled as a terminal.

Run from the repo root:  python docs/make_demo.py
Needs Pillow (dev-only; the tool itself has no dependencies). Spawns tests/fake_server.py.
"""
from __future__ import annotations

import io
import json
import re
import socket
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "skills" / "mcp-rollcall" / "scripts"))
import mcprollcall  # noqa: E402

FAKE = str(ROOT / "tests" / "fake_server.py")
FONT = next(p for p in [Path("C:/Windows/Fonts/CascadiaMono.ttf"),
                        Path("C:/Windows/Fonts/consola.ttf"),
                        Path("/System/Library/Fonts/Menlo.ttc"),
                        Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")] if p.exists())

BG, FG, DIM = (24, 26, 32), (220, 223, 228), (120, 126, 138)
GREEN, YELLOW, RED, BLUE, PURPLE = (126, 204, 140), (230, 190, 90), (240, 110, 110), (110, 170, 240), (190, 140, 240)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        home, proj, cj, managed = t / "home", t / "Code" / "nexium", t / "claude.json", t / "managed"
        claude_home = home / ".claude"
        claude_home.mkdir(parents=True); proj.mkdir(parents=True); managed.mkdir()
        py = sys.executable
        pyq = py.replace("\\", "/")
        fakeq = FAKE.replace("\\", "/")
        # the same context7 for Codex, plus a Codex-only docs server missing its token
        (home / ".codex").mkdir()
        (home / ".codex" / "config.toml").write_text(
            f'[mcp_servers.context7]\ncommand = "{pyq}"\nargs = ["{fakeq}", "ok", "2"]\n\n'
            '[mcp_servers.openai-docs]\nurl = "https://developers.openai.com/mcp"\nbearer_token_env_var = "OPENAI_DOCS_TOKEN"\n')
        # Cursor: a remote github server with an ${env:} header
        (home / ".cursor").mkdir()
        (home / ".cursor" / "mcp.json").write_text(json.dumps({"mcpServers": {
            "github": {"url": "https://api.githubcopilot.com/mcp", "headers": {"Authorization": "Bearer ${env:GITHUB_MCP_TOKEN}"}}}}))
        servers = {
            "context7":   {"command": py, "args": [FAKE, "ok", "2"]},
            "solidworks": {"command": py, "args": [FAKE, "ok", "132"]},
            "wireshark":  {"command": py, "args": [FAKE, "crash"], "env": {"WIRESHARK_MCP_ALLOWED_DIRS": "C:\\Users\\londo\\Downloads;C:\\Users\\londo\\captures"}},
            "anki":       {"command": py, "args": [FAKE, "ok", "0", f"http://127.0.0.1:{free_port()}"]},
            "kicad":      {"command": "C:\\Program Files\\nodejs\\node.exe", "args": ["C:\\Users\\londo\\mcp-servers\\kicad-mcp\\dist\\index.js"]},
            "obs":        {"command": "npx", "args": ["-y", "obs-mcp"], "env": {"OBS_WEBSOCKET_PASSWORD": "<your-password>"}},
        }
        (claude_home / "mcp-needs-auth-cache.json").write_text(json.dumps({f"plugin:x:{n}": {"timestamp": 1} for n in ("slack", "notion", "linear")}))
        cj.write_text(json.dumps({"mcpServers": servers, "projects": {}}))
        (proj / ".mcp.json").write_text(json.dumps({"mcpServers": {"repo-docs": {"command": py, "args": [FAKE, "ok", "4"]}}}))
        pretty = {"context7": "npx.cmd -y @upstash/context7-mcp", "solidworks": "solidworks-mcp.exe",
                  "wireshark": "uvx.exe wireshark-mcp", "anki": "npx.cmd -y mcp-remote http://127.0.0.1:3141",
                  "repo-docs": "npx.cmd -y repo-docs-mcp"}
        real_cmdline = mcprollcall.cmdline
        mcprollcall.cmdline = lambda s: pretty.get(s.name) or real_cmdline(s)
        buf = io.StringIO()
        with redirect_stdout(buf):
            mcprollcall.main(["--project", str(proj), "--home", str(home), "--claude-home", str(claude_home),
                              "--claude-json", str(cj), "--managed-dir", str(managed), "--no-plugins",
                              "--agent", "claude,codex,cursor", "--no-net",
                              "--probe", "context7,solidworks,wireshark", "--timeout", "15"])
        mcprollcall.cmdline = real_cmdline
        out = (buf.getvalue().replace(str(cj), "~/.claude.json").replace(str(proj), "~/Code/nexium")
               .replace(str(home), "~").replace(str(t), "~").replace("\\", "/"))
        out = out.replace("(--agent)", "(CLAUDECODE set; --agent widened it)")
        out = re.sub(r"127\.0\.0\.1:\d+", "127.0.0.1:3141", out)
        out = out.replace("fake 0.1", "Context7 4.1.1", 1).replace("fake 0.1", "SolidWorks MCP Server 4.0.3", 1).replace("fake 0.1", "Context7 4.1.1", 1)
        out = out.replace("ValueError: Allowed directories must already exist", "ValueError: Allowed directories must already exist and be directories")

    lines = ["$ python mcprollcall.py --agent claude,codex,cursor --probe context7,solidworks,wireshark", ""] + out.rstrip().splitlines()
    lines = [l if len(l) <= 122 else l[:121] + "…" for l in lines]
    font = ImageFont.truetype(str(FONT), 15)
    lh, pad, width = 22, 28, 1160
    height = pad * 2 + lh * len(lines) + 30
    img = Image.new("RGB", (width, height), BG)
    d = ImageDraw.Draw(img)
    for i, c in enumerate([(255, 95, 86), (255, 189, 46), (39, 201, 63)]):
        d.ellipse([pad + i * 22, 14, pad + i * 22 + 12, 26], fill=c)
    d.text((width // 2 - 50, 12), "mcp-rollcall", fill=DIM, font=font)
    y = pad + 20
    for line in lines:
        color = FG
        if line.startswith("$ "):
            color = GREEN
        elif line.startswith(("mcp-rollcall", "host", "findings", "connectors", "next")):
            color = BLUE
        elif re.search(r"\s(ERROR|FAILED|DENIED)(\s|$)", line) or line.strip().startswith("ERROR"):
            color = RED
        elif re.search(r"\s(warn|pending|shadowed)(\s|$)", line) or line.strip().startswith("warn"):
            color = YELLOW
        elif line.strip().startswith(("note", "->")) or line.startswith("  name") or re.match(r"\s+\w+ (user|local|project|managed|plugin):", line):
            color = DIM
        elif re.search(r"\s+ok(\s|$)", line):
            color = GREEN
        d.text((pad, y), line, fill=color, font=font)
        y += lh
    outp = ROOT / "docs" / "demo.png"
    img.save(outp, optimize=True)
    print(f"wrote {outp} ({width}x{height})")


if __name__ == "__main__":
    main()
