# grok-bot-machine

A native-arch Docker desktop that an agent on the host can see and operate.

It clones grok-bot **Layer B**: the Debian 13 XFCE-like session (Xvfb 1280×800, xfwm4, picom, Plank, Thunar, Chromium, noVNC). It is not the 8 vCPU / 16 GiB KVM guest around that session, and it does not vendor grok’s `exec-daemon` binary. There is no LLM in the image.

Drive it from the Mac with **`./scripts/gbm`**. Two contracts share one display:

| Contract | Port | Skill | What the agent does |
| --- | --- | --- | --- |
| Native Computer Use | `127.0.0.1:7070` | `gbm-desktop` | shell → Chrome DevTools → AT-SPI → `xdotool`. Screenshot last. |
| Grok-faithful Computer Use | `127.0.0.1:1337` | `gbm-grok-cu` | 1280×800 PNG, then click/type/key on the same coordinates. |

The pointer moves inside Xvfb, never on the Mac.

## Requirements

- Docker Compose v2. On Apple Silicon use **OrbStack** (native `linux/arm64`). Do not pass `--platform linux/amd64`.
- Host `python3`.
- About 1.3 GiB RAM for the container. Idle is a few hundred MiB; Chromium is what costs RAM.

## Run

```bash
docker compose up -d --build
./scripts/gbm ready
./scripts/gbm doctor
```

First build installs Debian + Chromium and takes several minutes. `./scripts/gbm ready` waits until `:7070` answers. If `docker` hangs, OrbStack is probably not running.

```bash
docker compose down
```

`./workspace` is bind-mounted as `/workspace` (the only persistent user files).

### Look at the desk

