"""Comprehensive unit and security tests for Phase 6 deterministic fastpath routing."""

from unittest.mock import MagicMock, patch
import pytest

from avi.config import Config
from avi.core.fastpath import (
    FastPathRegistry,
    IntentTemplate,
    is_safe_parameter,
    resolve_command_template,
)
from avi.core.router import Router
from avi.execution.models import CommandRequest
from avi.safety.models import RiskLevel


@pytest.fixture
def registry():
    return FastPathRegistry()


# 1. Exact deterministic matches
@pytest.mark.parametrize(
    "prompt,expected_program,expected_args",
    [
        ("pwd", "pwd", []),
        ("current directory", "pwd", []),
        ("what is my current directory", "pwd", []),
        ("where am i", "pwd", []),
        ("print working directory", "pwd", []),
        ("ls", "ls", ["-la"]),
        ("list files", "ls", ["-la"]),
        ("show files", "ls", ["-la"]),
        ("list files here", "ls", ["-la"]),
        ("df -h", "df", ["-h"]),
        ("disk usage", "df", ["-h"]),
        ("show disk usage", "df", ["-h"]),
        ("check disk space", "df", ["-h"]),
        ("ps aux", "ps", ["aux"]),
        ("processes", "ps", ["aux"]),
        ("show processes", "ps", ["aux"]),
        ("show current processes", "ps", ["aux"]),
        ("git branch", "git", ["branch", "--show-current"]),
        ("current git branch", "git", ["branch", "--show-current"]),
        ("what branch am i on", "git", ["branch", "--show-current"]),
        ("show current branch", "git", ["branch", "--show-current"]),
        ("git status", "git", ["status"]),
        ("show git status", "git", ["status"]),
        ("check git status", "git", ["status"]),
        ("is git clean", "git", ["status"]),
        ("recent commits", "git", ["log", "--oneline", "-10"]),
        ("git log", "git", ["log", "--oneline", "-10"]),
        ("git history", "git", ["log", "--oneline", "-10"]),
        ("git diff", "git", ["diff"]),
        ("show git diff", "git", ["diff"]),
        ("git changes", "git", ["diff"]),
        ("show uncommitted changes", "git", ["diff"]),
        ("date", "date", []),
        ("current date", "date", []),
        ("what is the date", "date", []),
        ("today's date", "date", []),
        ("whoami", "whoami", []),
        ("current user", "whoami", []),
        ("who am i", "whoami", []),
        ("uname -a", "uname", ["-a"]),
        ("operating system", "uname", ["-a"]),
        ("what os is this", "uname", ["-a"]),
        ("system info", "uname", ["-a"]),
        ("show environment/system information", "uname", ["-a"]),
        ("which python3", "which", ["python3"]),
        ("where is python3", "which", ["python3"]),
        ("python path", "which", ["python3"]),
        ("ss -tulpn", "ss", ["-tulpn"]),
        ("listening ports", "ss", ["-tulpn"]),
        ("show listening tcp ports", "ss", ["-tulpn"]),
        ("check python version", "python3", ["--version"]),
        ("python version", "python3", ["--version"]),
        ("check node version", "node", ["--version"]),
        ("node version", "node", ["--version"]),
        ("free memory", "free", ["-h"]),
        ("check free memory", "free", ["-h"]),
        ("memory usage", "free", ["-h"]),
        ("ram usage", "free", ["-h"]),
        ("uptime", "uptime", []),
        ("system uptime", "uptime", []),
        ("how long has the system been running", "uptime", []),
        ("git remotes", "git", ["remote", "-v"]),
        ("show git remotes", "git", ["remote", "-v"]),
        ("git remote -v", "git", ["remote", "-v"]),
        ("show staged changes", "git", ["diff", "--cached"]),
        ("git diff --staged", "git", ["diff", "--cached"]),
        ("git diff --cached", "git", ["diff", "--cached"]),
        ("directory size", "du", ["-sh", "."]),
        ("size of current directory", "du", ["-sh", "."]),
        ("du -sh", "du", ["-sh", "."]),
        ("check go version", "go", ["version"]),
        ("go version", "go", ["version"]),
        ("check rust version", "rustc", ["--version"]),
        ("rust version", "rustc", ["--version"]),
        ("rustc --version", "rustc", ["--version"]),
        ("find empty files", "find", [".", "-type", "f", "-empty"]),
        ("find empty directories", "find", [".", "-type", "d", "-empty"]),
    ],
)
def test_exact_deterministic_matches(registry, prompt, expected_program, expected_args):
    req = registry.resolve(prompt)
    assert req is not None, f"Prompt {prompt!r} failed to resolve"
    assert req.program == expected_program
    assert req.args == expected_args


