#!/usr/bin/env bash
# Run the offline pytest suite (installs pytest if missing).
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start.sh" --test "$@"
