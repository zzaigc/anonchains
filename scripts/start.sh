#!/usr/bin/env bash
# anonchains one-shot launcher for Linux / macOS.
# Checks Python, creates/uses .venv, verifies and installs dependencies, then
# starts the CLI, runs the offline tests, or only reports the environment.
#
# Usage:
#   ./scripts/start.sh                      # interactive numeric menu
#   ./scripts/start.sh status              # forward args to "python -m anonchains"
#   ./scripts/start.sh --check             # report only (exit 1 if incomplete)
#   ./scripts/start.sh --install           # install dependencies, do not start
#   ./scripts/start.sh --test              # run offline pytest suite
#   ./scripts/start.sh --enhanced          # also install optional dependencies
#   ./scripts/start.sh --no-venv           # use system / active Python
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV_DIR="$ROOT/.venv"
REQUIREMENTS="$ROOT/requirements.txt"
OPTIONAL="$ROOT/requirements-optional.txt"
MAIN_CONFIG="$ROOT/config/anonchains.conf"

CHECK_ONLY=0
INSTALL_ONLY=0
RUN_TEST=0
ENHANCED=0
NO_VENV=0
CLI_ARGS=()

info() { printf '[*] %s\n' "$*"; }
ok()   { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*"; }
die()  { printf '[x] %s\n' "$*" >&2; exit 1; }

usage() {
    cat <<'EOF'
Usage: start.sh [mode|options] [-- <anonchains args>]

Modes / options:
  (none)       start the CLI (default: interactive numeric menu)
  --check      only report environment and dependency status
  --install    create .venv, install dependencies, then exit
  --test       run the offline pytest suite
  --enhanced   also install requirements-optional.txt
  --no-venv    use the current/system Python instead of .venv
  -h, --help   show this help
  --           stop option parsing; everything after is passed to the CLI

Examples:
  ./scripts/start.sh
  ./scripts/start.sh status
  ./scripts/start.sh --install --enhanced
  ./scripts/start.sh -- --config /tmp/anonchains.conf status
EOF
}

while (($#)); do
    case "$1" in
        --check)    CHECK_ONLY=1 ;;
        --install)  INSTALL_ONLY=1 ;;
        --test)     RUN_TEST=1 ;;
        --enhanced) ENHANCED=1 ;;
        --no-venv)  NO_VENV=1 ;;
        -h|--help)  usage; exit 0 ;;
        --)         shift; CLI_ARGS+=("$@"); break ;;
        -*)         printf '[x] Unknown option: %s (try --help)\n' "$1" >&2; exit 2 ;;
        *)          CLI_ARGS+=("$1") ;;
    esac
    shift
done

find_python() {
    if command -v python3 >/dev/null 2>&1; then command -v python3; return 0; fi
    if command -v python  >/dev/null 2>&1; then command -v python;  return 0; fi
    return 1
}

# Git Bash / MSYS on Windows creates Scripts/python.exe instead of bin/python.
venv_python() {
    if [[ -x "$VENV_DIR/bin/python" ]]; then printf '%s\n' "$VENV_DIR/bin/python"
    elif [[ -x "$VENV_DIR/Scripts/python.exe" ]]; then printf '%s\n' "$VENV_DIR/Scripts/python.exe"
    fi
}

