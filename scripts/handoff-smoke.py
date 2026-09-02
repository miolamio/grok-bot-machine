#!/usr/bin/env python3
"""Handoff freezes GUI on :7070 and :1337; shell still works; resume unfreezes."""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GBM = os.path.join(ROOT, "scripts", "gbm")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def run(*args: str, expect: int = 0, timeout: float = 45.0) -> dict:
    r = subprocess.run([GBM, *args], cwd=ROOT, capture_output=True, text=True, timeout=timeout)
    raw = (r.stdout or "").strip() or (r.stderr or "").strip()
    try:
        body = json.loads(raw)
    except json.JSONDecodeError:
        body = {"_raw": raw}
    if r.returncode != expect:
        die(f"gbm {' '.join(args)} exit {r.returncode} want {expect}: {raw[:800]}")
    if not isinstance(body, dict):
        die(f"not an object: {body}")
    return body


def main() -> int:
    os.environ.setdefault("GBM_CONTROL_TOKEN", "dev-local-token")
    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)
    run("resume")  # clear leftover
    run("ready")

    st = run("handoff")
    if st.get("handoff"):
        die(f"expected empty handoff, got {st}")
    ok("handoff idle")

    started = run("handoff", "--reason", "captcha", "-m", "smoke")
    h = started.get("handoff") or {}
    if started.get("ok") is not True or h.get("reason") != "captcha":
        die(f"start {started}")
    if "6080" not in (h.get("novnc") or ""):
        die(f"missing novnc {h}")
    shot = h.get("screenshot")
    if shot:
        host = os.path.join(ROOT, "workspace", os.path.basename(shot))
        if not os.path.isfile(host):
            die(f"handoff png missing {host}")
    ok("handoff started")

    doc = run("doctor")
    if not (doc.get("handoff") or {}).get("reason") == "captcha":
        die(f"doctor handoff {doc.get('handoff')}")
    if not (doc.get("readiness") or {}).get("gui_frozen"):
        die(f"doctor not frozen {doc.get('readiness')}")
    ok("doctor gui_frozen")

    click = run("click", "10", "10", expect=1)
    if click.get("error") != "handoff" and click.get("status") != 409:
        die(f"click should 409 {click}")
    ok("click 409")

    ss = run("screenshot", "-o", os.path.join(ROOT, "workspace", "should-not.png"), expect=1)
    if ss.get("error") != "handoff" and ss.get("status") != 409:
        die(f"screenshot should 409 {ss}")
    ok("screenshot 409")

    cc = run("connect", "click", "11", "12", expect=1)
    if cc.get("status") != 409 and cc.get("code") != "failed_precondition" and "handoff" not in json.dumps(cc):
        die(f"connect click should 409 {cc}")
    ok("connect click 409")

    sh = run("shell", "echo handoff-ok > /workspace/handoff-smoke.txt")
    if sh.get("ok") is not True:
        die(f"shell during handoff {sh}")
    ok("shell still works")

    obs = run("observe")
    if obs.get("ok") is False:
        die(f"tree observe blocked {obs}")
    ok("observe tree allowed")

    obs_pix = run("observe", "--screenshot", expect=1)
    if obs_pix.get("error") != "handoff" and obs_pix.get("status") != 409:
        die(f"observe --screenshot should 409 {obs_pix}")
    ok("observe --screenshot 409")

    ended = run("resume")
    if ended.get("handoff") is not None:
        die(f"resume {ended}")
    ok("resume")

    mv = run("mouse", "40", "50")
    if mv.get("ok") is not True:
        die(f"mouse after resume {mv}")
    ok("mouse after resume")

    print("ALL HANDOFF SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
