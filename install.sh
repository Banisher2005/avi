#!/usr/bin/env bash
# install.sh — One-shot AVI installer for Linux
# Usage:   curl -fsSL https://raw.githubusercontent.com/Banisher2005/avi/main/install.sh | bash
# Or:      bash install.sh [--dev] [--pipx] [--prefix /usr/local]
#
# Installs the 'avi' CLI into:
#   - $HOME/.local/bin (default, via uv tool or pipx)
#   - /usr/local/bin   (system-wide, when --prefix /usr/local passed)
#   - editable venv    (when --dev is passed, for contributors)
#
# Requirements: Python >= 3.10, one of: uv | pipx | pip

set -euo pipefail

AVI_REPO="https://github.com/Banisher2005/avi.git"
AVI_VERSION="0.2.0"
INSTALL_MODE="tool"   # tool | pipx | dev
PREFIX=""

_info()  { printf '\033[1;34m[avi]\033[0m %s\n' "$*"; }
_ok()    { printf '\033[1;32m[avi]\033[0m %s\n' "$*"; }
_warn()  { printf '\033[1;33m[avi]\033[0m %s\n' "$*" >&2; }
_die()   { printf '\033[1;31m[avi] error:\033[0m %s\n' "$*" >&2; exit 1; }

# ── Parse arguments ────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dev)    INSTALL_MODE="dev"; shift ;;
        --pipx)   INSTALL_MODE="pipx"; shift ;;
        --prefix) PREFIX="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: install.sh [--dev] [--pipx] [--prefix /usr/local]"
            exit 0
            ;;
        *) _die "Unknown option: $1" ;;
    esac
done

# ── System checks ─────────────────────────────────────────────────────────
_info "AVI Installer v${AVI_VERSION}"
_info "Mode: ${INSTALL_MODE}"

# Require Linux
[[ "$(uname -s)" == "Linux" ]] || _die "AVI requires Linux (got $(uname -s))"

# Require Python >= 3.10
PYTHON=""
for candidate in python3 python3.14 python3.13 python3.12 python3.11 python3.10; do
    if command -v "$candidate" &>/dev/null; then
        ver=$("$candidate" -c "import sys; print(sys.version_info[:2])")
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)" 2>/dev/null; then
            PYTHON="$candidate"
            break
        fi
    fi
done
[[ -n "$PYTHON" ]] || _die "Python 3.10+ is required. Install it first: https://python.org/downloads"
_info "Using Python: $PYTHON ($("$PYTHON" --version))"

# ── Install ────────────────────────────────────────────────────────────────

if [[ "$INSTALL_MODE" == "tool" ]]; then
    # Preferred: uv tool install (isolated, zero venv management)
    if command -v uv &>/dev/null; then
        _info "Installing via 'uv tool install' (recommended)..."
        if [[ -n "$PREFIX" ]]; then
            # Install into a specific prefix using pip into prefix
            _warn "--prefix is not supported with uv tool; falling back to pip"
            INSTALL_MODE="pip"
        else
            uv tool install "avi==${AVI_VERSION}" 2>/dev/null \
                || uv tool install "git+${AVI_REPO}" \
                || _die "uv tool install failed"
            _ok "AVI installed via uv. Make sure ~/.local/bin is on your PATH."
            _ok "Run: avi --version"
            exit 0
        fi
    fi

    # Fallback 1: pipx
    if [[ "$INSTALL_MODE" == "tool" ]] && command -v pipx &>/dev/null; then
        INSTALL_MODE="pipx"
    fi

    # Fallback 2: pip --user
    if [[ "$INSTALL_MODE" == "tool" ]]; then
        _warn "Neither 'uv' nor 'pipx' found. Falling back to pip --user."
        INSTALL_MODE="pip"
    fi
fi

if [[ "$INSTALL_MODE" == "pipx" ]]; then
    _info "Installing via pipx..."
    pipx install "avi==${AVI_VERSION}" 2>/dev/null \
        || pipx install "git+${AVI_REPO}" \
        || _die "pipx install failed"
    _ok "AVI installed via pipx. Run: avi --version"
    exit 0
fi

if [[ "$INSTALL_MODE" == "pip" ]]; then
    DEST="${PREFIX:-${HOME}/.local}"
    _info "Installing via pip into ${DEST}..."
    if [[ -n "$PREFIX" ]]; then
        "$PYTHON" -m pip install --prefix "$PREFIX" "avi==${AVI_VERSION}" 2>/dev/null \
            || "$PYTHON" -m pip install --prefix "$PREFIX" "git+${AVI_REPO}"
    else
        "$PYTHON" -m pip install --user "avi==${AVI_VERSION}" 2>/dev/null \
            || "$PYTHON" -m pip install --user "git+${AVI_REPO}"
    fi
    _ok "AVI installed. Make sure ${DEST}/bin is on your PATH."
    _ok "Run: avi --version"
    exit 0
fi

if [[ "$INSTALL_MODE" == "dev" ]]; then
    _info "Developer install (editable, with tests)..."
    VENV_DIR="${PWD}/.venv"

    if [[ ! -f "pyproject.toml" ]]; then
        _info "Cloning repository..."
        git clone --depth=1 "$AVI_REPO" avi && cd avi
    fi

    if command -v uv &>/dev/null; then
        uv venv
        uv pip install -e ".[dev]"
        _ok "Dev install complete. Activate with: source .venv/bin/activate"
    else
        "$PYTHON" -m venv "$VENV_DIR"
        "$VENV_DIR/bin/pip" install -e ".[dev]"
        _ok "Dev install complete. Activate with: source ${VENV_DIR}/bin/activate"
    fi
    exit 0
fi