# 2. Parameterized templates
@pytest.mark.parametrize(
    "prompt,expected_program,expected_args",
    [
        ("show the last 20 git commits", "git", ["log", "--oneline", "-20"]),
        ("show last 5 commits", "git", ["log", "--oneline", "-5"]),
        ("git log -15", "git", ["log", "--oneline", "-15"]),
        ("find files larger than 500MB", "find", [".", "-type", "f", "-size", "+500M"]),
        ("find files bigger than 2GB", "find", [".", "-type", "f", "-size", "+2G"]),
        ("find files larger than 100k", "find", [".", "-type", "f", "-size", "+100k"]),
        ("find python files", "find", [".", "-name", "*.py"]),
        ("find javascript files", "find", [".", "-name", "*.js"]),
        ("find js files", "find", [".", "-name", "*.js"]),
        ("find rust files", "find", [".", "-name", "*.rs"]),
        ("find go files", "find", [".", "-name", "*.go"]),
        ("find cpp files", "find", [".", "-name", "*.cpp"]),
        ("find java files", "find", [".", "-name", "*.java"]),
        ("find bash files", "find", [".", "-name", "*.sh"]),
        ("find yaml files", "find", [".", "-name", "*.yaml"]),
        ("find toml files", "find", [".", "-name", "*.toml"]),
        ("find sql files", "find", [".", "-name", "*.sql"]),
        ("find files modified today", "find", [".", "-mtime", "0"]),
        ("find files modified in the last 7 days", "find", [".", "-mtime", "-7"]),
        ("grep for \"TODO\" in files", "grep", ["-r", "--", "TODO", "."]),
        ("grep for 'FIXME' in files", "grep", ["-r", "--", "FIXME", "."]),
        ("grep for bug in files", "grep", ["-r", "--", "bug", "."]),
        ("search for \"pattern\" in files", "grep", ["-r", "--", "pattern", "."]),
        ("search files for \"TODO\"", "grep", ["-r", "--", "TODO", "."]),
    ],
)
def test_parameterized_templates(registry, prompt, expected_program, expected_args):
    req = registry.resolve(prompt)
    assert req is not None, f"Prompt {prompt!r} failed to resolve"
    assert req.program == expected_program
    assert req.args == expected_args


# 3. Case-insensitivity & natural language punctuation
@pytest.mark.parametrize(
    "prompt,expected_program",
    [
        ("CURRENT DIRECTORY", "pwd"),
        ("List Files", "ls"),
        ("SHOW GIT DIFF", "git"),
        ("Check Python Version?", "python3"),
        ("what is my current directory???", "pwd"),
        ("git status!", "git"),
        ("find Python files.", "find"),
        ("Grep For \"TODO\" In Files", "grep"),
        ("FREE MEMORY", "free"),
        ("SYSTEM UPTIME", "uptime"),
        ("Show Git Remotes?", "git"),
        ("Directory Size!", "du"),
    ],
)
def test_case_and_punctuation_insensitivity(registry, prompt, expected_program):
    req = registry.resolve(prompt)
    assert req is not None
    assert req.program == expected_program


# 4. Ambiguous prompts must NOT match fastpath (fall back to LLM)
@pytest.mark.parametrize(
    "prompt",
    [
        "how do I use grep in Linux?",
        "how to list files in bash",
        "explain git log",
        "write a python script to search files",
        "should I delete these files?",
        "tell me a joke",
        "can you help me with python?",
        "how does git branch work?",
        "what is the difference between git diff and git log?",
        "rm test.tmp",
        "delete file",
        "",
        "   ",
    ],
)
def test_ambiguous_prompts_return_none(registry, prompt):
    req = registry.resolve(prompt)
    assert req is None, f"Ambiguous prompt {prompt!r} should not match fastpath, got {req}"


