"""Sweep that expires stale Lead/Property locks and Opportunities.

Requirement §11: "Lock expiry must run server-side and must not depend on the
browser being open." Two ways to drive it:
  * `start()` — an in-process timer, for long-running hosts (uvicorn on a VM).
  * `sweep_once()` — called by POST /api/v1/internal/sweep from an external
    scheduler (cron-job.org), for serverless hosts where no process stays up.
Lock/opportunity lookups also expire lapsed rows themselves, so the sweep only
keeps statuses and inventory tidy; it is not needed for correctness.
"""

import asyncio
import logging

from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence
from app.persistence.signup_persistence import SignupPersistence

logger = logging.getLogger("divine_vision.lock_sweeper")

_SWEEP_INTERVAL_SECONDS = 60


class LockSweeper:
    def __init__(
        self,
        lock_persistence: LockPersistence,
        opportunity_persistence: OpportunityPersistence,
        signup_persistence: SignupPersistence | None = None,
    ) -> None:
        self._lock_persistence = lock_persistence
        self._opportunity_persistence = opportunity_persistence
        self._signup_persistence = signup_persistence
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._run_forever())

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def sweep_once(self) -> dict[str, int]:
        lead_count, property_count = await self._lock_persistence.release_expired_locks()
        opportunity_count = await self._opportunity_persistence.expire_stale_opportunities()
        signup_count = await self._signup_persistence.delete_expired() if self._signup_persistence else 0
        if lead_count or property_count or opportunity_count or signup_count:
            logger.info(
                "Expired %s lead locks, %s property locks, %s opportunities; deleted %s stale signups",
                lead_count,
                property_count,
                opportunity_count,
                signup_count,
            )
        return {
            "expired_lead_locks": lead_count,
            "expired_property_locks": property_count,
            "expired_opportunities": opportunity_count,
            "deleted_expired_signups": signup_count,
        }

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.sweep_once()
            except Exception:
                logger.exception("Lock sweep iteration failed")
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
