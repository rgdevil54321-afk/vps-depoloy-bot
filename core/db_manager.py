"""Database Control Center - safe JSON data management with integrity guarantees."""
import os
import json
import shutil
import logging
import tempfile
from datetime import datetime, timezone

from core.services import metrics, health, bus, load_json, save_json, DATA_DIR, BACKUP_DIR

logger = logging.getLogger("turtle.db_manager")


class DBManager:
    def __init__(self):
        self.data_dir = DATA_DIR
        self.backup_dir = BACKUP_DIR
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def _json_files(self):
        return [
            f for f in os.listdir(self.data_dir)
            if f.endswith(".json") and os.path.isfile(os.path.join(self.data_dir, f))
        ]

    def get_status(self):
        files = self._json_files()
        total_size = 0
        for f in files:
            try:
                total_size += os.path.getsize(os.path.join(self.data_dir, f))
            except OSError:
                pass
        health_result = self.health_check()
        status = {
            "total_files": len(files),
            "total_size_bytes": total_size,
            "total_size_human": self._human_size(total_size),
            "file_count": len(files),
            "health_status": "healthy" if health_result["ok"] else "degraded",
            "corrupt_count": len(health_result["corrupt_files"]),
        }
        metrics.set("db.total_files", len(files))
        metrics.set("db.total_size_bytes", total_size)
        return status

    def health_check(self):
        files = self._json_files()
        corrupt = []
        healthy = 0
        for f in files:
            path = os.path.join(self.data_dir, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if not data and data != []:
                    corrupt.append({"file": f, "reason": "empty data"})
                else:
                    healthy += 1
            except json.JSONDecodeError as e:
                corrupt.append({"file": f, "reason": f"invalid JSON: {e}"})
            except Exception as e:
                corrupt.append({"file": f, "reason": str(e)})
        ok = len(corrupt) == 0
        result = {
            "ok": ok,
            "corrupt_files": corrupt,
            "healthy_count": healthy,
            "total_count": len(files),
        }
        metrics.set("db.healthy_files", healthy)
        metrics.set("db.corrupt_files", len(corrupt))
        if not ok:
            metrics.event("warning", "db_health", f"{len(corrupt)} corrupt file(s) detected")
            bus.emit("db.unhealthy", corrupt=corrupt)
        return result

    def backup(self, name=None):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        name = name or f"backup_{ts}"
        dest = os.path.join(self.backup_dir, name)
        os.makedirs(dest, exist_ok=True)
        files = self._json_files()
        copied = 0
        total_size = 0
        for f in files:
            src = os.path.join(self.data_dir, f)
            dst = os.path.join(dest, f)
            try:
                shutil.copy2(src, dst)
                copied += 1
                total_size += os.path.getsize(src)
            except Exception as e:
                logger.error("Backup copy failed for %s: %s", f, e)
        meta_path = os.path.join(dest, "_backup_meta.json")
        meta = {
            "name": name,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "file_count": copied,
            "size_bytes": total_size,
        }
        save_json(meta_path, meta)
        metrics.inc("db.backups_created")
        metrics.event("info", "backup", f"Backup '{name}' created", {"file_count": copied})
        bus.emit("backup.created", name=name, file_count=copied)
        return {"name": name, "path": dest, "file_count": copied, "size_bytes": total_size}

    def list_backups(self):
        backups = []
        if not os.path.isdir(self.backup_dir):
            return backups
        for entry in os.listdir(self.backup_dir):
            bpath = os.path.join(self.backup_dir, entry)
            if not os.path.isdir(bpath):
                continue
            meta_path = os.path.join(bpath, "_backup_meta.json")
            meta = load_json(meta_path, {})
            if not meta:
                files = [f for f in os.listdir(bpath) if f.endswith(".json") and f != "_backup_meta.json"]
                total_size = 0
                for f in files:
                    try:
                        total_size += os.path.getsize(os.path.join(bpath, f))
                    except OSError:
                        pass
                created = None
                try:
                    stat = os.stat(bpath)
                    created = datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc).isoformat()
                except OSError:
                    pass
                meta = {
                    "name": entry,
                    "file_count": len(files),
                    "size_bytes": total_size,
                    "created_at": created,
                }
            backups.append({
                "name": meta.get("name", entry),
                "file_count": meta.get("file_count", 0),
                "size_bytes": meta.get("size_bytes", 0),
                "created_at": meta.get("created_at"),
            })
        backups.sort(key=lambda b: b.get("created_at") or "", reverse=True)
        return backups

    def restore(self, backup_name):
        src = os.path.join(self.backup_dir, backup_name)
        if not os.path.isdir(src):
            return {"file_count": 0, "success": False, "error": "backup not found"}
        files = [f for f in os.listdir(src) if f.endswith(".json") and f != "_backup_meta.json"]
        restored = 0
        for f in files:
            bkp_file = os.path.join(src, f)
            dest_file = os.path.join(self.data_dir, f)
            try:
                tmp = dest_file + ".restore_tmp"
                shutil.copy2(bkp_file, tmp)
                os.replace(tmp, dest_file)
                restored += 1
            except Exception as e:
                logger.error("Restore failed for %s: %s", f, e)
        metrics.inc("db.restores")
        metrics.event("info", "restore", f"Restored from '{backup_name}'", {"file_count": restored})
        bus.emit("backup.restored", name=backup_name, file_count=restored)
        return {"file_count": restored, "success": restored > 0}

    def integrity_check(self):
        files = self._json_files()
        report = []
        for f in files:
            path = os.path.join(self.data_dir, f)
            entry = {"file": f, "valid": False, "empty": False, "record_count": 0, "errors": []}
            try:
                size = os.path.getsize(path)
                entry["size_bytes"] = size
                if size == 0:
                    entry["empty"] = True
                    entry["errors"].append("file is empty (0 bytes)")
                    report.append(entry)
                    continue
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                entry["valid"] = True
                entry["type"] = type(data).__name__
                if isinstance(data, (list, dict)):
                    entry["record_count"] = len(data)
                else:
                    entry["errors"].append(f"unexpected root type: {type(data).__name__}")
                    entry["valid"] = False
            except json.JSONDecodeError as e:
                entry["errors"].append(f"JSON parse error: {e}")
            except Exception as e:
                entry["errors"].append(str(e))
            report.append(entry)
        total = len(report)
        valid = sum(1 for r in report if r["valid"])
        metrics.set("db.integrity_valid", valid)
        metrics.set("db.integrity_total", total)
        if valid < total:
            metrics.event("warning", "integrity", f"{total - valid}/{total} files failed integrity check")
        return {
            "total_files": total,
            "valid_files": valid,
            "invalid_files": total - valid,
            "details": report,
        }

    def get_table_stats(self):
        files = self._json_files()
        stats = []
        for f in files:
            path = os.path.join(self.data_dir, f)
            entry = {"name": f, "size_bytes": 0, "record_count": 0, "last_modified": None}
            try:
                entry["size_bytes"] = os.path.getsize(path)
                stat = os.stat(path)
                entry["last_modified"] = datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat()
            except OSError:
                pass
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                if isinstance(data, (list, dict)):
                    entry["record_count"] = len(data)
            except Exception:
                pass
            stats.append(entry)
        stats.sort(key=lambda s: s["size_bytes"], reverse=True)
        return stats

    def cleanup_temp(self):
        count = 0
        for f in os.listdir(self.data_dir):
            if f.endswith(".tmp"):
                try:
                    os.remove(os.path.join(self.data_dir, f))
                    count += 1
                except Exception as e:
                    logger.error("Failed to remove temp file %s: %s", f, e)
        if count > 0:
            metrics.inc("db.temp_cleaned", count)
            metrics.event("info", "cleanup", f"Removed {count} temp file(s)")
        return count

    def export_data(self, path):
        os.makedirs(path, exist_ok=True)
        files = self._json_files()
        exported = 0
        for f in files:
            src = os.path.join(self.data_dir, f)
            dst = os.path.join(path, f)
            try:
                shutil.copy2(src, dst)
                exported += 1
            except Exception as e:
                logger.error("Export failed for %s: %s", f, e)
        metrics.inc("db.exports")
        return {"file_count": exported, "destination": path}

    def import_data(self, path):
        if not os.path.isdir(path):
            return {"file_count": 0, "success": False, "error": "source path not found"}
        source_files = [f for f in os.listdir(path) if f.endswith(".json")]
        validation_errors = []
        for f in source_files:
            fp = os.path.join(path, f)
            try:
                with open(fp, "r", encoding="utf-8") as fh:
                    json.load(fh)
            except json.JSONDecodeError as e:
                validation_errors.append({"file": f, "error": str(e)})
        if validation_errors:
            return {
                "file_count": 0,
                "success": False,
                "error": "validation failed",
                "validation_errors": validation_errors,
            }
        imported = 0
        for f in source_files:
            src = os.path.join(path, f)
            dest = os.path.join(self.data_dir, f)
            try:
                tmp = dest + ".import_tmp"
                shutil.copy2(src, tmp)
                os.replace(tmp, dest)
                imported += 1
            except Exception as e:
                logger.error("Import failed for %s: %s", f, e)
        metrics.inc("db.imports")
        metrics.event("info", "import", f"Imported {imported} file(s)")
        bus.emit("db.imported", file_count=imported)
        return {"file_count": imported, "success": imported > 0}

    @staticmethod
    def _human_size(n):
        for unit in ("B", "KB", "MB", "GB"):
            if n < 1024:
                return f"{n:.1f} {unit}"
            n /= 1024
        return f"{n:.1f} TB"
