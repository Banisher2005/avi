"""Web destination resolution with typo tolerance and browser targeting."""

import difflib
import re
from typing import Any

# Known web destinations with aliases and typo variations
WEB_DESTINATIONS: dict[str, dict[str, Any]] = {
    "chatgpt": {
        "canonical_name": "ChatGPT",
        "url": "https://chatgpt.com",
        "aliases": [
            "chatgpt",
            "chat gpt",
            "chat-gpt",
            "openai",
            "chatgpt.com",
            "caht gpt",
            "cahtgpt",
            "chagpt",
            "chatgtp",
            "chat gtp",
            "gpt",
        ],
    },
    "claude": {
        "canonical_name": "Claude",
        "url": "https://claude.ai",
        "aliases": ["claude", "claude ai", "claude.ai", "anthropic", "claud"],
    },
    "youtube": {
        "canonical_name": "YouTube",
        "url": "https://youtube.com",
        "aliases": ["youtube", "youtub", "yotube", "yt"],
    },
    "github": {
        "canonical_name": "GitHub",
        "url": "https://github.com",
        "aliases": ["github", "git hub", "gh", "githb"],
    },
    "reddit": {
        "canonical_name": "Reddit",
        "url": "https://reddit.com",
        "aliases": ["reddit", "redit", "reddt"],
    },
    "twitter": {
        "canonical_name": "X / Twitter",
        "url": "https://x.com",
        "aliases": ["twitter", "x", "x.com", "tweet", "twtr"],
    },
    "gmail": {
        "canonical_name": "Gmail",
        "url": "https://mail.google.com",
        "aliases": ["gmail", "google mail", "email", "g mail", "gmaill"],
    },
    "docs": {
        "canonical_name": "Google Docs",
        "url": "https://docs.google.com",
        "aliases": ["docs", "google docs", "gdocs", "googledocs"],
    },
    "sheets": {
        "canonical_name": "Google Sheets",
        "url": "https://sheets.google.com",
        "aliases": ["sheets", "google sheets", "gsheets", "googlesheets"],
    },
    "drive": {
        "canonical_name": "Google Drive",
        "url": "https://drive.google.com",
        "aliases": ["drive", "google drive", "gdrive", "googledrive"],
    },
    "google": {
        "canonical_name": "Google",
        "url": "https://google.com",
        "aliases": ["google", "goggle", "google search", "googl"],
    },
}

# Known browser application keywords
KNOWN_BROWSERS = {
    "chrome": "chrome",
    "google chrome": "google chrome",
    "chromium": "chromium",
    "firefox": "firefox",
    "brave": "brave",
    "brave browser": "brave browser",
    "edge": "microsoft-edge",
    "microsoft edge": "microsoft-edge",
    "opera": "opera",
    "vivaldi": "vivaldi",
}

# Regex for matching destination targeting browser:
# Examples:
# "chatgpt on chrome", "caht gpt in firefox", "chatgpt using brave", "chat gpt with chrome"
_DESTINATION_BROWSER_RE = re.compile(
    r"^(.+?)\s+(?:on|in|using|with|via)\s+([a-zA-Z0-9_\-\s]+)$",
    re.IGNORECASE,
)


def resolve_web_destination(target_str: str) -> tuple[str, str, str | None] | None:
    """Resolve a target string into (canonical_name, url, target_browser).

    Returns None if target does not correspond to a known web destination.
    """
    clean = target_str.strip().lower()
    if not clean:
        return None

    # Do not treat pure browsers as web destinations (e.g. "chrome" -> desktop app)
    if clean in KNOWN_BROWSERS or clean in ("terminal", "calculator", "files", "vlc", "spotify", "code"):
        return None

    browser: str | None = None
    dest_str = clean

    # Check for "destination on/in browser" syntax
    m = _DESTINATION_BROWSER_RE.match(clean)
    if m:
        candidate_dest = m.group(1).strip()
        candidate_browser = m.group(2).strip()
        # Verify candidate browser is recognized or fuzzy-matches
        matched_browser = _match_browser(candidate_browser)
        if matched_browser:
            dest_str = candidate_dest
            browser = matched_browser

    # Now match dest_str against WEB_DESTINATIONS
    matched = _match_destination(dest_str)
    if matched is not None:
        dest_key, info = matched
        return (info["canonical_name"], info["url"], browser)

    return None


def _match_browser(text: str) -> str | None:
    """Fuzzy/exact matching of browser name."""
    clean = text.strip().lower()
    if clean in KNOWN_BROWSERS:
        return KNOWN_BROWSERS[clean]
    # Fuzzy match browser name
    matches = difflib.get_close_matches(clean, KNOWN_BROWSERS.keys(), n=1, cutoff=0.7)
    if matches:
        return KNOWN_BROWSERS[matches[0]]
    return None


def _match_destination(text: str) -> tuple[str, dict[str, Any]] | None:
    """Match destination text exactly or with typo tolerance."""
    clean = text.strip().lower()

    # 1. Exact alias match
    for dest_key, info in WEB_DESTINATIONS.items():
        if clean in info["aliases"] or clean == dest_key:
            return (dest_key, info)

    # 2. Multi-word clean (e.g. "chat gpt" -> "chatgpt")
    clean_nospace = clean.replace(" ", "")
    for dest_key, info in WEB_DESTINATIONS.items():
        aliases_nospace = [a.replace(" ", "") for a in info["aliases"]]
        if clean_nospace in aliases_nospace:
            return (dest_key, info)

    # 3. Fuzzy match against all aliases
    all_aliases: dict[str, str] = {}
    for dest_key, info in WEB_DESTINATIONS.items():
        for a in info["aliases"]:
            all_aliases[a] = dest_key

    close = difflib.get_close_matches(clean, all_aliases.keys(), n=1, cutoff=0.68)
    if close:
        dest_key = all_aliases[close[0]]
        return (dest_key, WEB_DESTINATIONS[dest_key])

    # Also fuzzy match without spaces
    all_nospace: dict[str, str] = {}
    for dest_key, info in WEB_DESTINATIONS.items():
        for a in info["aliases"]:
            all_nospace[a.replace(" ", "")] = dest_key

    close_nospace = difflib.get_close_matches(clean_nospace, all_nospace.keys(), n=1, cutoff=0.7)
    if close_nospace:
        dest_key = all_nospace[close_nospace[0]]
        return (dest_key, WEB_DESTINATIONS[dest_key])

    return None
