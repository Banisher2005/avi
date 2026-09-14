"""Unit tests for PowerToys-inspired Command Palette, Application Registry, Math Evaluator, and Matcher."""

from pathlib import Path
from unittest.mock import patch

from avi.apps.registry import ApplicationEntry, ApplicationRegistry
from avi.commands.history import UsageHistory
from avi.commands.matcher import calculate_match_score
from avi.commands.models import (
    CommandCategory,
    CommandDefinition,
)
from avi.commands.registry import (
    CommandRegistry,
    evaluate_safe_arithmetic,
)
from avi.commands.resolver import CommandResolver
from avi.ui.hotkey import HotkeyManager, parse_hotkey_string

# ============================================================================
# 1. Safe AST Arithmetic Evaluator Tests
# ============================================================================


def test_safe_arithmetic_basic_operations():
    assert evaluate_safe_arithmetic("2 + 2") == "4"
    assert evaluate_safe_arithmetic("27 * 43") == "1161"
    assert evaluate_safe_arithmetic("100 - 45.5") == "54.5"
    assert evaluate_safe_arithmetic("10 / 4") == "2.5"
    assert evaluate_safe_arithmetic("10 // 3") == "3"
    assert evaluate_safe_arithmetic("10 % 3") == "1"
    assert evaluate_safe_arithmetic("2 ** 8") == "256"


def test_safe_arithmetic_precedence_and_grouping():
    assert evaluate_safe_arithmetic("(10 + 5) * 2") == "30"
    assert evaluate_safe_arithmetic("-5 + 10") == "5"
    assert evaluate_safe_arithmetic("+10 - (-5)") == "15"
    assert evaluate_safe_arithmetic("2 + 3 * 4") == "14"


def test_safe_arithmetic_errors_and_security():
    # Division by zero
    assert evaluate_safe_arithmetic("5 / 0") == "Division by zero is undefined."
    assert evaluate_safe_arithmetic("5 // 0") == "Division by zero is undefined."
    assert evaluate_safe_arithmetic("5 % 0") == "Division by zero is undefined."

    # Syntax errors
    assert "Invalid" in evaluate_safe_arithmetic("2 + + +")
    assert "Please provide a valid" in evaluate_safe_arithmetic("")
    assert "Invalid" in evaluate_safe_arithmetic("hello world")

    # Security: No eval/exec, function calls, imports, or variable access
    assert "Unsupported" in evaluate_safe_arithmetic("variable")
    assert "Unsupported" in evaluate_safe_arithmetic("__import__('os').system('ls')")
    assert "Unsupported" in evaluate_safe_arithmetic("open('/etc/passwd')")
    assert "Unsupported" in evaluate_safe_arithmetic("exit()")
    assert "Unsupported" in evaluate_safe_arithmetic("x + 1")


# ============================================================================
# 2. Command Registry Tests
# ============================================================================


def test_command_registry_default_builtins():
    registry = CommandRegistry()
    definitions = registry.list_all_definitions()
    command_ids = {cmd.id for cmd in definitions}

    expected_ids = {"calc", "ram", "cpu", "disk", "health", "files", "task", "help"}
    assert expected_ids.issubset(command_ids)


def test_command_registry_custom_registration():
    registry = CommandRegistry()
    custom_cmd = CommandDefinition(
        id="custom",
        name="Custom Action",
        description="A custom test command",
        icon="system-run",
        category=CommandCategory.SYSTEM,
        aliases=["mycustom", "testaction"],
        handler=lambda arg: f"Custom executed: {arg}",
    )
    registry.register_command(custom_cmd)

    assert registry.get_command("custom") is not None
    assert registry.get_command("mycustom") == custom_cmd
    assert registry.execute_command("custom", "hello") == "Custom executed: hello"

    registry.unregister_command("custom")
    assert registry.get_command("custom") is None


