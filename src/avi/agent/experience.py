"""Experience and strategy memory subsystem for persistent agent intelligence."""

import logging
from datetime import datetime, timezone

from avi.storage.database import Database
from avi.storage.models import ExperienceRecord

logger = logging.getLogger("avi.agent.experience")


class ExperienceStore:
    """Manages past execution experiences, strategies, and outcome memory."""

    def __init__(self, db: Database | None = None):
        self._db = db

    @property
    def db(self) -> Database:
        if self._db is None:
            self._db = Database()
        return self._db

    def record(self, experience: ExperienceRecord) -> None:
        """Store an experience record."""
        try:
            self.db.save_experience(experience)
            logger.info("Recorded experience for goal pattern '%s' (success=%s)", experience.goal_pattern, experience.success)
        except Exception as e:
            logger.warning("Failed to record experience: %s", e)

    def record_outcome(
        self,
        goal: str,
        strategy_name: str,
        capability_used: str = "",
        success: bool = True,
        failure_category: str | None = None,
        working_recovery: str | None = None,
        context_tags: list[str] | None = None,
        verification_result: bool | None = None,
    ) -> None:
        """Create and store an experience record from outcome components."""
        norm_goal = goal.strip().lower()
        exp = ExperienceRecord(
            goal_pattern=norm_goal[:64],
            normalized_goal=norm_goal,
            strategy_name=strategy_name,
            capability_used=capability_used,
            success=success,
            failure_category=failure_category,
            working_recovery=working_recovery,
            context_tags=context_tags or [],
            verification_result=verification_result,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self.record(exp)

    def get_failed_strategies(self, goal: str) -> list[str]:
        """Return strategy names that previously failed for this or similar goals."""
        try:
            exps = self.db.get_strategy_recommendations(goal)
            return list({e.strategy_name for e in exps if not e.success and e.strategy_name})
        except Exception as e:
            logger.warning("Error querying failed strategies: %s", e)
            return []

    def get_successful_strategies(self, goal: str) -> list[str]:
        """Return strategy names that previously succeeded for this or similar goals."""
        try:
            exps = self.db.get_strategy_recommendations(goal)
            return list({e.strategy_name for e in exps if e.success and e.strategy_name})
        except Exception as e:
            logger.warning("Error querying successful strategies: %s", e)
            return []

    def recommend_strategy(self, goal: str, available_strategies: list[str] | None = None) -> str | None:
        """Recommend a preferred strategy based on past execution history."""
        successful = self.get_successful_strategies(goal)
        failed = self.get_failed_strategies(goal)

        if available_strategies:
            # Prefer available strategies that have succeeded before and not failed
            for strat in successful:
                if strat in available_strategies and strat not in failed:
                    return strat
            # Otherwise return any available strategy that has not failed
            for strat in available_strategies:
                if strat not in failed:
                    return strat
            return available_strategies[0]

        return successful[0] if successful else None

    def search(self, query: str, limit: int = 10) -> list[ExperienceRecord]:
        """Search experiences matching query."""
        try:
            return self.db.search_experiences(query, limit=limit)
        except Exception as e:
            logger.warning("Error searching experiences: %s", e)
            return []