# 5. Parameter safety: Shell syntax, operators, and injection rejection
@pytest.mark.parametrize(
    "malicious_prompt",
    [
        "grep for \"TODO; rm -rf /\" in files",
        "grep for \"TODO && echo hi\" in files",
        "grep for \"TODO || ls\" in files",
        "grep for \"TODO | cat\" in files",
        "grep for \"$(whoami)\" in files",
        "grep for \"`id`\" in files",
        "grep for \"test > out.txt\" in files",
        "grep for \"test >> out.txt\" in files",
        "grep for \"test < in.txt\" in files",
        "grep for \"test \n rm file\" in files",
        "grep for \"unclosed quote in files",
        "grep for 'single quote in files",
        "grep for \"foo\\bar\" in files",
        "grep for \"${PATH}\" in files",
        "grep for \"$USER\" in files",
        "grep for \"test\x00data\" in files",
        "grep for \"test\x01data\" in files",
        "grep for \"test\x1fdata\" in files",
    ],
)
def test_malicious_parameters_rejected(registry, malicious_prompt):
    req = registry.resolve(malicious_prompt)
    assert req is None, f"Malicious prompt {malicious_prompt!r} must be rejected by fastpath"


# 6. Numeric limits on git commits
@pytest.mark.parametrize(
    "invalid_prompt",
    [
        "show the last 0 git commits",
        "show the last -5 git commits",
        "show the last 999999 git commits",
        "show the last abc git commits",
    ],
)
def test_invalid_commit_counts_rejected(registry, invalid_prompt):
    assert registry.resolve(invalid_prompt) is None


# 7. is_safe_parameter standalone tests
def test_is_safe_parameter_rules():
    assert is_safe_parameter("TODO") is True
    assert is_safe_parameter("valid_identifier_123") is True
    assert is_safe_parameter("simple text with spaces") is True
    assert is_safe_parameter("") is False
    assert is_safe_parameter("   ") is False
    assert is_safe_parameter("val; rm -rf /") is False
    assert is_safe_parameter("val && ls") is False
    assert is_safe_parameter("val | grep") is False
    assert is_safe_parameter("$(whoami)") is False
    assert is_safe_parameter("${SECRET}") is False
    assert is_safe_parameter("`whoami`") is False
    assert is_safe_parameter("val > file") is False
    assert is_safe_parameter("val < file") is False
    assert is_safe_parameter("unclosed \" quote") is False
    assert is_safe_parameter("escaped \\ backslash") is False
    assert is_safe_parameter("control\x00byte") is False
    assert is_safe_parameter("bell\x07char") is False


# 8. Fast-path requests do not invoke the LLM provider
def test_fastpath_bypasses_provider_in_router():
    config = Config.load()
    mock_provider = MagicMock()
    router = Router(config, provider=mock_provider)

    # Run a fast-path query: show git diff
    chunks = list(router.route("show git diff"))
    # Verify provider was NOT called
    mock_provider.generate.assert_not_called()
    mock_provider.generate_full.assert_not_called()
    assert router.last_metrics is not None


# 9. Resolved commands pass through SafetyEngine
def test_fastpath_passes_through_safety_engine():
    config = Config.load()
    router = Router(config)

    # Fastpath resolution produces CommandRequest
    req = router.resolve_fastpath("find files larger than 500MB")
    assert req is not None
    assert req.program == "find"
    assert req.args == [".", "-type", "f", "-size", "+500M"]

    # Evaluated by SafetyEngine
    assessment = router.evaluate_command(req)
    assert assessment.level == RiskLevel.SAFE
    assert assessment.is_safe is True


# 10. Router route_full returns fastpath output directly
def test_fastpath_route_full_execution():
    config = Config.load()
    mock_provider = MagicMock()
    router = Router(config, provider=mock_provider)

    resp = router.route_full("current date")
    mock_provider.generate.assert_not_called()
    mock_provider.generate_full.assert_not_called()
    assert resp.text != ""
    assert resp.metrics is not None


