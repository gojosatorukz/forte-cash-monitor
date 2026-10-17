#!/usr/bin/env bash
# Local-only llama-server for the VLM spike (binds 127.0.0.1).
# Usage: ./run_server.sh [model.gguf] [mmproj.gguf]   extra flags via EXTRA env
D=$(cd "$(dirname "$0")" && pwd)
B=$D/bin/llama-b11707
export LD_LIBRARY_PATH=$B:$D/bin/cudart-llama-b11707-bin-ubuntu-cuda-12.8-x64
M=${1:-$D/models/Qwen3VL-4B-Instruct-Q4_K_M.gguf}
P=${2:-$D/models/mmproj-Qwen3VL-4B-Instruct-F16.gguf}
exec "$B/llama-server" -m "$M" --mmproj "$P" \
  -ngl 99 -c ${CTX:-8192} -ctk q8_0 -ctv q8_0 -np 1 -fa on --no-mmproj-auto \
  --host 127.0.0.1 --port 8090 --temp 0 $EXTRA
