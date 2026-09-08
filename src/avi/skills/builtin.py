"""Built-in core skills for AVI: browser, applications, filesystem, system controls, terminal, media, screenshots."""

import logging
import os
import shutil
import subprocess
import webbrowser
from pathlib import Path
from typing import Any

from avi.apps.resolver import ApplicationResolver
from avi.skills.base import BaseSkill
from avi.skills.models import SkillAction, SkillResult

logger = logging.getLogger("avi.skills")


# ---------------------------------------------------------------------------
# 1. Browser Skill
# ---------------------------------------------------------------------------


class BrowserSkill(BaseSkill):
    """Skill for opening URLs and web navigation across specific or default browsers."""

    name = "browser"
    description = "Open web destinations, websites, and perform web searches."

    def __init__(self, app_resolver: ApplicationResolver | None = None) -> None:
        self.resolver = app_resolver or ApplicationResolver()

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "open_url": SkillAction(
                name="open_url",
                description="Open a URL in a specific browser or the default browser.",
                parameters={"url": {"type": "string"}, "browser": {"type": "string", "optional": True}},
                is_safe=True,
            ),
            "search": SkillAction(
                name="search",
                description="Search the web with a query.",
                parameters={"query": {"type": "string"}, "browser": {"type": "string", "optional": True}},
                is_safe=True,
            ),
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        if action == "open_url":
            url = parameters.get("url", "").strip()
            browser_name = parameters.get("browser", "").strip().lower()

            if not url:
                return SkillResult(success=False, action=action, error="No URL provided.")

            # Ensure scheme
            if not (url.startswith("http://") or url.startswith("https://")):
                url = f"https://{url}"

            # If a specific browser was requested (e.g. 'chrome', 'firefox', 'brave')
            if browser_name:
                res = self.resolver.resolve(browser_name)
                if res.installed and res.executable:
                    try:
                        # Launch browser with URL as argument
                        subprocess.Popen(
                            [res.executable, url],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL,
                            start_new_session=True,
                        )
                        return SkillResult(
                            success=True,
                            action=action,
                            target=url,
                            details={"browser": res.canonical_name, "executable": res.executable},
                            message=f"Opening {url} in {res.canonical_name}.",
                            data={"url": url, "browser": res.canonical_name},
                        )
                    except Exception as e:
                        logger.warning("Failed to launch specific browser %s: %s", browser_name, e)
                        # Fall through to default webbrowser

            # Default system browser fallback
            try:
                webbrowser.open(url)
                return SkillResult(
                    success=True,
                    action=action,
                    target=url,
                    details={"browser": "default"},
                    message=f"Opening {url}.",
                    data={"url": url},
                )
            except Exception as e:
                return SkillResult(
                    success=False,
                    action=action,
                    target=url,
                    error=f"Could not open browser: {e}",
                )

        if action == "search":
            query = parameters.get("query", "").strip()
            browser_name = parameters.get("browser", "")
            import urllib.parse
            url = f"https://www.google.com/search?q={urllib.parse.quote_plus(query)}"
            return self.execute("open_url", {"url": url, "browser": browser_name}, context=context)

        return SkillResult(success=False, action=action, error=f"Unknown browser action: {action}")

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        return result.success and bool(result.target)


# ---------------------------------------------------------------------------
# 2. Application Skill
# ---------------------------------------------------------------------------


