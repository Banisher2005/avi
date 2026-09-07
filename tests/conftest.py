"""Global pytest fixtures and configuration for AVI test suite."""

import pytest


@pytest.fixture(autouse=True)
def isolate_session_state(tmp_path, monkeypatch):
    """Ensure every test runs with an isolated session state directory."""
    state_dir = tmp_path / "avi_state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state_dir))
