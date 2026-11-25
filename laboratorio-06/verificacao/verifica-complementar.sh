#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.." || exit 2
for comando in aws python3 terraform; do
  command -v "$comando" >/dev/null 2>&1 || { echo "FALHA  comando ausente: $comando" >&2; exit 2; }
done
exec python3 verificacao/complementar.py "$@"
