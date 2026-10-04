"""Professional Backup Manager - retention policies, metadata, and verification."""
import os
import json
import shutil
import logging
from datetime import datetime, timezone, timedelta

from core.services import metrics, load_json, save_json, DATA_DIR, BACKUP_DIR
from core.db_manager import DBManager

logger = logging.getLogger("turtle.backup_center")

RETENTION_DEFAULTS = {
    "max_backups": 30,
    "max_age_days": 90,
    "min_keep": 3,
}
CONFIG_FILENAME = "backup_config.json"


class BackupManager:
    def __init__(self, db_manager: DBManager):
        self.db = db_manager
        self.config_path = os.path.join(DATA_DIR, CONFIG_FILENAME)
        self._config = load_json(self.config_path, dict(RETENTION_DEFAULTS))
        for k, v in RETENTION_DEFAULTS.items():
            self._config.setdefault(k, v)

    def _save_config(self):
        save_json(self.config_path, self._config)

    def _meta_path(self, backup_dir):
        return os.path.join(backup_dir, "_backup_meta.json")

    def _read_meta(self, name):
        bpath = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(bpath):
            return None
        meta = load_json(self._meta_path(bpath), {})
        if not meta:
            meta = self._build_meta(name, bpath)
        return meta

    def _build_meta(self, name, bpath):
        files = [
            f for f in os.listdir(bpath)
            if f.endswith(".json") and f != "_backup_meta.json"
        ]
        total_size = 0
        for f in files:
            try:
                total_size += os.path.getsize(os.path.join(bpath, f))
            except OSError:
                pass
        created = None
        try:
            created = datetime.fromtimestamp(
                os.stat(bpath).st_ctime, tz=timezone.utc
            ).isoformat()
        except OSError:
            pass
        return {
            "name": name,
            "path": bpath,
            "file_count": len(files),
            "size_bytes": total_size,
            "created_at": created,
            "reason": "unknown",
            "verified": False,
        }

    def _backup_info(self, name):
        meta = self._read_meta(name)
        if not meta:
            return None
        return {
            "name": meta.get("name", name),
            "path": meta.get("path", os.path.join(BACKUP_DIR, name)),
            "file_count": meta.get("file_count", 0),
            "size_bytes": meta.get("size_bytes", 0),
            "created_at": meta.get("created_at"),
            "reason": meta.get("reason", "unknown"),
            "verified": meta.get("verified", False),
        }

    def create_backup(self, name=None, reason="manual"):
        result = self.db.backup(name=name)
        bpath = result["path"]
        meta = self._read_meta(result["name"])
        if not meta:
            meta = {}
        meta.update({
            "name": result["name"],
            "path": bpath,
            "file_count": result["file_count"],
            "size_bytes": result["size_bytes"],
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "verified": False,
        })
        save_json(self._meta_path(bpath), meta)
        metrics.inc("backupcenter.created")
        return self._backup_info(result["name"])

    def list_backups(self):
        entries = []
        if not os.path.isdir(BACKUP_DIR):
            return entries
        for name in os.listdir(BACKUP_DIR):
            bpath = os.path.join(BACKUP_DIR, name)
            if not os.path.isdir(bpath):
                continue
            info = self._backup_info(name)
            if info:
                entries.append(info)
        entries.sort(key=lambda b: b.get("created_at") or "", reverse=True)
        return entries

    def restore_backup(self, name):
        backups = self.list_backups()
        names = [b["name"] for b in backups]
        if name not in names:
            return {"file_count": 0, "success": False, "error": f"backup '{name}' not found"}
        pre_backup = self.create_backup(name=f"_pre_restore_{name}", reason="pre_restore")
        result = self.db.restore(name)
        metrics.inc("backupcenter.restored")
        metrics.event("info", "backup_restore", f"Restored from '{name}'",
                       {"pre_backup": pre_backup["name"] if pre_backup else None})
        return {
            "file_count": result["file_count"],
            "success": result["success"],
            "pre_restore_backup": pre_backup["name"] if pre_backup else None,
        }

    def delete_backup(self, name):
        backups = self.list_backups()
        if len(backups) <= 1:
            metrics.event("warning", "backup_delete",
                          f"Refused to delete '{name}': only 1 backup remains")
            return False
        bpath = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(bpath):
            return False
        try:
            shutil.rmtree(bpath)
            metrics.inc("backupcenter.deleted")
            metrics.event("info", "backup_delete", f"Deleted backup '{name}'")
            return True
        except Exception as e:
            logger.error("Failed to delete backup %s: %s", name, e)
            return False

    def get_retention_policy(self):
        return dict(self._config)

    def set_retention_policy(self, key, value):
        if key not in RETENTION_DEFAULTS:
            return False
        if key == "min_keep":
            value = max(1, int(value))
        elif key in ("max_backups",):
            value = max(1, int(value))
        elif key == "max_age_days":
            value = max(1, int(value))
        self._config[key] = value
        self._save_config()
        metrics.event("info", "retention", f"Retention policy updated: {key}={value}")
        return True

    def cleanup_old_backups(self):
        backups = self.list_backups()
        deleted = 0
        max_backups = self._config.get("max_backups", 30)
        max_age_days = self._config.get("max_age_days", 90)
        min_keep = self._config.get("min_keep", 3)
        now = datetime.now(timezone.utc)
        candidates = []
        for b in backups:
            created_at = b.get("created_at")
            if created_at:
                try:
                    dt = datetime.fromisoformat(created_at)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    age_days = (now - dt).days
                except (ValueError, TypeError):
                    age_days = 0
            else:
                age_days = 0
            candidates.append({**b, "age_days": age_days})
        candidates.sort(key=lambda b: b.get("created_at") or "", reverse=True)
        to_delete = []
        for i, b in enumerate(candidates):
            if i < min_keep:
                continue
            reason = None
            if len(candidates) - deleted <= min_keep:
                break
            if i >= max_backups:
                reason = "exceeds_max_backups"
            elif b["age_days"] > max_age_days:
                reason = "exceeds_max_age"
            if reason:
                to_delete.append((b["name"], reason))
        for bname, reason in to_delete:
            bpath = os.path.join(BACKUP_DIR, bname)
            if os.path.isdir(bpath):
                try:
                    shutil.rmtree(bpath)
                    deleted += 1
                    metrics.inc("backupcenter.cleanup_deleted")
                except Exception as e:
                    logger.error("Cleanup failed for %s: %s", bname, e)
        if deleted > 0:
            metrics.event("info", "retention_cleanup",
                          f"Deleted {deleted} old backup(s)")
        return deleted

    def get_backup_stats(self):
        backups = self.list_backups()
        if not backups:
            return {
                "total_backups": 0,
                "total_size": 0,
                "total_size_human": "0 B",
                "oldest": None,
                "newest": None,
            }
        total_size = sum(b["size_bytes"] for b in backups)
        dates = [b["created_at"] for b in backups if b.get("created_at")]
        return {
            "total_backups": len(backups),
            "total_size": total_size,
            "total_size_human": DBManager._human_size(total_size),
            "oldest": min(dates) if dates else None,
            "newest": max(dates) if dates else None,
        }

    def verify_backup(self, name):
        bpath = os.path.join(BACKUP_DIR, name)
        if not os.path.isdir(bpath):
            return {"verified": False, "error": "backup not found", "details": []}
        files = [
            f for f in os.listdir(bpath)
            if f.endswith(".json") and f != "_backup_meta.json"
        ]
        details = []
        all_valid = True
        for f in files:
            fp = os.path.join(bpath, f)
            entry = {"file": f, "valid": False}
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                entry["valid"] = True
                entry["type"] = type(data).__name__
                entry["size"] = os.path.getsize(fp)
            except json.JSONDecodeError as e:
                entry["error"] = f"invalid JSON: {e}"
                all_valid = False
            except Exception as e:
                entry["error"] = str(e)
                all_valid = False
            details.append(entry)
        meta = self._read_meta(name)
        if meta:
            meta["verified"] = all_valid
            save_json(self._meta_path(bpath), meta)
        metrics.set(f"backup.verify.{name}", 1 if all_valid else 0)
        return {
            "verified": all_valid,
            "file_count": len(files),
            "valid_count": sum(1 for d in details if d["valid"]),
            "details": details,
        }
