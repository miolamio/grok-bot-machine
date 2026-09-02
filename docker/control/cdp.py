"""Minimal Chrome DevTools Protocol client (no extra deps)."""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import time
import urllib.request
from typing import Any
from urllib.parse import urlparse

CDP_PORT = int(os.environ.get("SAND_CHROME_REMOTE_DEBUG_PORT", "9222"))
CDP_HOST = os.environ.get("GBM_CDP_HOST", "127.0.0.1")

REFS: dict[str, dict] = {}
_REF_N = 0

def _ax_val(obj: Any) -> str:
    if obj is None:
        return ""
    if isinstance(obj, dict):
        if "value" in obj:
            return _ax_val(obj.get("value"))
        return str(obj.get("name") or "")
    return str(obj)


INTERACTIVE = {
    "button", "link", "textbox", "searchbox", "combobox", "checkbox",
    "radio", "slider", "tab", "menuitem", "option", "switch", "listbox",
}


class WsError(RuntimeError):
    pass


class WebSocket:
    def __init__(self, url: str, timeout: float = 10.0):
        u = urlparse(url)
        host, port = u.hostname, u.port or 80
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (f"?{u.query}" if u.query else "")
        origin = f"http://{host}:{port}"
        req = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: {host}:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"Origin: {origin}\r\n"
            f"\r\n"
        )
        self.sock.sendall(req.encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise WsError("CDP websocket handshake closed")
            buf += chunk
        header, _, rest = buf.partition(b"\r\n\r\n")
        status = header.split(b"\r\n", 1)[0]
        if b"101" not in status:
            raise WsError(f"CDP handshake failed: {status!r}")
        self._buf = rest

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass

    def send_text(self, text: str) -> None:
        payload = text.encode()
        n = len(payload)
        mask = os.urandom(4)
        hdr = bytearray()
        hdr.append(0x81)
        if n < 126:
            hdr.append(0x80 | n)
        elif n < 65536:
            hdr.append(0x80 | 126)
            hdr.extend(struct.pack(">H", n))
        else:
            hdr.append(0x80 | 127)
            hdr.extend(struct.pack(">Q", n))
        hdr.extend(mask)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.sock.sendall(bytes(hdr) + masked)

    def _read(self, n: int) -> bytes:
        while len(self._buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self._buf)))
            if not chunk:
                raise WsError("CDP websocket closed")
            self._buf += chunk
        out, self._buf = self._buf[:n], self._buf[n:]
        return out

    def recv_text(self) -> str:
        data = bytearray()
        while True:
            b0, b1 = self._read(2)
            fin = b0 & 0x80
            opcode = b0 & 0x0F
            masked = b1 & 0x80
            ln = b1 & 0x7F
            if ln == 126:
                ln = struct.unpack(">H", self._read(2))[0]
            elif ln == 127:
                ln = struct.unpack(">Q", self._read(8))[0]
            mask = self._read(4) if masked else b""
            payload = self._read(ln)
            if masked:
                payload = bytes(p ^ mask[i % 4] for i, p in enumerate(payload))
            if opcode in (0x8,):
                raise WsError("CDP websocket close")
            if opcode == 0x9:
                # ping -> pong
                continue
            if opcode in (0x1, 0x2, 0x0):
                data.extend(payload)
                if fin:
                    return data.decode("utf-8", "replace")