# 11. Command template syntax resolution tests
from avi.providers.base import BaseProvider, ProviderResponse


class FailingProvider(BaseProvider):
    def generate(self, prompt, system_prompt=None, context=None, stream=True):
        raise AssertionError("LLM must not be invoked for a fast-path command template")
        yield ""

    def generate_full(self, prompt, system_prompt=None, context=None):
        raise AssertionError("LLM must not be invoked for a fast-path command template")

    def is_available(self):
        return True

    def warmup(self):
        return True

    def get_model_name(self):
        return "test"

    @property
    def last_metrics(self):
        return None

    @property
    def last_context(self):
        return None


def test_command_templates_cover_common_queries():
    assert resolve_command_template("what command shows the current directory?") == "pwd"
    assert resolve_command_template("what command lists the files here?") == "ls -la"
    assert resolve_command_template("how do I check disk space?") == "df -h"
    assert resolve_command_template("what command shows running processes?") == "ps aux"
    assert resolve_command_template("what command shows my current git branch?") == "git branch --show-current"
    assert resolve_command_template("what command checks git status?") == "git status"
    assert resolve_command_template("what command shows recent git commits?") == "git log --oneline -10"
    assert resolve_command_template("what command shows listening ports?") == "ss -tulpn"
    assert resolve_command_template("what command shows system uptime?") == "uptime"
    assert resolve_command_template("what command checks free memory?") == "free -h"
    assert resolve_command_template("what command shows git remotes?") == "git remote -v"
    assert resolve_command_template("what command shows git staged changes?") == "git diff --cached"
    assert resolve_command_template("what command checks python version?") == "python3 --version"
    assert resolve_command_template("what command checks node version?") == "node --version"
    assert resolve_command_template("what command checks go version?") == "go version"
    assert resolve_command_template("what command checks rust version?") == "rustc --version"
    assert resolve_command_template("what command shows directory size?") == "du -sh ."


def test_unknown_request_is_not_fast_path():
    assert resolve_command_template("how do I deploy this application?") is None


def test_router_bypasses_llm_for_command_template():
    router = Router(Config.load(), provider=FailingProvider())
    assert router.route_full("what command shows the current directory?").text == "pwd"
    assert router.last_metrics is not None


def test_router_preserves_llm_path_for_non_template():
    class Provider(FailingProvider):
        def generate(self, prompt, system_prompt=None, context=None, stream=True):
            yield "model response"

        def generate_full(self, prompt, system_prompt=None, context=None):
            return ProviderResponse(text="model response", context=None)

    router = Router(Config.load(), provider=Provider())
    assert router.route_full("how should I structure this Python package?").text == "model response"


# 12. Registry structure, inspection, and safety classification
def test_registry_structure_and_helpers(registry):
    assert len(registry) >= 27
    names = [t.name for t in registry]
    assert "current_directory" in names
    assert "free_memory" in names
    assert "uptime" in names
    assert "git_remotes" in names
    assert "git_staged_diff"
    assert "find_by_language" in names
    assert "grep_in_files" in names

    uptime_tmpl = registry.get_template("uptime")
    assert uptime_tmpl is not None
    assert uptime_tmpl.name == "uptime"
    assert registry.get_template("nonexistent_template") is None

    # Verify all registered templates produce CommandRequests classified by SafetyEngine
    from avi.safety.engine import SafetyEngine
    engine = SafetyEngine()

    sample_prompts = [
        "pwd",
        "ls",
        "df -h",
        "ps aux",
        "git status",
        "git branch",
        "git diff",
        "free memory",
        "uptime",
        "git remotes",
        "show staged changes",
        "directory size",
        "check go version",
        "check rust version",
        "find empty files",
        "find python files",
        "find files larger than 100MB",
        "grep for 'test' in files",
    ]
    for p in sample_prompts:
        req = registry.resolve(p)
        assert req is not None, f"Sample prompt {p!r} failed to resolve"
        assessment = engine.evaluate(req)
        assert assessment.is_safe, f"Sample prompt {p!r} produced unsafe assessment: {assessment}"