[http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=off](http://127.0.0.1:6080/vnc.html?autoconnect=1&resize=off) — localhost only.

Keep `resize=off`. Scaling stretches the 1280×800 framebuffer and breaks click coordinates. You should see wallpaper, xfwm4, and a transparent Plank dock. Chromium is **not** started at idle.

### Try this: open a site in the container browser

From the repo root, with the box already `ready`. Open noVNC in another window if you want to watch.

```bash
./scripts/gbm shell 'box-chrome https://example.com >/tmp/chrome.log 2>&1 &'

# socat is up immediately; Chrome takes a few seconds to bind CDP
until curl -sfS http://127.0.0.1:9222/json/version | grep -q webSocketDebuggerUrl; do
  sleep 0.5
done

./scripts/gbm doctor    # readiness.can_cdp should be true
./scripts/gbm act '[{"type":"cdp","action":"snapshot"}]'
# elements include heading "Example Domain" and link "Learn more"

./scripts/gbm screenshot -o workspace/example.png
open workspace/example.png   # macOS; or just look at noVNC
```

That is Chrome **inside Docker**, not the Mac browser. Click/fill by CDP `ref` from the snapshot (`gbm-desktop`). Pixel clicks on the same desk: `./scripts/gbm connect screenshot -o workspace/cu.png` then `./scripts/gbm connect click X Y` (`gbm-grok-cu`).

If Chromium is already running, skip the `box-chrome` line and navigate instead:

```bash
./scripts/gbm act '[{"type":"cdp","action":"navigate","url":"https://example.com"}]'
```

## Host CLI

From the repository root. `--help` is the flag list. JSON on stdout. PNGs go to a file (`-o`, or `workspace/gbm-*.png`); the JSON keeps a path, not base64.

```bash
./scripts/gbm ready
./scripts/gbm doctor                 # blockers []; can_cdp is false until Chrome starts
./scripts/gbm shell 'echo hi > /workspace/hi.txt'
./scripts/gbm observe                # AT-SPI tree, no pixels
./scripts/gbm screenshot -o workspace/desk.png
./scripts/gbm mouse 640 400
./scripts/gbm click 640 400
./scripts/gbm type 'hello'
./scripts/gbm key Return
./scripts/gbm act '[{"type":"a11y","action":"snapshot"}]'

./scripts/gbm connect ping
./scripts/gbm connect caps
./scripts/gbm connect screenshot -o workspace/cu.png
./scripts/gbm connect click 640 400
./scripts/gbm connect type 'hello'
./scripts/gbm connect exec '[{"scroll":{"direction":2,"amount":3}}]'
```

| Command | API |
| --- | --- |
| `health` / `ready` / `doctor` / `observe` / `act` / `shell` | Native `:7070` |
| `screenshot` / `click` / `mouse` / `type` / `key` | Native `:7070` (`xdotool` or capture) |
| `connect …` | Connect-RPC `:1337` |

`:7070` uses `GBM_CONTROL_TOKEN` (compose default `dev-local-token`). If unset, the CLI reads `/tmp/gbm-control.token` from the container. `:1337` uses `Authorization: Bearer local` unless `GBM_CONNECT_TOKEN` is set. Those two secrets are different unless you set them equal.

Inside the container the same module is `gbm-act` (copied at image build). Killing the CLI does not kill Xvfb.

## Agent skills

Checked in at `.agents/skills/` (Agent Skills standard). Grok, Cursor, Codex, and Claude Code load them from the repo.

| Skill | Use when |
| --- | --- |
| [`gbm-desktop`](.agents/skills/gbm-desktop/SKILL.md) | Structured CU on the container: files, GTK, Chrome refs, fallback clicks |
| [`gbm-grok-cu`](.agents/skills/gbm-grok-cu/SKILL.md) | Vision loop: look at a 1280×800 PNG, then `connect click` / `type` |

Both skills tell the agent to call `./scripts/gbm`, not raw curl, and not to move the Mac cursor.

Native router (first match):

1. **shell** — files, packages, launch apps. Do not type commands into `xfce4-terminal`.
2. **cdp** — Chromium is up (`doctor` → `can_cdp`). Snapshot, then click/fill by **ref**.
3. **a11y** — GTK/Thunar. Snapshot, then `click` / `set_value` / `perform_action` by ref (AT-SPI, not `xdotool type`).
4. **xdotool** — no tree. Origin top-left, x `0…1279`, y `0…799`.
5. **screenshot** — last resort, or when a human asks to see the desk.

`act` batches 1–20 steps. An unknown `type` applies **nothing**. Shell success is `ok` **and** `exit == 0`.

## Ports

Compose binds `127.0.0.1` only.

| Host | Inside | What |
| --- | --- | --- |
| `6080` | `6080` | noVNC → x11vnc `localhost:5900` |
| `7070` | `7070` | Native control plane |
| `9222` | socat `9223` → Chrome `127.0.0.1:9222` | CDP, only while Chromium is running |
| `1337` | `1337` | Grok-faithful Connect-RPC |

## HTTP (if you are not using the CLI)

Prefer `./scripts/gbm`. The servers are still JSON HTTP.

**`:7070`** — `/health` is open; everything else needs `Authorization: Bearer <token>`.

```
GET  /health
GET  /doctor
POST /observe     { include_screenshot?, include_tree?, window? }
POST /act         { steps: [ { type: shell|cdp|a11y|xdotool|screenshot, ... } ] }
```

**`:1337`** — Connect-RPC over HTTP/1.1 (`Content-Type: application/json`, `Connect-Protocol-Version: 1`, `Bearer local`). Not websocket, not HTTP/2. `application/connect+json` → 415. `GET /` → 404.

```
POST /agent.v1.ControlService/Ping
POST /agent.v1.ControlService/GetCapabilities
POST /agent.v1.ExecService/Exec    { computerUseArgs: { actions: [...] } }
```

Actions: `mouse_move`, `click`, `mouse_down` / `mouse_up`, `drag`, `scroll`, `type`, `key`, `wait`, `screenshot`, `cursor_position`. Clicks are OS-level on the Xvfb desk, not Chrome CDP. This stand-in answers Exec as unary JSON; original grok streams Connect frames.

## MCP

Optional stdio proxy to `:7070` for hosts that prefer MCP over shell. It still drives the Docker desktop, never the Mac. Wrapper: `./scripts/gbm-mcp`. Config: `.mcp.json`. Tools: `doctor`, `observe`, `act`, `shell`.

## Verify

Container up, then from the repo root:

```bash
export GBM_CONTROL_TOKEN=dev-local-token

python3 scripts/cli-smoke.py
# host CLI: health, doctor, shell, PNG file, mouse, connect ping/click

./scripts/smoke.sh
# AT-SPI, doctor, shell, unknown-type atomicity, Thunar a11y,
# xdotool, box-chrome fixture, CDP fill+click → clicked:agent

python3 scripts/a11y-smoke.py      # GTK set_value + perform_action
python3 scripts/inside-smoke.py    # gbm-act in-box; kill it, Xvfb stays
python3 scripts/mcp-smoke.py       # MCP hits the container; Mac cursor still
python3 scripts/connect-smoke.py   # Connect 401/200/415/404, PNG, click
```

Each script prints `ALL … SMOKE CHECKS PASSED` and exits 0. `smoke.sh` leaves Thunar and Chromium running. Watch noVNC: the pointer should move in the **container**.

```bash
./scripts/bench-capture.sh         # optional capture timing
```

## Limits

One 1280×800 desk (`DISPLAY=:1`). Do not change width while an agent is recording.

Not cloned: `exec-daemon` ELF, pod-daemon, webauthn, egress, UA stamps, window-owner tokens, PTY `1338`, window-router `1339`, extra desks (`start-window N`). Host CDP is `9222` via socat; original formula is `:1 → 9223`.

Keep xfwm4 + picom xrender + Plank. Do not swap in fluxbox.

## Layout

```
compose.yaml                 # localhost ports, 1280m, shm 64m
Dockerfile                   # debian:trixie-slim, native arch
docker/entrypoint.sh         # Xvfb → wallpaper → vnc → wm → picom → plank → APIs
docker/control/              # :7070, :1337, AT-SPI, CDP, capture, CLI module
scripts/gbm                  # host CLI
.agents/skills/              # gbm-desktop, gbm-grok-cu
mcp/gbm_mcp.py               # host MCP stdio
scripts/                     # smokes
workspace/                   # bind-mounted /workspace
```