def test_command_registry_builtin_executions():
    registry = CommandRegistry()

    # Calc
    res_calc = registry.execute_command("calc", "15 * 6")
    assert "90" in res_calc

    # RAM
    res_ram = registry.execute_command("ram")
    assert "RAM" in res_ram or "Memory" in res_ram

    # CPU
    res_cpu = registry.execute_command("cpu")
    assert "%" in res_cpu

    # Disk
    res_disk = registry.execute_command("disk")
    assert "Disk" in res_disk

    # Help
    res_help = registry.execute_command("help")
    assert "/calc" in res_help
    assert "/ram" in res_help


# ============================================================================
# 3. Application Registry Tests
# ============================================================================


def test_application_registry_scanning(tmp_path: Path):
    desktop_dir = tmp_path / "applications"
    desktop_dir.mkdir(parents=True)

    # Valid application
    calc_desktop = desktop_dir / "calculator.desktop"
    calc_desktop.write_text(
        """[Desktop Entry]
Type=Application
Name=GNOME Calculator
GenericName=Calculator
Comment=Perform arithmetic, scientific or financial calculations
Exec=gnome-calculator %u
Icon=org.gnome.Calculator
Categories=GNOME;GTK;Utility;Calculator;
Keywords=calculation;arithmetic;scientific;financial;
Actions=Scientific;

[Desktop Action Scientific]
Name=Scientific Mode
Exec=gnome-calculator --mode=scientific
"""
    )

    # Hidden application (should be ignored)
    hidden_desktop = desktop_dir / "hidden.desktop"
    hidden_desktop.write_text(
        """[Desktop Entry]
Type=Application
Name=Hidden Tool
Exec=hidden-tool
NoDisplay=true
"""
    )

    app_registry = ApplicationRegistry(desktop_dirs=[desktop_dir])
    with patch("shutil.which", return_value="/usr/bin/gnome-calculator"):
        apps = app_registry.discover(force=True)

        assert len(apps) == 1
        calc_app = apps["calculator"]
        assert calc_app.display_name == "GNOME Calculator"
        assert calc_app.executable == "/usr/bin/gnome-calculator"
        assert calc_app.icon == "org.gnome.Calculator"
        assert "Calculator" in calc_app.categories
        assert "arithmetic" in calc_app.keywords
        assert len(calc_app.actions) >= 1
        assert any(act.id == "launch" for act in calc_app.actions)


def test_application_registry_lookup():
    apps = {
        "google-chrome": ApplicationEntry(
            id="google-chrome",
            canonical_name="google chrome",
            display_name="Google Chrome",
            generic_name="Web Browser",
            executable="/usr/bin/google-chrome",
            icon="google-chrome",
            categories=["Network", "WebBrowser"],
            keywords=["internet", "web", "browser"],
            aliases=["chrome", "browser", "gc"],
        ),
        "code": ApplicationEntry(
            id="code",
            canonical_name="visual studio code",
            display_name="Visual Studio Code",
            generic_name="Text Editor",
            executable="/usr/bin/code",
            icon="vscode",
            categories=["Development", "IDE"],
            keywords=["editor", "code", "programming"],
            aliases=["vscode", "vs"],
        ),
    }

    registry = ApplicationRegistry(desktop_dirs=[])
    registry._applications = dict(apps)
    registry._discovered = True

    app_chrome = registry.get_by_name_or_alias("chrome")
    assert app_chrome is not None
    assert app_chrome.display_name == "Google Chrome"

    app_vs = registry.get_by_name_or_alias("vs")
    assert app_vs is not None
    assert app_vs.display_name == "Visual Studio Code"


# ============================================================================
# 4. Usage History Tests
# ============================================================================


def test_usage_history_boost_and_persistence(tmp_path: Path):
    history_file = tmp_path / "palette_history.json"
    history = UsageHistory(history_file=history_file)

    # Initially zero boost
    assert history.get_score_boost("google-chrome") == 0.0

    # Record launches
    history.record("google-chrome")
    history.record("google-chrome")
    history.record("vscode")

    boost_chrome = history.get_score_boost("google-chrome")
    boost_vscode = history.get_score_boost("vscode")

    assert boost_chrome > boost_vscode
    assert boost_chrome > 0.0

    # Verify persistence
    new_history = UsageHistory(history_file=history_file)
    assert new_history.get_score_boost("google-chrome") == boost_chrome


