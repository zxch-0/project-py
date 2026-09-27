#!/usr/bin/env bash
# Build Render — zach-runner
# 1) Dependances Python  2) Node.js vendored (pour les scripts .js)
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

echo "Build termine."
