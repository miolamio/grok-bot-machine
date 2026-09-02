# Layer B of the grok-bot VM: Debian 13 desktop in Docker.
# Native arch (arm64 on Apple Silicon). Do not add --platform linux/amd64.
FROM debian:trixie-slim AS xcapture
ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev libx11-dev libxext-dev libpng-dev \
 && rm -rf /var/lib/apt/lists/*
COPY docker/control/xcapture.c /src/xcapture.c
RUN gcc -O2 -s -o /xcapture /src/xcapture.c -lX11 -lXext -lpng

FROM debian:trixie-slim

ENV DEBIAN_FRONTEND=noninteractive \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=UTC \
    DISPLAY=:1 \
    HOME=/home/box \
    USER=box \
    GTK_A11Y=1 \
    QT_ACCESSIBILITY=1

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
      ca-certificates \
      curl \
      wget \
      sudo \
      tini \
      tzdata \
      unzip \
      procps \
      dbus-x11 \
      dconf-cli \
      xvfb \
      x11-utils \
      x11-xserver-utils \
      xfwm4 \
      xfce4-terminal \
      thunar \
      plank \
      picom \
      hsetroot \
      x11vnc \
      novnc \
      websockify \
      python3 \
      nodejs \
      npm \
      git \
      fonts-dejavu-core \
      fonts-dejavu-mono \
      fonts-liberation \
      fonts-urw-base35 \
      fonts-noto-color-emoji \
      fonts-noto-cjk \
      adwaita-icon-theme \
      hicolor-icon-theme \
      xdotool \
      ffmpeg \
      x11-apps \
      at-spi2-core \
      libatk-bridge2.0-0 \
      python3-gi \
      gir1.2-atspi-2.0 \
      gir1.2-gtk-3.0 \
      python3-pyatspi \
      wmctrl \
      socat \
      libxext6 \
      libpng16-16; \
    ln -sf /usr/share/zoneinfo/Etc/UTC /etc/localtime; \
    echo UTC > /etc/timezone; \
    echo cursor > /etc/hostname; \
    useradd --uid 1000 --create-home --shell /bin/bash box; \
    printf '%s\n' 'box ALL=(ALL:ALL) NOPASSWD:ALL' > /etc/sudoers.d/box; \
    chmod 0440 /etc/sudoers.d/box; \
    mkdir -p /workspace /run/user/1000 /opt/grok-bot; \
    chown box:box /workspace /run/user/1000; \
    chmod 700 /run/user/1000; \
    rm -rf /var/lib/apt/lists/*

ARG TARGETARCH
RUN set -eux; \
    apt-get update; \
    if [ "$TARGETARCH" = "amd64" ] \
       && wget -q -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
       && apt-get install -y --no-install-recommends /tmp/chrome.deb; then \
      :; \
    else \
      apt-get install -y --no-install-recommends chromium; \
    fi; \
    rm -f /tmp/chrome.deb; \
    rm -rf /var/lib/apt/lists/*

RUN set -eux; \
    curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash -s -- bun-v1.4.0 \
      || curl -fsSL https://bun.sh/install | BUN_INSTALL=/usr/local bash; \
    curl -LsSf https://astral.sh/uv/0.12.8/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh \
      || curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh; \
    chmod 755 /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/bun /usr/local/bin/bunx 2>/dev/null || true

COPY --from=xcapture /xcapture /usr/local/bin/xcapture
COPY docker/skel /opt/grok-bot/skel
COPY docker/control /opt/grok-bot/control
COPY docker/fixtures /opt/grok-bot/fixtures
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY docker/box-chrome /usr/local/bin/box-chrome
COPY docker/gbm-doctor /usr/local/bin/gbm-doctor
COPY docker/control/gbm_act.py /usr/local/bin/gbm-act
COPY docker/novnc-index.html /usr/share/novnc/index.html
COPY docker/wallpaper/sand-wallpaper-02-a.png /usr/share/backgrounds/sand-wallpaper-02-a.png
COPY docker/wallpaper/sand-wallpaper-02-b.png /usr/share/backgrounds/sand-wallpaper-02-b.png
COPY docker/wallpaper/sand-wallpaper-02-c.png /usr/share/backgrounds/sand-wallpaper-02-c.png
COPY docker/chrome-policies/sand.json /etc/opt/chrome/policies/managed/sand.json
COPY docker/chrome-policies/sand-webrtc.json /etc/opt/chrome/policies/managed/sand-webrtc.json
COPY docker/chrome-policies/sand.json /etc/chromium/policies/managed/sand.json
COPY docker/chrome-policies/sand-webrtc.json /etc/chromium/policies/managed/sand-webrtc.json
COPY docker/icons /tmp/chrome-icons

RUN set -eux; \
    chmod 755 /usr/local/bin/entrypoint.sh /usr/local/bin/box-chrome \
      /usr/local/bin/xcapture /usr/local/bin/gbm-doctor /usr/local/bin/gbm-act \
      /opt/grok-bot/control/server.py /opt/grok-bot/control/connect_cu.py \
      /opt/grok-bot/fixtures/gtk_entry.py; \
    ln -sf /usr/local/bin/box-chrome /usr/local/bin/chrome; \
    ln -sfn /usr/share/backgrounds/sand-wallpaper-02-a.png /usr/share/backgrounds/cursor-box-wallpaper.jpg; \
    for s in 16 24 32 48 64 128 256; do \
      mkdir -p "/usr/share/icons/hicolor/${s}x${s}/apps"; \
      cp "/tmp/chrome-icons/product_logo_${s}.png" \
        "/usr/share/icons/hicolor/${s}x${s}/apps/google-chrome.png"; \
    done; \
    rm -rf /tmp/chrome-icons; \
    gtk-update-icon-cache -f /usr/share/icons/hicolor 2>/dev/null || true; \
    cp -a /opt/grok-bot/skel/. /home/box/; \
    chown -R box:box /home/box /opt/grok-bot/skel

WORKDIR /workspace
EXPOSE 6080 7070 9222 1337
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD wget -q -O- http://127.0.0.1:6080/vnc.html >/dev/null || exit 1

ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/bin/entrypoint.sh"]
