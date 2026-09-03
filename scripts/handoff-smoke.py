#!/usr/bin/env python3
"""Handoff freezes GUI on :7070 and :1337; shell still works; resume unfreezes.

Payload matches request_box_help: instruction required; reason auth|captcha|payment|other.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.request

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


def png_ok(path: str) -> bool:
    try:
        with open(path, "rb") as f:
            raw = f.read(24)
    except OSError:
        return False
    return raw[:8] == b"\x89PNG\r\n\x1a\n" and int.from_bytes(raw[16:20], "big") == 1280


def main() -> int:
    os.environ.setdefault("GBM_CONTROL_TOKEN", "dev-local-token")
    sys.path.insert(0, os.path.join(ROOT, "docker", "control"))
    import handoff as hmod  # noqa: E402

    if hmod.normalize_reason("auth") != "auth":
        die("auth reason")
    if hmod.normalize_reason("login") != "auth":
        die("login→auth")
    if hmod.normalize_reason("2fa") != "auth":
        die("2fa→auth")
    if hmod.normalize_reason(None) is not None:
        die("omit reason")
    if hmod.normalize_reason("captcha") != "captcha":
        die("captcha reason")
    if hmod.parse_kind("chat")[1] != "not_desk":
        die("chat kind")
    if hmod.parse_kind(None) != ("desk", None):
        die("default kind")
    if hmod.normalize_host("https://accounts.google.com/signin") != "accounts.google.com":
        die("domain host")
    ok("handoff.py unit")

    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)
    run("resume")  # clear leftover
    run("ready")

    st = run("handoff")
    if st.get("handoff"):
        die(f"expected empty handoff, got {st}")
    ok("handoff idle")

    missing = run("handoff", "--reason", "auth", expect=2)
    if missing.get("error") != "instruction_required":
        die(f"reason without instruction {missing}")
    ok("instruction required")

    wall = run("handoff", "-m", "Accept cookies")
    wh = wall.get("handoff") or {}
    if wall.get("ok") is not True or wh.get("instruction") != "Accept cookies":
        die(f"cookie wall {wall}")
    if "reason" in wh:
        die(f"cookie wall should omit reason {wh}")
    run("resume")
    ok("instruction without reason")

    chat = run("handoff", "--reason", "other", "--kind", "chat", "-m", "send mail?", expect=1)
    if chat.get("error") != "not_desk":
        die(f"chat kind should 400 not_desk {chat}")
    hostk = run("handoff", "--reason", "other", "--kind", "host", "-m", "Downloads", expect=1)
    if hostk.get("error") != "not_desk":
        die(f"host kind should 400 not_desk {hostk}")
    still = run("handoff")
    if still.get("handoff"):
        die(f"non-desk kind froze the desk {still}")
    ok("chat/host kind not_desk")

    started = run(
        "handoff", "-m", "Sign in to Google", "--reason", "login",
        "--domain", "drive.google.com",
        "--idp-domain", "https://accounts.google.com/signin",
    )
    h = started.get("handoff") or {}
    if started.get("ok") is not True or h.get("reason") != "auth":
        die(f"login→auth {started}")
    if h.get("instruction") != "Sign in to Google":
        die(f"instruction {h}")
    if h.get("kind") != "desk":
        die(f"kind {h}")
    if h.get("domain") != "drive.google.com":
        die(f"domain {h}")
    if h.get("idp_domain") != "accounts.google.com":
        die(f"idp {h}")
    if "6080" not in (h.get("novnc") or ""):
        die(f"missing novnc {h}")
    run("resume")
    ok("auth + domain/idp")

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
    after = ended.get("screenshot")
    if not after:
        die(f"resume missing after-screenshot {ended}")
    host_after = os.path.join(ROOT, "workspace", os.path.basename(str(after)))
    if not png_ok(host_after):
        die(f"handoff-after png bad {host_after}")
    ok("resume")

    mv = run("mouse", "40", "50")
    if mv.get("ok") is not True:
        die(f"mouse after resume {mv}")
    ok("mouse after resume")

    mounts = subprocess.run(
        ["docker", "inspect", "-f", "{{range .Mounts}}{{.Destination}}\n{{end}}", CONTAINER],
        capture_output=True, text=True, timeout=30,
    )
    if "/home/box/chrome-profile" not in (mounts.stdout or ""):
        die(f"chrome-profile not mounted {mounts.stdout}")
    run("shell", "mkdir -p /home/box/chrome-profile && echo persist-ok > /home/box/chrome-profile/.gbm-persist")
    host_prof = os.path.join(ROOT, "data", "chrome-profile", ".gbm-persist")
    if not os.path.isfile(host_prof):
        die(f"chrome profile not on host {host_prof}")
    ok("chrome profile volume")

    pub = json.loads(urllib.request.urlopen("http://127.0.0.1:7070/handoff/public", timeout=10).read())
    if pub.get("handoff"):
        die(f"public idle {pub}")
    run("handoff", "-m", "public-smoke")
    pub = json.loads(urllib.request.urlopen("http://127.0.0.1:7070/handoff/public", timeout=10).read())
    if (pub.get("handoff") or {}).get("instruction") != "public-smoke":
        die(f"public active {pub}")
    req = urllib.request.Request(
        "http://127.0.0.1:7070/handoff/resume",
        data=b"{}",
        method="POST",
        headers={"Origin": "http://127.0.0.1:6080", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        back = json.loads(resp.read())
    if back.get("handoff") is not None:
        die(f"origin resume {back}")
    ok("public GET + noVNC origin resume")

    def _resume_later() -> None:
        time.sleep(1.0)
        run("resume")

    threading.Thread(target=_resume_later, daemon=True).start()
    waited = run("handoff", "-m", "wait-smoke", "--wait", "--wait-timeout", "20", timeout=30)
    if waited.get("ok") is not True or waited.get("handoff"):
        die(f"wait {waited}")
    ok("handoff --wait")

    print("ALL HANDOFF SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
