"""Failed-Action Protection system.

Prevents infinite retry loops with retry limits and cooldowns. Stops repeated
failed operations automatically, escalates persistent failures to admins,
and records all failed operations.
"""
import os
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.fail_guard")

FAIL_GUARD_PATH = os.path.join(DATA_DIR, "fail_guard.json")

MAX_RETRIES = 3
COOLDOWN_BASE = 60
COOLDOWN_MULTIPLIER = 2
ESCALATION_THRESHOLD = 3
MAX_COOLDOWN = 3600


class FailGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._failures: dict[str, dict] = load_json(FAIL_GUARD_PATH, {})
        self._escalations: list = []
        logger.info("FailGuard initialized with %d tracked operations",
                     len(self._failures))

    def _save(self):
        save_json(FAIL_GUARD_PATH, self._failures)

    def _key(self, target, operation):
        return f"{target}:{operation}"

    def record_failure(self, target, operation, error="", admin_id=None):
        key = self._key(target, operation)
        now = time.time()
        with self._lock:
            entry = self._failures.get(key, {
                "count": 0, "first_failure": now, "last_failure": now,
                "errors": [], "blocked": False, "block_until": 0,
                "escalated": False,
            })
            entry["count"] += 1
            entry["last_failure"] = now
            entry["errors"].append({"error": str(error)[:200], "time": now})
            if len(entry["errors"]) > 10:
                entry["errors"] = entry["errors"][-10:]
            cooldown = min(COOLDOWN_BASE * (COOLDOWN_MULTIPLIER ** (entry["count"] - 1)), MAX_COOLDOWN)
            if entry["count"] >= MAX_RETRIES:
                entry["blocked"] = True
                entry["block_until"] = now + cooldown
            self._failures[key] = entry
            self._save()
        metrics.inc("fail_guard.failures")
        if entry["count"] >= MAX_RETRIES and not entry.get("escalated"):
            self._escalate(target, operation, entry, admin_id)
        logger.warning("Failure recorded: target=%s op=%s count=%d", target, operation, entry["count"])
        return entry

    def record_success(self, target, operation):
        key = self._key(target, operation)
        with self._lock:
            if key in self._failures:
                del self._failures[key]
                self._save()
        metrics.inc("fail_guard.successes")

    def is_blocked(self, target, operation):
        key = self._key(target, operation)
        now = time.time()
        with self._lock:
            entry = self._failures.get(key)
            if not entry:
                return False, 0, "ok"
            if entry.get("blocked"):
                remaining = entry.get("block_until", 0) - now
                if remaining > 0:
                    return True, int(remaining), f"Blocked: {entry['count']} failures"
                entry["blocked"] = False
                entry["count"] = min(entry["count"], MAX_RETRIES - 1)
                self._failures[key] = entry
                self._save()
                return False, 0, "Cooldown expired"
        return False, 0, "ok"

    def _escalate(self, target, operation, entry, admin_id=None):
        key = self._key(target, operation)
        with self._lock:
            entry["escalated"] = True
            self._failures[key] = entry
            self._save()
        escalation = {
            "target": target,
            "operation": operation,
            "failures": entry["count"],
            "admin_id": str(admin_id) if admin_id else None,
            "last_error": entry["errors"][-1]["error"] if entry["errors"] else "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._escalations.append(escalation)
        if len(self._escalations) > 100:
            self._escalations = self._escalations[-50:]
        metrics.inc("fail_guard.escalations")
        bus.emit("fail_guard.escalation", escalation)
        logger.error("FAILURE ESCALATION: target=%s op=%s failures=%d", target, operation, entry["count"])

    def get_status(self, target=None, operation=None):
        with self._lock:
            if target and operation:
                key = self._key(target, operation)
                return self._failures.get(key, {})
            if target:
                return {k: v for k, v in self._failures.items() if k.startswith(f"{target}:")}
            return dict(self._failures)

    def get_escalations(self, limit=20):
        return list(self._escalations[-limit:])

    def reset(self, target, operation):
        key = self._key(target, operation)
        with self._lock:
            if key in self._failures:
                del self._failures[key]
                self._save()
                return True
        return False

    def get_stats(self):
        with self._lock:
            total = len(self._failures)
            blocked = sum(1 for v in self._failures.values() if v.get("blocked"))
            escalated = sum(1 for v in self._failures.values() if v.get("escalated"))
        return {
            "tracked_operations": total,
            "currently_blocked": blocked,
            "escalated": escalated,
            "total_escalations": len(self._escalations),
        }


fail_guard = FailGuard()
