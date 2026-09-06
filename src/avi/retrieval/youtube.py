"""YouTube search retrieval provider for structured video metadata."""

import json
import logging
import re
import socket
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from avi.retrieval.base import BaseSearchProvider
from avi.retrieval.models import SearchOptions, SearchResult, SearchResults

logger = logging.getLogger("avi.retrieval.youtube")

# Strict regex for valid YouTube video IDs (11 base64-style characters)
_VIDEO_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{11}$")


def validate_youtube_url(url: str) -> bool:
    """Validate that a URL is strictly an HTTPS YouTube watch or video URL."""
    if not isinstance(url, str) or not url.strip():
        return False
    clean = url.strip()
    try:
        parsed = urllib.parse.urlparse(clean)
    except Exception:
        return False

    if parsed.scheme != "https":
        return False

    netloc = parsed.netloc.lower()
    if netloc not in ("www.youtube.com", "youtube.com", "m.youtube.com", "youtu.be"):
        return False

    if netloc == "youtu.be":
        vid = parsed.path.strip("/")
        return bool(_VIDEO_ID_RE.match(vid))

    if parsed.path.rstrip("/") in ("/watch", "/v"):
        qs = urllib.parse.parse_qs(parsed.query)
        v = qs.get("v", [""])[0]
        return bool(_VIDEO_ID_RE.match(v))

    return False


def parse_duration_seconds(duration_str: str | None) -> int | None:
    """Parse duration string like '14:32' or '1:12:45' into total seconds."""
    if not duration_str or not isinstance(duration_str, str):
        return None
    parts = duration_str.strip().split(":")
    try:
        if len(parts) == 2:
            return int(parts[0]) * 60 + int(parts[1])
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
        if len(parts) == 1 and parts[0].isdigit():
            return int(parts[0])
    except (ValueError, TypeError):
        return None
    return None


class YouTubeSearchRetrievalProvider(BaseSearchProvider):
    """Retrieve structured search results directly from YouTube client endpoint."""

    source_name: str = "youtube"
    ENDPOINT = "https://www.youtube.com/youtubei/v1/search"

    def validate_url(self, url: str) -> bool:
        return validate_youtube_url(url)

    def search(self, query: str, options: SearchOptions | None = None) -> SearchResults:
        """Search YouTube and return bounded normalized SearchResults."""
        clean_query = query.strip()
        if not clean_query:
            return SearchResults(
                query=query,
                results=[],
                total_found=0,
                source=self.source_name,
                error="Search query cannot be empty.",
            )

        max_results = min(max(options.max_results, 1), 25) if options else 10
        timeout = options.timeout if options else 6.0

        try:
            results = self._fetch_youtubei_results(
                clean_query, max_results=max_results, timeout=timeout
            )
            return SearchResults(
                query=clean_query,
                results=results,
                total_found=len(results),
                source=self.source_name,
            )
        except urllib.error.HTTPError as err:
            logger.warning("YouTube API HTTP error %d: %s", err.code, err.reason)
            return SearchResults(
                query=clean_query,
                results=[],
                total_found=0,
                source=self.source_name,
                error=f"YouTube service returned HTTP {err.code}.",
            )
        except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as err:
            logger.warning("YouTube network error: %s", err)
            return SearchResults(
                query=clean_query,
                results=[],
                total_found=0,
                source=self.source_name,
                error="Network unavailable or search request timed out.",
            )
        except Exception as err:
            logger.error(
                "Unexpected error retrieving YouTube search results: %s", err, exc_info=True
            )
            return SearchResults(
                query=clean_query,
                results=[],
                total_found=0,
                source=self.source_name,
                error=f"Retrieval error: {err}",
            )

    def _fetch_youtubei_results(
        self, query: str, max_results: int, timeout: float
    ) -> list[SearchResult]:
        """Query YouTube InnerTube endpoint and extract normalized video items."""
        payload = {
            "context": {
                "client": {
                    "clientName": "WEB",
                    "clientVersion": "2.20230522.01.00",
                }
            },
            "query": query,
        }

        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.ENDPOINT,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "avi/0.4.0 (Linux; Desktop Assistant)",
            },
            method="POST",
        )

        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))

        return self._extract_items_from_json(data, max_results=max_results)

    def _extract_items_from_json(
        self, data: dict[str, Any], max_results: int
    ) -> list[SearchResult]:
        """Extract and sanitize video items from YouTube JSON structure."""
        results: list[SearchResult] = []
        try:
            sections = (
                data.get("contents", {})
                .get("twoColumnSearchResultsRenderer", {})
                .get("primaryContents", {})
                .get("sectionListRenderer", {})
                .get("contents", [])
            )
            for section in sections:
                items = section.get("itemSectionRenderer", {}).get("contents", [])
                for item in items:
                    v = item.get("videoRenderer")
                    if not v or not isinstance(v, dict):
                        continue

                    vid_id = v.get("videoId")
                    if not vid_id or not _VIDEO_ID_RE.match(vid_id):
                        continue

                    title_runs = v.get("title", {}).get("runs", [])
                    title = "".join(r.get("text", "") for r in title_runs).strip()
                    if not title:
                        continue

                    owner_runs = v.get("ownerText", {}).get("runs", [])
                    channel = "".join(r.get("text", "") for r in owner_runs).strip() or None

                    duration = v.get("lengthText", {}).get("simpleText", "") or None
                    duration_sec = parse_duration_seconds(duration)

                    # Extract description snippet
                    desc_snippets = v.get("detailedMetadataSnippets", [])
                    desc = ""
                    if desc_snippets and isinstance(desc_snippets, list):
                        desc_runs = desc_snippets[0].get("snippetText", {}).get("runs", [])
                        desc = "".join(r.get("text", "") for r in desc_runs).strip()
                    if not desc:
                        desc_runs = v.get("descriptionSnippet", {}).get("runs", [])
                        desc = "".join(r.get("text", "") for r in desc_runs).strip()

                    url = f"https://www.youtube.com/watch?v={vid_id}"
                    if not self.validate_url(url):
                        continue

                    # Construct internal result ID (1-indexed based on current count)
                    item_id = f"yt_res_{len(results) + 1}"
                    results.append(
                        SearchResult(
                            id=item_id,
                            title=title,
                            url=url,
                            source=self.source_name,
                            description=desc,
                            channel=channel,
                            duration=duration,
                            metadata={
                                "video_id": vid_id,
                                "duration_seconds": duration_sec,
                            },
                        )
                    )

                    if len(results) >= max_results:
                        return results
        except Exception as err:
            logger.warning("Error parsing YouTube JSON payload: %s", err)

        return results
