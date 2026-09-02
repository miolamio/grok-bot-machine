#!/usr/bin/env python3
"""GBM-22: in-container gbm-act talks HTTP to 127.0.0.1:7070; killing it leaves the desktop."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")
HOST = os.environ.get("GBM_CONTROL_URL", "http://127.0.0.1:7070").rstrip("/")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WS = os.path.join(ROOT, "workspace")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def sh(*args: str, check: bool = True, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=check)


def box(*args: str, timeout: float = 45.0) -> subprocess.CompletedProcess:
    return sh("docker", "exec", "-u", "box", CONTAINER, *args, timeout=timeout)


def parse_json(raw: str) -> dict:
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        die(f"not json: {raw[:500]}")
        raise


def host_health() -> dict:
    with urllib.request.urlopen(HOST + "/health", timeout=5) as resp:
        return json.loads(resp.read())


def main() -> int:
    sh("docker", "inspect", CONTAINER)

    idle = box("bash", "-lc", "ps -eo args | grep -E '[g]bm-act|[g]bm-agent' || true")
    if idle.stdout.strip():
        die(f"gbm-act already running at idle (must not start on entrypoint):\n{idle.stdout}")
    ok("entrypoint did not start an in-box agent")

    health = parse_json(box("gbm-act", "health").stdout)
    if health.get("ok") is not True:
        die(f"in-box health: {health}")
    ok("docker exec -u box gbm-act health")

    doc = parse_json(box("gbm-act", "doctor").stdout)
    if doc.get("display") != ":1":
        die(f"doctor.display={doc.get('display')!r} (must be container :1)")
    if doc.get("blockers"):
        die(f"doctor blockers: {doc.get('blockers')}")
    r = doc.get("readiness") or {}
    if not r.get("can_shell"):
        die(f"readiness {r}")
    ok("gbm-act doctor display=:1 blockers empty")

    obs = parse_json(box("gbm-act", "observe", "--no-tree").stdout)
    if "windows" not in obs and "focused" not in obs:
        die(f"observe: {list(obs)}")
    if obs.get("screenshot"):
        die("observe --no-tree included screenshot")
    ok("gbm-act observe (in-box HTTP)")

    path = os.path.join(WS, "inside.txt")
    try:
        os.remove(path)
    except FileNotFoundError:
        pass
    box("bash", "-lc", "sudo -n chmod 1777 /workspace >/dev/null 2>&1 || true")
    sh_out = parse_json(box("gbm-act", "shell", "echo from-inside > /workspace/inside.txt").stdout)
    if not sh_out.get("ok") or sh_out.get("results", [{}])[0].get("result", {}).get("exit") != 0:
        die(f"shell: {sh_out}")
    with open(path) as f:
        if f.read().strip() != "from-inside":
            die("inside.txt contents")
    ok("gbm-act shell wrote workspace/inside.txt")

    moved = parse_json(box(
        "gbm-act", "act",
        '[{"type":"xdotool","action":"mousemove","x":311,"y":155}]',
    ).stdout)
    if not moved.get("ok"):
        die(f"act: {moved}")
    loc = box("bash", "-lc", "set -a; . /tmp/gbm.env; xdotool getmouselocation --shell").stdout
    vals = dict(line.split("=", 1) for line in loc.splitlines() if "=" in line)
    if vals.get("X") != "311" or vals.get("Y") != "155":
        die(f"cursor {vals} after in-box act")
    ok("in-box act moved container cursor to 311,155")

    # Long-lived in-box client; kill it; desktop must stay.
    proc = subprocess.Popen(
        ["docker", "exec", "-u", "box", CONTAINER, "bash", "-lc", "gbm-act doctor >/dev/null; sleep 120"],
    )
    time.sleep(0.4)
    if proc.poll() is not None:
        die("sleep helper exited early")
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)
    time.sleep(0.3)
    h = host_health()
    if h.get("ok") is not True:
        die(f"desktop died after killing in-box client: {h}")
    xvfb = box("bash", "-lc", "pidof Xvfb || pgrep -x Xvfb").stdout.strip()
    if not xvfb:
        die("Xvfb gone after killing in-box client")
    ok("killing in-box gbm-act left desktop + control up")

    print("ALL INSIDE-AGENT SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
