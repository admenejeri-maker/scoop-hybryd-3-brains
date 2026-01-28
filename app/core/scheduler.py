"""
Scoop Scheduler - TTL Cleanup Scheduler
========================================

APScheduler-based background task scheduler for automated maintenance:
- Daily cleanup of expired daily_facts
- Runs at 04:00 AM UTC to minimize impact on users

Usage:
    from app.core.scheduler import ScoopScheduler
    scheduler = ScoopScheduler()
    await scheduler.start()
    # ... on shutdown ...
    await scheduler.shutdown()
"""
import logging
from datetime import datetime
from typing import Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

logger = logging.getLogger(__name__)


async def run_daily_cleanup() -> None:
    """
    Daily cleanup job for expired daily_facts.
    
    Runs at 04:00 AM UTC via APScheduler cron trigger.
    Removes all daily_facts where expires_at < now().
    """
    from app.memory.mongo_store import db_manager
    
    try:
        if not db_manager.db:
            logger.warning("⚠️ Scheduler: MongoDB not connected, skipping cleanup")
            return
        
        start_time = datetime.utcnow()
        modified_count = await db_manager.cleanup_expired_daily_facts()
        duration_ms = (datetime.utcnow() - start_time).total_seconds() * 1000
        
        logger.info(
            f"🧹 TTL Cleanup complete: {modified_count} users cleaned, "
            f"took {duration_ms:.1f}ms"
        )
    except Exception as e:
        logger.error(f"❌ TTL Cleanup failed: {e}", exc_info=True)


class ScoopScheduler:
    """
    APScheduler wrapper for Scoop background tasks.
    
    Currently schedules:
    - `run_daily_cleanup`: Removes expired daily_facts at 04:00 UTC
    
    Designed for FastAPI lifespan integration.
    """
    
    def __init__(self):
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._started = False
    
    async def start(self) -> None:
        """Initialize and start the scheduler."""
        if self._started:
            logger.warning("Scheduler already started")
            return
        
        self._scheduler = AsyncIOScheduler()
        
        # Schedule daily cleanup at 04:00 AM UTC
        self._scheduler.add_job(
            run_daily_cleanup,
            trigger=CronTrigger(hour=4, minute=0, timezone="UTC"),
            id="daily_facts_cleanup",
            name="Daily Facts TTL Cleanup",
            replace_existing=True,
        )
        
        self._scheduler.start()
        self._started = True
        logger.info("🚀 Scheduler started: daily_facts cleanup @ 04:00 UTC")
    
    async def shutdown(self) -> None:
        """Shutdown the scheduler gracefully."""
        if self._scheduler and self._started:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("🛑 Scheduler shutdown complete")
    
    @property
    def is_running(self) -> bool:
        """Check if scheduler is running."""
        return self._started and self._scheduler is not None
    
    def get_jobs(self) -> list:
        """Get list of scheduled jobs (for testing/debugging)."""
        if self._scheduler:
            return self._scheduler.get_jobs()
        return []
