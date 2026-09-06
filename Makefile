# AVI Makefile — Developer convenience targets
# Requires: uv (recommended) or Python 3.10+ with pip

PYTHON   ?= python3
UV       ?= uv
SRC      := src
TESTS    := tests
DIST     := dist

.PHONY: help setup test lint format build clean release verify-version

help:  ## Show this help message
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
	    | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ── Environment ─────────────────────────────────────────────────────────────

setup:  ## Create .venv and install project with dev dependencies
	$(UV) venv
	$(UV) pip install -e ".[dev]"
	@echo "✓ Virtual environment ready. Run: source .venv/bin/activate"

setup-pip:  ## Install without uv (plain pip)
	$(PYTHON) -m venv .venv
	.venv/bin/pip install -e ".[dev]"

# ── Testing ──────────────────────────────────────────────────────────────────

test:  ## Run the full test suite
	$(UV) run pytest

test-fast:  ## Run tests, stop on first failure
	$(UV) run pytest -x --tb=short

test-verbose:  ## Run tests with verbose output
	$(UV) run pytest -v

test-cov:  ## Run tests with coverage (requires pytest-cov)
	$(UV) run pytest --cov=$(SRC)/avi --cov-report=term-missing

test-security:  ## Run security invariant tests only
	$(UV) run pytest tests/unit/test_gateway.py -k "security" -v

# ── Code Quality ──────────────────────────────────────────────────────────────

lint:  ## Run ruff lint check
	$(UV) run ruff check $(SRC)/ $(TESTS)/

format:  ## Auto-format code with ruff
	$(UV) run ruff format $(SRC)/ $(TESTS)/

format-check:  ## Check formatting without writing files
	$(UV) run ruff format --check $(SRC)/ $(TESTS)/

typecheck:  ## Run mypy type checking
	$(UV) run mypy $(SRC)/avi/

# ── Build & Distribution ───────────────────────────────────────────────────────

verify-version:  ## Ensure pyproject.toml and __init__.py versions match
	@TOML_VER=$$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2); \
	INIT_VER=$$(grep '^__version__ = ' $(SRC)/avi/__init__.py | cut -d'"' -f2); \
	echo "pyproject.toml: $$TOML_VER"; \
	echo "__init__.py:    $$INIT_VER"; \
	[ "$$TOML_VER" = "$$INIT_VER" ] && echo "✓ Versions match" || (echo "✗ Version mismatch!" && exit 1)

build: verify-version  ## Build wheel and sdist into dist/
	$(UV) build
	@echo "✓ Built distributions:"
	@ls -lh $(DIST)/

clean:  ## Remove build artifacts and caches
	rm -rf $(DIST)/ .pytest_cache/ .ruff_cache/ .mypy_cache/
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

clean-all: clean  ## Remove .venv too (full reset)
	rm -rf .venv/

# ── Release ───────────────────────────────────────────────────────────────────

release: test build  ## Full release: test → build. Tag manually with: git tag v<VERSION>
	@VERSION=$$(grep '^version' pyproject.toml | head -1 | cut -d'"' -f2); \
	echo ""; \
	echo "══════════════════════════════════════════════════"; \
	echo "  Release build complete: v$$VERSION"; \
	echo "  To publish to GitHub + PyPI:"; \
	echo "    git tag v$$VERSION"; \
	echo "    git push origin v$$VERSION"; \
	echo "══════════════════════════════════════════════════"

# ── Development ───────────────────────────────────────────────────────────────

dev: setup  ## Alias for setup

run:  ## Run avi CLI
	$(UV) run avi

run-ui:  ## Launch AVI desktop popup (requires GTK4)
	$(UV) run avi ui

run-gateway:  ## Launch AVI MCP/JSON-RPC gateway on stdio
	$(UV) run avi gateway --transport stdio

benchmark:  ## Quick FastPath latency benchmark
	@$(UV) run python3 -c 'import time, sys; sys.path.insert(0, "src"); from avi.core.fastpath import FastPathRegistry; reg = FastPathRegistry(); N = 10000; prompts = ["what files are here?", "show git status", "what branch am i on?"]; t0 = time.perf_counter(); [reg.resolve(prompts[i % len(prompts)]) for i in range(N)]; elapsed = time.perf_counter() - t0; print(f"FastPath: {elapsed/N*1e6:.2f} µs/query over {N} queries")'
