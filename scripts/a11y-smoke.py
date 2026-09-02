#!/usr/bin/env python3
"""GBM-24: GTK set_value / perform_action must use AT-SPI, not xdotool."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

BASE = os.environ.get("GBM_CONTROL_URL", "http://127.0.0.1:7070").rstrip("/")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def token() -> str:
    t = os.environ.get("GBM_CONTROL_TOKEN", "").strip()
    if t:
        return t
    r = subprocess.run(
        ["docker", "exec", CONTAINER, "cat", "/tmp/gbm-control.token"],
        capture_output=True, text=True, check=True, timeout=8,
    )
    return r.stdout.strip()


def act(steps: list, timeout: float = 30.0) -> dict:
    data = json.dumps({"steps": steps}).encode()
    req = urllib.request.Request(
        BASE + "/act",
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer " + token(),
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = json.loads(resp.read())
    if not body.get("ok"):
        die(f"act failed: {body}")
    return body


def main() -> int:
    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)
    subprocess.run(
        ["docker", "exec", CONTAINER, "sudo", "-n", "chmod", "1777", "/workspace"],
        check=False, timeout=8,
    )

    act([{"type": "shell", "cmd":
         "python3 /opt/grok-bot/fixtures/gtk_entry.py >/tmp/gtk_entry.log 2>&1 & echo $!"}])
    snap = None
    for _ in range(25):
        time.sleep(0.25)
        body = act([{"type": "a11y", "action": "snapshot"}])
        win = (body["results"][0]["result"] or {}).get("window") or {}
        title = win.get("title") or ""
        if "GBM24" in title:
            snap = body["results"][0]["result"]
            break
    if not snap:
        log = subprocess.run(
            ["docker", "exec", CONTAINER, "cat", "/tmp/gtk_entry.log"],
            capture_output=True, text=True, timeout=5,
        ).stdout
        die(f"GBM24 window never appeared. gtk_entry.log:\n{log}")
    ok(f"GBM24 window pid={snap['window'].get('pid')}")

    els = snap.get("elements") or []
    entry = next((e for e in els if e.get("canSetValue") and e.get("role") in {"entry", "text", "password text"}), None)
    if entry is None:
        entry = next((e for e in els if e.get("canSetValue")), None)
    if entry is None:
        die(f"no editable element: {els}")
    ok(f"editable {entry['ref']} role={entry['role']} name={entry['name']!r}")

    marker = "GBM24-ok"
    setr = act([{"type": "a11y", "action": "set_value", "ref": entry["ref"], "text": marker}])
    result = setr["results"][0]["result"]
    if result.get("used") != "atspi":
        die(f"set_value used {result.get('used')}, want atspi: {result}")
    if result.get("value") != marker:
        # re-read
        rd = act([{"type": "a11y", "action": "get_text", "ref": entry["ref"]}])
        val = rd["results"][0]["result"].get("value")
        if val != marker:
            die(f"readback {val!r} != {marker!r} (set result {result})")
    ok(f"set_value via AT-SPI, value={result.get('value')!r}")

    # Fresh snapshot must show the value without xdotool.
    snap2 = act([{"type": "a11y", "action": "snapshot"}])["results"][0]["result"]
    match = [e for e in snap2.get("elements") or [] if e.get("value") == marker]
    if not match:
        die(f"tree did not contain value {marker!r}: "
            f"{[(e.get('ref'), e.get('role'), e.get('value')) for e in snap2.get('elements') or [] if e.get('canSetValue')]}")
    ok("tree read-back contains GBM24-ok")

    btn = next((e for e in (snap2.get("elements") or [])
                if e.get("canPress") and (e.get("name") or "").lower() == "ok"), None)
    if btn is None:
        die(f"no OK button: {snap2.get('elements')}")
    click = act([{"type": "a11y", "action": "click", "ref": btn["ref"]}])["results"][0]["result"]
    if click.get("used") != "atspi":
        die(f"click used {click.get('used')}, want atspi: {click}")
    ok(f"click OK via AT-SPI doAction ({btn['ref']})")

    print("ALL A11Y SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
