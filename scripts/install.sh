#!/usr/bin/env bash
# Install anonchains dependencies only (no CLI start).
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start.sh" --install "$@"