class ApplicationSkill(BaseSkill):
    """Skill for resolving and launching native desktop applications."""

    name = "applications"
    description = "Launch and manage desktop applications."

    def __init__(self, app_resolver: ApplicationResolver | None = None) -> None:
        self.resolver = app_resolver or ApplicationResolver()

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "launch": SkillAction(
                name="launch",
                description="Launch a desktop application by name.",
                parameters={"app_name": {"type": "string"}},
                is_safe=True,
            ),
            "is_installed": SkillAction(
                name="is_installed",
                description="Check if an application is installed.",
                parameters={"app_name": {"type": "string"}},
                is_safe=True,
            ),
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        app_name = parameters.get("app_name", "").strip()
        if not app_name:
            return SkillResult(success=False, action=action, error="No application name specified.")

        resolution = self.resolver.resolve(app_name)

        if action == "is_installed":
            return SkillResult(
                success=True,
                action=action,
                target=app_name,
                data={"installed": resolution.installed, "canonical_name": resolution.canonical_name},
                message=f"Application '{resolution.canonical_name}' is {'installed' if resolution.installed else 'not installed'}.",
            )

        if action == "launch":
            if not resolution.installed:
                return SkillResult(
                    success=False,
                    action=action,
                    target=app_name,
                    error=f"Application '{resolution.canonical_name}' is not installed.",
                    message=f"Application '{resolution.canonical_name}' is not installed on this system.",
                )

            ok, msg = self.resolver.launch(resolution)
            return SkillResult(
                success=ok,
                action=action,
                target=resolution.canonical_name,
                details={"executable": resolution.executable, "confidence": resolution.confidence},
                message=msg,
                error=None if ok else msg,
                data={"executable": resolution.executable},
            )

        return SkillResult(success=False, action=action, error=f"Unknown application action: {action}")

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        return result.success


# ---------------------------------------------------------------------------
# 3. Filesystem Skill
# ---------------------------------------------------------------------------


class FilesystemSkill(BaseSkill):
    """Skill for inspecting, creating, moving, and managing files and directories."""

    name = "filesystem"
    description = "Search, create, copy, move, and manage files and folders."

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "search": SkillAction(
                name="search",
                description="Search for files by name or pattern.",
                parameters={"query": {"type": "string"}, "directory": {"type": "string", "optional": True}},
                is_safe=True,
            ),
            "create_dir": SkillAction(
                name="create_dir",
                description="Create a directory.",
                parameters={"path": {"type": "string"}},
                is_safe=True,
            ),
            "copy": SkillAction(
                name="copy",
                description="Copy a file or directory.",
                parameters={"source": {"type": "string"}, "destination": {"type": "string"}},
                is_safe=True,
            ),
            "move": SkillAction(
                name="move",
                description="Move or rename a file or directory.",
                parameters={"source": {"type": "string"}, "destination": {"type": "string"}},
                is_safe=True,
            ),
            "delete": SkillAction(
                name="delete",
                description="Delete a file or directory (destructive).",
                parameters={"path": {"type": "string"}},
                is_safe=False,
                requires_confirmation=True,
            ),
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        if action == "create_dir":
            raw_path = parameters.get("path", "").strip()
            if not raw_path:
                return SkillResult(success=False, action=action, error="No path specified.")
            p = Path(raw_path).expanduser().resolve()
            try:
                p.mkdir(parents=True, exist_ok=True)
                return SkillResult(
                    success=True,
                    action=action,
                    target=str(p),
                    message=f"Directory created: {p}",
                    data={"path": str(p)},
                )
            except Exception as e:
                return SkillResult(success=False, action=action, target=str(p), error=str(e))

        if action == "copy":
            src = parameters.get("source", "").strip()
            dst = parameters.get("destination", "").strip()
            if not src or not dst:
                return SkillResult(success=False, action=action, error="Source and destination required.")
            p_src = Path(src).expanduser().resolve()
            p_dst = Path(dst).expanduser().resolve()
            if not p_src.exists():
                return SkillResult(success=False, action=action, error=f"Source does not exist: {p_src}")
            try:
                if p_src.is_dir():
                    shutil.copytree(str(p_src), str(p_dst))
                else:
                    shutil.copy2(str(p_src), str(p_dst))
                return SkillResult(
                    success=True,
                    action=action,
                    target=str(p_dst),
                    message=f"Copied '{p_src.name}' to {p_dst}.",
                    data={"path": str(p_dst)},
                )
            except Exception as e:
                return SkillResult(success=False, action=action, error=str(e))

        if action == "move":
            src = parameters.get("source", "").strip()
            dst = parameters.get("destination", "").strip()
            if not src or not dst:
                return SkillResult(success=False, action=action, error="Source and destination required.")
            p_src = Path(src).expanduser().resolve()
            p_dst = Path(dst).expanduser().resolve()
            if not p_src.exists():
                return SkillResult(success=False, action=action, error=f"Source does not exist: {p_src}")
            try:
                shutil.move(str(p_src), str(p_dst))
                return SkillResult(
                    success=True,
                    action=action,
                    target=str(p_dst),
                    message=f"Moved '{p_src.name}' to {p_dst}.",
                    data={"path": str(p_dst)},
                )
            except Exception as e:
                return SkillResult(success=False, action=action, error=str(e))

        if action == "delete":
            raw_path = parameters.get("path", "").strip()
            p = Path(raw_path).expanduser().resolve()
            if str(p) in (str(Path.home()), "/", "/root"):
                return SkillResult(success=False, action=action, error="Refusing to delete root or home directory.")
            if not p.exists():
                return SkillResult(success=False, action=action, error=f"File not found: {p}")
            try:
                if p.is_dir():
                    shutil.rmtree(p)
                else:
                    p.unlink()
                return SkillResult(success=True, action=action, target=str(p), message=f"Deleted {p}.")
            except Exception as e:
                return SkillResult(success=False, action=action, target=str(p), error=str(e))

        if action == "search":
            q = parameters.get("query", "").strip()
            base_dir = Path(parameters.get("directory", str(Path.home()))).expanduser().resolve()
            matches = []
            try:
                for root, _, files in os.walk(base_dir):
                    for f in files:
                        if q.lower() in f.lower():
                            matches.append(str(Path(root) / f))
                            if len(matches) >= 10:
                                break
                    if len(matches) >= 10:
                        break
                return SkillResult(
                    success=True,
                    action=action,
                    target=q,
                    details={"count": len(matches)},
                    data={"matches": matches},
                    message=f"Found {len(matches)} matching files for '{q}'.",
                )
            except Exception as e:
                return SkillResult(success=False, action=action, error=str(e))

        return SkillResult(success=False, action=action, error=f"Unknown filesystem action: {action}")

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        if not result.success:
            return False
        if action == "create_dir":
            return Path(result.target).is_dir()
        if action == "move":
            return Path(result.target).exists()
        if action == "delete":
            return not Path(result.target).exists()
        return True


