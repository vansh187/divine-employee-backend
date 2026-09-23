"""Internal API — /api/v1/internal/* (scheduler-only, not for the employee app).

Authenticated by the `X-Cron-Secret` header, not an employee JWT, and not
rate-limited: the only caller is the external scheduler (cron-job.org).
"""

import logging

from fastapi import APIRouter

from app.core.deps import CronAuthDep, DatabaseDep
from app.core.exceptions import AppError
from app.core.lock_sweeper import LockSweeper
from app.core.responses import SuccessResponse
from app.persistence.lock_persistence import LockPersistence
from app.persistence.opportunity_persistence import OpportunityPersistence

logger = logging.getLogger("divine_vision.internal_api")

router = APIRouter(prefix="/internal", tags=["Internal"])


@router.post("/sweep", response_model=SuccessResponse[dict[str, int]])
async def sweep_expired_locks(_cron: CronAuthDep, db: DatabaseDep) -> SuccessResponse[dict[str, int]]:
    try:
        result = await LockSweeper(LockPersistence(db), OpportunityPersistence(db)).sweep_once()
        return SuccessResponse(data=result, message="Sweep completed")
    except AppError:
        raise
    except Exception:
        logger.exception("Unexpected error running lock sweep")
        raise
