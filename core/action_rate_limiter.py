"""Admin Action Rate Limiting system.

Rate-limits sensitive operations, prevents command/button spam, adds cooldowns
for dangerous actions, detects repeated failures, and temporarily blocks
excessive requests with admin alerts.
"""
import time
import threading
import logging
from collections import defaultdict
from datetime import datetime, timezone

from core.services import metrics, bus

logger = logging.getLogger("turtle.rate_limiter")

DANGEROUS_ACTIONS = {
    "purge_deep", "purge_cache", "purge_temp", "delete_vps", "reinstall_os",
    "force_stop", "restore_backup", "emergency_lockdown", "full_lockdown",
    "emergency_restart", "db_wipe", "node_isolate_all",
}

ACTION_LIMITS = {
    "purge_deep": {"count": 2, "window": 300},
    "purge_cache": {"count": 3, "window": 120},
    "purge_temp": {"count": 3, "window": 120},
    "delete_vps": {"count": 3, "window": 300},
    "reinstall_os": {"count": 1, "window": 600},
    "force_stop": {"count": 3, "window": 300},
    "restore_backup": {"count": 2, "window": 300},
    "emergency_lockdown": {"count": 1, "window": 600},
    "full_lockdown": {"count": 1, "window": 600},
    "emergency_restart": {"count": 1, "window": 600},
    "db_wipe": {"count": 1, "window": 3600},
    "deploy_vps": {"count": 5, "window": 300},
    "backup_create": {"count": 5, "window": 300},
    "config_change": {"count": 10, "window": 300},
    "admin_add": {"count": 3, "window": 300},
    "admin_remove": {"count": 3, "window": 300},
    "default": {"count": 30, "window": 60},
}

FAILURE_LIMIT = 5
FAILURE_WINDOW = 300
BLOCK_DURATION = 300
ALERT_THRESHOLD = 3


class ActionRateLimiter:
    def __init__(self):
        self._lock = threading.Lock()
        self._buckets: dict[str, list[float]] = defaultdict(list)
        self._failures: dict[str, list[float]] = defaultdict(list)
        self._blocks: dict[str, float] = {}
        self._blocked_users: dict[str, dict] = {}
        logger.info("ActionRateLimiter initialized")

    def _cleanup(self):
        now = time.time()
        stale = [k for k, v in self._buckets.items() if not v or now - v[-1] > 600]
        for k in stale:
            del self._buckets[k]
        stale_f = [k for k, v in self._failures.items() if not v or now - v[-1] > 600]
        for k in stale_f:
            del self._failures[k]
        expired = [k for k, v in self._blocks.items() if now - v > BLOCK_DURATION]
        for k in expired:
            del self._blocks[k]
            self._blocked_users.pop(k, None)

    def is_blocked(self, user_id, action):
        key = f"{user_id}:{action}"
        now = time.time()
        with self._lock:
            if key in self._blocks:
                elapsed = now - self._blocks[key]
                if elapsed < BLOCK_DURATION:
                    return True, int(BLOCK_DURATION - elapsed)
                del self._blocks[key]
                self._blocked_users.pop(key, None)
        return False, 0

    def check_rate(self, user_id, action, custom_limit=None):
        key = f"{user_id}:{action}"
        now = time.time()
        with self._lock:
            self._cleanup()
            blocked, remaining = self.is_blocked(user_id, action)
            if blocked:
                metrics.inc("rate_limiter.blocked")
                return False, f"Blocked for {remaining}s due to excessive requests"
            limit_cfg = custom_limit or ACTION_LIMITS.get(action, ACTION_LIMITS["default"])
            limit = limit_cfg["count"]
            window = limit_cfg["window"]
            timestamps = self._buckets[key]
            timestamps = [t for t in timestamps if now - t < window]
            if len(timestamps) >= limit:
                timestamps.append(now)
                self._buckets[key] = timestamps
                self._record_failure(user_id, action)
                metrics.inc("rate_limiter.exceeded")
                bus.emit("rate_limiter.exceeded", user_id=str(user_id), action=action,
                         count=len(timestamps), limit=limit)
                logger.warning("Rate limit exceeded: user=%s action=%s count=%d/%d",
                               user_id, action, len(timestamps), limit)
                return False, f"Rate limit: {len(timestamps)}/{limit} in {window}s"
            timestamps.append(now)
            self._buckets[key] = timestamps
        return True, "ok"

    def _record_failure(self, user_id, action):
        key = f"{user_id}:{action}"
        now = time.time()
        failures = self._failures[key]
        failures = [t for t in failures if now - t < FAILURE_WINDOW]
        failures.append(now)
        self._failures[key] = failures
        if len(failures) >= FAILURE_LIMIT:
            self._blocks[key] = now
            self._blocked_users[key] = {
                "user_id": user_id, "action": action,
                "blocked_at": datetime.now(timezone.utc).isoformat(),
                "failures": len(failures),
            }
            metrics.inc("rate_limiter.auto_blocked")
            bus.emit("rate_limiter.auto_blocked", user_id=str(user_id), action=action,
                     failures=len(failures))
            logger.warning("Auto-blocked user=%s action=%s after %d failures",
                           user_id, action, len(failures))
        elif len(failures) >= ALERT_THRESHOLD:
            bus.emit("rate_limiter.alert", user_id=str(user_id), action=action,
                     failures=len(failures))
            logger.warning("Rate limit alert: user=%s action=%s failures=%d",
                           user_id, action, len(failures))

    def record_success(self, user_id, action):
        key = f"{user_id}:{action}"
        with self._lock:
            self._failures.pop(key, None)

    def is_dangerous(self, action):
        return action in DANGEROUS_ACTIONS

    def get_user_stats(self, user_id):
        with self._lock:
            self._cleanup()
            stats = {}
            for key, timestamps in self._buckets.items():
                if key.startswith(f"{user_id}:"):
                    action = key.split(":", 1)[1]
                    stats[action] = len(timestamps)
            blocked = {}
            for key, info in self._blocked_users.items():
                if key.startswith(f"{user_id}:"):
                    blocked[key.split(":", 1)[1]] = info
            return {"active_actions": stats, "blocked": blocked}

    def get_global_stats(self):
        with self._lock:
            self._cleanup()
            return {
                "active_buckets": len(self._buckets),
                "blocked_users": len(self._blocked_users),
                "total_failures": sum(len(v) for v in self._failures.values()),
            }

    def force_unblock(self, user_id, action=None):
        with self._lock:
            if action:
                key = f"{user_id}:{action}"
                self._blocks.pop(key, None)
                self._blocked_users.pop(key, None)
            else:
                keys_to_remove = [k for k in self._blocks if k.startswith(f"{user_id}:")]
                for k in keys_to_remove:
                    del self._blocks[k]
                    self._blocked_users.pop(k, None)


action_rate_limiter = ActionRateLimiter()