# ============================================================================
# 5. Fast Fuzzy Matcher Tests
# ============================================================================


def test_matcher_scores():
    # Exact match > Prefix match > Acronym match > Substring match > Fuzzy match > Mismatch
    score_exact = calculate_match_score("calc", "calc")
    score_prefix = calculate_match_score("calc", "calculator")
    score_acronym = calculate_match_score("gc", "Google Chrome")
    score_substring = calculate_match_score("term", "gnome-terminal")
    score_mismatch = calculate_match_score("xyz", "Google Chrome")

    assert score_exact == 1.0
    assert score_prefix >= 0.8
    assert score_acronym >= 0.7
    assert score_substring >= 0.4
    assert score_mismatch == 0.0
    assert score_exact > score_prefix > score_substring > score_mismatch


def test_matcher_with_aliases():
    score_no_alias = calculate_match_score("ff", "Mozilla Firefox")
    score_with_alias = calculate_match_score("ff", "Mozilla Firefox", aliases=["ff", "browser"])
    assert score_with_alias > score_no_alias


# ============================================================================
# 6. Command Resolver Tests
# ============================================================================


def test_command_resolver_slash_query():
    resolver = CommandResolver()

    # /calc with expression
    results = resolver.resolve("/calc 27 * 43")
    assert len(results) > 0
    top = results[0]
    assert top.action_type == "calculate"
    assert top.subtitle == "= 1161"

    # /ram command
    results_ram = resolver.resolve("/ram")
    assert len(results_ram) > 0
    assert any(r.payload.get("command_id") == "ram" for r in results_ram)

    # /help command
    results_help = resolver.resolve("/help")
    assert len(results_help) > 0
    assert any(r.payload.get("command_id") == "help" for r in results_help)


def test_command_resolver_inline_math_detection():
    resolver = CommandResolver()
    results = resolver.resolve("12 * 12")
    assert len(results) > 0
    assert results[0].action_type == "calculate"
    assert results[0].subtitle == "= 144"


def test_command_resolver_fallback_to_ask_avi():
    resolver = CommandResolver()
    results = resolver.resolve("What is quantum computing?")
    assert len(results) > 0
    top = [r for r in results if r.action_type == "agent"]
    assert len(top) > 0
    assert top[0].category == CommandCategory.ACTION


def test_command_resolver_execution():
    resolver = CommandResolver()

    # Test calculate execution
    calc_results = resolver.resolve("/calc 25 * 4")
    assert len(calc_results) > 0
    calc_res = resolver.execute_result(calc_results[0])
    assert calc_res.success
    assert "100" in (calc_res.user_message or str(calc_res.result))

    # Test built-in command execution
    ram_results = resolver.resolve("/ram")
    assert len(ram_results) > 0
    ram_res = resolver.execute_result(ram_results[0])
    assert ram_res.success
    msg = ram_res.user_message or str(ram_res.result)
    assert any(term in msg for term in ("RAM", "Memory", "GB"))


# ============================================================================
# 7. Hotkey Manager Unit Tests
# ============================================================================


def test_hotkey_manager_parsing():
    mod_mask, keysym = parse_hotkey_string("<Alt>space")
    # Mod1Mask (Alt) is (1 << 3) = 8
    assert mod_mask == 8
    assert keysym == 0x0020

    mod_ctrl_alt, keysym2 = parse_hotkey_string("<Control><Alt>k")
    assert mod_ctrl_alt == (4 | 8)
    assert keysym2 == ord("k")


def test_hotkey_manager_window_focus_restoration():
    manager = HotkeyManager()

    with patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "123456\n"
        with patch("shutil.which", return_value="/usr/bin/xdotool"):
            manager.record_active_window()
            assert manager._previous_window_id == "123456"

    with patch("subprocess.Popen") as mock_popen:
        with patch("shutil.which", return_value="/usr/bin/xdotool"):
            res = manager.restore_previous_focus()
            assert res is True
            mock_popen.assert_called_once()
            assert "windowactivate" in mock_popen.call_args[0][0]
