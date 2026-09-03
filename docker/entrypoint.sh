#!/bin/bash
# Matches grok-bot start-desktop.sh: Xvfb + hsetroot + x11vnc + noVNC +
# xfwm4 --compositor=off + picom xrender + plank. No xfdesktop, no xfce4-session.
# AT-SPI + gbm-control added for native Computer Use.
set -euo pipefail

export DISPLAY="${DISPLAY:-:1}"
export HOME=/home/box
export USER=box
export LOGNAME=box
export SHELL=/bin/bash
export LANG="${LANG:-C.UTF-8}"
export LC_ALL="${LC_ALL:-C.UTF-8}"
export TZ="${TZ:-UTC}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/tmp/xdg-runtime-box}"
export XDG_SESSION_TYPE=x11
export GTK_A11Y=1
export QT_ACCESSIBILITY=1
export ACCESSIBILITY_ENABLED=1
unset NO_AT_BRIDGE || true

mkdir -p "$XDG_RUNTIME_DIR" "$HOME/.config" "$HOME/.cache" "$HOME/.local/share" \
  "$HOME/chrome-profile" /tmp/.X11-unix /workspace
chmod 700 "$XDG_RUNTIME_DIR" || true
chmod 1777 /tmp/.X11-unix 2>/dev/null || true

if [ "$(id -u)" -eq 0 ]; then
  chown -R box:box "$XDG_RUNTIME_DIR" "$HOME" || true
  # OrbStack virtiofs often refuses chown on the bind mount; make it world-writable.
  chown box:box /workspace "$HOME/chrome-profile" 2>/dev/null || true
  chmod 1777 /workspace "$HOME/chrome-profile" 2>/dev/null || true
  if command -v runuser >/dev/null 2>&1; then
    exec runuser -u box -- "$0" "$@"
  fi
  exec su -s /bin/bash box -c '"$0" "$@"' -- "$0" "$@"
fi

export PATH="/usr/local/bin:/usr/bin:/bin:/usr/local/games:/usr/games:/usr/sbin:/sbin"

mkdir -p "$HOME/.config" "$HOME/.local/share/applications"
cp -an /opt/grok-bot/skel/.config/. "$HOME/.config/"
cp -an /opt/grok-bot/skel/.local/. "$HOME/.local/"

eval "$(dbus-launch --sh-syntax)"

SCREEN_WIDTH=1280
SCREEN_HEIGHT=800
DISPLAY_NUM="${DISPLAY#:}"
DISPLAY_NUM="${DISPLAY_NUM%%.*}"
if [ "$DISPLAY_NUM" -le 1 ]; then
  VNC_PORT="${SAND_VNC_PORT:-5900}"
  export SAND_CHROME_REMOTE_DEBUG_PORT="${SAND_CHROME_REMOTE_DEBUG_PORT:-9222}"
else
  VNC_PORT="${SAND_VNC_PORT:-$((5900 + DISPLAY_NUM))}"
  export SAND_CHROME_REMOTE_DEBUG_PORT="${SAND_CHROME_REMOTE_DEBUG_PORT:-$((9222 + DISPLAY_NUM))}"
fi
NOVNC_PORT="${SAND_NOVNC_PORT:-6080}"
CONTROL_PORT="${GBM_CONTROL_PORT:-7070}"
WALLPAPER="${GROK_WALLPAPER:-/usr/share/backgrounds/cursor-box-wallpaper.jpg}"

if [ -z "${GBM_CONTROL_TOKEN:-}" ]; then
  GBM_CONTROL_TOKEN="$(python3 -c 'import secrets; print(secrets.token_hex(16))')"
fi
export GBM_CONTROL_TOKEN
printf '%s\n' "$GBM_CONTROL_TOKEN" > "$XDG_RUNTIME_DIR/gbm-control.token"
printf '%s\n' "$GBM_CONTROL_TOKEN" > /tmp/gbm-control.token
chmod 600 "$XDG_RUNTIME_DIR/gbm-control.token" /tmp/gbm-control.token || true

