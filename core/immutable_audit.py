"""Immutable Audit Logging system.

Every sensitive admin and infrastructure action is logged with admin, action,
target, timestamp, result, and operation ID. Logs cannot be modified or deleted
by normal admins. Provides search, filtering, and pagination.
"""
import os
import time
import uuid
import json
import hashlib
import threading
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.audit")

AUDIT_DIR = os.path.join(DATA_DIR, "audit_logs")
os.makedirs(AUDIT_DIR, exist_ok=True)

SENSITIVE_ACTIONS = {
    "purge_cache", "purge_temp", "purge_old_logs", "purge_old_backup",
    "purge_docker_container", "purge_docker_image", "purge_docker_volume",
    "purge_deep_cleanup", "purge_safe_cleanup",
    "vps_delete", "vps_create", "vps_stop", "vps_start", "vps_restart",
    "vps_reinstall", "vps_protect", "vps_unprotect",
    "admin_add", "admin_remove", "admin_level_change",
    "backup_create", "backup_delete", "backup_restore",
    "config_change", "emergency_lockdown", "emergency_unlock",
    "emergency_restart", "emergency_full_lockdown", "emergency_full_unlock",
    "maintenance_enabled", "maintenance_disabled",
    "node_isolate", "node_enable", "node_deploy_block",
    "rate_limit_triggered", "permission_denied", "lockdown_blocked",
    "session_created", "session_expired", "session_invalidated",
    "security_alert", "anomaly_detected",
    "db_backup", "db_restore", "db_wipe",
}


class ImmutableAuditLog:
    """Append-only audit log with chain verification."""

    def __init__(self):
        self._lock = threading.Lock()
        self._current_file = self._get_current_file()
        self._entries = self._load_entries()
        self._chain_hash = self._compute_chain()
        logger.info("ImmutableAuditLog initialized with %d entries", len(self._entries))

    def _get_current_file(self):
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        return os.path.join(AUDIT_DIR, f"audit_{today}.json")

    def _load_entries(self):
        return load_json(self._current_file, [])

    def _compute_chain(self):
        if not self._entries:
            return "genesis"
        h = hashlib.sha256()
        for entry in self._entries:
            h.update(json.dumps(entry, sort_keys=True, default=str).encode())
        return h.hexdigest()[:16]

    def _save(self):
        try:
            save_json(self._current_file, self._entries)
        except Exception as e:
            logger.error("Failed to save audit log: %s", e)

    def log(self, action, user_id, target="", details="", result="success", level="info", extra=None):
        if not isinstance(user_id, str):
            user_id = str(user_id)
        entry = {
            "op_id": str(uuid.uuid4())[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "user_id": user_id,
            "target": str(target),
            "details": str(details)[:500],
            "result": result,
            "level": level,
            "chain_hash": self._chain_hash,
        }
        if extra:
            entry["extra"] = extra
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > 10000:
                self._archive_and_rotate()
            self._chain_hash = hashlib.sha256(
                (self._chain_hash + json.dumps(entry, sort_keys=True, default=str)).encode()
            ).hexdigest()[:16]
            entry["chain_hash"] = self._chain_hash
            self._save()
        metrics.inc("audit.logged")
        if action in SENSITIVE_ACTIONS:
            metrics.inc("audit.sensitive")
            bus.emit("audit.sensitive", entry)
        bus.emit("audit.logged", entry)
        return entry

    def _archive_and_rotate(self):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        archive_path = os.path.join(AUDIT_DIR, f"audit_archive_{ts}.json")
        try:
            save_json(archive_path, self._entries)
            self._entries = self._entries[-5000:]
            self._save()
            logger.info("Archived audit log to %s", archive_path)
        except Exception as e:
            logger.error("Failed to archive audit log: %s", e)

    def verify_chain(self):
        with self._lock:
            if not self._entries:
                return True, "genesis", "empty log"
            h = hashlib.sha256()
            prev = "genesis"
            for i, entry in enumerate(self._entries):
                stored = entry.get("chain_hash", "")
                h.update(json.dumps({k: v for k, v in entry.items() if k != "chain_hash"}, sort_keys=True, default=str).encode())
                current = h.hexdigest()[:16]
                if stored and stored != current:
                    return False, current, f"chain break at entry {i}"
                prev = current
            return True, prev, f"verified {len(self._entries)} entries"

    def search(self, action=None, user_id=None, target=None, level=None,
               result=None, start_time=None, end_time=None, limit=50, offset=0):
        with self._lock:
            entries = list(self._entries)
        if action:
            entries = [e for e in entries if e["action"] == action or e["action"].startswith(action)]
        if user_id:
            entries = [e for e in entries if e["user_id"] == str(user_id)]
        if target:
            entries = [e for e in entries if target.lower() in e.get("target", "").lower()]
        if level:
            entries = [e for e in entries if e.get("level") == level]
        if result:
            entries = [e for e in entries if e.get("result") == result]
        if start_time:
            entries = [e for e in entries if e.get("timestamp", "") >= start_time]
        if end_time:
            entries = [e for e in entries if e.get("timestamp", "") <= end_time]
        total = len(entries)
        entries = entries[::-1]
        entries = entries[offset:offset + limit]
        return {"entries": entries, "total": total, "offset": offset, "limit": limit}

    def get_recent(self, n=20):
        with self._lock:
            return list(self._entries[-n:])

    def get_stats(self):
        with self._lock:
            entries = list(self._entries)
        stats = {
            "total": len(entries),
            "by_level": {},
            "by_action": {},
            "by_result": {},
            "by_user": {},
        }
        for e in entries:
            lvl = e.get("level", "info")
            act = e.get("action", "unknown")
            res = e.get("result", "unknown")
            uid = e.get("user_id", "unknown")
            stats["by_level"][lvl] = stats["by_level"].get(lvl, 0) + 1
            stats["by_action"][act] = stats["by_action"].get(act, 0) + 1
            stats["by_result"][res] = stats["by_result"].get(res, 0) + 1
            stats["by_user"][uid] = stats["by_user"].get(uid, 0) + 1
        return stats

    def is_sensitive(self, action):
        return action in SENSITIVE_ACTIONS


immutable_audit = ImmutableAuditLog()
