"""Disaster Recovery system.

Maintains verified backups, provides controlled restore procedures, tests
backup integrity, tracks recovery points, provides emergency recovery workflow,
and logs every disaster recovery operation.
"""
import os
import json
import time
import shutil
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR, BACKUP_DIR

logger = logging.getLogger("turtle.disaster_recovery")

DR_PATH = os.path.join(DATA_DIR, "disaster_recovery.json")


class DisasterRecovery:
    def __init__(self):
        self._state = load_json(DR_PATH, {
            "recovery_points": [],
            "restore_log": [],
            "integrity_checks": [],
        })
        self._action_log: list = []
        logger.info("DisasterRecovery initialized")

    def _save(self):
        save_json(DR_PATH, self._state)

    def _log(self, action, details=""):
        entry = {
            "action": action, "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._action_log.append(entry)
        if len(self._action_log) > 500:
            self._action_log = self._action_log[-300:]
        bus.emit("disaster_recovery.action", entry)

    def create_recovery_point(self, name=None, reason="manual"):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        rp_name = name or f"rp_{ts}"
        rp_dir = os.path.join(BACKUP_DIR, rp_name)
        os.makedirs(rp_dir, exist_ok=True)
        copied = 0
        for fname in os.listdir(DATA_DIR):
            if fname.endswith(".json") and os.path.isfile(os.path.join(DATA_DIR, fname)):
                try:
                    shutil.copy2(os.path.join(DATA_DIR, fname), rp_dir)
                    copied += 1
                except Exception as e:
                    logger.error("Failed to copy %s: %s", fname, e)
        meta = {
            "name": rp_name, "path": rp_dir,
            "files": copied, "reason": reason,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "verified": False,
        }
        meta_path = os.path.join(rp_dir, "_recovery_meta.json")
        save_json(meta_path, meta)
        self._state.setdefault("recovery_points", []).append(meta)
        self._save()
        self._log("recovery_point_created", rp_name)
        metrics.inc("dr.recovery_point_created")
        logger.info("Recovery point created: %s (%d files)", rp_name, copied)
        return meta

    def verify_recovery_point(self, rp_name):
        rp_dir = os.path.join(BACKUP_DIR, rp_name)
        if not os.path.isdir(rp_dir):
            return {"verified": False, "error": "not found"}
        files = [f for f in os.listdir(rp_dir) if f.endswith(".json")]
        valid = 0
        errors = []
        for fname in files:
            if fname == "_recovery_meta.json":
                continue
            fpath = os.path.join(rp_dir, fname)
            try:
                with open(fpath, "r", encoding="utf-8") as fh:
                    json.load(fh)
                valid += 1
            except (json.JSONDecodeError, IOError) as e:
                errors.append(f"{fname}: {e}")
        all_valid = valid == len([f for f in files if f != "_recovery_meta.json"])
        meta_path = os.path.join(rp_dir, "_recovery_meta.json")
        meta = load_json(meta_path, {})
        meta["verified"] = all_valid
        meta["verified_at"] = datetime.now(timezone.utc).isoformat()
        save_json(meta_path, meta)
        for rp in self._state.get("recovery_points", []):
            if rp.get("name") == rp_name:
                rp["verified"] = all_valid
        self._save()
        entry = {
            "recovery_point": rp_name, "verified": all_valid,
            "valid_files": valid, "errors": errors,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        self._state.setdefault("integrity_checks", []).append(entry)
        self._save()
        return entry

    def restore_from_recovery_point(self, rp_name, admin_id):
        rp_dir = os.path.join(BACKUP_DIR, rp_name)
        if not os.path.isdir(rp_dir):
            return {"success": False, "error": "Recovery point not found"}
        meta_path = os.path.join(rp_dir, "_recovery_meta.json")
        meta = load_json(meta_path, {})
        if not meta.get("verified", False):
            return {"success": False, "error": "Recovery point not verified"}
        pre_restore = self.create_recovery_point(
            name=f"pre_restore_{rp_name}",
            reason="pre_disaster_recovery"
        )
        restored = 0
        errors = []
        for fname in os.listdir(rp_dir):
            if fname.endswith(".json") and fname != "_recovery_meta.json":
                src = os.path.join(rp_dir, fname)
                dst = os.path.join(DATA_DIR, fname)
                try:
                    shutil.copy2(src, dst)
                    restored += 1
                except Exception as e:
                    errors.append(f"{fname}: {e}")
        entry = {
            "recovery_point": rp_name, "admin_id": str(admin_id),
            "restored_files": restored, "errors": errors,
            "pre_restore_backup": pre_restore["name"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state.setdefault("restore_log", []).append(entry)
        self._save()
        self._log("restored", f"{rp_name} by {admin_id}")
        metrics.inc("dr.restored")
        bus.emit("disaster_recovery.restored", entry)
        logger.warning("Disaster recovery restore: %s by %s (%d files)", rp_name, admin_id, restored)
        return {"success": True, "restored": restored, "errors": errors,
                "pre_restore_backup": pre_restore["name"]}

    def get_recovery_points(self):
        return self._state.get("recovery_points", [])

    def get_restore_log(self, limit=10):
        return list(self._state.get("restore_log", [])[-limit:])

    def get_integrity_checks(self, limit=10):
        return list(self._state.get("integrity_checks", [])[-limit:])

    def get_action_log(self, limit=20):
        return list(self._action_log[-limit:])

    def get_status(self):
        rps = self._state.get("recovery_points", [])
        verified = sum(1 for rp in rps if rp.get("verified"))
        return {
            "total_recovery_points": len(rps),
            "verified_recovery_points": verified,
            "total_restores": len(self._state.get("restore_log", [])),
            "integrity_checks": len(self._state.get("integrity_checks", [])),
        }


disaster_recovery = DisasterRecovery()
