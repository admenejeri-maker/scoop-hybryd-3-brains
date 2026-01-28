"""
Test Scoop Scheduler
====================

Tests for the APScheduler-based TTL cleanup scheduler.
Verifies configuration without waiting for actual execution time.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


class TestScoopScheduler:
    """Tests for ScoopScheduler initialization and configuration."""
    
    @pytest.mark.asyncio
    async def test_scheduler_starts_successfully(self):
        """Verify scheduler starts and is running."""
        from app.core.scheduler import ScoopScheduler
        
        scheduler = ScoopScheduler()
        assert not scheduler.is_running
        
        await scheduler.start()
        assert scheduler.is_running
        
        await scheduler.shutdown()
        assert not scheduler.is_running
    
    @pytest.mark.asyncio
    async def test_scheduler_adds_cleanup_job(self):
        """Verify that the cleanup job is added with correct configuration."""
        from app.core.scheduler import ScoopScheduler
        
        scheduler = ScoopScheduler()
        await scheduler.start()
        
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1
        
        job = jobs[0]
        assert job.id == "daily_facts_cleanup"
        assert job.name == "Daily Facts TTL Cleanup"
        
        # Verify trigger is CronTrigger at 04:00 UTC
        from apscheduler.triggers.cron import CronTrigger
        assert isinstance(job.trigger, CronTrigger)
        
        await scheduler.shutdown()
    
    @pytest.mark.asyncio
    async def test_scheduler_double_start_is_safe(self):
        """Verify calling start() twice doesn't cause issues."""
        from app.core.scheduler import ScoopScheduler
        
        scheduler = ScoopScheduler()
        await scheduler.start()
        await scheduler.start()  # Should not raise
        
        jobs = scheduler.get_jobs()
        assert len(jobs) == 1  # Still only one job
        
        await scheduler.shutdown()
    
    @pytest.mark.asyncio
    async def test_shutdown_without_start_is_safe(self):
        """Verify calling shutdown() without start() doesn't crash."""
        from app.core.scheduler import ScoopScheduler
        
        scheduler = ScoopScheduler()
        await scheduler.shutdown()  # Should not raise


class TestRunDailyCleanup:
    """Tests for the cleanup job function."""
    
    @pytest.mark.asyncio
    async def test_cleanup_calls_db_manager(self):
        """Verify cleanup function calls db_manager.cleanup_expired_daily_facts."""
        from app.core.scheduler import run_daily_cleanup
        
        with patch("app.memory.mongo_store.db_manager") as mock_db:
            mock_db.db = MagicMock()  # Simulate connected DB
            mock_db.cleanup_expired_daily_facts = AsyncMock(return_value=5)
            
            await run_daily_cleanup()
            
            mock_db.cleanup_expired_daily_facts.assert_called_once()
    
    @pytest.mark.asyncio
    async def test_cleanup_skips_when_db_not_connected(self):
        """Verify cleanup skips gracefully when MongoDB not connected."""
        from app.core.scheduler import run_daily_cleanup
        
        with patch("app.memory.mongo_store.db_manager") as mock_db:
            mock_db.db = None  # Simulate not connected
            mock_db.cleanup_expired_daily_facts = AsyncMock()
            
            await run_daily_cleanup()  # Should not raise
            
            mock_db.cleanup_expired_daily_facts.assert_not_called()
    
    @pytest.mark.asyncio
    async def test_cleanup_handles_exceptions(self):
        """Verify cleanup handles exceptions without crashing."""
        from app.core.scheduler import run_daily_cleanup
        
        with patch("app.memory.mongo_store.db_manager") as mock_db:
            mock_db.db = MagicMock()
            mock_db.cleanup_expired_daily_facts = AsyncMock(
                side_effect=Exception("DB Error")
            )
            
            # Should not raise - just log the error
            await run_daily_cleanup()
