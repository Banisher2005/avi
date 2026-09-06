"""Output normalization and sanitization for AVI."""

import re
from typing import Iterator


def normalize_response(text: str) -> str:
    """Normalize model output for clean terminal display.

    Strips unnecessary markdown code fences, backticks, and command prefixes
    to yield clean, directly usable shell commands or concise answers.
    """
    cleaned = text.strip()

    # 1. Strip full markdown code block fence if wrapped in one
    # Handles: ```bash\ncommand\n```, ```sh\ncommand\n```, ```\ncommand\n```
    code_block_match = re.match(r"^```[a-zA-Z0-9_-]*\n?(.*?)\n?```$", cleaned, re.DOTALL)
    if code_block_match:
        cleaned = code_block_match.group(1).strip()

    # 2. Strip single inline backticks: `pwd` -> pwd
    if cleaned.startswith("`") and cleaned.endswith("`") and len(cleaned) >= 2:
        inner = cleaned[1:-1]
        if not inner.startswith("`") and not inner.endswith("`"):
            cleaned = inner.strip()

    # 3. Strip leading shell prompt symbol if on single command line: "$ pwd" -> "pwd"
    prompt_match = re.match(r"^\$\s+(.+)$", cleaned)
    if prompt_match:
        cleaned = prompt_match.group(1).strip()

    return cleaned


class StreamNormalizer:
    """Normalizes streaming text chunks in real-time.

    Suppresses leading markdown code block headers (e.g. ```bash)
    and trailing code fences (```) so the streamed output is clean.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._header_checked = False
        self._inside_fence = False

    def process_chunk(self, chunk: str) -> str:
        """Process an incoming streaming chunk and return normalized output."""
        if not chunk:
            return ""

        self._buffer += chunk

        # If we haven't processed the header yet, wait for a newline or non-fence start
        if not self._header_checked:
            if "\n" in self._buffer:
                first_line, remainder = self._buffer.split("\n", 1)
                first_line_stripped = first_line.strip()
                if first_line_stripped.startswith("```"):
                    # Discard the fence header line
                    self._inside_fence = True
                    self._header_checked = True
                    self._buffer = remainder
                else:
                    self._header_checked = True
                    # Not a fence, keep full buffer
            elif len(self._buffer) > 20 and not self._buffer.strip().startswith("```"):
                # Clearly not starting with ```
                self._header_checked = True

        if self._header_checked:
            # Output buffered content, reserving potential trailing fence
            return self._flush_safe()

        return ""

    def _flush_safe(self) -> str:
        """Flush buffer while holding back characters that might form a trailing fence."""
        if not self._buffer:
            return ""

        # If inside a fence and buffer contains ```, don't output ```
        if "```" in self._buffer:
            idx = self._buffer.rfind("```")
            out = self._buffer[:idx]
            self._buffer = self._buffer[idx:]
            return out

        # Keep back up to 3 chars at end in case '```' is currently arriving
        if len(self._buffer) > 4:
            out = self._buffer[:-4]
            self._buffer = self._buffer[-4:]
            return out

        return ""

    def finalize(self) -> str:
        """Flush any remaining content at the end of the stream."""
        remaining = self._buffer
        self._buffer = ""

        if not self._header_checked:
            return normalize_response(remaining)

        # Strip any trailing code fence from remaining
        remaining = re.sub(r"\n?```\s*$", "", remaining)
        return remaining.strip("\r\n")


def normalize_stream(chunk_iterator: Iterator[str]) -> Iterator[str]:
    """Generator wrapping an iterator of chunks to apply stream normalization."""
    normalizer = StreamNormalizer()
    for chunk in chunk_iterator:
        out = normalizer.process_chunk(chunk)
        if out:
            yield out
    final_out = normalizer.finalize()
    if final_out:
        yield final_out
