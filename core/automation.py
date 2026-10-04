"""Automation Engine for Turtle Nodes Discord hosting bot."""
import asyncio
import logging
import time
import threading
from collections import deque
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.automation")

HISTORY_PATH = str(DATA_DIR / "job_history.json") if hasattr(DATA_DIR, "__truediv__") else None


def _default_history_path():
    import os
    return os.path.join(str(DATA_DIR), "job_history.json")


if HISTORY_PATH is None:
    HISTORY_PATH = _default_history_path()


def _utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Job data class
# ---------------------------------------------------------------------------

class Job:
    """Represents a single scheduled automation job."""

    __slots__ = (
        "name", "func_name", "interval_seconds", "enabled",
        "last_run", "next_run", "run_count", "fail_count", "last_error",
    )

    def __init__(self, name, func_name, interval_seconds, enabled=True):
        self.name = name
        self.func_name = func_name
        self.interval_seconds = interval_seconds
        self.enabled = enabled
        self.last_run = None
        self.next_run = time.time() + interval_seconds
        self.run_count = 0
        self.fail_count = 0
        self.last_error = None

    # -- properties --------------------------------------------------------

    @property
    def is_due(self):
        if not self.enabled:
            return False
        return time.time() >= self.next_run

    @property
    def status_str(self):
        if not self.enabled:
            return "disabled"
        if self.fail_count > 0 and self.run_count == 0:
            return "error"
        if self.last_error:
            return "degraded"
        return "healthy"

    @property
    def next_run_str(self):
        if self.next_run is None:
            return "n/a"
        remaining = self.next_run - time.time()
        if remaining <= 0:
            return "now"
        if remaining < 60:
            return f"{int(remaining)}s"
        if remaining < 3600:
            return f"{int(remaining // 60)}m {int(remaining % 60)}s"
        hours = int(remaining // 3600)
        mins = int((remaining % 3600) // 60)
        return f"{hours}h {mins}m"

    # -- serialisation -----------------------------------------------------

    def to_dict(self):
        return {
            "name": self.name,
            "func_name": self.func_name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "next_run": self.next_run,
            "run_count": self.run_count,
            "fail_count": self.fail_count,
            "last_error": self.last_error,
            "status": self.status_str,
            "next_run_display": self.next_run_str,
        }

    def __repr__(self):
        return (
            f"<Job {self.name!r} func={self.func_name!r} "
            f"interval={self.interval_seconds}s enabled={self.enabled}>"
        )


# ---------------------------------------------------------------------------
# Job Scheduler
# ---------------------------------------------------------------------------

class JobScheduler:
    """Core scheduler that ticks, executes, and tracks automation jobs."""

    def __init__(self, bot):
        self.bot = bot
        self.jobs: dict[str, Job] = {}
        self.func_map: dict[str, callable] = {}
        self._history: deque = deque(maxlen=200)
        self._lock = threading.Lock()
        self._running = False
        self._tick_count = 0

    # -- registration ------------------------------------------------------

    def register_job(self, name, func_name, interval_seconds, enabled=True):
        if name in self.jobs:
            logger.warning("Job %s already registered, overwriting", name)
        job = Job(name, func_name, interval_seconds, enabled=enabled)
        with self._lock:
            self.jobs[name] = job
        logger.info(
            "Registered job %s (func=%s, interval=%ds, enabled=%s)",
            name, func_name, interval_seconds, enabled,
        )
        metrics.inc("scheduler.jobs_registered")
        return job

    def register_func(self, name, func):
        self.func_map[name] = func

    def unregister_job(self, name):
        with self._lock:
            removed = self.jobs.pop(name, None)
        if removed:
            logger.info("Unregistered job %s", name)
            metrics.inc("scheduler.jobs_unregistered")
        return removed

    def enable_job(self, name):
        with self._lock:
            job = self.jobs.get(name)
            if job is None:
                return None
            job.enabled = True
            if job.next_run is None or job.next_run < time.time():
                job.next_run = time.time() + job.interval_seconds
        logger.info("Enabled job %s", name)
        metrics.inc("scheduler.jobs_enabled")
        return job

    def disable_job(self, name):
        with self._lock:
            job = self.jobs.get(name)
            if job is None:
                return None
            job.enabled = False
        logger.info("Disabled job %s", name)
        metrics.inc("scheduler.jobs_disabled")
        return job

    # -- query -------------------------------------------------------------

    def get_jobs(self):
        with self._lock:
            return [j.to_dict() for j in self.jobs.values()]

    def get_job(self, name):
        with self._lock:
            job = self.jobs.get(name)
            return job.to_dict() if job else None

    def get_history(self, n=20):
        with self._lock:
            return list(self._history)[:n]

    def get_stats(self):
        with self._lock:
            total = len(self.jobs)
            enabled = sum(1 for j in self.jobs.values() if j.enabled)
            total_runs = sum(j.run_count for j in self.jobs.values())
            total_fails = sum(j.fail_count for j in self.jobs.values())
        return {
            "total": total,
            "enabled": enabled,
            "disabled": total - enabled,
            "total_runs": total_runs,
            "total_fails": total_fails,
        }

    # -- execution ---------------------------------------------------------

    async def _execute_job(self, job):
        func = self.func_map.get(job.func_name)
        if func is None:
            job.fail_count += 1
            job.last_error = f"Function {job.func_name!r} not found in func_map"
            self._record(job, False, job.last_error)
            logger.error("Job %s: function %s not registered", job.name, job.func_name)
            metrics.inc("scheduler.job_missing_func")
            return None

        start = time.monotonic()
        success = False
        result = None
        error_msg = None

        try:
            result = await func()
            success = True
            job.last_error = None
        except Exception as exc:
            error_msg = str(exc)
            job.last_error = error_msg
            job.fail_count += 1
            logger.exception("Job %s failed: %s", job.name, error_msg)
            metrics.inc("scheduler.job_failures")

        elapsed = time.monotonic() - start
        job.run_count += 1
        job.last_run = time.time()
        job.next_run = time.time() + job.interval_seconds

        metrics.inc("scheduler.job_executions")
        metrics.timer(f"scheduler.job.{job.name}.duration", round(elapsed, 4))
        metrics.set(f"scheduler.job.{job.name}.last_duration", round(elapsed, 4))

        self._record(job, success, error_msg, result, elapsed)

        if success:
            bus.emit("job:completed", name=job.name, elapsed=elapsed, result=result)
        else:
            bus.emit("job:failed", name=job.name, error=error_msg)

        return {"success": success, "result": result, "error": error_msg, "elapsed": elapsed}

    def _record(self, job, success, error=None, result=None, elapsed=0.0):
        entry = {
            "job": job.name,
            "func": job.func_name,
            "success": success,
            "error": error,
            "result_summary": _summarise_result(result),
            "elapsed_s": round(elapsed, 4),
            "ts": _utcnow().isoformat(),
            "run_count": job.run_count,
            "fail_count": job.fail_count,
        }
        with self._lock:
            self._history.appendleft(entry)

    # -- public run --------------------------------------------------------

    async def run_job_now(self, name):
        with self._lock:
            job = self.jobs.get(name)
        if job is None:
            return {"error": f"Job {name!r} not found"}
        logger.info("Manually executing job %s", name)
        metrics.inc("scheduler.manual_runs")
        return await self._execute_job(job)

    # -- tick loop ---------------------------------------------------------

    async def tick(self):
        self._tick_count += 1
        due_jobs = []
        with self._lock:
            for job in self.jobs.values():
                if job.is_due:
                    due_jobs.append(job)

        if due_jobs:
            logger.debug(
                "Tick #%d: %d job(s) due [%s]",
                self._tick_count,
                len(due_jobs),
                ", ".join(j.name for j in due_jobs),
            )

        for job in due_jobs:
            asyncio.create_task(self._safe_execute(job))

        metrics.set("scheduler.tick_count", self._tick_count)
        metrics.set("scheduler.due_this_tick", len(due_jobs))

    async def _safe_execute(self, job):
        try:
            await self._execute_job(job)
        except Exception as exc:
            logger.exception("Unexpected error in job %s: %s", job.name, exc)
            metrics.inc("scheduler.unhandled_errors")

    # -- persistence helpers -----------------------------------------------

    def save_state(self):
        state = {
            "jobs": {
                name: {
                    "enabled": job.enabled,
                    "run_count": job.run_count,
                    "fail_count": job.fail_count,
                    "last_run": job.last_run,
                    "last_error": job.last_error,
                }
                for name, job in self.jobs.items()
            },
            "tick_count": self._tick_count,
        }
        ok = save_json(HISTORY_PATH, state)
        if ok:
            logger.debug("Scheduler state saved")
        return ok

    def load_state(self):
        state = load_json(HISTORY_PATH, {})
        saved_jobs = state.get("jobs", {})
        restored = 0
        for name, data in saved_jobs.items():
            job = self.jobs.get(name)
            if job is None:
                continue
            job.enabled = data.get("enabled", job.enabled)
            job.run_count = data.get("run_count", job.run_count)
            job.fail_count = data.get("fail_count", job.fail_count)
            job.last_run = data.get("last_run", job.last_run)
            job.last_error = data.get("last_error", job.last_error)
            restored += 1
        self._tick_count = state.get("tick_count", 0)
        logger.info("Restored state for %d job(s)", restored)
        return restored


# ---------------------------------------------------------------------------
# Result summariser helper
# ---------------------------------------------------------------------------

def _summarise_result(result, max_len=200):
    if result is None:
        return None
    s = str(result)
    if len(s) > max_len:
        return s[:max_len] + "..."
    return s


# ---------------------------------------------------------------------------
# Default placeholder job functions
# ---------------------------------------------------------------------------

async def job_vps_expiry_check():
    logger.info("Job vps_expiry_check executed")
    metrics.inc("job.vps_expiry_check")
    return True


async def job_backup_cleanup():
    logger.info("Job backup_cleanup executed")
    metrics.inc("job.backup_cleanup")
    return True


async def job_health_monitor():
    logger.info("Job health_monitor executed")
    metrics.inc("job.health_monitor")
    return True


async def job_metrics_collect():
    logger.info("Job metrics_collect executed")
    metrics.inc("job.metrics_collect")
    return True


# ---------------------------------------------------------------------------
# Scheduler bootstrap
# ---------------------------------------------------------------------------

async def start_scheduler(bot):
    scheduler = JobScheduler(bot)

    # register default job functions into the function map
    scheduler.register_func("vps_expiry_check", job_vps_expiry_check)
    scheduler.register_func("backup_cleanup", job_backup_cleanup)
    scheduler.register_func("health_monitor", job_health_monitor)
    scheduler.register_func("metrics_collect", job_metrics_collect)

    # register default jobs
    scheduler.register_job("vps_expiry_check", "vps_expiry_check", 3600)
    scheduler.register_job("backup_cleanup", "backup_cleanup", 86400)
    scheduler.register_job("health_monitor", "health_monitor", 60)
    scheduler.register_job("metrics_collect", "metrics_collect", 30)

    # restore persisted state if available
    scheduler.load_state()

    # attach reference to the bot instance
    bot.scheduler = scheduler

    logger.info(
        "Scheduler started with %d jobs (tick_count=%d)",
        len(scheduler.jobs),
        scheduler._tick_count,
    )
    metrics.inc("scheduler.starts")
    bus.emit("scheduler:started", job_count=len(scheduler.jobs))

    # -- background tick loop ----------------------------------------------
    scheduler._running = True

    async def _tick_loop():
        while scheduler._running:
            try:
                await scheduler.tick()
            except Exception as exc:
                logger.exception("Tick loop error: %s", exc)
                metrics.inc("scheduler.tick_errors")
            await asyncio.sleep(30)

        logger.info("Tick loop stopped, saving state")
        scheduler.save_state()
        bus.emit("scheduler:stopped")

    asyncio.create_task(_tick_loop())
    logger.info("Background tick loop launched (interval=30s)")

    return scheduler
