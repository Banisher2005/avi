"""Packaging integrity tests for AVI v0.2.0.

These tests verify:
  - Version consistency between pyproject.toml and __init__.py
  - All public modules import cleanly
  - CLI entry-point works and returns a sensible exit code
  - Package metadata is well-formed
  - install.sh and Makefile exist and have required content
  - GitHub Actions workflow files exist
"""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

# Project root = three levels up from this file (tests/unit/ → tests/ → project_root)
PROJECT_ROOT = Path(__file__).parent.parent.parent


# ============================================================
# 1. Version consistency
# ============================================================

class TestVersionConsistency:
    """pyproject.toml version must equal avi.__version__."""

    def _read_toml_version(self) -> str:
        toml = (PROJECT_ROOT / "pyproject.toml").read_text()
        for line in toml.splitlines():
            line = line.strip()
            if line.startswith("version") and "=" in line:
                return line.split("=", 1)[1].strip().strip('"').strip("'")
        raise ValueError("version not found in pyproject.toml")

    def test_versions_match(self):
        import avi
        toml_ver = self._read_toml_version()
        assert avi.__version__ == toml_ver, (
            f"pyproject.toml version {toml_ver!r} != avi.__version__ {avi.__version__!r}"
        )

    def test_version_is_semver(self):
        import avi
        parts = avi.__version__.split(".")
        assert len(parts) == 3, f"Expected semver x.y.z, got {avi.__version__!r}"
        assert all(p.isdigit() for p in parts), f"Non-numeric version part in {avi.__version__!r}"

    def test_version_minimum(self):
        """Must be >= 0.2.0 (Phase 10 baseline)."""
        import avi
        major, minor, patch = (int(x) for x in avi.__version__.split(".")[:3])
        assert (major, minor, patch) >= (0, 2, 0), f"Version {avi.__version__} too old"


# ============================================================
# 2. Module imports
# ============================================================

CORE_MODULES = [
    "avi",
    "avi.cli",
    "avi.config",
    "avi.context",
    "avi.core.fastpath",
    "avi.core.router",
    "avi.core.session",
    "avi.execution",
    "avi.execution.executor",
    "avi.execution.models",
    "avi.gateway",
    "avi.gateway.core",
    "avi.gateway.jsonrpc",
    "avi.gateway.transports",
    "avi.gateway.models",
    "avi.hotkey",
    "avi.hotkey.detector",
    "avi.hotkey.service",
    "avi.providers",
    "avi.providers.base",
    "avi.providers.models",
    "avi.providers.registry",
    "avi.providers.ollama",
    "avi.safety",
    "avi.safety.engine",
    "avi.safety.models",
    "avi.safety.parser",
    "avi.tools",
    "avi.tools.base",
    "avi.tools.filesystem",
    "avi.tools.git",
    "avi.tools.registry",
    "avi.tools.system",
    "avi.ui",
    "avi.ui.app",
    "avi.ui.window",
]


@pytest.mark.parametrize("module_name", CORE_MODULES)
def test_module_importable(module_name: str):
    """Every AVI module must be importable without side-effects."""
    mod = importlib.import_module(module_name)
    assert mod is not None


# ============================================================
# 3. CLI entry-point
# ============================================================

