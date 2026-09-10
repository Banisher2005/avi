"""SQLite-backed storage layer for AVI with schema migrations, credential screening, and thread safety."""

import json
import logging
import os
import re
import sqlite3
import threading
import uuid
from pathlib import Path
from typing import Any

from avi.storage.models import (
    ActionRecord,
    AliasRecord,
    DurableTaskRecord,
    ExperienceRecord,
    MemoryRecord,
    TaskRecord,
    utc_now_iso,
)

logger = logging.getLogger("avi.storage")

DEFAULT_DATA_DIR = Path.home() / ".local" / "share" / "avi"
DEFAULT_DB_FILE = DEFAULT_DATA_DIR / "avi.db"

# Patterns indicating potential passwords, auth tokens, or private secrets
_SENSITIVE_PATTERNS = [
    re.compile(r"(?:\b[a-zA-Z0-9_]*(?:password|passwd|secret|api[_-]?key|token)\b)(?:\s*[:=]|\s+is)\s*['\"]?[a-zA-Z0-9_\-\.]{4,}", re.IGNORECASE),
    re.compile(r"\b(?:password|passwd|secret)\s*[:=]\s*['\"]?[^\s'\"]+", re.IGNORECASE),
    re.compile(r"\b(?:sk-[a-zA-Z0-9]{15,}|ghp_[a-zA-Z0-9]{15,}|gho_[a-zA-Z0-9]{15,}|glpat-[a-zA-Z0-9_\-]{15,})\b"),
    re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
]

