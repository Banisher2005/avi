"""Fast deterministic fuzzy matcher and intelligent ranking for the Command Palette."""

from __future__ import annotations

import difflib
import re
from typing import Sequence

# Pre-compiled word splitting pattern
_WORD_RE = re.compile(r"[\s\-_]+")


def _extract_acronym(text: str) -> str:
    """Extract first letter of each word in text (e.g. 'Visual Studio Code' -> 'vsc')."""
    words = _WORD_RE.split(text.strip().lower())
    return "".join(w[0] for w in words if w)


def _is_subsequence(query: str, text: str) -> bool:
    """Return True if all characters of query appear in text in order."""
    if not query:
        return True
    if len(query) > len(text):
        return False
    it = iter(text)
    return all(char in it for char in query)


def calculate_match_score(
    query: str,
    title: str,
    aliases: Sequence[str] | None = None,
    keywords: Sequence[str] | None = None,
    description: str = "",
) -> float:
    """Compute intelligent relevance score between 0.0 and 1.0 for a palette item.

    Prioritises:
    1. Exact title match (1.0)
    2. Prefix title match (0.90 - 0.95)
    3. Acronym / initials match (0.85 - 0.88)
    4. Exact or prefix alias match (0.80 - 0.85)
    5. Substring title match (0.75)
    6. Substring alias match (0.70)
    7. Keyword match (0.65)
    8. Description match (0.55)
    9. Fuzzy subsequence / difflib match (0.40 - 0.60)
    """
    clean_q = query.strip().lower()
    if clean_q.startswith("/"):
        clean_q = clean_q[1:].strip()

    if not clean_q:
        # Empty query (e.g. just "/") gives a baseline score so items can be listed
        return 0.50

    clean_title = title.strip().lower()
    aliases_lower = [a.strip().lower() for a in (aliases or []) if a.strip()]
    keywords_lower = [k.strip().lower() for k in (keywords or []) if k.strip()]
    clean_desc = description.strip().lower()

    # 1. Exact title match
    if clean_q == clean_title:
        return 1.0

    # 2. Prefix title match
    if clean_title.startswith(clean_q):
        # Shorter remainder gets higher score (e.g. 'calc' on 'calculator' vs 'calculating something huge')
        ratio = len(clean_q) / max(1, len(clean_title))
        return round(0.90 + (0.05 * ratio), 4)

    # 3. Acronym / initials match (e.g. 'vsc' or 'vs' -> 'Visual Studio Code')
    acronym = _extract_acronym(clean_title)
    if acronym and (acronym == clean_q or acronym.startswith(clean_q)):
        return 0.88

    # Check words boundary inside title (e.g. 'code' matching 'Visual Studio Code')
    title_words = _WORD_RE.split(clean_title)
    for word in title_words:
        if word == clean_q:
            return 0.86
        if word.startswith(clean_q):
            return 0.84

    # 4. Exact or prefix alias match
    for alias in aliases_lower:
        if clean_q == alias:
            return 0.85
        if alias.startswith(clean_q):
            return 0.82
        alias_acronym = _extract_acronym(alias)
        if alias_acronym and (alias_acronym == clean_q or alias_acronym.startswith(clean_q)):
            return 0.80

    # 5. Substring title match
    if clean_q in clean_title:
        return 0.75

    # 6. Substring alias match
    for alias in aliases_lower:
        if clean_q in alias:
            return 0.70

    # 7. Keyword match
    for kw in keywords_lower:
        if clean_q == kw or kw.startswith(clean_q):
            return 0.65
        if clean_q in kw:
            return 0.60

    # 8. Description match
    if clean_desc and clean_q in clean_desc:
        return 0.55

    # 9. Subsequence match (characters in order)
    if len(clean_q) >= 2 and _is_subsequence(clean_q, clean_title):
        density = len(clean_q) / max(1, len(clean_title))
        return round(0.40 + (0.15 * density), 4)

    for alias in aliases_lower:
        if len(clean_q) >= 2 and _is_subsequence(clean_q, alias):
            density = len(clean_q) / max(1, len(alias))
            return round(0.38 + (0.15 * density), 4)

    # 10. Close typo match via difflib SequenceMatcher
    if len(clean_q) >= 3:
        sim = difflib.SequenceMatcher(None, clean_q, clean_title).ratio()
        if sim >= 0.65:
            return round(0.35 + (0.25 * sim), 4)
        for alias in aliases_lower:
            sim_alias = difflib.SequenceMatcher(None, clean_q, alias).ratio()
            if sim_alias >= 0.70:
                return round(0.35 + (0.25 * sim_alias), 4)

    return 0.0