class TestCliEntryPoint:
    """The 'avi' script entry-point must work."""

    def test_version_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "avi.cli", "--version"],
            capture_output=True,
            text=True,
        )
        # Accept exit 0 or 2 (argparse prints and exits)
        output = result.stdout + result.stderr
        assert "avi" in output.lower() or "0.2" in output, (
            f"Expected version output, got: {output!r}"
        )

    def test_help_flag(self):
        result = subprocess.run(
            [sys.executable, "-m", "avi.cli", "--help"],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "avi" in result.stdout.lower()

    def test_main_callable(self):
        from avi.cli import main
        assert callable(main)

    def test_main_returns_int(self):
        """main(['--help']) exits via SystemExit with an integer code."""
        from avi.cli import main
        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        # argparse --help exits with 0
        assert isinstance(exc_info.value.code, int)
        assert exc_info.value.code == 0


# ============================================================
# 4. Package files
# ============================================================

class TestPackageFiles:
    """Required distribution files must exist and have expected content."""

    def test_pyproject_toml_exists(self):
        assert (PROJECT_ROOT / "pyproject.toml").is_file()

    def test_changelog_exists(self):
        assert (PROJECT_ROOT / "CHANGELOG.md").is_file()

    def test_changelog_has_current_version(self):
        import avi
        changelog = (PROJECT_ROOT / "CHANGELOG.md").read_text()
        assert avi.__version__ in changelog, (
            f"CHANGELOG.md must contain version {avi.__version__}"
        )

    def test_license_exists(self):
        assert (PROJECT_ROOT / "LICENSE").is_file()

    def test_readme_exists(self):
        assert (PROJECT_ROOT / "README.md").is_file()

    def test_install_sh_exists(self):
        assert (PROJECT_ROOT / "install.sh").is_file()

    def test_install_sh_has_safety_flags(self):
        content = (PROJECT_ROOT / "install.sh").read_text()
        assert "set -euo pipefail" in content, "install.sh must use set -euo pipefail"

    def test_makefile_exists(self):
        assert (PROJECT_ROOT / "Makefile").is_file()

    def test_makefile_has_test_target(self):
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "test:" in content

    def test_makefile_has_build_target(self):
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "build:" in content

    def test_makefile_has_release_target(self):
        content = (PROJECT_ROOT / "Makefile").read_text()
        assert "release:" in content

    def test_pyproject_urls(self):
        content = (PROJECT_ROOT / "pyproject.toml").read_text()
        assert "[project.urls]" in content
        assert "Homepage" in content
        assert "Changelog" in content

    def test_pyproject_beta_classifier(self):
        content = (PROJECT_ROOT / "pyproject.toml").read_text()
        assert "Development Status :: 4 - Beta" in content

    def test_pyproject_build_system(self):
        content = (PROJECT_ROOT / "pyproject.toml").read_text()
        assert "hatchling" in content


# ============================================================
# 5. GitHub Actions workflows
# ============================================================

class TestGitHubWorkflows:
    """CI and Release workflow files must exist and be well-formed YAML."""

    def _workflows_dir(self) -> Path:
        return PROJECT_ROOT / ".github" / "workflows"

    def test_ci_workflow_exists(self):
        assert (self._workflows_dir() / "ci.yml").is_file()

    def test_release_workflow_exists(self):
        assert (self._workflows_dir() / "release.yml").is_file()

    def test_ci_workflow_has_test_job(self):
        content = (self._workflows_dir() / "ci.yml").read_text()
        assert "pytest" in content

    def test_ci_workflow_multi_python(self):
        content = (self._workflows_dir() / "ci.yml").read_text()
        assert "3.10" in content
        assert "3.11" in content
        assert "3.12" in content

    def test_release_workflow_triggers_on_tag(self):
        content = (self._workflows_dir() / "release.yml").read_text()
        assert "tags:" in content
        # Must trigger on version tags
        assert "v[0-9]" in content

    def test_release_workflow_has_pypi_publish(self):
        content = (self._workflows_dir() / "release.yml").read_text()
        assert "pypi-publish" in content or "gh-action-pypi-publish" in content

    def test_release_workflow_has_github_release(self):
        content = (self._workflows_dir() / "release.yml").read_text()
        assert "github-release" in content or "softprops/action-gh-release" in content


# ============================================================
# 6. Build sanity (wheel can be built)
# ============================================================

class TestBuildSanity:
    """Verify the wheel build produces expected outputs."""

    def test_uv_build_available(self):
        """uv must be available for CI builds."""
        result = subprocess.run(["uv", "--version"], capture_output=True, text=True)
        assert result.returncode == 0, "uv is not installed / not on PATH"
        assert "uv" in result.stdout.lower()

    def test_wheel_builds_successfully(self, tmp_path):
        """uv build must produce a .whl and .tar.gz."""
        result = subprocess.run(
            ["uv", "build", "--out-dir", str(tmp_path)],
            capture_output=True,
            text=True,
            cwd=PROJECT_ROOT,
        )
        assert result.returncode == 0, f"uv build failed:\n{result.stderr}"
        wheels = list(tmp_path.glob("*.whl"))
        sdists = list(tmp_path.glob("*.tar.gz"))
        assert wheels, "No .whl produced by uv build"
        assert sdists, "No .tar.gz produced by uv build"

    def test_wheel_contains_avi_ui(self, tmp_path):
        """Wheel must include the ui subpackage."""
        import zipfile

        subprocess.run(
            ["uv", "build", "--out-dir", str(tmp_path)],
            capture_output=True,
            cwd=PROJECT_ROOT,
            check=True,
        )
        wheel = list(tmp_path.glob("*.whl"))[0]
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
        ui_files = [n for n in names if "avi/ui/" in n]
        assert ui_files, f"avi/ui/ not found in wheel. Contents sample: {names[:15]}"

    def test_wheel_contains_avi_gateway(self, tmp_path):
        """Wheel must include the gateway subpackage."""
        import zipfile

        subprocess.run(
            ["uv", "build", "--out-dir", str(tmp_path)],
            capture_output=True,
            cwd=PROJECT_ROOT,
            check=True,
        )
        wheel = list(tmp_path.glob("*.whl"))[0]
        with zipfile.ZipFile(wheel) as zf:
            names = zf.namelist()
        gw_files = [n for n in names if "avi/gateway/" in n]
        assert gw_files, "avi/gateway/ not found in wheel"
