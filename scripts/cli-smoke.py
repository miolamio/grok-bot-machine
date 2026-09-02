#!/usr/bin/env python3
"""Host CLI smoke: ./scripts/gbm talks to the running container, not the Mac."""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GBM = os.path.join(ROOT, "scripts", "gbm")
WS = os.path.join(ROOT, "workspace")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def gbm(*args: str, timeout: float = 45.0) -> dict:
    r = subprocess.run(
        [GBM, *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if r.returncode != 0:
        die(f"gbm {' '.join(args)} exit {r.returncode}: {r.stdout}{r.stderr}")
    try:
        return json.loads(r.stdout)
    except json.JSONDecodeError:
        die(f"gbm {' '.join(args)} not JSON: {r.stdout[:500]}")
        raise


def main() -> int:
    os.environ.setdefault("GBM_CONTROL_TOKEN", "dev-local-token")
    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)

    h = gbm("health")
    if h.get("ok") is not True:
        die(f"health {h}")
    ok("health")

    ready = gbm("--timeout", "20", "ready")
    if ready.get("ok") is not True:
        die(f"ready {ready}")
    ok("ready")

    doc = gbm("doctor")
    if doc.get("blockers"):
        die(f"doctor blockers {doc.get('blockers')}")
    r = doc.get("readiness") or {}
    if not (r.get("can_shell") and r.get("can_xdotool") and r.get("can_screenshot")):
        die(f"doctor readiness {r}")
    ok("doctor")

    path = os.path.join(WS, "cli-smoke.txt")
    try:
        os.remove(path)
    except OSError:
        pass
    sh = gbm("shell", "echo cli-ok > /workspace/cli-smoke.txt")
    if sh.get("ok") is not True or (sh.get("results") or [{}])[0].get("result", {}).get("exit") != 0:
        die(f"shell {sh}")
    with open(path) as f:
        if f.read().strip() != "cli-ok":
            die("workspace/cli-smoke.txt mismatch")
    ok("shell wrote workspace/cli-smoke.txt")

    shot = os.path.join(WS, "cli-smoke.png")
    try:
        os.remove(shot)
    except OSError:
        pass
    ss = gbm("screenshot", "-o", shot)
    if not os.path.isfile(shot) or os.path.getsize(shot) < 100:
        die(f"screenshot missing: {ss}")
    with open(shot, "rb") as f:
        raw = f.read(24)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        die("screenshot not PNG")
    w = int.from_bytes(raw[16:20], "big")
    hgt = int.from_bytes(raw[20:24], "big")
    if (w, hgt) != (1280, 800):
        die(f"PNG {w}x{hgt}")
    blob = json.dumps(ss)
    if "iVBOR" in blob:
        die("screenshot JSON still contains base64")
    ok("screenshot 1280×800 saved, no base64 in JSON")

    mv = gbm("mouse", "50", "80")
    if mv.get("ok") is not True:
        die(f"mouse {mv}")
    loc = subprocess.run(
        ["docker", "exec", "-u", "box", CONTAINER, "bash", "-lc",
         "set -a; . /tmp/gbm.env; xdotool getmouselocation --shell"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    vals = dict(line.split("=", 1) for line in loc.splitlines() if "=" in line)
    if vals.get("X") != "50" or vals.get("Y") != "80":
        die(f"cursor after mouse {vals}")
    ok("mouse 50,80")

    ping = gbm("connect", "ping")
    if ping != {}:
        die(f"connect ping {ping}")
    ok("connect ping")

    caps = gbm("connect", "caps")
    if caps.get("computerUseSupported") is not True:
        die(f"connect caps {caps}")
    ok("connect caps")

    cshot = os.path.join(WS, "cli-connect.png")
    try:
        os.remove(cshot)
    except OSError:
        pass
    cs = gbm("connect", "screenshot", "-o", cshot, timeout=45.0)
    if not os.path.isfile(cshot):
        die(f"connect screenshot missing {cs}")
    with open(cshot, "rb") as f:
        raw = f.read(24)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        die("connect screenshot not PNG")
    ok("connect screenshot")

    gbm("connect", "click", "311", "155")
    loc = subprocess.run(
        ["docker", "exec", "-u", "box", CONTAINER, "bash", "-lc",
         "set -a; . /tmp/gbm.env; xdotool getmouselocation --shell"],
        capture_output=True, text=True, check=True, timeout=15,
    ).stdout
    vals = dict(line.split("=", 1) for line in loc.splitlines() if "=" in line)
    if vals.get("X") != "311" or vals.get("Y") != "155":
        die(f"cursor after connect click {vals}")
    ok("connect click 311,155")

    print("ALL CLI SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
