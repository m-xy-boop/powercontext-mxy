#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if ! command -v node >/dev/null && [[ -x "$HOME/.local/node-v22.16.0-linux-x64/bin/node" ]]; then
  export PATH="$HOME/.local/node-v22.16.0-linux-x64/bin:$PATH"
fi
if ! command -v node >/dev/null; then echo '需要 Node.js 22+' >&2; exit 1; fi
if [[ ! -d node_modules ]]; then npm ci; fi
if [[ ! -f public/models/Xenova/bge-small-zh-v1.5/onnx/model_quantized.onnx ]]; then node scripts/download-model.mjs; fi
npm run build
exec env NODE_ENV=production node server.mjs