Xvfb "$DISPLAY" -screen 0 "${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24" \
  -ac +extension GLX +render -noreset &
XPID=$!

ok=0
for _ in $(seq 1 100); do
  if xdpyinfo -display "$DISPLAY" >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 0.2
done
if [ "$ok" -ne 1 ]; then
  echo "Xvfb failed to start on $DISPLAY" >&2
  exit 1
fi

xset s off -dpms s noblank >/dev/null 2>&1 || true

start_atspi() {
  local launcher="" registry="" p
  for p in /usr/libexec/at-spi-bus-launcher /usr/lib/at-spi2-core/at-spi-bus-launcher; do
    if [ -x "$p" ]; then launcher="$p"; break; fi
  done
  for p in /usr/libexec/at-spi2-registryd /usr/lib/at-spi2-core/at-spi2-registryd; do
    if [ -x "$p" ]; then registry="$p"; break; fi
  done
  if [ -n "$launcher" ]; then
    "$launcher" --launch-immediately >/tmp/at-spi-bus.log 2>&1 &
  fi
  if [ -n "$registry" ]; then
    "$registry" >/tmp/at-spi-registry.log 2>&1 &
  fi
  local i
  for i in $(seq 1 50); do
    if dbus-send --session --dest=org.a11y.Bus --print-reply /org/a11y/bus \
         org.freedesktop.DBus.Peer.Ping >/dev/null 2>&1; then
      dbus-send --session --dest=org.a11y.Bus --type=method_call /org/a11y/bus \
        org.freedesktop.DBus.Properties.Set \
        string:org.a11y.Status string:IsEnabled variant:boolean:true \
        >/dev/null 2>&1 || true
      dbus-send --session --dest=org.a11y.Bus --type=method_call /org/a11y/bus \
        org.freedesktop.DBus.Properties.Set \
        string:org.a11y.Status string:ScreenReaderEnabled variant:boolean:true \
        >/dev/null 2>&1 || true
      break
    fi
    sleep 0.1
  done
}
start_atspi || true

hsetroot -cover "$WALLPAPER" \
  || hsetroot -solid "#1e2330" \
  || xsetroot -solid "#1e2330" \
  || true

x11vnc \
  -skip_lockkeys \
  -display "$DISPLAY" \
  -localhost \
  -nopw \
  -shared \
  -forever \
  -noxdamage \
  -rfbport "$VNC_PORT" \
  -quiet &
VNCPID=$!

websockify \
  --web=/usr/share/novnc \
  --heartbeat=30 \
  "0.0.0.0:${NOVNC_PORT}" \
  "localhost:${VNC_PORT}" &
WEBID=$!

xfwm4 --compositor=off &
WMPID=$!

for _ in $(seq 1 50); do
  xprop -root -display "$DISPLAY" _NET_SUPPORTING_WM_CHECK >/dev/null 2>&1 && break
  sleep 0.2
done

picom --backend xrender --no-vsync --no-frame-pacing --no-use-damage &
PICOM_PID=$!

python3 - "$DISPLAY" 2>/dev/null <<'PY' || true
import ctypes, sys, time
xlib = ctypes.CDLL("libX11.so.6")
xlib.XOpenDisplay.restype = ctypes.c_void_p
xlib.XInternAtom.restype = ctypes.c_ulong
xlib.XInternAtom.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
xlib.XGetSelectionOwner.restype = ctypes.c_ulong
xlib.XGetSelectionOwner.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
dpy = xlib.XOpenDisplay(sys.argv[1].encode())
if not dpy:
    raise SystemExit(0)
selection = xlib.XInternAtom(dpy, b"_NET_WM_CM_S0", False)
deadline = time.monotonic() + 5.0
while not xlib.XGetSelectionOwner(dpy, selection):
    if time.monotonic() >= deadline:
        break
    time.sleep(0.1)
