"""Persistent session state for cross-process multi-turn continuity.

Persists minimal bounded assistant state across separate CLI invocations
(e.g., recommendation search followed by 'open it') in standard XDG state directory.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from avi.orchestrator.models import ConversationTurn
from avi.retrieval.models import SearchResult

logger = logging.getLogger("avi.session.state")

# Default TTL: 30 minutes
DEFAULT_STATE_TTL_SECONDS = 1800.0


def get_default_state_path() -> Path:
    """Resolve default XDG session state file path."""
    xdg_state = os.environ.get("XDG_STATE_HOME")
    if xdg_state:
        base_dir = Path(xdg_state)
    else:
        base_dir = Path.home() / ".local" / "state"
    return base_dir / "avi" / "session_state.json"


def save_session_state(
    turn: ConversationTurn,
    state_path: Path | None = None,
) -> bool:
    """Persist minimal turn state needed for subsequent cross-process follow-ups.

    Only stores search results, selected recommendations, and basic turn metadata.
    Avoids storing sensitive prompt text or unbounded data.
    """
    # Only persist if there are actionable results to follow up on
    if not turn.search_results and not turn.selected_result:
        return False

    path = state_path or get_default_state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)

        results_data = []
        if turn.search_results:
            for r in turn.search_results:
                if isinstance(r, SearchResult):
                    results_data.append(r.to_dict())
                elif isinstance(r, dict):
                    results_data.append(r)

        selected_data = None
        if turn.selected_result:
            if isinstance(turn.selected_result, SearchResult):
                selected_data = turn.selected_result.to_dict()
            elif isinstance(turn.selected_result, dict):
                selected_data = turn.selected_result

        payload: dict[str, Any] = {
            "version": 1,
            "turn_id": turn.turn_id,
            "timestamp": turn.timestamp,
            "intent_type": turn.intent_type,
            "user_query": turn.user_query[:200],  # Bounded query length
            "target": turn.target,
            "search_results": results_data,
            "selected_result": selected_data,
        }

        tmp_path = path.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        tmp_path.replace(path)
        return True
    except Exception as err:
        logger.debug("Failed to persist session state: %s", err)
        return False


def load_session_state(
    state_path: Path | None = None,
    ttl_seconds: float = DEFAULT_STATE_TTL_SECONDS,
) -> ConversationTurn | None:
    """Load and validate persisted session state if not expired."""
    path = state_path or get_default_state_path()
    if not path.is_file():
        return None

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict) or data.get("version") != 1:
            return None

        saved_time = float(data.get("timestamp", 0.0))
        now = time.time()
        if now - saved_time > ttl_seconds or saved_time > now + 300.0:
            # Expired or clock skew
            clear_session_state(path)
            return None

        # Reconstruct SearchResult objects
        search_results: list[SearchResult] = []
        raw_results = data.get("search_results") or []
        for item in raw_results:
            if isinstance(item, dict) and "id" in item and "url" in item:
                search_results.append(
                    SearchResult(
                        id=item.get("id", ""),
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        source=item.get("source", "youtube"),
                        description=item.get("description", ""),
                        channel=item.get("channel"),
                        thumbnail_url=item.get("thumbnail_url"),
                        published_at=item.get("published_at"),
                        duration=item.get("duration"),
                        metadata=item.get("metadata", {}),
                    )
                )

        selected_result: SearchResult | None = None
        raw_selected = data.get("selected_result")
        if isinstance(raw_selected, dict) and "id" in raw_selected and "url" in raw_selected:
            selected_result = SearchResult(
                id=raw_selected.get("id", ""),
                title=raw_selected.get("title", ""),
                url=raw_selected.get("url", ""),
                source=raw_selected.get("source", "youtube"),
                description=raw_selected.get("description", ""),
                channel=raw_selected.get("channel"),
                thumbnail_url=raw_selected.get("thumbnail_url"),
                published_at=raw_selected.get("published_at"),
                duration=raw_selected.get("duration"),
                metadata=raw_selected.get("metadata", {}),
            )

        return ConversationTurn(
            turn_id=int(data.get("turn_id", 1)),
            user_query=str(data.get("user_query", "")),
            intent_type=str(data.get("intent_type", "")),
            response_text="",
            target=data.get("target"),
            search_results=search_results,
            selected_result=selected_result,
            timestamp=saved_time,
        )
    except Exception as err:
        logger.debug("Failed to load session state: %s", err)
        return None


def clear_session_state(state_path: Path | None = None) -> None:
    """Remove persisted session state file."""
    path = state_path or get_default_state_path()
    try:
        if path.is_file():
            path.unlink(missing_ok=True)
    except OSError:
        pass