# ---------------------------------------------------------------------------
# 4. System Controls Skill (Volume & Audio)
# ---------------------------------------------------------------------------


class SystemControlsSkill(BaseSkill):
    """Skill for adjusting volume, muting, and querying audio state."""

    name = "system_controls"
    description = "Control system volume, audio state, and system settings."

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "get_volume": SkillAction(name="get_volume", description="Get current volume.", is_safe=True),
            "set_volume": SkillAction(
                name="set_volume",
                description="Set volume percentage.",
                parameters={"level": {"type": "integer"}},
                is_safe=True,
            ),
            "mute": SkillAction(name="mute", description="Mute audio.", is_safe=True),
            "unmute": SkillAction(name="unmute", description="Unmute audio.", is_safe=True),
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        from avi.capabilities.desktop.system_controls import VolumeGetCapability, VolumeSetCapability

        if action == "get_volume":
            cap_res = VolumeGetCapability().execute()
            return SkillResult(
                success=cap_res.success,
                action=action,
                data=cap_res.data,
                message=cap_res.message,
                error=cap_res.error,
            )

        if action in ("set_volume", "mute", "unmute"):
            sub_action = "mute" if action == "mute" else ("unmute" if action == "unmute" else "set")
            level = parameters.get("level")
            delta = parameters.get("delta")
            payload: dict[str, Any] = {"action": sub_action}
            if level is not None:
                payload["level"] = level
            if delta is not None:
                payload["delta"] = delta
            cap_res = VolumeSetCapability().execute(**payload)
            return SkillResult(
                success=cap_res.success,
                action=action,
                target=str(level) if level is not None else sub_action,
                data=cap_res.data,
                message=cap_res.message,
                error=cap_res.error,
            )

        return SkillResult(success=False, action=action, error=f"Unknown system control action: {action}")

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        return result.success


