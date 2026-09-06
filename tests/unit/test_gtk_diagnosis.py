"""Unit tests for GTK environment diagnostics."""

import sys
from unittest.mock import patch

import pytest

from avi.ui.detector import GtkEnvironmentReport, check_display_server, check_gtk4_in_python, diagnose_gtk_environment


class TestGtkEnvironmentDiagnosis:
    def test_check_display_server(self):
        with patch.dict("os.environ", {"WAYLAND_DISPLAY": "wayland-0"}):
            assert check_display_server() is True
        with patch.dict("os.environ", {"DISPLAY": ":0"}, clear=True):
            assert check_display_server() is True
        with patch.dict("os.environ", {}, clear=True):
            assert check_display_server() is False

    def test_diagnose_gtk_when_gtk_available(self):
        with (
            patch("avi.ui.detector.check_display_server", return_value=True),
            patch("builtins.__import__", return_value=None),
        ):
            report = diagnose_gtk_environment()
            assert isinstance(report, GtkEnvironmentReport)
            assert report.display_available is True

    def test_diagnose_identifies_venv_abi_mismatch(self):
        # Simulate running in a venv without gi, but /usr/bin/python3 has GTK4
        with (
            patch("avi.ui.detector.check_display_server", return_value=True),
            patch("avi.ui.detector.check_gtk4_in_python", return_value=(True, "3.14.4")),
            patch.object(sys, "prefix", "/home/user/project/.venv"),
            patch.object(sys, "base_prefix", "/usr"),
        ):
            report = diagnose_gtk_environment()
            if not report.gtk4_available:
                assert report.system_python_has_gtk4 is True
                assert "PyGObject" in report.diagnostic_message
                assert "virtual environment" in report.diagnostic_message
