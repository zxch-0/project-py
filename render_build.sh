#!/usr/bin/env bash
# Build Render — zach-runner
# 1) Dependances Python  2) Node.js vendored (scripts .js)  3) unrar vendored (archives .rar)
set -eu

pip install -r requirements.txt || pip install --break-system-packages -r requirements.txt

NODE_VERSION="20.18.0"
if [ -x "vendor/node/bin/node" ]; then
  echo "Node.js vendored deja present : $(vendor/node/bin/node --version)"
elif command -v node >/dev/null 2>&1; then
  echo "Node.js systeme detecte : $(node --version) (pas de vendoring)"
else
  echo "Telechargement de Node.js v${NODE_VERSION}..."
  if ( set -e
    mkdir -p vendor
    curl -fsSL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-x64.tar.xz" -o /tmp/node.tar.xz
    rm -rf "vendor/node-v${NODE_VERSION}-linux-x64" "vendor/node"
    tar -xJf /tmp/node.tar.xz -C vendor
    mv "vendor/node-v${NODE_VERSION}-linux-x64" vendor/node
    rm -f /tmp/node.tar.xz
    test -x "vendor/node/bin/node"
  ); then
    echo "Node.js vendored : $(vendor/node/bin/node --version)"
  else
    echo "AVERTISSEMENT : Node.js non installe — les scripts .js seront indisponibles (Python/Shell OK)."
  fi
fi

UNRAR_VERSION="723"
if [ -x "vendor/bin/unrar" ]; then
  echo "unrar vendored deja present."
elif command -v unrar >/dev/null 2>&1 || command -v unar >/dev/null 2>&1 || command -v bsdtar >/dev/null 2>&1; then
  echo "Outil .rar systeme detecte (pas de vendoring)."
else
  echo "Telechargement de unrar (RAR for Linux ${UNRAR_VERSION})..."
  if ( set -e
    rm -rf /tmp/rarlinux && mkdir -p /tmp/rarlinux vendor/bin
    curl -fsSL "https://www.rarlab.com/rar/rarlinux-x64-${UNRAR_VERSION}.tar.gz" -o /tmp/rarlinux.tgz
    tar -xzf /tmp/rarlinux.tgz -C /tmp/rarlinux
    UNRAR_BIN="$(find /tmp/rarlinux -name unrar -type f | head -n 1)"
    test -n "$UNRAR_BIN"
    cp "$UNRAR_BIN" vendor/bin/unrar
    chmod +x vendor/bin/unrar
    rm -rf /tmp/rarlinux /tmp/rarlinux.tgz
    test -x "vendor/bin/unrar"
  ); then
    echo "unrar vendored : $(vendor/bin/unrar 2>&1 | head -n 1)"
  else
    echo "AVERTISSEMENT : unrar non installe — les archives .rar seront refusees (.zip/.tar.gz/.7z OK)."
  fi
fi

echo "Build termine."
