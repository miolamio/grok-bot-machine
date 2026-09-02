#!/usr/bin/env python3
"""Host MCP stdio proxy → grok-bot control plane.

Drives the Docker desktop, not the Mac. JSON-RPC over stdio
(newline-delimited, with Content-Length accepted on input).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from typing import Any

NAME = "gbm"
VERSION = "0.1"
PROTOCOL = "2024-11-05"
BASE = os.environ.get("GBM_CONTROL_URL", "http://127.0.0.1:7070").rstrip("/")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")

_FRAMING = "ndjson"  # or "lsp" after first Content-Length message


def log(msg: str) -> None:
    sys.stderr.write(f"gbm-mcp: {msg}\n")
    sys.stderr.flush()


def token() -> str:
    t = os.environ.get("GBM_CONTROL_TOKEN", "").strip()
    if t:
        return t
    try:
        r = subprocess.run(
            ["docker", "exec", CONTAINER, "cat", "/tmp/gbm-control.token"],
            capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except Exception as e:
        log(f"token docker exec failed: {e}")
    return ""


def http(method: str, path: str, body: dict | None = None, timeout: float = 60.0) -> Any:
    url = BASE + path
    data = None if body is None else json.dumps(body).encode()
    headers = {"Accept": "application/json"}
    tok = token()
    if tok:
        headers["Authorization"] = "Bearer " + tok
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return json.loads(raw.decode() or "null")
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", "replace")
        raise RuntimeError(f"HTTP {e.code} {path}: {err_body[:800]}") from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"control plane unreachable at {url}: {e}") from e


TOOLS = [
    {
        "name": "doctor",
        "description": (
            "Readiness of the grok-bot Docker desktop (AT-SPI, CDP, xdotool, "
            "capture). This is NOT the Mac display."
        ),
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "observe",
        "description": (
            "Observe the container desktop: windows, AT-SPI tree of the focused "
            "app, optional screenshot. Default is tree without pixels."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_screenshot": {"type": "boolean"},
                "include_tree": {"type": "boolean"},
                "window": {"type": "string", "description": "Window ref @wN"},
            },
        },
    },
    {
        "name": "act",
        "description": (
            "Batched actions on the container desktop. Each step has type "
            "shell | cdp | a11y | xdotool | screenshot. Unknown types fail "
            "the whole batch without applying earlier steps."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "steps": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "1–20 steps",
                },
            },
            "required": ["steps"],
        },
    },
    {
        "name": "shell",
        "description": (
            "Run a command as user box in /workspace inside the container "
            "(not the Mac host shell)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "cmd": {"type": "string"},
                "cwd": {"type": "string"},
            },
            "required": ["cmd"],
        },
    },
]


def _as_text(obj: Any) -> dict:
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False, indent=2)
    return {"content": [{"type": "text", "text": text}], "isError": False}


def _as_error(msg: str) -> dict:
    return {"content": [{"type": "text", "text": msg}], "isError": True}


def call_tool(name: str, args: dict) -> dict:
    args = args or {}
    try:
        if name == "doctor":
            return _as_text(http("GET", "/doctor"))
        if name == "observe":
            body = {}
            if "include_screenshot" in args:
                body["include_screenshot"] = bool(args["include_screenshot"])
            if "include_tree" in args:
                body["include_tree"] = bool(args["include_tree"])
            if args.get("window"):
                body["window"] = args["window"]
            return _as_text(http("POST", "/observe", body))
        if name == "act":
            steps = args.get("steps")
            if not isinstance(steps, list):
                return _as_error("act requires steps[]")
            return _as_text(http("POST", "/act", {"steps": steps}))
        if name == "shell":
            cmd = args.get("cmd") or args.get("command")
            if not cmd:
                return _as_error("shell requires cmd")
            step: dict[str, Any] = {"type": "shell", "cmd": cmd}
            if args.get("cwd"):
                step["cwd"] = args["cwd"]
            return _as_text(http("POST", "/act", {"steps": [step]}))
        return _as_error(f"unknown tool {name}")
    except Exception as e:
        return _as_error(str(e))


def handle(msg: dict) -> dict | None:
    method = msg.get("method")
    mid = msg.get("id")
    params = msg.get("params") or {}
    if method is None:
        return None
    # Notifications have no id.
    if mid is None:
        return None
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "result": {
                "protocolVersion": PROTOCOL,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": NAME, "version": VERSION},
                "instructions": (
                    "Control the grok-bot Docker XFCE/Xvfb desktop. "
                    "Never target the host Mac cursor or display."
                ),
            },
        }
    if method == "ping":
        return {"jsonrpc": "2.0", "id": mid, "result": {}}
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS}}
    if method == "tools/call":
        name = params.get("name") or ""
        args = params.get("arguments") or {}
        return {"jsonrpc": "2.0", "id": mid, "result": call_tool(name, args)}
    if method in ("resources/list", "prompts/list"):
        key = "resources" if method.startswith("resources") else "prompts"
        return {"jsonrpc": "2.0", "id": mid, "result": {key: []}}
    return {
        "jsonrpc": "2.0",
        "id": mid,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def write_msg(obj: dict) -> None:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode()
    if _FRAMING == "lsp":
        sys.stdout.buffer.write(f"Content-Length: {len(raw)}\r\n\r\n".encode() + raw)
    else:
        sys.stdout.buffer.write(raw + b"\n")
    sys.stdout.buffer.flush()


def read_msg() -> dict | None:
    global _FRAMING
    first = sys.stdin.buffer.readline()
    if not first:
        return None
    stripped = first.lstrip()
    if stripped.startswith(b"{") or stripped.startswith(b"["):
        _FRAMING = "ndjson"
        return json.loads(first)
    # LSP / Content-Length
    _FRAMING = "lsp"
    headers = first
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            return None
        headers += line
        if line in (b"\r\n", b"\n"):
            break
    length = 0
    for hl in headers.split(b"\n"):
        if hl.lower().startswith(b"content-length:"):
            length = int(hl.split(b":", 1)[1].strip())
    body = b""
    while len(body) < length:
        chunk = sys.stdin.buffer.read(length - len(body))
        if not chunk:
            break
        body += chunk
    return json.loads(body.decode())


def main() -> int:
    log(f"proxy {BASE} container={CONTAINER}")
    while True:
        try:
            msg = read_msg()
        except json.JSONDecodeError as e:
            log(f"bad json: {e}")
            continue
        if msg is None:
            return 0
        try:
            reply = handle(msg)
        except Exception as e:
            log(f"handle error: {e}")
            if msg.get("id") is not None:
                write_msg({
                    "jsonrpc": "2.0",
                    "id": msg.get("id"),
                    "error": {"code": -32603, "message": str(e)},
                })
            continue
        if reply is not None:
            write_msg(reply)


if __name__ == "__main__":
    raise SystemExit(main())
