#!/usr/bin/env python3
"""Drive the host MCP proxy and prove it hits the container, not the Mac."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MCP = os.path.join(ROOT, "scripts", "gbm-mcp")
BASE = os.environ.get("GBM_CONTROL_URL", "http://127.0.0.1:7070").rstrip("/")
CONTAINER = os.environ.get("GBM_CONTAINER", "grok-bot")
WS = os.path.join(ROOT, "workspace")


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
        capture_output=True, text=True, check=True, timeout=5,
    )
    return r.stdout.strip()


def curl_doctor() -> dict:
    req = urllib.request.Request(
        BASE + "/doctor",
        headers={"Authorization": "Bearer " + token(), "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


class Mcp:
    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [MCP],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=ROOT,
            env={**os.environ, "PYTHONUNBUFFERED": "1"},
        )
        assert self.proc.stdin and self.proc.stdout
        self._n = 0

    def notify(self, method: str, params: dict | None = None) -> None:
        msg: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        assert self.proc.stdin
        self.proc.stdin.write(json.dumps(msg, separators=(",", ":")).encode() + b"\n")
        self.proc.stdin.flush()

    def close(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.proc.kill()

    def rpc(self, method: str, params: dict | None = None, timeout: float = 45.0) -> dict:
        self._n += 1
        msg = {"jsonrpc": "2.0", "id": self._n, "method": method}
        if params is not None:
            msg["params"] = params
        line = json.dumps(msg, separators=(",", ":")).encode() + b"\n"
        assert self.proc.stdin
        self.proc.stdin.write(line)
        self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        assert self.proc.stdout
        while time.monotonic() < deadline:
            raw = self.proc.stdout.readline()
            if not raw:
                err = b""
                if self.proc.stderr:
                    err = self.proc.stderr.read()
                die(f"MCP stdout closed: {err[-500:]!r}")
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if obj.get("id") != self._n:
                continue
            if "error" in obj:
                die(f"{method}: {obj['error']}")
            return obj["result"]
        die(f"timeout waiting for {method}")
        raise AssertionError

    def tool(self, name: str, arguments: dict | None = None) -> dict:
        result = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        texts = [
            c.get("text", "")
            for c in (result.get("content") or [])
            if c.get("type") == "text"
        ]
        blob = "\n".join(texts)
        if result.get("isError"):
            die(f"tool {name} isError: {blob[:800]}")
        try:
            return json.loads(blob)
        except json.JSONDecodeError:
            return {"text": blob}


def container_cursor() -> tuple[int, int]:
    r = subprocess.run(
        ["docker", "exec", "-u", "box", CONTAINER, "bash", "-lc",
         "set -a; . /tmp/gbm.env; xdotool getmouselocation --shell"],
        capture_output=True, text=True, timeout=8, check=True,
    )
    vals = dict(line.split("=", 1) for line in r.stdout.splitlines() if "=" in line)
    return int(vals["X"]), int(vals["Y"])


def mac_mouse() -> tuple[float, float] | None:
    try:
        import ctypes
        import ctypes.util
        lib = ctypes.util.find_library("CoreGraphics") or ctypes.util.find_library("ApplicationServices")
        if not lib:
            return None
        cg = ctypes.cdll.LoadLibrary(lib)

        class CGPoint(ctypes.Structure):
            _fields_ = [("x", ctypes.c_double), ("y", ctypes.c_double)]

        cg.CGEventCreate.restype = ctypes.c_void_p
        cg.CGEventCreate.argtypes = [ctypes.c_void_p]
        cg.CGEventGetLocation.restype = CGPoint
        cg.CGEventGetLocation.argtypes = [ctypes.c_void_p]
        ev = cg.CGEventCreate(None)
        if not ev:
            return None
        pt = cg.CGEventGetLocation(ev)
        return (float(pt.x), float(pt.y))
    except Exception:
        return None


def main() -> int:
    subprocess.run(["docker", "inspect", CONTAINER], capture_output=True, check=True)

    mcp = Mcp()
    try:
        init = mcp.rpc("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "gbm-mcp-smoke", "version": "0.1"},
        })
        if init.get("serverInfo", {}).get("name") != "gbm":
            die(f"bad serverInfo: {init}")
        ok("initialize")
        mcp.notify("notifications/initialized")

        listed = mcp.rpc("tools/list")
        names = {t["name"] for t in listed.get("tools") or []}
        for n in ("doctor", "observe", "act", "shell"):
            if n not in names:
                die(f"missing tool {n}: {names}")
        ok(f"tools/list {sorted(names)}")

        via_mcp = mcp.tool("doctor")
        via_http = curl_doctor()
        for key in ("display", "blockers"):
            if via_mcp.get(key) != via_http.get(key):
                die(f"doctor.{key} mcp={via_mcp.get(key)!r} http={via_http.get(key)!r}")
        r_m = via_mcp.get("readiness") or {}
        r_h = via_http.get("readiness") or {}
        for k in ("can_shell", "can_atspi", "can_xdotool", "can_screenshot"):
            if r_m.get(k) != r_h.get(k):
                die(f"readiness.{k} mcp={r_m.get(k)} http={r_h.get(k)}")
        if via_mcp.get("display") != ":1":
            die(f"doctor.display is {via_mcp.get('display')!r}, expected :1 (container)")
        ok("doctor matches curl /doctor (display=:1)")

        path = os.path.join(WS, "mcp-smoke.txt")
        try:
            os.remove(path)
        except FileNotFoundError:
            pass
        sh = mcp.tool("shell", {"cmd": "sudo -n chmod 1777 /workspace && echo mcp-ok > /workspace/mcp-smoke.txt"})
        if not sh.get("ok") or sh.get("results", [{}])[0].get("result", {}).get("exit") != 0:
            die(f"shell: {sh}")
        with open(path) as f:
            if f.read().strip() != "mcp-ok":
                die("mcp-smoke.txt contents")
        ok("shell via MCP wrote workspace/mcp-smoke.txt")

        obs = mcp.tool("observe", {"include_tree": True, "include_screenshot": False})
        if "windows" not in obs and "elements" not in obs:
            die(f"observe missing windows/elements: {list(obs)}")
        if obs.get("screenshot"):
            die("observe default/false still included screenshot")
        ok(f"observe windows={len(obs.get('windows') or [])} elements={len(obs.get('elements') or [])}")

        mac_before = mac_mouse()
        target = (333, 144)
        moved = mcp.tool("act", {"steps": [
            {"type": "xdotool", "action": "mousemove", "x": target[0], "y": target[1]},
        ]})
        if not moved.get("ok"):
            die(f"act mousemove: {moved}")
        time.sleep(0.15)
        cx, cy = container_cursor()
        if (cx, cy) != target:
            die(f"container cursor {cx},{cy} != {target[0]},{target[1]}")
        ok(f"container cursor moved to {cx},{cy}")

        mac_after = mac_mouse()
        if mac_before is not None and mac_after is not None:
            dx = abs(mac_after[0] - mac_before[0])
            dy = abs(mac_after[1] - mac_before[1])
            # Human jitter OK; a 100px+ jump would mean we hit the Mac.
            if dx > 40 or dy > 40:
                die(f"Mac cursor jumped {dx:.0f},{dy:.0f}px — MCP must not drive the host")
            ok(f"Mac cursor unchanged (Δ {dx:.1f},{dy:.1f}px)")
        else:
            ok("Mac cursor probe unavailable (CoreGraphics); container move is the proof")
    finally:
        mcp.close()

    # Content-Length framing (Claude Desktop / some hosts).
    proc = subprocess.Popen(
        [MCP],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        cwd=ROOT,
        env={**os.environ, "PYTHONUNBUFFERED": "1"},
    )
    try:
        payload = json.dumps({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "lsp-smoke", "version": "0"},
            },
        }).encode()
        assert proc.stdin and proc.stdout
        proc.stdin.write(f"Content-Length: {len(payload)}\r\n\r\n".encode() + payload)
        proc.stdin.flush()
        header = b""
        while b"\r\n\r\n" not in header:
            chunk = proc.stdout.read(1)
            if not chunk:
                die("LSP initialize: no headers")
            header += chunk
        n = 0
        for hl in header.split(b"\n"):
            if hl.lower().startswith(b"content-length:"):
                n = int(hl.split(b":", 1)[1])
        body = proc.stdout.read(n)
        obj = json.loads(body)
        if obj.get("result", {}).get("serverInfo", {}).get("name") != "gbm":
            die(f"LSP initialize: {obj}")
        ok("Content-Length/LSP initialize")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()

    print("ALL MCP SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
