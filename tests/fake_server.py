"""A minimal MCP stdio server for tests. Mode is argv[1]:

  ok [N]    answers initialize and tools/list with N tools (default 3)
  crash     writes a traceback-ish message to stderr and exits 3
  hang      never answers anything
  notools   answers initialize, then ignores tools/list
  slow      sleeps 1.5s before answering initialize
  env       like ok, but the single tool's description is $FAKE_ENV (proves env reached the child)
"""
import json
import os
import sys
import time

mode = sys.argv[1] if len(sys.argv) > 1 else "ok"
if mode == "crash":
    print("Traceback (most recent call last):", file=sys.stderr)
    print('  File "server.py", line 1, in <module>', file=sys.stderr)
    print("ValueError: Allowed directories must already exist", file=sys.stderr)
    sys.exit(3)
if mode == "hang":
    time.sleep(3600)

n = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 3
tools = [{"name": f"tool_{i}", "description": "x" * 40, "inputSchema": {"type": "object"}} for i in range(n)]
if mode == "env":
    tools = [{"name": "env_tool", "description": os.environ.get("FAKE_ENV", "unset"), "inputSchema": {"type": "object"}}]

print("fake server starting", file=sys.stderr, flush=True)
for line in sys.stdin:
    try:
        msg = json.loads(line)
    except ValueError:
        continue
    if msg.get("method") == "initialize":
        if mode == "slow":
            time.sleep(1.5)
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
            "serverInfo": {"name": "fake", "version": "0.1"}}}), flush=True)
    elif msg.get("method") == "tools/list":
        if mode == "notools":
            continue
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": tools}}), flush=True)