# Sets the global PYTHON instead of printing it, so log lines are never
# captured into the interpreter path.
resolve_python() {
    if (( NO_VENV == 0 )) && [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
        PYTHON="$VIRTUAL_ENV/bin/python"
        return 0
    fi
    if (( NO_VENV == 0 )); then
        local existing
        existing="$(venv_python)"
        if [[ -n "$existing" ]]; then PYTHON="$existing"; return 0; fi
        local base
        base="$(find_python)" || die "Python >= 3.10 not found. Install it and add it to PATH."
        if (( CHECK_ONLY == 1 )); then PYTHON="$base"; return 0; fi
        # Already-installed system Python is the fastest, fully offline path.
        if [[ "$(core_status "$base")" == "CORE=OK" ]]; then
            info "Using system Python (core dependencies already present)"
            PYTHON="$base"
            return 0
        fi
        info "Creating virtual environment at .venv ..."
        "$base" -m venv "$VENV_DIR" || die "Could not create the virtual environment."
        local venv_py
        venv_py="$(venv_python)"
        [[ -n "$venv_py" ]] || die "venv created but no interpreter found under .venv"
        ok "Virtual environment ready"
        PYTHON="$venv_py"
        return 0
    fi
    PYTHON="$(find_python)" || die "Python >= 3.10 not found. Install it and add it to PATH."
}

python_label() {
    case "$1" in
        "$VENV_DIR"/*) printf '.venv/%s\n' "${1#"$VENV_DIR"/}" ;;
        *) basename "$1" ;;
    esac
}

check_python_version() {
    local version
    local label
    label="$(python_label "$1")"
    version="$("$1" -c 'import sys; print(".".join(map(str, sys.version_info[:3])))')" \
        || die "Cannot run Python: $label"
    "$1" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
        || die "Python >= 3.10 is required (found $version)."
    ok "Python $version ($label)"
}

probe_dependencies() {
    "$1" - <<'PY'
core = ("aiohttp", "cryptography", "yaml")
optional = ("argon2.low_level", "curl_cffi", "pytest")
def missing(names):
    out = []
    for name in names:
        try:
            __import__(name)
        except Exception:
            out.append(name)
    return out
mc = missing(core)
mo = missing(optional)
print("CORE=" + ("OK" if not mc else "MISSING:" + ",".join(mc)))
print("OPTIONAL=" + ",".join(mo))
PY
}

core_status() {
    local report
    report="$(probe_dependencies "$1" 2>/dev/null || true)"
    printf '%s\n' "$report" | grep '^CORE=' || true
}

cd "$ROOT"
info "Project root: . ($(basename "$ROOT"))"
resolve_python
check_python_version "$PYTHON"
REPORT="$(probe_dependencies "$PYTHON")" || die "Dependency probe failed."
CORE_LINE="$(printf '%s\n' "$REPORT" | grep '^CORE=' || true)"
OPTIONAL_MISSING="$(printf '%s\n' "$REPORT" | sed -n 's/^OPTIONAL=//p')"

if [[ "$CORE_LINE" == "CORE=OK" ]]; then
    ok "Core dependencies: installed"
else
    warn "Core dependencies: ${CORE_LINE:-unknown}"
fi
if [[ -n "$OPTIONAL_MISSING" ]]; then
    info "Optional packages missing: $OPTIONAL_MISSING"
else
    ok "Optional packages: installed"
fi

if (( CHECK_ONLY )); then
    if [[ -d "$VENV_DIR" ]]; then ok "Virtual environment: .venv"; else warn "Virtual environment: not created (run install.sh)"; fi
    for item in "$REQUIREMENTS" "$OPTIONAL" "$MAIN_CONFIG"; do
        if [[ -f "$item" ]]; then ok "$(basename "$item"): found"; else warn "$(basename "$item"): MISSING"; fi
    done
    if [[ "$CORE_LINE" == "CORE=OK" && -f "$MAIN_CONFIG" ]]; then
        ok "Environment check passed"
        exit 0
    fi
    warn "Environment check incomplete; run install.sh"
    exit 1
fi

[[ -f "$REQUIREMENTS" ]] || die "Missing dependency file: requirements.txt"
[[ -f "$OPTIONAL" ]] || die "Missing dependency file: requirements-optional.txt"

if [[ "$CORE_LINE" != "CORE=OK" ]]; then
    info "Installing core dependencies from requirements.txt ..."
    "$PYTHON" -m pip install --disable-pip-version-check -r "$REQUIREMENTS" \
        || die "pip install failed (check network access or proxy settings)."
    ok "Core dependencies installed"
else
    info "Core dependencies already present; skipping install"
fi

if (( ENHANCED )); then
    info "Installing optional dependencies from requirements-optional.txt ..."
    "$PYTHON" -m pip install --disable-pip-version-check -r "$OPTIONAL" \
        || die "Optional dependency install failed."
    ok "Optional dependencies installed"
fi

if (( INSTALL_ONLY )); then
    ok "Install finished. Start with: ./scripts/start.sh"
    exit 0
fi

if (( RUN_TEST )); then
    if ! "$PYTHON" -c 'import pytest' >/dev/null 2>&1; then
        info "pytest not found; installing requirements-optional.txt ..."
        "$PYTHON" -m pip install --disable-pip-version-check -r "$OPTIONAL" \
            || die "Could not install pytest."
    fi
    info "Running offline test suite ..."
    exec "$PYTHON" -m pytest tests -q
fi

info "Starting anonchains ..."
if (( ${#CLI_ARGS[@]} )); then
    exec "$PYTHON" -m anonchains "${CLI_ARGS[@]}"
else
    exec "$PYTHON" -m anonchains
fi
