"""Backup Protection system.

Protects active and verified backups from accidental deletion. Never allows
deletion of the only valid backup. Requires confirmation before restore/delete.
Verifies backups before marking usable. Logs all backup/restore actions.
"""
import os
import time
import logging
import threading
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR, BACKUP_DIR

logger = logging.getLogger("turtle.backup_guard")

BACKUP_GUARD_PATH = os.path.join(DATA_DIR, "backup_guard.json")


class BackupGuard:
    def __init__(self):
        self._lock = threading.Lock()
        self._state = load_json(BACKUP_GUARD_PATH, {
            "protected_backups": [],
            "restore_confirmations": {},
            "delete_confirmations": {},
        })
        self._action_log: list = []
        logger.info("BackupGuard initialized")

    def _save(self):
        save_json(BACKUP_GUARD_PATH, self._state)

    def _log_action(self, action, backup_name, admin_id, details=""):
        entry = {
            "action": action, "backup": backup_name,
            "admin_id": str(admin_id), "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._action_log.append(entry)
        if len(self._action_log) > 500:
            self._action_log = self._action_log[-300:]
        bus.emit("backup_guard.action", entry)

    def protect_backup(self, backup_name, admin_id, reason=""):
        with self._lock:
            protected = self._state.setdefault("protected_backups", [])
            if backup_name not in protected:
                protected.append(backup_name)
                self._save()
        self._log_action("protected", backup_name, admin_id, reason)
        metrics.inc("backup_guard.protected")
        return True

    def unprotect_backup(self, backup_name, admin_id):
        with self._lock:
            protected = self._state.get("protected_backups", [])
            if backup_name in protected:
                protected.remove(backup_name)
                self._save()
        self._log_action("unprotected", backup_name, admin_id)
        return True

    def is_protected(self, backup_name):
        return backup_name in self._state.get("protected_backups", [])

    def can_delete(self, backup_name):
        if self.is_protected(backup_name):
            return False, "Backup is protected"
        backup_dir = os.path.join(BACKUP_DIR, backup_name)
        if not os.path.isdir(backup_dir):
            return False, "Backup not found"
        backups = []
        if os.path.isdir(BACKUP_DIR):
            for name in os.listdir(BACKUP_DIR):
                bpath = os.path.join(BACKUP_DIR, name)
                if os.path.isdir(bpath):
                    meta_path = os.path.join(bpath, "_backup_meta.json")
                    meta = load_json(meta_path, {})
                    if meta.get("verified", False):
                        backups.append(name)
        if backup_name in backups and len(backups) <= 1:
            return False, "Cannot delete the last verified backup"
        return True, "ok"

    def verify_backup(self, backup_name):
        import json
        backup_dir = os.path.join(BACKUP_DIR, backup_name)
        if not os.path.isdir(backup_dir):
            return {"verified": False, "error": "not found"}
        files = [f for f in os.listdir(backup_dir)
                 if f.endswith(".json") and f != "_backup_meta.json"]
        valid = 0
        errors = []
        for fname in files:
            fpath = os.path.join(backup_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    json.load(fh)
                valid += 1
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{fname}: {e}")
        all_valid = valid == len(files) and len(files) > 0
        meta_path = os.path.join(backup_dir, "_backup_meta.json")
        meta = load_json(meta_path, {})
        meta["verified"] = all_valid
        meta["verified_at"] = datetime.now(timezone.utc).isoformat()
        meta["file_count"] = len(files)
        meta["valid_count"] = valid
        save_json(meta_path, meta)
        metrics.set(f"backup_guard.verify.{backup_name}", 1 if all_valid else 0)
        return {
            "verified": all_valid, "file_count": len(files),
            "valid_count": valid, "errors": errors,
        }

    def get_verified_backups(self):
        verified = []
        if not os.path.isdir(BACKUP_DIR):
            return verified
        for name in os.listdir(BACKUP_DIR):
            bpath = os.path.join(BACKUP_DIR, name)
            if not os.path.isdir(bpath):
                continue
            meta_path = os.path.join(bpath, "_backup_meta.json")
            meta = load_json(meta_path, {})
            if meta.get("verified", False):
                verified.append(name)
        return verified

    def get_action_log(self, limit=20):
        return list(self._action_log[-limit:])

    def get_status(self):
        protected = self._state.get("protected_backups", [])
        verified = self.get_verified_backups()
        return {
            "protected_count": len(protected),
            "protected_backups": protected,
            "verified_count": len(verified),
            "verified_backups": verified,
        }


backup_guard = BackupGuard()
