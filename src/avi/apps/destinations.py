"""Web destination resolution with typo tolerance, browser targeting, and application precedence."""

from __future__ import annotations

import difflib
import re
import urllib.parse
from typing import Any

from avi.apps.models import DestinationResolution, DestinationType
from avi.apps.resolver import DEFAULT_ALIASES, ApplicationResolver

# Known web destinations with aliases, search URLs, and typo variations
WEB_DESTINATIONS: dict[str, dict[str, Any]] = {
    "kaggle": {
        "canonical_name": "Kaggle",
        "url": "https://www.kaggle.com/",
        "search_url": "https://www.kaggle.com/search?q={query}",
        "aliases": ["kaggle", "kaggl", "kagle", "kaggel", "kaggle.com"],
    },
    "chatgpt": {
        "canonical_name": "ChatGPT",
        "url": "https://chatgpt.com",
        "search_url": "https://chatgpt.com/?q={query}",
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
            "chatgptt",
        ],
    },
    "claude": {
        "canonical_name": "Claude",
        "url": "https://claude.ai",
        "search_url": "https://claude.ai/new?q={query}",
        "aliases": ["claude", "claude ai", "claude.ai", "anthropic", "claud"],
    },
    "youtube": {
        "canonical_name": "YouTube",
        "url": "https://youtube.com",
        "search_url": "https://www.youtube.com/results?search_query={query}",
        "aliases": ["youtube", "youtub", "yotube", "yt", "youtube.com"],
    },
    "github": {
        "canonical_name": "GitHub",
        "url": "https://github.com",
        "search_url": "https://github.com/search?q={query}",
        "aliases": ["github", "git hub", "gh", "githb", "github.com"],
    },
    "reddit": {
        "canonical_name": "Reddit",
        "url": "https://reddit.com",
        "search_url": "https://www.reddit.com/search/?q={query}",
        "aliases": ["reddit", "redit", "reddt", "reddit.com"],
    },
    "wikipedia": {
        "canonical_name": "Wikipedia",
        "url": "https://www.wikipedia.org/",
        "search_url": "https://en.wikipedia.org/wiki/Special:Search?search={query}",
        "aliases": ["wikipedia", "wiki", "wikipidia", "wikipidea", "wikipedia.org"],
    },
    "twitter": {
        "canonical_name": "X / Twitter",
        "url": "https://x.com",
        "search_url": "https://x.com/search?q={query}",
        "aliases": ["twitter", "x", "x.com", "tweet", "twtr", "twitter.com"],
    },
    "gmail": {
        "canonical_name": "Gmail",
        "url": "https://mail.google.com",
        "search_url": "https://mail.google.com/mail/u/0/#search/{query}",
        "aliases": ["gmail", "google mail", "email", "g mail", "gmaill", "gmail.com"],
    },
    "docs": {
        "canonical_name": "Google Docs",
        "url": "https://docs.google.com",
        "search_url": "https://docs.google.com/document/u/0/?q={query}",
        "aliases": ["docs", "google docs", "gdocs", "googledocs"],
    },
    "sheets": {
        "canonical_name": "Google Sheets",
        "url": "https://sheets.google.com",
        "search_url": "https://docs.google.com/spreadsheets/u/0/?q={query}",
        "aliases": ["sheets", "google sheets", "gsheets", "googlesheets"],
    },
    "drive": {
        "canonical_name": "Google Drive",
        "url": "https://drive.google.com",
        "search_url": "https://drive.google.com/drive/search?q={query}",
        "aliases": ["drive", "google drive", "gdrive", "googledrive"],
    },
    "google": {
        "canonical_name": "Google",
        "url": "https://google.com",
        "search_url": "https://www.google.com/search?q={query}",
        "aliases": ["google", "goggle", "google search", "googl", "google.com"],
    },
    "hackernews": {
        "canonical_name": "Hacker News",
        "url": "https://news.ycombinator.com",
        "search_url": "https://hn.algolia.com/?q={query}",
        "aliases": ["hackernews", "hacker news", "hn", "ycombinator"],
    },
    "stackoverflow": {
        "canonical_name": "Stack Overflow",
        "url": "https://stackoverflow.com",
        "search_url": "https://stackoverflow.com/search?q={query}",
        "aliases": ["stackoverflow", "stack overflow", "so"],
    },
    "duckduckgo": {
        "canonical_name": "DuckDuckGo",
        "url": "https://duckduckgo.com",
        "search_url": "https://duckduckgo.com/?q={query}",
        "aliases": ["duckduckgo", "duck duck go", "ddg"],
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

# Common top-level domain match pattern
_DOMAIN_PATTERN_RE = re.compile(
    r"^(?:https?://)?(?:[a-zA-Z0-9-]+\.)+(?:com|org|net|io|edu|gov|co|ai|dev|app|me|info|tv)(?:/[^\s]*)?$",
    re.IGNORECASE,
)


def _match_browser(text: str) -> str | None:
    """Fuzzy/exact matching of browser name."""
    clean = text.strip().lower()
    if clean in KNOWN_BROWSERS:
        return KNOWN_BROWSERS[clean]
    matches = difflib.get_close_matches(clean, KNOWN_BROWSERS.keys(), n=1, cutoff=0.7)
    if matches:
        return KNOWN_BROWSERS[matches[0]]
    return None


def _match_destination(text: str) -> tuple[str, dict[str, Any]] | None:
    """Match destination text exactly or with high-confidence typo tolerance."""
    clean = text.strip().lower()
    if not clean:
        return None

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

    # 3. High-confidence fuzzy match against all aliases
    all_aliases: dict[str, str] = {}
    for dest_key, info in WEB_DESTINATIONS.items():
        for a in info["aliases"]:
            all_aliases[a] = dest_key

    close = difflib.get_close_matches(clean, all_aliases.keys(), n=1, cutoff=0.78)
    if close:
        dest_key = all_aliases[close[0]]
        return (dest_key, WEB_DESTINATIONS[dest_key])

    # Also fuzzy match without spaces
    all_nospace: dict[str, str] = {}
    for dest_key, info in WEB_DESTINATIONS.items():
        for a in info["aliases"]:
            all_nospace[a.replace(" ", "")] = dest_key

    close_nospace = difflib.get_close_matches(clean_nospace, all_nospace.keys(), n=1, cutoff=0.78)
    if close_nospace:
        dest_key = all_nospace[close_nospace[0]]
        return (dest_key, WEB_DESTINATIONS[dest_key])

    return None


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
        matched_browser = _match_browser(candidate_browser)
        if matched_browser:
            dest_str = candidate_dest
            browser = matched_browser

    # Match dest_str against WEB_DESTINATIONS
    matched = _match_destination(dest_str)
    if matched is not None:
        _, info = matched
        return (info["canonical_name"], info["url"], browser)

    return None


class DestinationResolver:
    """Intelligent multi-tier destination resolver for Avi.

    Implements the resolution priority:
    1. Explicit application intent (e.g. 'open the app VLC', 'launch Spotify')
    2. Explicit website intent (e.g. 'open kaggle.com', 'open github in chrome', 'visit reddit')
    3. Installed application precedence (if X is installed locally, prefer desktop app)
    4. Browser fallback (known web destination such as Kaggle, domain check, or saved alias)
    5. Typo tolerance (high confidence auto-resolves, ambiguous triggers clarification)
    6. Nonexistent desktop app fallback (reports application not installed when no web interpretation)
    """

    def __init__(
        self,
        app_resolver: ApplicationResolver | None = None,
        database: Any | None = None,
        memory: Any | None = None,
    ) -> None:
        self.app_resolver = app_resolver or ApplicationResolver()
        self.database = database
        self.memory = memory

    def _normalize_input(self, raw_input: str) -> tuple[str, bool, bool, str | None]:
        """Extract canonical target, explicit app flag, explicit web flag, and browser override.

        Returns: (normalized_target, is_explicit_app, is_explicit_web, browser)
        """
        text = raw_input.strip()
        lower = text.lower()
        is_explicit_app = False
        is_explicit_web = False
        browser = None

        # 1. Strip common action prefixes
        action_prefixes = (
            ("open the website ", False, True),
            ("open the web site ", False, True),
            ("open the site ", False, True),
            ("open website ", False, True),
            ("open site ", False, True),
            ("launch the website ", False, True),
            ("launch website ", False, True),
            ("go to the website ", False, True),
            ("go to website ", False, True),
            ("go to ", False, True),
            ("visit the website ", False, True),
            ("visit website ", False, True),
            ("visit ", False, True),
            ("browse to ", False, True),
            ("navigate to ", False, True),
            ("launch the app ", True, False),
            ("launch the application ", True, False),
            ("launch app ", True, False),
            ("launch application ", True, False),
            ("open the app ", True, False),
            ("open the application ", True, False),
            ("open app ", True, False),
            ("open application ", True, False),
            ("start app ", True, False),
            ("start application ", True, False),
            ("open up ", False, False),
            ("open ", False, False),
            ("launch ", False, False),
            ("start ", False, False),
            ("run ", False, False),
        )

        for pfx, exp_app, exp_web in action_prefixes:
            if lower.startswith(pfx):
                text = text[len(pfx) :].strip()
                lower = text.lower()
                if exp_app:
                    is_explicit_app = True
                if exp_web:
                    is_explicit_web = True
                break

        # 2. Check browser targeting clauses (e.g. "kaggle in chrome", "github on firefox")
        m_browser = _DESTINATION_BROWSER_RE.match(text)
        if m_browser:
            cand_dest = m_browser.group(1).strip()
            cand_browser = m_browser.group(2).strip().lower()
            matched_b = _match_browser(cand_browser)
            if matched_b or cand_browser in ("browser", "the browser", "web", "the web"):
                text = cand_dest
                lower = text.lower()
                is_explicit_web = True
                browser = matched_b or "chrome"

        # 3. Check trailing web / app modifiers
        trailing_web = (
            " website",
            " web site",
            " web",
            " online",
            " site",
        )
        for tw in trailing_web:
            if lower.endswith(tw):
                text = text[: -len(tw)].strip()
                lower = text.lower()
                is_explicit_web = True
                break

        trailing_app = (
            " application",
            " app",
            " desktop app",
        )
        for ta in trailing_app:
            if lower.endswith(ta):
                text = text[: -len(ta)].strip()
                lower = text.lower()
                is_explicit_app = True
                break

        # 4. Clean article noise (e.g. "the kaggle" -> "kaggle")
        for art in ("the ", "a ", "an "):
            if lower.startswith(art):
                text = text[len(art) :].strip()
                lower = text.lower()
                break

        # 5. Check if target directly contains URL scheme or domain extension
        if lower.startswith(("http://", "https://")) or _DOMAIN_PATTERN_RE.match(text):
            is_explicit_web = True

        return text, is_explicit_app, is_explicit_web, browser

    def get_search_url(self, destination: str, query: str) -> str:
        """Construct site-specific search URL for known destinations or default to Google."""
        norm_dest, _, _, _ = self._normalize_input(destination)
        clean_q = urllib.parse.quote_plus(query.strip())

        matched = _match_destination(norm_dest)
        if matched:
            _, info = matched
            search_fmt = info.get("search_url")
            if search_fmt:
                return search_fmt.format(query=clean_q)
            return f"{info['url'].rstrip('/')}/search?q={clean_q}"

        return f"https://www.google.com/search?q={clean_q}"

    def resolve(self, raw_input: str) -> DestinationResolution:
        """Resolve destination query with full priority logic."""
        target, is_explicit_app, is_explicit_web, browser = self._normalize_input(raw_input)
        if not target:
            return DestinationResolution(
                query=raw_input,
                target="",
                destination_type=DestinationType.UNKNOWN,
                confidence=0.0,
            )

        lower_target = target.lower()

        # Ignore confirmation / conversational keywords unless explicit app requested
        if not is_explicit_app and lower_target in (
            "yes", "no", "y", "n", "yeah", "yep", "nope", "nah", "cancel", "ok", "okay", "sure"
        ):
            return DestinationResolution(
                query=raw_input,
                target=target,
                destination_type=DestinationType.UNKNOWN,
                confidence=0.0,
            )

        # ── 1. Explicit Application Intent ──────────────────────────────────
        if is_explicit_app:
            app_res = self.app_resolver.resolve(target)
            return DestinationResolution(
                query=raw_input,
                target=app_res.canonical_name,
                destination_type=DestinationType.APPLICATION,
                app_resolution=app_res,
                confidence=app_res.confidence if app_res.is_resolved else 0.0,
                is_explicit_app=True,
            )

        # ── 2. Explicit Website Intent ──────────────────────────────────────
        if is_explicit_web:
            # A. Full URL or valid domain pattern (e.g. kaggle.com, https://github.com)
            if lower_target.startswith(("http://", "https://")) or _DOMAIN_PATTERN_RE.match(target):
                url = target if lower_target.startswith(("http://", "https://")) else f"https://{target}"
                return DestinationResolution(
                    query=raw_input,
                    target=target,
                    destination_type=DestinationType.WEBSITE,
                    url=url,
                    browser=browser,
                    confidence=1.0,
                    is_explicit_web=True,
                )

            # B. Database user alias check
            alias_url = self._check_alias(lower_target)
            if alias_url:
                return DestinationResolution(
                    query=raw_input,
                    target=target.title(),
                    destination_type=DestinationType.WEBSITE,
                    url=alias_url,
                    browser=browser,
                    confidence=0.95,
                    is_explicit_web=True,
                )

            # C. Known web destination
            matched_web = _match_destination(lower_target)
            if matched_web is not None:
                _, info = matched_web
                return DestinationResolution(
                    query=raw_input,
                    target=info["canonical_name"],
                    destination_type=DestinationType.WEBSITE,
                    url=info["url"],
                    browser=browser,
                    confidence=0.95,
                    is_explicit_web=True,
                )

            # D. Fallback domain or search
            if "." in lower_target and not lower_target.startswith("/"):
                url = f"https://{target}"
            else:
                url = f"https://www.google.com/search?q={urllib.parse.quote_plus(target)}"

            return DestinationResolution(
                query=raw_input,
                target=target.title(),
                destination_type=DestinationType.WEBSITE,
                url=url,
                browser=browser,
                confidence=0.85,
                is_explicit_web=True,
            )

        # ── 3. Installed Application Takes Precedence ───────────────────────
        # If the user did not say "in chrome" or "website", check if an installed application matches
        app_res = self.app_resolver.resolve(target)
        if app_res.is_resolved:
            # If target exactly matches a known web destination (e.g. "google", "youtube", "github")
            # but only matched an app due to loose substring matching (e.g. "google" -> "Google Chrome"),
            # the exact web destination takes precedence over the partial app match.
            matched_web = _match_destination(lower_target)
            if (
                matched_web is not None
                and app_res.canonical_name.lower() != lower_target
                and lower_target not in self.app_resolver.aliases
            ):
                _, info = matched_web
                return DestinationResolution(
                    query=raw_input,
                    target=info["canonical_name"],
                    destination_type=DestinationType.WEBSITE,
                    url=info["url"],
                    browser=browser,
                    confidence=0.95,
                )

            return DestinationResolution(
                query=raw_input,
                target=app_res.canonical_name,
                destination_type=DestinationType.APPLICATION,
                app_resolution=app_res,
                confidence=app_res.confidence,
            )

        # If target matches a known application alias or is a known desktop application
        if lower_target in self.app_resolver.aliases or self.app_resolver.is_known_app(target):
            matched_web = _match_destination(lower_target)
            if matched_web is None:
                return DestinationResolution(
                    query=raw_input,
                    target=app_res.canonical_name if app_res else target.title(),
                    destination_type=DestinationType.APPLICATION,
                    app_resolution=app_res,
                    confidence=0.9,
                )

        # ── 4. Browser Fallback: Application Not Installed ──────────────────
        # Check if target is a known web destination
        matched_web = _match_destination(lower_target)
        if matched_web is not None:
            _, info = matched_web
            return DestinationResolution(
                query=raw_input,
                target=info["canonical_name"],
                destination_type=DestinationType.WEBSITE,
                url=info["url"],
                browser=browser,
                confidence=0.95,
            )

        # Check database user aliases
        alias_url = self._check_alias(lower_target)
        if alias_url:
            return DestinationResolution(
                query=raw_input,
                target=target.title(),
                destination_type=DestinationType.WEBSITE,
                url=alias_url,
                browser=browser,
                confidence=0.95,
            )

        # Check domain pattern (e.g. "kaggle.com", "myproject.io")
        if _DOMAIN_PATTERN_RE.match(target):
            url = target if lower_target.startswith(("http://", "https://")) else f"https://{target}"
            return DestinationResolution(
                query=raw_input,
                target=target,
                destination_type=DestinationType.WEBSITE,
                url=url,
                browser=browser,
                confidence=0.9,
            )

        # ── 5. Typo Tolerance & Clarification ────────────────────────────────
        # Check fuzzy match against known web destinations
        all_web_aliases: dict[str, tuple[str, dict[str, Any]]] = {}
        for dk, dinfo in WEB_DESTINATIONS.items():
            all_web_aliases[dk] = (dk, dinfo)
            for a in dinfo["aliases"]:
                all_web_aliases[a] = (dk, dinfo)

        close_web = difflib.get_close_matches(lower_target, all_web_aliases.keys(), n=1, cutoff=0.75)
        if close_web:
            best_alias = close_web[0]
            dk, dinfo = all_web_aliases[best_alias]
            sim = difflib.SequenceMatcher(None, lower_target, best_alias).ratio()
            if sim >= 0.78:
                # High confidence typo match -> auto-resolve
                return DestinationResolution(
                    query=raw_input,
                    target=dinfo["canonical_name"],
                    destination_type=DestinationType.WEBSITE,
                    url=dinfo["url"],
                    browser=browser,
                    confidence=0.85,
                )
            elif sim >= 0.75:
                # Medium confidence -> suggest clarification
                return DestinationResolution(
                    query=raw_input,
                    target=dinfo["canonical_name"],
                    destination_type=DestinationType.AMBIGUOUS,
                    url=dinfo["url"],
                    suggested_clarification=f"Did you mean '{dinfo['canonical_name']}'?",
                    confidence=sim,
                )

        # Check fuzzy match against installed / known desktop app aliases
        all_app_aliases: dict[str, str] = {}
        for ak in DEFAULT_ALIASES:
            all_app_aliases[ak] = ak
        close_app = difflib.get_close_matches(lower_target, all_app_aliases.keys(), n=1, cutoff=0.68)
        if close_app:
            cand_app = close_app[0]
            sim = difflib.SequenceMatcher(None, lower_target, cand_app).ratio()
            if sim < 0.85:
                # Ambiguous app typo -> clarification
                disp_name = cand_app.title()
                return DestinationResolution(
                    query=raw_input,
                    target=disp_name,
                    destination_type=DestinationType.AMBIGUOUS,
                    suggested_clarification=f"Did you mean '{disp_name}'?",
                    confidence=sim,
                )

        # ── 6. No reasonable web interpretation exists ──────────────────────
        # Neither installed as an app nor matches any web destination -> report application not installed
        return DestinationResolution(
            query=raw_input,
            target=app_res.canonical_name,
            destination_type=DestinationType.APPLICATION,
            app_resolution=app_res,
            confidence=0.0,
        )

    def _check_alias(self, name: str) -> str | None:
        """Check user-configured database aliases or memory preferences."""
        db = self.database
        if db is None:
            try:
                from avi.storage.database import Database
                db = Database()
            except Exception:
                db = None

        if db is not None and hasattr(db, "get_alias"):
            try:
                rec = db.get_alias(name)
                if rec and rec.target:
                    tgt = rec.target.strip()
                    if tgt.startswith(("http://", "https://")) or "." in tgt:
                        return tgt if tgt.startswith(("http://", "https://")) else f"https://{tgt}"
            except Exception:
                pass
        return None
