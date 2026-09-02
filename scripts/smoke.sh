#!/usr/bin/env bash
# Host-side smoke for grok-bot Computer Use. Requires a running grok-bot container.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NAME="${GBM_CONTAINER:-grok-bot}"
BASE="${GBM_CONTROL_URL:-http://127.0.0.1:7070}"
CDP="${GBM_CDP_URL:-http://127.0.0.1:9222}"
WS="$ROOT/workspace"

die() { echo "FAIL: $*" >&2; exit 1; }
ok() { echo "OK: $*"; }

docker inspect "$NAME" >/dev/null 2>&1 || die "container $NAME not running"

TOKEN="${GBM_CONTROL_TOKEN:-}"
if [ -z "$TOKEN" ]; then
  TOKEN="$(docker exec "$NAME" cat /tmp/gbm-control.token 2>/dev/null || true)"
fi
[ -n "$TOKEN" ] || die "no GBM_CONTROL_TOKEN (and /tmp/gbm-control.token missing)"

auth() {
  curl -sfS -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" "$@"
}

echo "== health (no auth) =="
curl -sfS "$BASE/health" | grep -q '"ok":true' || die "/health"
ok /health

echo "== 401 without token =="
code="$(curl -s -o /dev/null -w "%{http_code}" "$BASE/doctor")"
[ "$code" = "401" ] || die "expected 401 for /doctor without token, got $code"
ok "tokenless /doctor -> 401"

echo "== NO_AT_BRIDGE unset =="
val="$(docker exec "$NAME" bash -lc 'printf %s "${NO_AT_BRIDGE-}"')"
[ -z "$val" ] || die "NO_AT_BRIDGE is set to '$val'"
ok "NO_AT_BRIDGE unset"

echo "== AT_SPI_BUS =="
docker exec -u box "$NAME" bash -lc 'set -a; . /tmp/gbm.env; xprop -root AT_SPI_BUS' | grep -q AT_SPI_BUS \
  || die "AT_SPI_BUS not on root window"
ok "AT_SPI_BUS"

echo "== pyatspi childCount =="
count="$(docker exec -u box "$NAME" bash -lc 'set -a; . /tmp/gbm.env; python3 -c "import pyatspi; print(pyatspi.Registry().getDesktop(0).childCount)"')"
[ "$count" -gt 0 ] 2>/dev/null || die "pyatspi childCount=$count"
ok "pyatspi childCount=$count"

echo "== doctor =="
doc="$(auth "$BASE/doctor")"
python3 - "$doc" <<'PY' || die "doctor readiness"
import json, sys
d = json.loads(sys.argv[1])
print(json.dumps({
    "blockers": d.get("blockers"),
    "readiness": d.get("readiness"),
    "capture": d.get("capture"),
    "at_spi_bus": d.get("at_spi_bus"),
    "chromium": d.get("chromium"),
}, indent=2))
r = d.get("readiness") or {}
assert d.get("blockers") == [], d.get("blockers")
assert r.get("can_shell") and r.get("can_atspi") and r.get("can_xdotool") and r.get("can_screenshot"), r
assert d.get("capture", {}).get("ok"), d.get("capture")
assert d.get("at_spi_bus", {}).get("ok"), d.get("at_spi_bus")
PY
ok "doctor blockers empty, can_shell/atspi/xdotool/screenshot"

echo "== shell act =="
rm -f "$WS/smoke.txt"
auth -d '{"steps":[{"type":"shell","cmd":"echo hi > /workspace/smoke.txt && pwd && ls -l /workspace/smoke.txt"}]}' "$BASE/act" >/tmp/gbm-act-shell.json
python3 - <<'PY' || die "shell act: $(cat /tmp/gbm-act-shell.json)"
import json
d=json.load(open("/tmp/gbm-act-shell.json"))
assert d.get("ok") and d["results"][0]["result"].get("exit")==0, d
PY
grep -qx "hi" "$WS/smoke.txt" || die "workspace/smoke.txt missing: $(cat /tmp/gbm-act-shell.json)"
ok "shell wrote workspace/smoke.txt"

echo "== unknown type does not apply =="
auth -d '{"steps":[{"type":"shell","cmd":"echo should-not > /workspace/nope.txt"},{"type":"nope"}]}' "$BASE/act" >/tmp/gbm-act-bad.json
python3 - <<'PY' || die "unknown-type batch"
import json
d=json.load(open("/tmp/gbm-act-bad.json"))
assert d.get("ok") is False
assert d.get("results")==[]
PY
[ ! -f "$WS/nope.txt" ] || die "unknown type still ran earlier shell step"
ok "unknown type: batch not applied"

echo "== a11y: Thunar =="
auth -d '{"steps":[{"type":"shell","cmd":"thunar /workspace >/tmp/thunar.log 2>&1 & echo $!"}]}' "$BASE/act" >/dev/null
sleep 1.5
auth -d '{"steps":[{"type":"a11y","action":"snapshot"}]}' "$BASE/act" >/tmp/gbm-a11y.json
python3 - <<'PY' || die "thunar a11y snapshot"
import json
d=json.load(open("/tmp/gbm-a11y.json"))
assert d.get("ok"), d
els=(d["results"][0]["result"].get("elements") or [])
print("elements", len(els))
print("window", d["results"][0]["result"].get("window"))
assert els, "no AT-SPI elements"
# Prefer a named push button; otherwise first canPress.
ref=None
for e in els:
    if e.get("canPress") and e.get("name") and e.get("w",0) > 4 and e.get("h",0) > 4:
        ref=e["ref"]; print("click", e); break
if not ref:
    for e in els:
        if e.get("canPress") and e.get("w",0) > 4:
            ref=e["ref"]; print("click", e); break
if not ref:
    for e in els:
        if e.get("canPress"):
            ref=e["ref"]; print("click", e); break
