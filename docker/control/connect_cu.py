#!/usr/bin/env python3
"""Grok-faithful computer-use worker.

Connect-RPC (@connectrpc/connect) over HTTP/1.1 on :1337.
Not websocket. Not HTTP/2. application/json only (application/connect+json → 415).
Auth: Authorization: Bearer local  (also accepts GBM_CONTROL_TOKEN).

Implements the computer-use slice of agent.v1.ControlService / ExecService.
Clicks are xdotool on $DISPLAY (1280×800), not CDP.
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import capture  # noqa: E402
import handoff  # noqa: E402

BIND = os.environ.get("GBM_CONNECT_BIND", "0.0.0.0")
PORT = int(os.environ.get("GBM_CONNECT_PORT", "1337"))
AUTH = (
    os.environ.get("GBM_CONNECT_TOKEN", "").strip()
    or os.environ.get("GBM_CONNECT_AUTH_TOKEN", "").strip()
    or "local"
)
ALSO = os.environ.get("GBM_CONTROL_TOKEN", "")
DISPLAY = os.environ.get("DISPLAY", ":1")

BTN_XDOTOOL = {0: "1", 1: "1", 2: "3", 3: "2", 4: "8", 5: "9"}
SCROLL_BTN = {0: "5", 1: "4", 2: "5", 3: "6", 4: "7"}  # 1 UP=4, 2 DOWN=5, 3 LEFT=6, 4 RIGHT=7


def _camel(d: Any) -> Any:
    if isinstance(d, dict):
        out = {}
        for k, v in d.items():
            ck = "".join(p[:1].upper() + p[1:] if i else p for i, p in enumerate(k.split("_"))) if "_" in k else k
            out[ck] = _camel(v)
            out[k] = _camel(v)
        return out
    if isinstance(d, list):
        return [_camel(x) for x in d]
    return d


def _pick(d: dict, *names: str, default: Any = None) -> Any:
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
        # camelCase / snake_case
        snake = "".join(("_" + c.lower() if c.isupper() else c) for c in n).lstrip("_")
        if snake in d and d[snake] is not None:
            return d[snake]
        camel = "".join(p[:1].upper() + p[1:] if i else p for i, p in enumerate(n.split("_")))
        if camel in d and d[camel] is not None:
            return d[camel]
    return default


def _coord(obj: Any) -> tuple[int, int] | None:
    if not isinstance(obj, dict):
        return None
    x = _pick(obj, "x")
    y = _pick(obj, "y")
    if x is None or y is None:
        return None
    return int(x), int(y)


def _xdo(*args: str) -> None:
    subprocess.run(["xdotool", *args], check=False, timeout=15, capture_output=True)


def _cursor() -> dict:
    try:
        r = subprocess.run(
            ["xdotool", "getmouselocation", "--shell"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
        return {"x": int(vals.get("X", "0")), "y": int(vals.get("Y", "0"))}
    except Exception:
        return {"x": 0, "y": 0}


def _screenshot_b64() -> str:
    g = capture.grab()
    return base64.b64encode(g["png"]).decode("ascii")


def _run_action(action: dict) -> None:
    if not isinstance(action, dict):
        raise ValueError("action must be an object")
    # oneof: the non-empty nested message
    kind = None
    payload: dict = {}
    for key in (
        "mouse_move", "mouseMove",
        "click",
        "mouse_down", "mouseDown",
        "mouse_up", "mouseUp",
        "drag",
        "scroll",
        "type",
        "key",
        "wait",
        "screenshot",
        "cursor_position", "cursorPosition",
    ):
        if key in action and action[key] is not None:
            kind = key
            payload = action[key] if isinstance(action[key], dict) else {}
            break
    if kind is None:
        raise ValueError(f"unknown ComputerUseAction: {list(action)}")

    if kind in ("mouse_move", "mouseMove"):
        xy = _coord(_pick(payload, "coordinate"))
        if xy:
            _xdo("mousemove", str(xy[0]), str(xy[1]))
        return
    if kind == "click":
        xy = _coord(_pick(payload, "coordinate"))
        btn = BTN_XDOTOOL.get(int(_pick(payload, "button") or 1), "1")
        count = max(1, int(_pick(payload, "count") or 1))
        if xy:
            _xdo("mousemove", str(xy[0]), str(xy[1]))
        for _ in range(count):
            _xdo("click", btn)
        return
    if kind in ("mouse_down", "mouseDown"):
        xy = _coord(_pick(payload, "coordinate"))
        btn = BTN_XDOTOOL.get(int(_pick(payload, "button") or 1), "1")
        if xy:
            _xdo("mousemove", str(xy[0]), str(xy[1]))
        _xdo("mousedown", btn)
        return
    if kind in ("mouse_up", "mouseUp"):
        xy = _coord(_pick(payload, "coordinate"))
        btn = BTN_XDOTOOL.get(int(_pick(payload, "button") or 1), "1")
        if xy:
            _xdo("mousemove", str(xy[0]), str(xy[1]))
        _xdo("mouseup", btn)
        return
    if kind == "drag":
        path = _pick(payload, "path") or []
        btn = BTN_XDOTOOL.get(int(_pick(payload, "button") or 1), "1")
        pts = [_coord(p) for p in path]
        pts = [p for p in pts if p]
        if not pts:
            return
        _xdo("mousemove", str(pts[0][0]), str(pts[0][1]), "mousedown", btn)
        for p in pts[1:]:
            _xdo("mousemove", str(p[0]), str(p[1]))
        _xdo("mouseup", btn)
        return
    if kind == "scroll":
        xy = _coord(_pick(payload, "coordinate"))
        if xy:
            _xdo("mousemove", str(xy[0]), str(xy[1]))
        direction = int(_pick(payload, "direction") or 2)
        amount = max(1, int(_pick(payload, "amount") or 1))
        b = SCROLL_BTN.get(direction, "5")
        for _ in range(amount):
            _xdo("click", b)
        return
    if kind == "type":
        text = str(_pick(payload, "text") or "")
        if text:
            _xdo("type", "--delay", "8", "--", text)
        return
    if kind == "key":
        key = str(_pick(payload, "key") or "")
        hold = int(_pick(payload, "hold_duration_ms", "holdDurationMs") or 0)
        if key:
            if hold > 0:
                _xdo("keydown", key)
                time.sleep(hold / 1000.0)
                _xdo("keyup", key)
            else:
                _xdo("key", key)
        return
    if kind == "wait":
        ms = int(_pick(payload, "duration_ms", "durationMs") or 0)
        if ms > 0:
            time.sleep(min(ms, 30_000) / 1000.0)
        return
    if kind == "screenshot":
        return
    if kind in ("cursor_position", "cursorPosition"):
        return


def run_computer_use(args: dict) -> dict:
    actions = _pick(args, "actions") or []
    if not isinstance(actions, list):
        raise ValueError("actions must be an array")
    t0 = time.perf_counter()
    want_shot = False
    for a in actions:
        if isinstance(a, dict) and ("screenshot" in a):
            want_shot = True
        _run_action(a)
    dur = int((time.perf_counter() - t0) * 1000)
    cur = _cursor()
    result: dict[str, Any] = {
        "actionCount": len(actions),
        "action_count": len(actions),
        "durationMs": dur,
        "duration_ms": dur,
        "cursorPosition": cur,
        "cursor_position": cur,
    }
    if want_shot or not actions:
        b64 = _screenshot_b64()
        result["screenshot"] = b64
    return result


def extract_cu_args(body: dict) -> dict | None:
    if not isinstance(body, dict):
        return None
    for key in ("computerUseArgs", "computer_use_args"):
        if isinstance(body.get(key), dict):
            return body[key]
    inner = body.get("execServerMessage") or body.get("exec_server_message")
    if isinstance(inner, dict):
        return extract_cu_args(inner)
    if isinstance(body.get("actions"), list):
        return body
    return None


class Handler(BaseHTTPRequestHandler):
    server_version = "gbm-connect-cu/0.1"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))

    def _send(self, code: int, obj: Any, content_type: str = "application/json") -> None:
        data = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Connect-Protocol-Version", "1")
        self.end_headers()
        self.wfile.write(data)

    def _auth(self) -> bool:
        hdr = self.headers.get("Authorization") or ""
        tok = ""
        if hdr.lower().startswith("bearer "):
            tok = hdr[7:].strip()
        allowed = {AUTH, "local"}
        if ALSO:
            allowed.add(ALSO)
        if tok not in allowed or not tok:
            self._send(401, {"code": "unauthenticated", "message": "Unauthorized"})
            return False
        return True

    def do_GET(self) -> None:  # noqa: N802
        self._send(404, {"code": "unimplemented", "message": "GET / = 404"})

    def do_POST(self) -> None:  # noqa: N802
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/connect+json":
            self._send(415, {"code": "invalid_argument", "message": "application/connect+json not supported"})
            return
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length") or "0")
        raw = self.rfile.read(n) if n > 0 else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except json.JSONDecodeError:
            body = {}
        if not isinstance(body, dict):
            body = {}

        if path in (
            "/agent.v1.ControlService/Ping",
            "/agent.v1.ControlService/ping",
        ):
            if not self._auth():
                return
            self._send(200, {})
            return
        if path in (
            "/agent.v1.ControlService/GetCapabilities",
            "/agent.v1.ControlService/getCapabilities",
        ):
            if not self._auth():
                return
            self._send(200, {
                "computerUseSupported": True,
                "installPluginArtifactSupported": False,
            })
            return
        if path in (
            "/agent.v1.ExecService/Exec",
            "/agent.v1.ControlService/Exec",
        ):
            if not self._auth():
                return
            try:
                held = handoff.read()
                if held:
                    self._send(409, {
                        "code": "failed_precondition",
                        "message": "handoff active: human has the desk",
                        "handoff": held,
                    })
                    return
                args = extract_cu_args(body)
                if args is None:
                    self._send(400, {"code": "invalid_argument", "message": "missing computer_use_args"})
                    return
                result = run_computer_use(args)
                self._send(200, {
                    "execClientMessage": {"computerUseResult": result},
                    "exec_client_message": {"computer_use_result": result},
                })
            except Exception as e:
                traceback.print_exc()
                self._send(500, {"code": "internal", "message": str(e)})
            return
        self._send(404, {"code": "unimplemented", "message": path})


def main() -> int:
    httpd = ThreadingHTTPServer((BIND, PORT), Handler)
    sys.stderr.write(f"gbm-connect-cu listening on {BIND}:{PORT} display={DISPLAY} auth=Bearer {AUTH}\n")
    httpd.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