PY

dconf write /net/launchpad/plank/docks/dock1/dock-items \
  "['chrome.dockitem', 'thunar.dockitem', 'xfce4-terminal.dockitem']" >/dev/null 2>&1 || true
dconf write /net/launchpad/plank/docks/dock1/position "'bottom'" >/dev/null 2>&1 || true
dconf write /net/launchpad/plank/docks/dock1/theme "'Transparent'" >/dev/null 2>&1 || true
dconf write /net/launchpad/plank/docks/dock1/icon-size 48 >/dev/null 2>&1 || true
dconf write /net/launchpad/plank/docks/dock1/hide-mode "'none'" >/dev/null 2>&1 || true
dconf write /org/gtk/settings/file-chooser/window-size "(1100, 680)" >/dev/null 2>&1 || true
dconf write /org/gtk/settings/file-chooser/window-position "(90, 60)" >/dev/null 2>&1 || true

plank --name dock1 &
PLANK_PID=$!

{
  echo "DISPLAY=$DISPLAY"
  echo "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"
  echo "XDG_RUNTIME_DIR=$XDG_RUNTIME_DIR"
  echo "GBM_CONTROL_TOKEN=$GBM_CONTROL_TOKEN"
  echo "GBM_CONTROL_URL=http://127.0.0.1:${CONTROL_PORT}"
  echo "SAND_CHROME_REMOTE_DEBUG_PORT=$SAND_CHROME_REMOTE_DEBUG_PORT"
  echo "GTK_A11Y=1"
  echo "QT_ACCESSIBILITY=1"
} > /tmp/gbm.env
chmod 600 /tmp/gbm.env || true

# Chromium binds DevTools to 127.0.0.1:9222 (ignores 0.0.0.0). Docker publish
# cannot hit that, so socat listens on the veth (9223) and compose maps 9222:9223.
(
  exec socat TCP-LISTEN:9223,bind=0.0.0.0,fork,reuseaddr TCP:127.0.0.1:9222
) >/tmp/socat-cdp.log 2>&1 &
SOCATPID=$!

(
  while true; do
    python3 /opt/grok-bot/control/server.py >>"$XDG_RUNTIME_DIR/gbm-control.log" 2>&1 || true
    echo "gbm-control exited, restarting" >>"$XDG_RUNTIME_DIR/gbm-control.log"
    sleep 1
  done
) &
CTRLPID=$!

(
  while true; do
    python3 /opt/grok-bot/control/connect_cu.py >>"$XDG_RUNTIME_DIR/gbm-connect.log" 2>&1 || true
    echo "gbm-connect-cu exited, restarting" >>"$XDG_RUNTIME_DIR/gbm-connect.log"
    sleep 1
  done
) &
CONNECTPID=$!

echo "grok-bot desktop ready on ${DISPLAY} ${SCREEN_WIDTH}x${SCREEN_HEIGHT}x24"
echo "noVNC: http://127.0.0.1:${NOVNC_PORT}/vnc.html?autoconnect=1&resize=off"
echo "control: http://127.0.0.1:${CONTROL_PORT}/ (Bearer token in /tmp/gbm-control.token)"
echo "connect-cu: http://127.0.0.1:1337/ (Connect-RPC, Bearer local)"
echo "cdp: 127.0.0.1:${SAND_CHROME_REMOTE_DEBUG_PORT}"

cleanup() {
  kill "$XPID" "$VNCPID" "$WEBID" "$WMPID" "$PICOM_PID" "$PLANK_PID" "$CTRLPID" "$CONNECTPID" "$SOCATPID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

while kill -0 "$XPID" 2>/dev/null \
   && kill -0 "$VNCPID" 2>/dev/null \
   && kill -0 "$WEBID" 2>/dev/null; do
  sleep 3
done

echo "a desktop process exited" >&2
exit 1
