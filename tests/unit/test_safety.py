"""Comprehensive security and classification tests for the SafetyEngine."""

import pytest

from avi.execution.models import CommandRequest
from avi.safety.engine import SafetyEngine
from avi.safety.models import RiskLevel


@pytest.fixture
def engine():
    return SafetyEngine()


# 1. Safe commands test
@pytest.mark.parametrize(
    "cmd",
    [
        "pwd",
        "ls",
        "ls -la /tmp",
        "git status",
        "git branch",
        "git log -n 5",
        "git diff HEAD~1",
        "git show",
        "df -h",
        "du -sh .",
        "ps aux",
        "uname -a",
        "whoami",
        "uptime",
        "cat README.md",
        "head -n 20 file.txt",
        "tail -f log.txt",
        "wc -l file.py",
        "which python3",
        "whereis git",
        "echo hello world",
        "grep -r pattern .",
        "find . -name '*.py'",
        "file /bin/ls",
        "stat README.md",
        "date",
    ],
)
def test_safe_commands(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.SAFE, (
        f"Expected SAFE for {cmd}, got {assessment.level}: {assessment.reason}"
    )
    assert assessment.is_safe is True
    assert assessment.requires_confirmation is False
    assert assessment.is_blocked is False


# 2. Confirmation-required modifying commands
@pytest.mark.parametrize(
    "cmd",
    [
        "rm file",
        "rm -f temp.log",
        "mv a b",
        "cp src.txt dst.txt",
        "mkdir my_folder",
        "rmdir empty_dir",
        "touch newfile.txt",
        "chmod 755 run.sh",
        "chown user file",
        "kill 1234",
        "pkill python",
        "systemctl restart nginx",
        "service apache2 reload",
        "git checkout main",
        "git restore file.txt",
        "git reset --hard HEAD~1",
        "git commit -m 'initial'",
        "git merge feature",
        "git rebase main",
        "git push origin main",
        "git branch -d old-feature",
        "git branch -D old-feature",
        "git stash",
        "find . -delete",
        "find . -exec rm {} +",
        "tar -xzf archive.tar.gz",
        "unzip file.zip",
        "pip install requests",
        "date -s '2026-09-06 12:00:00'",
        "custom_binary_script --arg",
    ],
)
def test_confirmation_commands(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.CONFIRM, (
        f"Expected CONFIRM for {cmd}, got {assessment.level}: {assessment.reason}"
    )
    assert assessment.requires_confirmation is True
    assert assessment.is_safe is False
    assert assessment.is_blocked is False


# 3. Blocked catastrophic commands
@pytest.mark.parametrize(
    "cmd",
    [
        "rm -rf /",
        "rm -rf /*",
        "rm -rf /etc",
        "rm -rf /boot",
        "rm -rf /sys",
        "rm -rf /proc",
        "rm -rf /var",
        "rm -rf /usr",
        "rm -r /",
        "rm -f /",
        "mkfs /dev/sda",
        "mkfs.ext4 /dev/sdb1",
        "wipefs -a /dev/sda",
        "fdisk /dev/sda",
        "parted /dev/sda",
        "dd if=/dev/zero of=/dev/sda",
        "dd if=/dev/urandom of=/dev/nvme0n1",
        ":(){ :|:& };:",
        ": () { : | : & } ; :",
        "chmod -R 777 /",
        "chmod -R 777 /*",
        "chown -R root /",
        "shutdown -h now",
        "reboot",
        "poweroff",
        "init 0",
        "init 6",
        "sudo rm test.tmp",
        "su -",
    ],
)
def test_blocked_catastrophic_commands(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.BLOCK, (
        f"Expected BLOCK for {cmd}, got {assessment.level}: {assessment.reason}"
    )
    assert assessment.is_blocked is True
    assert assessment.is_safe is False
    assert assessment.requires_confirmation is False


# 4. Compound commands cannot bypass safety
@pytest.mark.parametrize(
    "cmd",
    [
        "ls && rm file",
        "ls; rm file",
        "cat file | rm file",
        "ls || rm file",
        "echo hi & rm file",
        "pwd; whoami",
        "ls\nrm file",
    ],
)
def test_compound_commands_blocked(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.BLOCK, (
        f"Expected BLOCK for compound {cmd}, got {assessment.level}"
    )
    assert assessment.is_blocked is True


# 5. Shell substitutions cannot bypass safety
@pytest.mark.parametrize(
    "cmd",
    [
        "echo $(whoami)",
        "ls $(pwd)",
        "echo `id`",
        "cat `echo /etc/passwd`",
    ],
)
def test_substitutions_blocked(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.BLOCK, (
        f"Expected BLOCK for substitution {cmd}, got {assessment.level}"
    )
    assert assessment.is_blocked is True


# 6. Redirections cannot bypass safety
@pytest.mark.parametrize(
    "cmd",
    [
        "echo hello > file.txt",
        "echo append >> file.txt",
        "cat < input.txt",
        "ls 2> errors.txt",
        "echo test &> all.txt",
    ],
)
def test_redirections_blocked(engine, cmd):
    assessment = engine.evaluate(cmd)
    assert assessment.level == RiskLevel.BLOCK, (
        f"Expected BLOCK for redirection {cmd}, got {assessment.level}"
    )
    assert assessment.is_blocked is True


# 7. find command action flags cannot be classified as SAFE
def test_find_delete_cannot_be_safe(engine):
    assessment = engine.evaluate("find . -delete")
    assert assessment.level != RiskLevel.SAFE
    assert assessment.level == RiskLevel.CONFIRM


def test_find_exec_cannot_be_safe(engine):
    assessment = engine.evaluate("find . -exec rm {} \\;")
    assert assessment.level != RiskLevel.SAFE


def test_find_delete_targeting_root_is_blocked(engine):
    assessment = engine.evaluate("find / -delete")
    assert assessment.level == RiskLevel.BLOCK


# 8. Malformed & empty commands
def test_empty_command_is_blocked(engine):
    assert engine.evaluate("").level == RiskLevel.BLOCK
    assert engine.evaluate("   ").level == RiskLevel.BLOCK


def test_malformed_quotes_are_blocked(engine):
    assert engine.evaluate('rm "unclosed quote').level == RiskLevel.BLOCK
    assert engine.evaluate("ls 'single unclosed").level == RiskLevel.BLOCK


def test_evaluate_command_request_object(engine):
    req = CommandRequest(program="rm", args=["test.tmp"])
    assessment = engine.evaluate(req)
    assert assessment.level == RiskLevel.CONFIRM


def test_quoted_metacharacters_in_arguments(engine):
    # Semicolon or operators INSIDE git commit message quotes should not trigger compound operator BLOCK
    assessment = engine.evaluate('git commit -m "feat: add feature; fix bug"')
    # It requires confirmation because it is git commit, but NOT blocked as compound
    assert assessment.level == RiskLevel.CONFIRM
