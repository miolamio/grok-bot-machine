#!/usr/bin/env python3
"""GBM-26: Connect-RPC computer-use on :1337 matches original Ping/GetCapabilities/Exec."""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("GBM_CONNECT_URL", "http://127.0.0.1:1337").rstrip("/")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")


def die(msg: str) -> None:
    print(f"FAIL: {msg}", file=sys.stderr)
    raise SystemExit(1)


def ok(msg: str) -> None:
    print(f"OK: {msg}")


def post(path: str, body: dict | None = None, token: str | None = "local",
         content_type: str = "application/json", timeout: float = 30.0):
    data = json.dumps(body or {}).encode()
    headers = {
        "Content-Type": content_type,
        "Connect-Protocol-Version": "1",
    }
    if token is not None:
        headers["Authorization"] = "Bearer " + token
    req = urllib.request.Request(BASE + path, data=data, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            parsed = raw
        return e.code, parsed


def main() -> int:
    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)

    code, body = post("/agent.v1.ControlService/Ping", token=None)
    if code != 401:
        die(f"no auth Ping expected 401, got {code} {body}")
    ok("Ping without Bearer → 401")

    code, body = post("/agent.v1.ControlService/Ping", token="local")
    if code != 200 or body != {}:
        die(f"Ping: {code} {body}")
    ok("Ping Bearer local → 200 {}")

    code, body = post("/agent.v1.ControlService/GetCapabilities")
    if code != 200 or body.get("computerUseSupported") is not True:
        die(f"GetCapabilities: {code} {body}")
    ok("GetCapabilities computerUseSupported=true")

    code, body = post(
        "/agent.v1.ControlService/Ping",
        content_type="application/connect+json",
    )
    if code != 415:
        die(f"connect+json expected 415, got {code} {body}")
    ok("application/connect+json → 415")

    try:
        urllib.request.urlopen(BASE + "/", timeout=5)
        die("GET / should 404")
    except urllib.error.HTTPError as e:
        if e.code != 404:
            die(f"GET / expected 404, got {e.code}")
    ok("GET / → 404")

    code, body = post("/agent.v1.ExecService/Exec", {
        "computerUseArgs": {"actions": [{"screenshot": {}}]},
    })
    if code != 200:
        die(f"Exec screenshot: {code} {body}")
    result = (
        (body.get("execClientMessage") or {}).get("computerUseResult")
        or (body.get("exec_client_message") or {}).get("computer_use_result")
        or {}
    )
    shot = result.get("screenshot") or ""
    raw = base64.b64decode(shot)
    if raw[:8] != b"\x89PNG\r\n\x1a\n":
        die("screenshot is not PNG")
    w = int.from_bytes(raw[16:20], "big")
    h = int.from_bytes(raw[20:24], "big")
    if (w, h) != (1280, 800):
        die(f"PNG size {w}x{h} != 1280x800")
    ok("Exec screenshot → 1280×800 PNG")

    code, body = post("/agent.v1.ExecService/Exec", {
        "computer_use_args": {
            "actions": [{"click": {"coordinate": {"x": 640, "y": 400}, "button": 1, "count": 1}}],
        },
    })
    if code != 200:
        die(f"Exec click: {code} {body}")
    loc = subprocess.run(
        ["docker", "exec", "-u", "box", CONTAINER, "bash", "-lc",
         "set -a; . /tmp/gbm.env; xdotool getmouselocation --shell"],
        capture_output=True, text=True, check=True, timeout=8,
    ).stdout
    vals = dict(line.split("=", 1) for line in loc.splitlines() if "=" in line)
    if vals.get("X") != "640" or vals.get("Y") != "400":
        die(f"cursor after click {vals} != 640,400")
    ok("Exec click (640,400) moved container cursor")

    print("ALL CONNECT-CU SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
