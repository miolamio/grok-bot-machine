#!/usr/bin/env bash
# 100 captures via control plane vs ffmpeg x11grab. GBM-18.
set -euo pipefail
NAME="${GBM_CONTAINER:-grok-bot}"
TOKEN="${GBM_CONTROL_TOKEN:-$(docker exec "$NAME" cat /tmp/gbm-control.token)}"
BASE="${GBM_CONTROL_URL:-http://127.0.0.1:7070}"

python3 - "$BASE" "$TOKEN" "$NAME" <<'PY'
import json, statistics, subprocess, sys, time, urllib.request

base, token, name = sys.argv[1:]
req = urllib.request.Request(
    base + "/act",
    data=json.dumps({"steps": [{"type": "screenshot"}]}).encode(),
    headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    method="POST",
)

def one():
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = json.loads(resp.read())
    ms = (time.perf_counter() - t0) * 1000
    assert body.get("ok"), body
    r = body["results"][0]["result"]
    assert r["width"] == 1280 and r["height"] == 800, r
    return ms, r["backend"], r["ms"]

# warmup
one()
times = []
backends = set()
inner = []
for _ in range(100):
    ms, backend, inner_ms = one()
    times.append(ms)
    inner.append(inner_ms)
    backends.add(backend)

times.sort()
p95 = times[94]
print(f"backend={backends} n=100")
print(f"http_roundtrip_ms: min={times[0]:.1f} median={times[49]:.1f} p95={p95:.1f} max={times[-1]:.1f}")
print(f"inside_ms: min={min(inner):.1f} median={sorted(inner)[49]:.1f} p95={sorted(inner)[94]:.1f}")

# ffmpeg baseline inside the container
cmd = [
    "docker", "exec", "-u", "box", name, "bash", "-lc",
    "set -a; . /tmp/gbm.env; "
    "python3 - <<'IN'\n"
    "import subprocess, time, statistics\n"
    "cmd=['ffmpeg','-hide_banner','-loglevel','error','-f','x11grab','-video_size','1280x800',"
    "'-i',__import__('os').environ.get('DISPLAY',':1'),'-frames:v','1','-f','image2','-vcodec','png','/tmp/bench.png']\n"
    "subprocess.run(cmd, check=True)\n"
    "xs=[]\n"
    "for _ in range(20):\n"
    "    t=time.perf_counter()\n"
    "    subprocess.run(cmd, check=True, capture_output=True)\n"
    "    xs.append((time.perf_counter()-t)*1000)\n"
    "xs.sort()\n"
    "print(f'ffmpeg_n20_ms min={xs[0]:.1f} median={xs[9]:.1f} p95={xs[18]:.1f}')\n"
    "IN"
]
r = subprocess.run(cmd, capture_output=True, text=True)
print(r.stdout.strip())
if r.returncode != 0:
    print(r.stderr)
    sys.exit(1)
if p95 > 500:
    print("WARN: p95 roundtrip > 500ms", file=sys.stderr)
print("BENCH OK")
PY
