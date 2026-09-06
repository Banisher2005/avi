"""Diagnostics for GTK4 runtime environment and Python ABI compatibility."""

import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class GtkEnvironmentReport:
    """Detailed diagnostic status of Python, virtualenv, and GTK availability."""

    python_executable: str
    python_version: str
    is_virtualenv: bool
    gi_importable: bool
    gtk4_available: bool
    display_available: bool
    system_python_has_gtk4: bool
    system_python_path: str | None
    system_python_version: str | None
    diagnostic_message: str


def check_display_server() -> bool:
    """Check if Wayland or X11 display session is active."""
    return bool(os.getenv("WAYLAND_DISPLAY") or os.getenv("DISPLAY"))


def check_gtk4_in_python(python_bin: str) -> tuple[bool, str]:
    """Test if a given Python executable can successfully import and initialize GTK4."""
    if not shutil.which(python_bin) and not os.path.exists(python_bin):
        return False, ""

    cmd = [
        python_bin,
        "-c",
        "import sys, gi; gi.require_version('Gtk', '4.0'); from gi.repository import Gtk; print(sys.version.split()[0])",
    ]
    try:
        proc = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.0,
            check=False,
        )
        if proc.returncode == 0:
            version_str = proc.stdout.strip()
            return True, version_str
    except Exception:
        pass
    return False, ""


def diagnose_gtk_environment() -> GtkEnvironmentReport:
    """Analyze current Python interpreter and system for GTK4 capability."""
    current_exe = sys.executable
    current_ver = platform.python_version()
    base_prefix = getattr(sys, "base_prefix", sys.prefix)
    is_venv = sys.prefix != base_prefix
    display_ok = check_display_server()

    # 1. Test current interpreter
    gi_ok = False
    gtk4_ok = False

    try:
        import gi

        gi_ok = True
        gi.require_version("Gtk", "4.0")
        gtk4_ok = True
    except Exception:
        gtk4_ok = False

    # 2. If current fails, inspect common system Python interpreters
    sys_has_gtk4 = False
    sys_py_path = None
    sys_py_ver = None

    if not gtk4_ok:
        candidates = [
            "/usr/bin/python3",
            "/usr/bin/python3.14",
            "/usr/bin/python3.13",
            "/usr/bin/python3.12",
        ]
        # Also check base prefix if in venv
        if is_venv:
            base_bin = Path(base_prefix) / "bin" / "python3"
            if base_bin.exists() and str(base_bin) not in candidates:
                candidates.insert(0, str(base_bin))

        for cand in candidates:
            ok, ver = check_gtk4_in_python(cand)
            if ok:
                sys_has_gtk4 = True
                sys_py_path = cand
                sys_py_ver = ver
                break

    # 3. Formulate diagnostic message
    lines = []
    if gtk4_ok and display_ok:
        msg = f"GTK4 is fully available under Python {current_ver}."
    elif gtk4_ok and not display_ok:
        msg = "No display server detected (WAYLAND_DISPLAY or DISPLAY not set). Cannot launch desktop UI."
    elif sys_has_gtk4 and sys_py_path:
        lines.append(
            f"PyGObject (GTK4) is installed system-wide for {sys_py_path} (Python {sys_py_ver}),"
        )
        lines.append(
            f"but AVI is running inside an isolated Python {current_ver} virtual environment:"
        )
        lines.append(f"  {current_exe}")
        lines.append("")
        lines.append("C-extension modules like '_gi' cannot cross Python ABI versions.")
        lines.append("")
        lines.append("To launch the UI using the system Python interpreter:")
        lines.append(f"  PYTHONPATH=src {sys_py_path} -m avi.cli ui")
        lines.append("Or run:")
        lines.append("  avi ui --use-system-python")
        lines.append("Or recreate your virtual environment with system site-packages:")
        lines.append("  python3 -m venv --system-site-packages .venv")
        msg = "\n".join(lines)
    else:
        lines.append("Error: GTK4 (PyGObject) is not installed on this system.")
        lines.append("Install it with your system package manager:")
        lines.append("  sudo apt install python3-gi gir1.2-gtk-4.0   # Debian / Ubuntu")
        lines.append("  sudo pacman -S python-gobject gtk4            # Arch Linux")
        lines.append("  sudo dnf install python3-gobject gtk4         # Fedora")
        msg = "\n".join(lines)

    return GtkEnvironmentReport(
        python_executable=current_exe,
        python_version=current_ver,
        is_virtualenv=is_venv,
        gi_importable=gi_ok,
        gtk4_available=gtk4_ok,
        display_available=display_ok,
        system_python_has_gtk4=sys_has_gtk4,
        system_python_path=sys_py_path,
        system_python_version=sys_py_ver,
        diagnostic_message=msg,
    )
