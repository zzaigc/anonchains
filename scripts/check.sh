#!/usr/bin/env bash
# Report Python, dependencies, config and venv status without changing anything.
set -euo pipefail
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/start.sh" --check "$@"
