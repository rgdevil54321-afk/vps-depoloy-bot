"""Health Verification system.

After every automated recovery or sensitive infrastructure operation:
- Runs a health check
- Verifies the expected result
- Marks operation as successful only after verification
- If verification fails, stops further retries and creates an incident
"""
import time
import logging
import threading
from datetime import datetime, timezone

from core.services import metrics, bus

logger = logging.getLogger("turtle.health_verify")


class HealthVerifier:
    def __init__(self):
        self._lock = threading.Lock()
        self._verifications: list = []
        self._pending: dict[str, dict] = {}
        logger.info("HealthVerifier initialized")

    def verify_operation(self, op_id, operation, target, checks, timeout=60):
        results = {}
        all_ok = True
        for check_name, check_fn in checks.items():
            try:
                ok, detail = check_fn()
                results[check_name] = {"ok": ok, "detail": detail}
                if not ok:
                    all_ok = False
            except Exception as e:
                results[check_name] = {"ok": False, "detail": str(e)}
                all_ok = False
        entry = {
            "op_id": op_id, "operation": operation, "target": str(target),
            "results": results, "all_ok": all_ok,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._verifications.append(entry)
            if len(self._verifications) > 1000:
                self._verifications = self._verifications[-500:]
        if all_ok:
            metrics.inc(f"health_verify.pass.{operation}")
        else:
            metrics.inc(f"health_verify.fail.{operation}")
            bus.emit("health_verify.failed", entry)
            logger.warning("Health verification failed: op=%s target=%s", operation, target)
        return entry

    def register_pending(self, op_id, operation, target, expected, timeout=60):
        with self._lock:
            self._pending[op_id] = {
                "operation": operation, "target": str(target),
                "expected": expected, "timeout": timeout,
                "registered_at": time.time(),
            }

    def complete_pending(self, op_id, actual_results):
        with self._lock:
            pending = self._pending.pop(op_id, None)
        if not pending:
            return None
        expected = pending.get("expected", {})
        match = True
        mismatches = []
        for key, expected_val in expected.items():
            actual_val = actual_results.get(key)
            if actual_val != expected_val:
                match = False
                mismatches.append(f"{key}: expected={expected_val} actual={actual_val}")
        entry = {
            "op_id": op_id, "operation": pending["operation"],
            "target": pending["target"], "match": match,
            "mismatches": mismatches,
            "verified_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._verifications.append(entry)
        if not match:
            metrics.inc(f"health_verify.mismatch.{pending['operation']}")
            bus.emit("health_verify.mismatch", entry)
            logger.error("Health verification mismatch: op=%s mismatches=%s",
                         pending["operation"], mismatches)
        return entry

    def verify_container_health(self, container_name, check_fn):
        try:
            ok, detail = check_fn()
            entry = {
                "type": "container_health", "target": container_name,
                "ok": ok, "detail": detail,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            with self._lock:
                self._verifications.append(entry)
            if ok:
                metrics.inc("health_verify.container.ok")
            else:
                metrics.inc("health_verify.container.fail")
                bus.emit("health_verify.container_failed", entry)
            return ok, detail
        except Exception as e:
            return False, str(e)

    def verify_backup_integrity(self, backup_name, check_fn):
        try:
            ok, detail = check_fn()
            entry = {
                "type": "backup_integrity", "target": backup_name,
                "ok": ok, "detail": detail,
                "verified_at": datetime.now(timezone.utc).isoformat(),
            }
            with self._lock:
                self._verifications.append(entry)
            return ok, detail
        except Exception as e:
            return False, str(e)

    def get_recent_verifications(self, limit=20):
        with self._lock:
            return list(self._verifications[-limit:])

    def get_stats(self):
        with self._lock:
            total = len(self._verifications)
            passed = sum(1 for v in self._verifications if v.get("all_ok") or v.get("ok"))
            failed = total - passed
        return {"total": total, "passed": passed, "failed": failed}

    def get_pending(self):
        now = time.time()
        with self._lock:
            expired = [k for k, v in self._pending.items()
                       if now - v["registered_at"] > v["timeout"]]
            for k in expired:
                del self._pending[k]
            return dict(self._pending)


health_verifier = HealthVerifier()