_REDACT_REPLACEMENTS = [
    (re.compile(r"((?:\b[a-zA-Z0-9_]*(?:password|passwd|secret|api[_-]?key|token)\b)(?:\s*[:=]|\s+is)\s*['\"]?)[a-zA-Z0-9_\-\.]{4,}", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"(\b(?:password|passwd|secret)\s*[:=]\s*['\"]?)[^\s'\"]+", re.IGNORECASE), r"\1[REDACTED]"),
    (re.compile(r"\b(?:sk-[a-zA-Z0-9]{15,}|ghp_[a-zA-Z0-9]{15,}|gho_[a-zA-Z0-9]{15,}|glpat-[a-zA-Z0-9_\-]{15,})\b"), "[REDACTED_SECRET]"),
    (re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"), "[REDACTED_PRIVATE_KEY]"),
    (re.compile(r"\bBearer\s+[a-zA-Z0-9_\-\.]{15,}\b", re.IGNORECASE), "Bearer [REDACTED]"),
]


def redact_sensitive_data(data: Any) -> Any:
    """Recursively redact passwords, auth tokens, bearer headers, and private keys."""
    if isinstance(data, str):
        cleaned = data
        for pat, repl in _REDACT_REPLACEMENTS:
            cleaned = pat.sub(repl, cleaned)
        return cleaned
    elif isinstance(data, dict):
        sanitized: dict[str, Any] = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("password", "passwd", "secret", "api_key", "apikey", "auth_token", "cookie")):
                sanitized[k] = "[REDACTED]"
            elif k_lower in ("html", "page_source", "outer_html", "inner_html") and isinstance(v, str) and len(v) > 500:
                sanitized[k] = v[:500] + "... [TRUNCATED_HTML]"
            else:
                sanitized[k] = redact_sensitive_data(v)
        return sanitized
    elif isinstance(data, list):
        return [redact_sensitive_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(redact_sensitive_data(item) for item in data)
    return data


def screen_for_sensitive_data(text: str) -> None:
    """Validate that text does not contain passwords, bearer tokens, or API secrets."""
    if not text:
        return
    for pat in _SENSITIVE_PATTERNS:
        if pat.search(text):
            raise ValueError(
                "Cannot persist content: security policy blocks storing sensitive credentials, API keys, or passwords."
            )


class Database:
    """Thread-safe SQLite database manager for AVI local persistence."""

    def __init__(self, db_path: str | Path | None = None) -> None:
        if db_path is None:
            env_path = os.getenv("AVI_DB_PATH")
            if env_path:
                self.db_path = Path(env_path)
            else:
                self.db_path = DEFAULT_DB_FILE
        elif str(db_path) == ":memory:":
            self.db_path = ":memory:"
        else:
            self.db_path = Path(db_path)

        self._lock = threading.RLock()
        self._local = threading.local()

        if self.db_path != ":memory:":
            # Ensure local directory exists with safe permissions
            parent = Path(self.db_path).parent
            parent.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(parent, 0o700)
            except OSError:
                pass

        self._init_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get or create thread-local SQLite connection."""
        conn = getattr(self._local, "conn", None)
        if conn is None:
            path_str = str(self.db_path)
            conn = sqlite3.connect(
                path_str,
                timeout=10.0,
                check_same_thread=False,
            )
            conn.row_factory = sqlite3.Row
            with conn:
                conn.execute("PRAGMA foreign_keys = ON;")
                if self.db_path != ":memory:":
                    conn.execute("PRAGMA journal_mode = WAL;")
                    conn.execute("PRAGMA busy_timeout = 5000;")
            self._local.conn = conn
        return conn

    def _init_schema(self) -> None:
        """Initialize database tables and indexes."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS preferences (
                        key TEXT PRIMARY KEY,
                        value_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS memories (
                        id TEXT PRIMARY KEY,
                        content TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'general',
                        created_at TEXT NOT NULL,
                        last_used_at TEXT NOT NULL,
                        confidence REAL NOT NULL DEFAULT 1.0,
                        metadata_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE INDEX IF NOT EXISTS idx_memories_category ON memories(category);
                    CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);

                    CREATE TABLE IF NOT EXISTS aliases (
                        alias TEXT PRIMARY KEY,
                        target TEXT NOT NULL,
                        category TEXT NOT NULL DEFAULT 'general',
                        created_at TEXT NOT NULL
                    );

                    CREATE TABLE IF NOT EXISTS task_history (
                        task_id TEXT PRIMARY KEY,
                        goal TEXT NOT NULL,
                        status TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        completed_at TEXT,
                        plan_json TEXT NOT NULL DEFAULT '{}',
                        result_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE INDEX IF NOT EXISTS idx_tasks_created ON task_history(created_at);

                    CREATE TABLE IF NOT EXISTS action_history (
                        action_id TEXT PRIMARY KEY,
                        task_id TEXT,
                        action_name TEXT NOT NULL,
                        target TEXT NOT NULL DEFAULT '',
                        arguments_json TEXT NOT NULL DEFAULT '{}',
                        success INTEGER NOT NULL DEFAULT 1,
                        error TEXT,
                        duration_ms REAL NOT NULL DEFAULT 0.0,
                        executed_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_actions_task ON action_history(task_id);
                    CREATE INDEX IF NOT EXISTS idx_actions_executed ON action_history(executed_at);

                    CREATE TABLE IF NOT EXISTS durable_tasks (
                        task_id TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL DEFAULT '',
                        goal TEXT NOT NULL,
                        normalized_goal TEXT NOT NULL DEFAULT '',
                        status TEXT NOT NULL,
                        strategy_name TEXT NOT NULL DEFAULT 'primary',
                        schema_version INTEGER NOT NULL DEFAULT 1,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        completed_at TEXT,
                        data_json TEXT NOT NULL DEFAULT '{}'
                    );

                    CREATE INDEX IF NOT EXISTS idx_durable_tasks_status ON durable_tasks(status);
                    CREATE INDEX IF NOT EXISTS idx_durable_tasks_session ON durable_tasks(session_id);
                    CREATE INDEX IF NOT EXISTS idx_durable_tasks_updated ON durable_tasks(updated_at);

                    CREATE TABLE IF NOT EXISTS execution_experiences (
                        experience_id TEXT PRIMARY KEY,
                        goal_pattern TEXT NOT NULL,
                        normalized_goal TEXT NOT NULL,
                        strategy_name TEXT NOT NULL,
                        capability_used TEXT NOT NULL,
                        success INTEGER NOT NULL,
                        failure_category TEXT,
                        working_recovery TEXT,
                        context_tags_json TEXT NOT NULL DEFAULT '[]',
                        verification_result INTEGER,
                        created_at TEXT NOT NULL
                    );

                    CREATE INDEX IF NOT EXISTS idx_experiences_pattern ON execution_experiences(goal_pattern);
                    CREATE INDEX IF NOT EXISTS idx_experiences_success ON execution_experiences(success);
                    """
                )

    # -----------------------------------------------------------------------
    # Preferences Store
    # -----------------------------------------------------------------------

    def get_preference(self, key: str, default: Any = None) -> Any:
        """Retrieve a stored user preference value."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT value_json FROM preferences WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row is None:
                return default
            try:
                return json.loads(row["value_json"])
            except (json.JSONDecodeError, TypeError):
                return row["value_json"]

    def set_preference(self, key: str, value: Any) -> None:
        """Persist a user preference value."""
        val_str = json.dumps(value)
        screen_for_sensitive_data(val_str)
        with self._lock:
            conn = self._get_connection()
            now = utc_now_iso()
            with conn:
                conn.execute(
                    """
                    INSERT INTO preferences (key, value_json, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value_json = excluded.value_json,
                        updated_at = excluded.updated_at
                    """,
                    (key, val_str, now),
                )

    def delete_preference(self, key: str) -> bool:
        """Delete a stored preference."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
                return cursor.rowcount > 0

    def list_preferences(self) -> dict[str, Any]:
        """List all stored preferences as a dictionary."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT key, value_json FROM preferences")
            res = {}
            for row in cursor.fetchall():
                try:
                    res[row["key"]] = json.loads(row["value_json"])
                except Exception:
                    res[row["key"]] = row["value_json"]
            return res

    # -----------------------------------------------------------------------
    # Memory Store
    # -----------------------------------------------------------------------

    def save_memory(self, record: MemoryRecord) -> None:
        """Insert or update a memory record."""
        screen_for_sensitive_data(record.content)
        meta_str = json.dumps(record.metadata)
        screen_for_sensitive_data(meta_str)

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO memories (id, content, category, created_at, last_used_at, confidence, metadata_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        content = excluded.content,
                        category = excluded.category,
                        last_used_at = excluded.last_used_at,
                        confidence = excluded.confidence,
                        metadata_json = excluded.metadata_json
                    """,
                    (
                        record.id,
                        record.content,
                        record.category,
                        record.created_at,
                        record.last_used_at,
                        record.confidence,
                        meta_str,
                    ),
                )

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        """Retrieve a specific memory by its unique ID."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT id, content, category, created_at, last_used_at, confidence, metadata_json FROM memories WHERE id = ?",
                (memory_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return self._row_to_memory(row)

    def search_memories(
        self,
        query: str,
        category: str | None = None,
        limit: int = 20,
    ) -> list[MemoryRecord]:
        """Search memories containing query words, optionally filtering by category."""
        clean_q = query.strip()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            params: list[Any] = []
            sql = "SELECT id, content, category, created_at, last_used_at, confidence, metadata_json FROM memories WHERE 1=1"

            if category:
                sql += " AND category = ?"
                params.append(category)

            if clean_q:
                # Substring search or word matching
                sql += " AND (content LIKE ? OR metadata_json LIKE ?)"
                like_arg = f"%{clean_q}%"
                params.extend([like_arg, like_arg])

            sql += " ORDER BY last_used_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(sql, params)
            return [self._row_to_memory(r) for r in cursor.fetchall()]

    def list_memories(
        self,
        category: str | None = None,
        limit: int = 50,
    ) -> list[MemoryRecord]:
        """List recent memories ordered by last-used timestamp."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if category:
                cursor.execute(
                    "SELECT id, content, category, created_at, last_used_at, confidence, metadata_json FROM memories WHERE category = ? ORDER BY last_used_at DESC LIMIT ?",
                    (category, limit),
                )
            else:
                cursor.execute(
                    "SELECT id, content, category, created_at, last_used_at, confidence, metadata_json FROM memories ORDER BY last_used_at DESC LIMIT ?",
                    (limit,),
                )
            return [self._row_to_memory(r) for r in cursor.fetchall()]

    def touch_memory(self, memory_id: str) -> None:
        """Update the last_used_at timestamp for a memory."""
        with self._lock:
            conn = self._get_connection()
            now = utc_now_iso()
            with conn:
                conn.execute(
                    "UPDATE memories SET last_used_at = ? WHERE id = ?",
                    (now, memory_id),
                )

    def delete_memory(self, memory_id: str) -> bool:
        """Delete a memory record by ID."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
                return cursor.rowcount > 0

    def delete_memories_matching(self, query: str, category: str | None = None) -> int:
        """Delete all memories matching query/category."""
        with self._lock:
            conn = self._get_connection()
            params: list[Any] = []
            sql = "DELETE FROM memories WHERE 1=1"
            if category:
                sql += " AND category = ?"
                params.append(category)
            if query:
                sql += " AND (content LIKE ? OR metadata_json LIKE ?)"
                like_arg = f"%{query}%"
                params.extend([like_arg, like_arg])
            with conn:
                cursor = conn.execute(sql, params)
                return cursor.rowcount

    def clear_memories(self) -> int:
        """Clear all stored memories."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM memories")
                return cursor.rowcount

    def _row_to_memory(self, row: sqlite3.Row) -> MemoryRecord:
        try:
            meta = json.loads(row["metadata_json"])
        except Exception:
            meta = {}
        return MemoryRecord(
            id=row["id"],
            content=row["content"],
            category=row["category"],
            created_at=row["created_at"],
            last_used_at=row["last_used_at"],
            confidence=float(row["confidence"]),
            metadata=meta,
        )

    # -----------------------------------------------------------------------
    # Aliases Store
    # -----------------------------------------------------------------------

    def set_alias(self, alias: str, target: str, category: str = "general") -> None:
        """Register a user alias."""
        screen_for_sensitive_data(alias)
        screen_for_sensitive_data(target)
        with self._lock:
            conn = self._get_connection()
            now = utc_now_iso()
            with conn:
                conn.execute(
                    """
                    INSERT INTO aliases (alias, target, category, created_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(alias) DO UPDATE SET
                        target = excluded.target,
                        category = excluded.category
                    """,
                    (alias.strip().lower(), target.strip(), category, now),
                )

    def get_alias(self, alias: str) -> AliasRecord | None:
        """Look up an alias."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT alias, target, category, created_at FROM aliases WHERE alias = ?",
                (alias.strip().lower(),),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return AliasRecord(
                alias=row["alias"],
                target=row["target"],
                category=row["category"],
                created_at=row["created_at"],
            )

    def list_aliases(self, category: str | None = None) -> list[AliasRecord]:
        """List all aliases."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if category:
                cursor.execute(
                    "SELECT alias, target, category, created_at FROM aliases WHERE category = ?",
                    (category,),
                )
            else:
                cursor.execute("SELECT alias, target, category, created_at FROM aliases")
            return [
                AliasRecord(
                    alias=r["alias"],
                    target=r["target"],
                    category=r["category"],
                    created_at=r["created_at"],
                )
                for r in cursor.fetchall()
            ]

    def delete_alias(self, alias: str) -> bool:
        """Delete an alias."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute(
                    "DELETE FROM aliases WHERE alias = ?",
                    (alias.strip().lower(),),
                )
                return cursor.rowcount > 0

    # -----------------------------------------------------------------------
    # Task History & Action Logging
    # -----------------------------------------------------------------------

    def record_task(self, task: TaskRecord) -> None:
        """Record or update a task execution entry."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO task_history (task_id, goal, status, created_at, completed_at, plan_json, result_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        status = excluded.status,
                        completed_at = excluded.completed_at,
                        result_json = excluded.result_json
                    """,
                    (
                        task.task_id,
                        task.goal,
                        task.status,
                        task.created_at,
                        task.completed_at,
                        json.dumps(task.plan_data),
                        json.dumps(task.result_data),
                    ),
                )

    def get_task(self, task_id: str) -> TaskRecord | None:
        """Retrieve task details by task ID."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, goal, status, created_at, completed_at, plan_json, result_json FROM task_history WHERE task_id = ?",
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            try:
                p_data = json.loads(row["plan_json"])
            except Exception:
                p_data = {}
            try:
                r_data = json.loads(row["result_json"])
            except Exception:
                r_data = {}
            return TaskRecord(
                task_id=row["task_id"],
                goal=row["goal"],
                status=row["status"],
                created_at=row["created_at"],
                completed_at=row["completed_at"],
                plan_data=p_data,
                result_data=r_data,
            )

    def list_tasks(self, limit: int = 20) -> list[TaskRecord]:
        """List recent tasks."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT task_id, goal, status, created_at, completed_at, plan_json, result_json FROM task_history ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            res = []
            for row in cursor.fetchall():
                try:
                    p = json.loads(row["plan_json"])
                except Exception:
                    p = {}
                try:
                    r = json.loads(row["result_json"])
                except Exception:
                    r = {}
                res.append(
                    TaskRecord(
                        task_id=row["task_id"],
                        goal=row["goal"],
                        status=row["status"],
                        created_at=row["created_at"],
                        completed_at=row["completed_at"],
                        plan_data=p,
                        result_data=r,
                    )
                )
            return res

    def record_action(self, action: ActionRecord) -> None:
        """Log an executed action with structured status, timing, and errors."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO action_history (
                        action_id, task_id, action_name, target, arguments_json, success, error, duration_ms, executed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(action_id) DO UPDATE SET
                        success = excluded.success,
                        error = excluded.error,
                        duration_ms = excluded.duration_ms
                    """,
                    (
                        action.action_id,
                        action.task_id,
                        action.action_name,
                        action.target,
                        json.dumps(action.arguments),
                        1 if action.success else 0,
                        action.error,
                        action.duration_ms,
                        action.executed_at,
                    ),
                )

    def list_actions(
        self,
        task_id: str | None = None,
        limit: int = 50,
    ) -> list[ActionRecord]:
        """List action execution records."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            if task_id:
                cursor.execute(
                    """
                    SELECT action_id, task_id, action_name, target, arguments_json, success, error, duration_ms, executed_at
                    FROM action_history WHERE task_id = ? ORDER BY executed_at ASC LIMIT ?
                    """,
                    (task_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT action_id, task_id, action_name, target, arguments_json, success, error, duration_ms, executed_at
                    FROM action_history ORDER BY executed_at DESC LIMIT ?
                    """,
                    (limit,),
                )
            res = []
            for r in cursor.fetchall():
                try:
                    args = json.loads(r["arguments_json"])
                except Exception:
                    args = {}
                res.append(
                    ActionRecord(
                        action_id=r["action_id"],
                        task_id=r["task_id"],
                        action_name=r["action_name"],
                        target=r["target"],
                        arguments=args,
                        success=bool(r["success"]),
                        error=r["error"],
                        duration_ms=float(r["duration_ms"]),
                        executed_at=r["executed_at"],
                    )
                )
            return res

    # -----------------------------------------------------------------------
    # Durable Task Persistence (Phase 7)
    # -----------------------------------------------------------------------

    def save_durable_task(self, record: DurableTaskRecord | dict[str, Any]) -> None:
        """Persist or update a durable, versioned task state checkpoint."""
        if isinstance(record, dict):
            task_id = record.get("task_id", "")
            goal = record.get("goal", "")
            norm_goal = record.get("normalized_goal", "")
            status = record.get("status", "pending")
            strategy_name = record.get("strategy_name", "primary")
            session_id = record.get("session_id", "")
            schema_version = record.get("schema_version", 1)
            created_at = record.get("created_at", utc_now_iso())
            updated_at = record.get("updated_at", utc_now_iso())
            completed_at = record.get("completed_at")
            data = record.get("data", {})
        else:
            task_id = record.task_id
            goal = record.goal
            norm_goal = record.normalized_goal
            status = record.status
            strategy_name = record.strategy_name
            session_id = record.session_id
            schema_version = record.schema_version
            created_at = record.created_at
            updated_at = record.updated_at
            completed_at = record.completed_at
            data = record.data

        # Screen & redact sensitive information before saving
        sanitized_data = redact_sensitive_data(data)
        data_json = json.dumps(sanitized_data)

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO durable_tasks (
                        task_id, session_id, goal, normalized_goal, status,
                        strategy_name, schema_version, created_at, updated_at,
                        completed_at, data_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(task_id) DO UPDATE SET
                        session_id = excluded.session_id,
                        status = excluded.status,
                        strategy_name = excluded.strategy_name,
                        schema_version = excluded.schema_version,
                        updated_at = excluded.updated_at,
                        completed_at = excluded.completed_at,
                        data_json = excluded.data_json
                    """,
                    (
                        task_id,
                        session_id,
                        goal,
                        norm_goal,
                        status,
                        strategy_name,
                        schema_version,
                        created_at,
                        updated_at,
                        completed_at,
                        data_json,
                    ),
                )

    def get_durable_task(self, task_id: str) -> DurableTaskRecord | None:
        """Retrieve a durable task record by task ID."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT task_id, session_id, goal, normalized_goal, status,
                       strategy_name, schema_version, created_at, updated_at,
                       completed_at, data_json
                FROM durable_tasks WHERE task_id = ?
                """,
                (task_id,),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            try:
                data_dict = json.loads(row["data_json"])
            except Exception:
                data_dict = {}
            return DurableTaskRecord(
                task_id=row["task_id"],
                session_id=row["session_id"],
                goal=row["goal"],
                normalized_goal=row["normalized_goal"],
                status=row["status"],
                strategy_name=row["strategy_name"],
                schema_version=int(row["schema_version"]),
                created_at=row["created_at"],
                updated_at=row["updated_at"],
                completed_at=row["completed_at"],
                data=data_dict,
            )

    def list_durable_tasks(
        self,
        status: str | None = None,
        session_id: str | None = None,
        limit: int = 50,
    ) -> list[DurableTaskRecord]:
        """List durable tasks with optional status or session filtering."""
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            query = (
                "SELECT task_id, session_id, goal, normalized_goal, status, "
                "strategy_name, schema_version, created_at, updated_at, completed_at, data_json "
                "FROM durable_tasks WHERE 1=1"
            )
            params: list[Any] = []
            if status:
                query += " AND status = ?"
                params.append(status)
            if session_id:
                query += " AND session_id = ?"
                params.append(session_id)
            query += " ORDER BY updated_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(query, params)
            res = []
            for row in cursor.fetchall():
                try:
                    data_dict = json.loads(row["data_json"])
                except Exception:
                    data_dict = {}
                res.append(
                    DurableTaskRecord(
                        task_id=row["task_id"],
                        session_id=row["session_id"],
                        goal=row["goal"],
                        normalized_goal=row["normalized_goal"],
                        status=row["status"],
                        strategy_name=row["strategy_name"],
                        schema_version=int(row["schema_version"]),
                        created_at=row["created_at"],
                        updated_at=row["updated_at"],
                        completed_at=row["completed_at"],
                        data=data_dict,
                    )
                )
            return res

    def update_durable_task_status(
        self,
        task_id: str,
        status: str,
        final_response: str = "",
    ) -> bool:
        """Update the lifecycle status of a durable task."""
        with self._lock:
            conn = self._get_connection()
            now = utc_now_iso()
            completed_at = now if status in ("completed", "failed", "cancelled") else None
            with conn:
                if completed_at:
                    cursor = conn.execute(
                        """
                        UPDATE durable_tasks
                        SET status = ?, updated_at = ?, completed_at = ?
                        WHERE task_id = ?
                        """,
                        (status, now, completed_at, task_id),
                    )
                else:
                    cursor = conn.execute(
                        """
                        UPDATE durable_tasks
                        SET status = ?, updated_at = ?
                        WHERE task_id = ?
                        """,
                        (status, now, task_id),
                    )
                return cursor.rowcount > 0

    def archive_durable_task(self, task_id: str) -> bool:
        """Archive or delete a durable task."""
        with self._lock:
            conn = self._get_connection()
            with conn:
                cursor = conn.execute("DELETE FROM durable_tasks WHERE task_id = ?", (task_id,))
                return cursor.rowcount > 0

    # -----------------------------------------------------------------------
    # Execution Experiences Store (Phase 7)
    # -----------------------------------------------------------------------

    def save_experience(self, record: ExperienceRecord | dict[str, Any]) -> None:
        """Store a structured execution experience record."""
        if isinstance(record, dict):
            experience_id = record.get("experience_id", str(uuid.uuid4()))
            goal_pattern = record.get("goal_pattern", "")
            normalized_goal = record.get("normalized_goal", "")
            strategy_name = record.get("strategy_name", "")
            capability_used = record.get("capability_used", "")
            success = bool(record.get("success", True))
            failure_category = record.get("failure_category")
            working_recovery = record.get("working_recovery")
            context_tags = record.get("context_tags", [])
            verification_result = record.get("verification_result")
            created_at = record.get("created_at", utc_now_iso())
        else:
            experience_id = record.experience_id
            goal_pattern = record.goal_pattern
            normalized_goal = record.normalized_goal
            strategy_name = record.strategy_name
            capability_used = record.capability_used
            success = record.success
            failure_category = record.failure_category
            working_recovery = record.working_recovery
            context_tags = record.context_tags
            verification_result = record.verification_result
            created_at = record.created_at

        # Redact any accidental secrets
        clean_recovery = redact_sensitive_data(working_recovery) if working_recovery else None
        clean_tags = redact_sensitive_data(context_tags)

        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute(
                    """
                    INSERT INTO execution_experiences (
                        experience_id, goal_pattern, normalized_goal, strategy_name,
                        capability_used, success, failure_category, working_recovery,
                        context_tags_json, verification_result, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(experience_id) DO UPDATE SET
                        success = excluded.success,
                        failure_category = excluded.failure_category,
                        working_recovery = excluded.working_recovery,
                        context_tags_json = excluded.context_tags_json,
                        verification_result = excluded.verification_result
                    """,
                    (
                        experience_id,
                        goal_pattern,
                        normalized_goal,
                        strategy_name,
                        capability_used,
                        1 if success else 0,
                        failure_category,
                        clean_recovery,
                        json.dumps(clean_tags),
                        1 if verification_result is True else (0 if verification_result is False else None),
                        created_at,
                    ),
                )

    def search_experiences(
        self,
        query: str,
        limit: int = 10,
    ) -> list[ExperienceRecord]:
        """Search execution experiences by goal pattern, normalized goal, or strategy."""
        clean = query.strip().lower()
        with self._lock:
            conn = self._get_connection()
            cursor = conn.cursor()
            sql = (
                "SELECT experience_id, goal_pattern, normalized_goal, strategy_name, "
                "capability_used, success, failure_category, working_recovery, "
                "context_tags_json, verification_result, created_at "
                "FROM execution_experiences WHERE 1=1"
            )
            params: list[Any] = []
            if clean:
                sql += (
                    " AND (goal_pattern LIKE ? OR normalized_goal LIKE ? OR strategy_name LIKE ? "
                    "OR capability_used LIKE ?)"
                )
                like_arg = f"%{clean}%"
                params.extend([like_arg, like_arg, like_arg, like_arg])
            sql += " ORDER BY created_at DESC LIMIT ?"
            params.append(limit)

            cursor.execute(sql, params)
            res = []
            for row in cursor.fetchall():
                try:
                    tags = json.loads(row["context_tags_json"])
                except Exception:
                    tags = []
                v_res = None
                if row["verification_result"] is not None:
                    v_res = bool(row["verification_result"])
                res.append(
                    ExperienceRecord(
                        experience_id=row["experience_id"],
                        goal_pattern=row["goal_pattern"],
                        normalized_goal=row["normalized_goal"],
                        strategy_name=row["strategy_name"],
                        capability_used=row["capability_used"],
                        success=bool(row["success"]),
                        failure_category=row["failure_category"],
                        working_recovery=row["working_recovery"],
                        context_tags=tags,
                        verification_result=v_res,
                        created_at=row["created_at"],
                    )
                )
            return res

    def get_strategy_recommendations(
        self,
        normalized_goal: str,
    ) -> list[ExperienceRecord]:
        """Get past strategy experience relevant to the normalized goal."""
        return self.search_experiences(normalized_goal, limit=5)

    def close(self) -> None:
        """Close current thread connection."""
        conn = getattr(self._local, "conn", None)
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
            self._local.conn = None