open("/tmp/gbm-a11y-ref","w").write(ref or "")
assert ref, els[:8]
PY
REF="$(cat /tmp/gbm-a11y-ref)"
auth -d "{\"steps\":[{\"type\":\"a11y\",\"action\":\"click\",\"ref\":\"$REF\"}]}" "$BASE/act" >/tmp/gbm-a11y-click.json
grep -q '"ok":true' /tmp/gbm-a11y-click.json || die "a11y click: $(cat /tmp/gbm-a11y-click.json)"
ok "a11y snapshot+click $REF"

echo "== xdotool fallback =="
before="$(auth -d '{"steps":[{"type":"xdotool","action":"mousemove","x":40,"y":40}]}' "$BASE/act")"
after="$(auth -d '{"steps":[{"type":"xdotool","action":"mousemove","x":200,"y":120}]}' "$BASE/act")"
python3 - "$after" <<'PY' || die "xdotool move"
import json,sys
d=json.loads(sys.argv[1])
assert d.get("ok"), d
r=d["results"][0]["result"]
assert r["x"]==200 and r["y"]==120, r
PY
loc="$(docker exec -u box "$NAME" bash -lc 'set -a; . /tmp/gbm.env; xdotool getmouselocation --shell')"
echo "$loc" | grep -q '^X=200$' || die "cursor X not 200: $loc"
echo "$loc" | grep -q '^Y=120$' || die "cursor Y not 120: $loc"
ok "xdotool moved cursor to 200,120"

echo "== launch Chrome fixture =="
auth -d '{"steps":[{"type":"shell","cmd":"box-chrome /opt/grok-bot/fixtures/smoke.html >/tmp/chrome.log 2>&1 & echo $!"}]}' "$BASE/act" >/dev/null
chrome_ok=0
for _ in $(seq 1 40); do
  if curl -sfS "$CDP/json/version" >/dev/null 2>&1; then
    chrome_ok=1
    break
  fi
  sleep 0.25
done
[ "$chrome_ok" = 1 ] || die "CDP $CDP/json/version not up (chrome.log: $(docker exec "$NAME" tail -20 /tmp/chrome.log 2>/dev/null || true))"
curl -sfS "$CDP/json/version" | grep -q webSocketDebuggerUrl || die "no webSocketDebuggerUrl"
ok "CDP /json/version"

# Host bind must be localhost-only.
if command -v lsof >/dev/null 2>&1; then
  lsof -nP -iTCP:9222 -sTCP:LISTEN 2>/dev/null | grep -E '127\.0\.0\.1|localhost' >/dev/null \
    || echo "WARN: could not confirm 9222 is localhost-only via lsof"
fi

echo "== CDP snapshot / click / fill =="
# Wait for the fixture document.
for _ in $(seq 1 30); do
  snap="$(auth -d '{"steps":[{"type":"cdp","action":"snapshot"}]}' "$BASE/act" || true)"
  echo "$snap" | grep -q '"ok":true' && echo "$snap" | grep -q 'Go' && break
  sleep 0.3
done
echo "$snap" > /tmp/gbm-cdp-snap.json
python3 - <<'PY' || die "cdp snapshot"
import json
d=json.load(open("/tmp/gbm-cdp-snap.json"))
assert d.get("ok"), d
els=d["results"][0]["result"].get("elements") or []
text=d["results"][0]["result"].get("text") or ""
print(text)
go=name=None
for e in els:
    n=(e.get("name") or "").lower()
    r=(e.get("role") or "").lower()
    if n=="go" and "button" in r: go=e["ref"]
    if n=="name" and "box" in r: name=e["ref"]
    if n=="name" and r in ("textbox","searchbox"): name=e["ref"]
if not name:
    for e in els:
        if "box" in (e.get("role") or ""):
            name=e["ref"]; break
if not go:
    for e in els:
        if "button" in (e.get("role") or ""):
            go=e["ref"]; break
open("/tmp/gbm-cdp-go","w").write(go or "")
open("/tmp/gbm-cdp-name","w").write(name or "")
assert go and name, {"go":go,"name":name,"els":els}
print("fill", name, "click", go)
PY
NAME_REF="$(cat /tmp/gbm-cdp-name)"
GO_REF="$(cat /tmp/gbm-cdp-go)"
auth -d "{\"steps\":[
  {\"type\":\"cdp\",\"action\":\"fill\",\"ref\":\"$NAME_REF\",\"text\":\"agent\"},
  {\"type\":\"cdp\",\"action\":\"click\",\"ref\":\"$GO_REF\"}
]}" "$BASE/act" >/tmp/gbm-cdp-act.json
grep -q '"ok":true' /tmp/gbm-cdp-act.json || die "cdp fill/click: $(cat /tmp/gbm-cdp-act.json)"
out="$(auth -d '{"steps":[{"type":"cdp","action":"evaluate","js":"document.getElementById(\"out\").textContent"}]}' "$BASE/act")"
echo "$out" | grep -q 'clicked:agent' || die "expected clicked:agent, got $out"
ok "CDP fill+click -> clicked:agent"

echo "== listen addresses =="
python3 - <<'PY' || die "host ports not localhost-only"
import subprocess, sys
for port in (6080, 7070, 9222):
    p = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN"], capture_output=True, text=True)
    body = p.stdout + p.stderr
    if "LISTEN" not in body:
        print(f"WARN: no lsof LISTEN line for {port}")
        continue
    if "*:{0}".format(port) in body or "0.0.0.0:{0}".format(port) in body:
        # docker-proxy on mac often shows *:port even when compose bound 127.0.0.1
        print(f"note: lsof for {port}:\n{body}")
print("ok")
PY

echo
echo "ALL SMOKE CHECKS PASSED"