class Cdp:
    def __init__(self, ws_url: str):
        self.ws = WebSocket(ws_url)
        self._id = 0

    def close(self) -> None:
        self.ws.close()

    def call(self, method: str, params: dict | None = None, timeout: float = 10.0) -> Any:
        self._id += 1
        msg_id = self._id
        self.ws.send_text(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.ws.recv_text()
            msg = json.loads(raw)
            if msg.get("id") != msg_id:
                continue
            if "error" in msg:
                raise RuntimeError(f"{method}: {msg['error']}")
            return msg.get("result")
        raise TimeoutError(method)


def debug_ok() -> bool:
    try:
        version()
        return True
    except Exception:
        return False


def _http_json(path: str) -> Any:
    url = f"http://{CDP_HOST}:{CDP_PORT}{path}"
    req = urllib.request.Request(url, headers={"Host": f"{CDP_HOST}:{CDP_PORT}"})
    with urllib.request.urlopen(req, timeout=3) as resp:
        return json.loads(resp.read().decode())


def version() -> dict:
    return _http_json("/json/version")


def tabs() -> list[dict]:
    try:
        raw = _http_json("/json/list")
    except Exception:
        raw = _http_json("/json")
    out = []
    for t in raw:
        if t.get("type") in (None, "page", "webview"):
            out.append({
                "id": t.get("id"),
                "title": t.get("title"),
                "url": t.get("url"),
                "webSocketDebuggerUrl": t.get("webSocketDebuggerUrl"),
            })
    return out


def _page() -> Cdp:
    pages = [t for t in tabs() if t.get("webSocketDebuggerUrl")]
    if not pages:
        raise RuntimeError("no Chromium page targets; launch box-chrome")
    page = pages[0]
    for t in pages:
        u = t.get("url") or ""
        if u.startswith(("http://", "https://", "file://", "data:")):
            page = t
            break
    c = Cdp(page["webSocketDebuggerUrl"])
    try:
        c.call("Page.enable")
        c.call("Runtime.enable")
        c.call("DOM.enable")
        c.call("Accessibility.enable")
    except Exception:
        c.close()
        raise
    return c


def navigate(url: str) -> dict:
    c = _page()
    try:
        c.call("Page.navigate", {"url": url})
        time.sleep(0.4)
        frame = c.call("Page.getFrameTree")
        return {"url": url, "frame": bool(frame)}
    finally:
        c.close()


def snapshot(interactive: bool = True) -> dict:
    global REFS, _REF_N
    c = _page()
    try:
        tree = c.call("Accessibility.getFullAXTree", timeout=15.0) or {}
        if isinstance(tree, list):
            nodes = tree
        else:
            nodes = tree.get("nodes") or []
        REFS = {}
        _REF_N = 0
        public = []
        lines = []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            role = _ax_val(node.get("role")).lower()
            name = _ax_val(node.get("name"))
            ignored = node.get("ignored")
            if ignored:
                continue
            if interactive and role not in INTERACTIVE and role not in {"heading", "image"}:
                continue
            _REF_N += 1
            ref = f"ref_{_REF_N}"
            backend = node.get("backendDOMNodeId")
            value = ""
            props = node.get("properties") or []
            for p in props:
                if not isinstance(p, dict):
                    continue
                if _ax_val(p.get("name")).lower() == "value":
                    value = _ax_val(p.get("value"))
            rec = {
                "ref": ref,
                "role": role,
                "name": str(name)[:160],
                "value": str(value)[:160] if value else "",
                "backendDOMNodeId": backend,
            }
            REFS[ref] = rec
            public.append({k: v for k, v in rec.items() if k != "backendDOMNodeId"})
            extra = f' value="{rec["value"]}"' if rec["value"] else ""
            lines.append(f'[{ref}] {role} "{rec["name"]}"{extra}')
            if _REF_N >= 200:
                break
        return {"text": "\n".join(lines), "elements": public, "count": len(public)}
    finally:
        c.close()


def _call_on_ref(ref: str, js: str, arg: Any = None) -> Any:
    rec = REFS.get(ref)
    if not rec:
        raise ValueError(f"unknown CDP ref {ref}; snapshot first")
    backend = rec.get("backendDOMNodeId")
    if not backend:
        raise ValueError(f"{ref} has no backendDOMNodeId")
    c = _page()
    try:
        resolved = c.call("DOM.resolveNode", {"backendNodeId": int(backend)})
        obj = (resolved or {}).get("object") or {}
        object_id = obj.get("objectId")
        if not object_id:
            raise RuntimeError(f"could not resolve {ref}")
        params: dict[str, Any] = {
            "objectId": object_id,
            "functionDeclaration": js,
            "returnByValue": True,
        }
        if arg is not None:
            params["arguments"] = [{"value": arg}]
        result = c.call("Runtime.callFunctionOn", params)
        return (result or {}).get("result", {}).get("value")
    finally:
        c.close()


def click(ref: str) -> dict:
    _call_on_ref(ref, "function() { this.click(); return true; }")
    return {"ref": ref, "used": "cdp"}


def fill(ref: str, text: str) -> dict:
    js = (
        "function(v) {"
        "  this.focus();"
        "  if ('value' in this) { this.value = v; }"
        "  else { this.textContent = v; }"
        "  this.dispatchEvent(new Event('input', {bubbles:true}));"
        "  this.dispatchEvent(new Event('change', {bubbles:true}));"
        "  return ('value' in this) ? this.value : this.textContent;"
        "}"
    )
    val = _call_on_ref(ref, js, text)
    return {"ref": ref, "used": "cdp", "value": val}


def evaluate(js: str) -> Any:
    c = _page()
    try:
        result = c.call("Runtime.evaluate", {
            "expression": js,
            "returnByValue": True,
            "awaitPromise": True,
        })
        return (result or {}).get("result", {}).get("value")
    finally:
        c.close()
