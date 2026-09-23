"""Background sweep that expires stale Lead/Property locks on a timer.

Requirement §11: "Lock expiry must run server-side and must not depend on the
browser being open." This runs inside the API process itself so no separate
cron/worker deployment is required for V1.
"""

import asyncio
import logging

from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence

logger = logging.getLogger("divine_vision.lock_sweeper")

_SWEEP_INTERVAL_SECONDS = 60


class LockSweeper:
    def __init__(self, lock_persistence: LockPersistence, opportunity_persistence: OpportunityPersistence) -> None:
        self._lock_persistence = lock_persistence
        self._opportunity_persistence = opportunity_persistence
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

    async def _run_forever(self) -> None:
        while True:
            try:
                lead_count, property_count = await self._lock_persistence.release_expired_locks()
                opportunity_count = await self._opportunity_persistence.expire_stale_opportunities()
                if lead_count or property_count or opportunity_count:
                    logger.info(
                        "Expired %s lead locks, %s property locks, %s opportunities",
                        lead_count,
                        property_count,
                        opportunity_count,
                    )
            except Exception:
                logger.exception("Lock sweep iteration failed")
            await asyncio.sleep(_SWEEP_INTERVAL_SECONDS)
