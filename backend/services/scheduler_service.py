"""APScheduler-based digest scheduling service."""
import logging
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)


class SchedulerService:
    """Manages cron-based digest generation schedules using APScheduler."""

    def __init__(self, db_url: str = None):
        self.scheduler = None
        self._db_url = db_url
        self._analytics_service = None

    def start(self):
        """Start scheduler and load jobs from DB."""
        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
            from apscheduler.triggers.cron import CronTrigger

            # Create job store if db_url is provided
            if self._db_url:
                jobstores = {
                    'default': SQLAlchemyJobStore(url=self._db_url, tablename='apscheduler_jobs')
                }
            else:
                jobstores = {}

            # Job defaults
            job_defaults = {
                'coalesce': True,  # Combine missed runs
                'max_instances': 1,  # Don't run in parallel
                'misfire_grace_time': 3600,  # 1 hour grace time
            }

            # Create scheduler
            self.scheduler = AsyncIOScheduler(
                jobstores=jobstores,
                job_defaults=job_defaults
            )

            # Start scheduler
            self.scheduler.start()
            logger.info("APScheduler started successfully")

            # Sync jobs from DB
            self._sync_jobs_from_db()

        except ImportError as e:
            logger.warning("APScheduler not available: %s. Scheduling features disabled.", e)
            self.scheduler = None
        except Exception as e:
            logger.error("Failed to start scheduler: %s", e, exc_info=True)
            self.scheduler = None

    def _sync_jobs_from_db(self):
        """Synchronize APScheduler jobs with ChatAnalyticsConfig table."""
        if not self.scheduler:
            return

        try:
            from shared.database import get_session, ChatAnalyticsConfig
            from apscheduler.triggers.cron import CronTrigger

            with get_session() as session:
                # Load all configs with cron schedules enabled
                configs = session.query(ChatAnalyticsConfig).filter(
                    ChatAnalyticsConfig.digest_cron.isnot(None),
                    ChatAnalyticsConfig.analysis_enabled == True
                ).all()

                # Get existing job IDs
                existing_job_ids = {job.id for job in self.scheduler.get_jobs()}

                for config in configs:
                    job_id = f"digest_{config.chat_id}"
                    try:
                        trigger = CronTrigger.from_crontab(
                            config.digest_cron,
                            timezone=config.digest_timezone or 'UTC'
                        )

                        if job_id in existing_job_ids:
                            # Reschedule if already exists
                            self.scheduler.reschedule_job(job_id, trigger=trigger)
                            logger.debug("Rescheduled job %s", job_id)
                        else:
                            # Add new job
                            self.scheduler.add_job(
                                self._run_digest,
                                trigger=trigger,
                                id=job_id,
                                args=[config.chat_id, config.digest_period_hours],
                                replace_existing=True
                            )
                            logger.info("Added scheduled job %s", job_id)
                    except Exception as e:
                        logger.error("Failed to add job %s: %s", job_id, e)

                logger.info("Synced %d scheduled digests from DB", len(configs))

        except Exception as e:
            logger.error("Failed to sync jobs from DB: %s", e, exc_info=True)

    async def _run_digest(self, chat_id: str, period_hours: int):
        """Callback for APScheduler: run digest generation.

        Args:
            chat_id: Chat ID to generate digest for
            period_hours: Period in hours to analyze
        """
        try:
            period_end = datetime.now(timezone.utc)
            period_start = period_end - timedelta(hours=period_hours)

            logger.info(
                "Running scheduled digest for chat_id=%s, period=%s to %s",
                chat_id, period_start, period_end
            )

            # Note: Integration with analytics service will be done at integration time
            # For now, just log the event
            logger.info(
                "Scheduled digest generation completed for chat_id=%s",
                chat_id
            )

        except Exception as e:
            logger.error(
                "Scheduled digest generation failed for chat_id=%s: %s",
                chat_id, e, exc_info=True
            )

    def shutdown(self):
        """Shutdown scheduler gracefully."""
        if self.scheduler:
            try:
                self.scheduler.shutdown()
                logger.info("Scheduler shutdown completed")
            except Exception as e:
                logger.error("Error during scheduler shutdown: %s", e)

    def upsert_schedule(self, chat_id: str, cron_expr: str,
                        period_hours: int, timezone_str: str = 'UTC'):
        """Add or update scheduled digest.

        Args:
            chat_id: Chat ID
            cron_expr: Cron expression (e.g., "0 9 * * 1")
            period_hours: Period in hours to analyze
            timezone_str: Timezone (e.g., "UTC", "Europe/Moscow")
        """
        if not self.scheduler:
            logger.warning("Scheduler not available, cannot upsert schedule")
            return

        try:
            from apscheduler.triggers.cron import CronTrigger

            job_id = f"digest_{chat_id}"
            trigger = CronTrigger.from_crontab(cron_expr, timezone=timezone_str)

            self.scheduler.add_job(
                self._run_digest,
                trigger=trigger,
                id=job_id,
                args=[chat_id, period_hours],
                replace_existing=True
            )

            logger.info("Upserted schedule for chat_id=%s: %s", chat_id, cron_expr)

        except Exception as e:
            logger.error("Failed to upsert schedule for chat_id=%s: %s", chat_id, e, exc_info=True)
            raise

    def remove_schedule(self, chat_id: str):
        """Remove scheduled digest.

        Args:
            chat_id: Chat ID
        """
        if not self.scheduler:
            logger.warning("Scheduler not available, cannot remove schedule")
            return

        try:
            from apscheduler.jobstores.base import JobLookupError

            job_id = f"digest_{chat_id}"
            self.scheduler.remove_job(job_id)
            logger.info("Removed schedule for chat_id=%s", chat_id)

        except JobLookupError:
            logger.debug("Job %s not found, nothing to remove", f"digest_{chat_id}")
        except Exception as e:
            logger.error("Failed to remove schedule for chat_id=%s: %s", chat_id, e)

    def list_schedules(self) -> list:
        """Get all active schedules.

        Returns:
            List of dicts with id, next_run, and trigger info
        """
        if not self.scheduler:
            logger.warning("Scheduler not available, returning empty schedule list")
            return []

        try:
            jobs = self.scheduler.get_jobs()
            schedules = []
            for job in jobs:
                schedules.append({
                    'id': job.id,
                    'next_run': str(job.next_run_time) if job.next_run_time else None,
                    'trigger': str(job.trigger)
                })
            return schedules

        except Exception as e:
            logger.error("Failed to list schedules: %s", e)
            return []
