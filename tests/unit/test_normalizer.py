"""Unit tests for output normalizer."""

from avi.core.normalizer import StreamNormalizer, normalize_response, normalize_stream


def test_normalize_plain_command():
    assert normalize_response("pwd") == "pwd"
    assert normalize_response("  find . -type f  ") == "find . -type f"


def test_normalize_code_block_fences():
    # bash fence
    assert normalize_response("```bash\npwd\n```") == "pwd"
    # sh fence
    assert normalize_response("```sh\nls -la\n```") == "ls -la"
    # generic fence
    assert normalize_response("```\nfind . -name '*.py'\n```") == "find . -name '*.py'"


def test_normalize_inline_backticks():
    assert normalize_response("`pwd`") == "pwd"
    assert normalize_response("`git status`") == "git status"


def test_normalize_shell_prompt_prefix():
    assert normalize_response("$ pwd") == "pwd"
    assert normalize_response("$   ls -l") == "ls -l"


def test_preserve_multiline_and_explanations():
    explanation = "To see the current directory, use the pwd command."
    assert normalize_response(explanation) == explanation


def test_stream_normalizer_with_fenced_input():
    chunks = ["```bash\n", "find . ", "-name '*.py'\n", "```"]
    result = list(normalize_stream(iter(chunks)))
    joined = "".join(result).strip()
    assert joined == "find . -name '*.py'"


def test_stream_normalizer_with_plain_input():
    chunks = ["p", "w", "d"]
    result = list(normalize_stream(iter(chunks)))
    joined = "".join(result).strip()
    assert joined == "pwd"
