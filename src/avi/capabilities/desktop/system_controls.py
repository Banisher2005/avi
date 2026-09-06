"""Linux system audio volume and media player capabilities."""

import re
import shutil
import subprocess
from typing import Any

from avi.capabilities.models import (
    BaseCapability,
    CapabilityResult,
    DataClassification,
    ExecutionStatus,
)
from avi.safety.models import ActionCategory


class VolumeGetCapability(BaseCapability):
    """Inspects the current system audio output volume level."""

    name = "system.volume.get"
    description = "Get current system audio volume level percentage and mute state."
    input_schema = {"type": "object", "properties": {}}
    risk_category = ActionCategory.READ_ONLY
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(self, **kwargs: Any) -> CapabilityResult:
        """Query current volume using wpctl, pactl, or amixer."""
        # 1. Try wpctl (PipeWire / WirePlumber standard)
        if shutil.which("wpctl"):
            try:
                proc = subprocess.run(
                    ["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    out = proc.stdout.strip()
                    # e.g. "Volume: 0.65" or "Volume: 0.65 [MUTED]"
                    match = re.search(r"Volume:\s+([0-9.]+)", out)
                    if match:
                        vol_pct = int(round(float(match.group(1)) * 100))
                        is_muted = "[MUTED]" in out
                        mute_str = " (muted)" if is_muted else ""
                        return CapabilityResult(
                            success=True,
                            status=ExecutionStatus.SUCCESS,
                            data={
                                "volume_percent": vol_pct,
                                "level": vol_pct,
                                "is_muted": is_muted,
                                "muted": is_muted,
                            },
                            message=f"System volume is at {vol_pct}%{mute_str}.",
                            classification=self.data_classification,
                        )
            except Exception:
                pass

        # 2. Try amixer (ALSA universal fallback)
        if shutil.which("amixer"):
            try:
                proc = subprocess.run(
                    ["amixer", "sget", "Master"],
                    shell=False,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    check=False,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    out = proc.stdout.strip()
                    match = re.search(r"\[(\d+)%\]\s+\[(on|off)\]", out)
                    if match:
                        vol_pct = int(match.group(1))
                        is_muted = match.group(2) == "off"
                        mute_str = " (muted)" if is_muted else ""
                        return CapabilityResult(
                            success=True,
                            status=ExecutionStatus.SUCCESS,
                            data={
                                "volume_percent": vol_pct,
                                "level": vol_pct,
                                "is_muted": is_muted,
                                "muted": is_muted,
                            },
                            message=f"System volume is at {vol_pct}%{mute_str}.",
                            classification=self.data_classification,
                        )
            except Exception:
                pass

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error="Could not query audio volume on this system.",
            message="Unable to detect audio subsystem.",
            classification=self.data_classification,
        )


class VolumeSetCapability(BaseCapability):
    """Sets system audio output volume level."""

    name = "system.volume.set"
    description = "Set the system audio output volume level to a specified percentage (0-100)."
    input_schema = {
        "type": "object",
        "properties": {
            "level": {
                "type": "integer",
                "description": "Desired volume percentage (0 to 100).",
                "minimum": 0,
                "maximum": 100,
            }
        },
        "required": ["level"],
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(self, level: int = 50, **kwargs: Any) -> CapabilityResult:
        """Set volume using wpctl, pactl, or amixer."""
        clamped_level = max(0, min(100, int(level)))

        # 1. Try wpctl
        if shutil.which("wpctl"):
            try:
                fraction = clamped_level / 100.0
                proc = subprocess.run(
                    ["wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{fraction:.2f}"],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        data={"volume_percent": clamped_level, "level": clamped_level},
                        message=f"Volume set to {clamped_level}%.",
                        classification=self.data_classification,
                    )
            except Exception:
                pass

        # 2. Try amixer
        if shutil.which("amixer"):
            try:
                proc = subprocess.run(
                    ["amixer", "sset", "Master", f"{clamped_level}%"],
                    shell=False,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    check=False,
                    timeout=2.0,
                )
                if proc.returncode == 0:
                    return CapabilityResult(
                        success=True,
                        status=ExecutionStatus.SUCCESS,
                        data={"volume_percent": clamped_level, "level": clamped_level},
                        message=f"Volume set to {clamped_level}%.",
                        classification=self.data_classification,
                    )
            except Exception:
                pass

        return CapabilityResult(
            success=False,
            status=ExecutionStatus.FAILED,
            error="Could not set audio volume on this system.",
            message="Unable to adjust volume: no supported audio controller.",
            classification=self.data_classification,
        )


class MediaControlCapability(BaseCapability):
    """Controls media playback using MPRIS / playerctl."""

    name = "system.media"
    description = (
        "Control media playback (play, pause, play_pause, next, previous, stop) via system player."
    )
    input_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "description": "Playback command: 'play', 'pause', 'play_pause', 'next', 'previous', 'stop'.",
                "enum": ["play", "pause", "play_pause", "next", "previous", "stop"],
                "default": "play_pause",
            }
        },
    }
    risk_category = ActionCategory.LOW_RISK_ACTION
    data_classification = DataClassification.LOCAL_ONLY
    requires_confirmation = False

    def execute(self, action: str = "play_pause", **kwargs: Any) -> CapabilityResult:
        """Control media via playerctl."""
        if not shutil.which("playerctl"):
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="playerctl utility not found.",
                message="Media control utility (playerctl) is not installed.",
                classification=self.data_classification,
            )

        cmd_param = kwargs.get("command") or action
        action_map = {
            "play": "play",
            "pause": "pause",
            "play_pause": "play-pause",
            "next": "next",
            "previous": "previous",
            "stop": "stop",
        }
        playerctl_cmd = action_map.get(cmd_param.lower(), "play-pause")

        try:
            proc = subprocess.run(
                ["playerctl", playerctl_cmd],
                shell=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                check=False,
                timeout=2.0,
            )
            if proc.returncode == 0:
                return CapabilityResult(
                    success=True,
                    status=ExecutionStatus.SUCCESS,
                    data={"action": cmd_param, "command": cmd_param},
                    message=f"Media control: {cmd_param}.",
                    classification=self.data_classification,
                )
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error="No active media player responded.",
                message="No active media player found.",
                classification=self.data_classification,
            )
        except Exception as err:
            return CapabilityResult(
                success=False,
                status=ExecutionStatus.FAILED,
                error=str(err),
                message=f"Media control error: {err}",
                classification=self.data_classification,
            )