# ---------------------------------------------------------------------------
# 5. Screenshot Skill
# ---------------------------------------------------------------------------


class ScreenshotSkill(BaseSkill):
    """Skill for capturing display screenshots."""

    name = "screenshots"
    description = "Capture user screen and save to disk."

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "capture": SkillAction(
                name="capture",
                description="Capture a screenshot of the active display.",
                parameters={"destination": {"type": "string", "optional": True}},
                is_safe=True,
            )
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        from avi.capabilities.desktop.screenshot import ScreenshotCapability

        dest = parameters.get("destination")
        payload = {"destination_dir": dest} if dest else {}
        cap_res = ScreenshotCapability().execute(payload)

        path = cap_res.data.get("path") if cap_res.data else ""
        return SkillResult(
            success=cap_res.success,
            action=action,
            target=path,
            data=cap_res.data,
            message=cap_res.message,
            error=cap_res.error,
        )

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        if not result.success or not result.target:
            return False
        p = Path(result.target)
        return p.is_file() and p.stat().st_size > 0


# ---------------------------------------------------------------------------
# 6. Terminal Skill
# ---------------------------------------------------------------------------


class TerminalSkill(BaseSkill):
    """Skill for executing shell commands with safety checks."""

    name = "terminal"
    description = "Execute shell commands."

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "execute": SkillAction(
                name="execute",
                description="Execute a shell command line.",
                parameters={"command": {"type": "string"}},
                is_safe=True,
            )
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        cmd = parameters.get("command", "").strip()
        if not cmd:
            return SkillResult(success=False, action=action, error="Empty command.")
        import shlex

        try:
            tokens = shlex.split(cmd)
            res = subprocess.run(
                tokens,
                shell=False,
                capture_output=True,
                text=True,
                timeout=15.0,
            )
            ok = (res.returncode == 0)
            return SkillResult(
                success=ok,
                action=action,
                target=cmd,
                details={"exit_code": res.returncode, "stdout": res.stdout, "stderr": res.stderr},
                data={"stdout": res.stdout, "stderr": res.stderr, "exit_code": res.returncode},
                message=res.stdout if ok else (res.stderr or f"Command failed with exit code {res.returncode}"),
                error=None if ok else f"Exit code {res.returncode}",
            )
        except Exception as e:
            return SkillResult(success=False, action=action, target=cmd, error=str(e))

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        return result.success


# ---------------------------------------------------------------------------
# 7. Media Skill
# ---------------------------------------------------------------------------


class MediaSkill(BaseSkill):
    """Skill for controlling media playback (play, pause, next, previous)."""

    name = "media"
    description = "Control media playback across system players."

    def get_actions(self) -> dict[str, SkillAction]:
        return {
            "control": SkillAction(
                name="control",
                description="Control media playback.",
                parameters={"action": {"type": "string"}},
                is_safe=True,
            )
        }

    def execute(
        self,
        action: str,
        parameters: dict[str, Any],
        context: dict[str, Any] | None = None,
    ) -> SkillResult:
        from avi.capabilities.desktop.system_controls import MediaControlCapability

        sub_act = parameters.get("action", "play")
        cap_res = MediaControlCapability().execute(action=sub_act)
        return SkillResult(
            success=cap_res.success,
            action=action,
            target=sub_act,
            data=cap_res.data,
            message=cap_res.message,
            error=cap_res.error,
        )

    def verify(self, action: str, parameters: dict[str, Any], result: SkillResult) -> bool:
        return result.success
