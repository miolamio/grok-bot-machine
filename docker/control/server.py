#!/usr/bin/env python3
"""GBM control plane: HTTP observe/act for agents outside (or inside) the box."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import a11y  # noqa: E402
import capture  # noqa: E402
import cdp  # noqa: E402

TOKEN = os.environ.get("GBM_CONTROL_TOKEN", "")
BIND = os.environ.get("GBM_CONTROL_BIND", "0.0.0.0")
PORT = int(os.environ.get("GBM_CONTROL_PORT", "7070"))
DISPLAY = os.environ.get("DISPLAY", ":1")
LOCK = threading.Lock()

ACT_TYPES = {"shell", "cdp", "a11y", "xdotool", "screenshot"}


def _json(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()


def doctor() -> dict:
    session_bus = bool(os.environ.get("DBUS_SESSION_BUS_ADDRESS"))
    at_ok = a11y.bus_ok()
    at_count = a11y.child_count() if a11y.available() else -1
    xdotool_ok = shutil.which("xdotool") is not None
    xtest_ok = False
    try:
        r = subprocess.run(
            ["xdpyinfo"], capture_output=True, text=True, timeout=3,
        )
        xtest_ok = "XTEST" in r.stdout or "XTest" in r.stdout
    except Exception:
        xtest_ok = False
    chrome_ok = cdp.debug_ok()
    cap_ok = False
    cap_backend = capture.last_backend()
    try:
        g = capture.grab()
        cap_ok = g.get("width", 0) > 0
        cap_backend = g.get("backend") or cap_backend
    except Exception:
        cap_ok = False
    no_bridge = os.environ.get("NO_AT_BRIDGE") == "1"
    blockers = []
    if no_bridge:
        blockers.append("NO_AT_BRIDGE=1; see GBM-13")
    if not session_bus:
        blockers.append("DBUS_SESSION_BUS_ADDRESS unset")
    if not at_ok:
        blockers.append("AT_SPI_BUS missing on root window; start at-spi-bus-launcher")
    if not xdotool_ok:
        blockers.append("xdotool not on PATH")
    if not os.environ.get("GBM_CONTROL_TOKEN"):
        blockers.append("GBM_CONTROL_TOKEN unset")
    next_step = None
    if blockers:
        next_step = blockers[0]
    elif not chrome_ok:
        next_step = "launch box-chrome to enable browser use"
    readiness = {
        "can_shell": True,
        "can_cdp": chrome_ok,
        "can_atspi": at_ok and at_count >= 0 and a11y.available(),
        "can_xdotool": xdotool_ok,
        "can_screenshot": cap_ok,
    }
    return {
        "display": DISPLAY,
        "size": {"width": 1280, "height": 800},
        "session_bus": {"ok": session_bus},
        "at_spi_bus": {"ok": at_ok, "child_count": at_count, "error": a11y.error()},
        "xtest": {"ok": xtest_ok},
        "xdotool": {"ok": xdotool_ok},
        "chromium": {
            "debug_port": cdp.CDP_PORT,
            "debug_ok": chrome_ok,
        },
        "capture": {"backend": cap_backend, "ok": cap_ok, "ms": capture.last_ms()},
        "control": {"token_set": bool(TOKEN), "port": PORT},
        "readiness": readiness,
        "blockers": blockers,
        "next_step": next_step,
        "suggest": _suggest(),
    }


def _suggest() -> str:
    w = a11y.focused_window() or {}
    title = (w.get("title") or "").lower()
    if any(s in title for s in ("chrom", "google-chrome")):
        return "cdp"
    if w:
        return "a11y"
    return "shell"


def observe(body: dict) -> dict:
    include_shot = bool(body.get("include_screenshot"))
    include_tree = body.get("include_tree", True)
    snap = a11y.snapshot(body.get("window")) if include_tree else {
        "windows": a11y.list_windows(),
        "window": a11y.focused_window(),
        "elements": [],
    }
    tree_empty = include_tree and not snap.get("elements")
    out = {
        "display": DISPLAY,
        "apps": a11y.apps(),
        "windows": snap.get("windows"),
        "focused": snap.get("window"),
        "elements": snap.get("elements") if include_tree else [],
        "suggest": _suggest(),
        "cursor": None,
        "screenshot": None,
    }
    if include_shot or tree_empty:
        g = capture.grab()
        import base64
        out["screenshot"] = {
            "pngBase64": base64.b64encode(g["png"]).decode("ascii"),
            "width": g["width"],
            "height": g["height"],
            "backend": g["backend"],
            "ms": g["ms"],
        }
        out["cursor"] = g["cursor"]
        out["activeWindow"] = g.get("activeWindow")
        if not include_shot and tree_empty:
            out["screenshotReason"] = "tree_empty"
    else:
        out["cursor"] = capture._cursor()
    return out


def _run_shell(cmd: str, cwd: str | None = None, timeout: float = 30.0) -> dict:
    work = cwd or "/workspace"
    try:
        os.makedirs(work, exist_ok=True)
    except OSError:
        work = os.environ.get("HOME") or "/tmp"
    r = subprocess.run(
        ["/bin/sh", "-c", cmd],
        cwd=work,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=os.environ.copy(),
    )
    def clip(s: str) -> str:
        return s if len(s) <= 65536 else s[:65536] + "\n…truncated"
    return {
        "exit": r.returncode,
        "stdout": clip(r.stdout),
        "stderr": clip(r.stderr),
    }


def _xdotool(step: dict) -> dict:
    action = step.get("action") or "click"
    if action == "click":
        x, y = int(step["x"]), int(step["y"])
        btn = str(step.get("button", "1"))
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y), "click", btn],
            check=False, timeout=5, capture_output=True,
        )
        return {"x": x, "y": y, "button": btn}
    if action == "mousemove":
        x, y = int(step["x"]), int(step["y"])
        subprocess.run(
            ["xdotool", "mousemove", str(x), str(y)],
            check=False, timeout=5, capture_output=True,
        )
        return {"x": x, "y": y}
    if action == "type":
        subprocess.run(
            ["xdotool", "type", "--delay", "8", "--", step["text"]],
            check=False, timeout=30, capture_output=True,
        )
        return {"typed": len(step.get("text") or "")}
    if action == "key":
        keys = step.get("keys") or [step.get("key")]
        keys = [k for k in keys if k]
        subprocess.run(["xdotool", "key", *keys], check=False, timeout=5, capture_output=True)
        return {"keys": keys}
    raise ValueError(f"unknown xdotool action {action}")


def _cdp(step: dict) -> dict:
    action = step.get("action") or "snapshot"
    if action == "navigate":
        return cdp.navigate(step["url"])
    if action == "snapshot":
        return cdp.snapshot(interactive=step.get("interactive", True))
    if action == "click":
        return cdp.click(step["ref"])
    if action in ("fill", "type", "set_value"):
        return cdp.fill(step["ref"], step.get("text") or step.get("value") or "")
    if action == "evaluate":
        return {"value": cdp.evaluate(step["js"])}
    if action == "tabs":
        return {"tabs": cdp.tabs()}
    raise ValueError(f"unknown cdp action {action}")


def _a11y(step: dict) -> dict:
    action = step.get("action") or "snapshot"
    if action == "snapshot":
        return a11y.snapshot(step.get("window"))
    if action == "click":
        return a11y.click(step["ref"])
    if action in ("set_value", "set_text", "fill"):
        return a11y.set_value(step["ref"], step.get("text") or step.get("value") or "")
    if action in ("get_text", "read"):
        return a11y.get_text(step["ref"])
    if action == "perform_action":
        return a11y.perform_action(step["ref"], step.get("name"))
    if action == "windows":
        return {"windows": a11y.list_windows()}
    raise ValueError(f"unknown a11y action {action}")


def act(body: dict) -> dict:
    steps = body.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValueError("act requires steps[]")
    if len(steps) > 20:
        raise ValueError("max 20 steps per batch")
    unknown = []
    for i, s in enumerate(steps):
        t = (s or {}).get("type")
        if t not in ACT_TYPES:
            unknown.append({"index": i, "type": t})
    if unknown:
        return {
            "ok": False,
            "error": "unknown step type; batch not applied",
            "unknown": unknown,
            "results": [],
        }
    results = []
    for i, s in enumerate(steps):
        t = s["type"]
        try:
            if t == "shell":
                r = _run_shell(s.get("cmd") or s.get("command") or "", s.get("cwd"), float(s.get("timeout", 30)))
            elif t == "cdp":
                r = _cdp(s)
            elif t == "a11y":
                r = _a11y(s)
            elif t == "xdotool":
                r = _xdotool(s)
            elif t == "screenshot":
                g = capture.grab()
                import base64
                r = {
                    "pngBase64": base64.b64encode(g["png"]).decode("ascii"),
                    "width": g["width"],
                    "height": g["height"],
                    "backend": g["backend"],
                    "ms": g["ms"],
                    "cursor": g["cursor"],
                    "activeWindow": g.get("activeWindow"),
                }
            else:
                raise ValueError(t)
            results.append({"index": i, "type": t, "ok": True, "result": r})
        except Exception as e:
            results.append({"index": i, "type": t, "ok": False, "error": str(e)})
            return {"ok": False, "error": f"step {i} ({t}) failed", "results": results}
    return {"ok": True, "results": results}


class Handler(BaseHTTPRequestHandler):
    server_version = "gbm-control/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _auth(self) -> bool:
        hdr = self.headers.get("Authorization") or ""
        tok = self.headers.get("X-GBM-Token") or ""
        if hdr.lower().startswith("bearer "):
            tok = hdr[7:].strip()
        if not TOKEN or tok != TOKEN:
            self._send(401, {"error": "unauthorized"})
            return False
        return True

    def _read_json(self) -> dict:
        n = int(self.headers.get("Content-Length") or "0")
        if n <= 0:
            return {}
        raw = self.rfile.read(n)
        if not raw:
            return {}
        return json.loads(raw.decode())

    def _send(self, code: int, obj: Any) -> None:
        data = _json(obj)
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True, "display": DISPLAY})
            return
        if not self._auth():
            return
        if path in ("/doctor", "/v1/doctor"):
            with LOCK:
                self._send(200, doctor())
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/health":
            self._send(200, {"ok": True})
            return
        if not self._auth():
            return
        try:
            body = self._read_json()
        except json.JSONDecodeError as e:
            self._send(400, {"error": f"bad json: {e}"})
            return
        try:
            with LOCK:
                if path in ("/observe", "/v1/observe"):
                    self._send(200, observe(body if isinstance(body, dict) else {}))
                    return
                if path in ("/act", "/v1/act", "/v1/desktop"):
                    self._send(200, act(body if isinstance(body, dict) else {}))
                    return
                if path in ("/doctor", "/v1/doctor"):
                    self._send(200, doctor())
                    return
        except Exception as e:
            traceback.print_exc()
            self._send(500, {"error": str(e)})
            return
        self._send(404, {"error": "not found"})


def main() -> int:
    subprocess.run(["sudo", "-n", "chmod", "1777", "/workspace"], check=False, capture_output=True)
    if os.environ.get("GBM_CONTROL_TOKEN", "") == "" and "--doctor" not in sys.argv:
        sys.stderr.write("GBM_CONTROL_TOKEN is empty; refusing to start\n")
        return 2
    if "--doctor" in sys.argv:
        print(json.dumps(doctor(), indent=2))
        return 0
    httpd = HTTPServer((BIND, PORT), Handler)
    sys.stderr.write(f"gbm-control listening on {BIND}:{PORT} display={DISPLAY}\n")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
