"""Purge System - safe, previewable cleanup operations for Turtle Nodes bot.

Every purge shows exactly what will be removed BEFORE deletion and requires
explicit confirmation. Critical data files and running containers are NEVER touched.
Protected VPS containers are NEVER purged.
"""
import os
import shutil
import glob
import logging
import time
import json
import asyncio
import subprocess

from core.services import (
    metrics, bus, load_json, save_json,
    DATA_DIR, BASE_DIR, BACKUP_DIR, CACHE_DIR,
)

logger = logging.getLogger("turtle.purge")

PROTECTED_FILES = {
    "user_data.json", "vps_data.json", "admin_data.json",
    "transactions.json", "billing.json", "coupons.json",
    "tickets.json", "pending.json", "nodes.json",
    "protected_vps.json",
}

PROTECTED_ROOT_FILES = {"config.json", "token.txt", ".env"}

MAX_BACKUP_RETENTION = 30

PROTECTED_VPS_PATH = os.path.join(DATA_DIR, "protected_vps.json")


def _load_protected_vps() -> set:
    """Load the set of protected VPS container names."""
    try:
        data = load_json(PROTECTED_VPS_PATH, {"containers": []})
        return set(data.get("containers", []))
    except Exception:
        return set()


def _dir_size(path: str) -> int:
    total = 0
    try:
        for dirpath, _dirnames, filenames in os.walk(path):
            for fn in filenames:
                fp = os.path.join(dirpath, fn)
                try:
                    total += os.path.getsize(fp)
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _safe_remove_path(path: str) -> bool:
    try:
        if os.path.isfile(path) or os.path.islink(path):
            os.remove(path)
            return True
        if os.path.isdir(path):
            shutil.rmtree(path)
            return True
    except Exception as exc:
        logger.error("Failed to remove %s: %s", path, exc)
        return False
    return False


class PurgeManager:
    """Safe previewable purge manager for bot infrastructure cleanup."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _file_info(path: str) -> dict:
        try:
            st = os.stat(path)
            return {"path": path, "size_bytes": st.st_size}
        except OSError:
            return {"path": path, "size_bytes": 0}

    @staticmethod
    def _file_info_with_mod(path: str) -> dict:
        try:
            st = os.stat(path)
            return {
                "path": path,
                "size_bytes": st.st_size,
                "modified": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(st.st_mtime)),
            }
        except OSError:
            return {"path": path, "size_bytes": 0, "modified": None}

    @staticmethod
    def _make_result(removed: int = 0, freed: int = 0, errors: list | None = None):
        return {"removed": removed, "freed_bytes": freed, "errors": errors or []}

    @staticmethod
    async def _run_docker(cmd: list[str], timeout: int = 30) -> tuple[int, str, str]:
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
            return (
                proc.returncode or 0,
                stdout.decode(errors="replace").strip(),
                stderr.decode(errors="replace").strip(),
            )
        except FileNotFoundError:
            return -1, "", "docker binary not found"
        except asyncio.TimeoutError:
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return -2, "", "docker command timed out"
        except Exception as exc:
            return -1, "", str(exc)

    # ==================================================================
    # PREVIEW METHODS - return what WOULD be removed, no deletions
    # ==================================================================

    def preview_cache(self) -> list[dict]:
        """List all __pycache__ directories under BASE_DIR."""
        results = []
        for dirpath, dirnames, filenames in os.walk(BASE_DIR):
            if os.path.basename(dirpath) == "__pycache__":
                results.append({
                    "path": dirpath,
                    "size_bytes": _dir_size(dirpath),
                })
        return results

    def preview_temp(self) -> list[dict]:
        """List .tmp files in DATA_DIR, BACKUP_DIR, CACHE_DIR."""
        results = []
        for scan_dir in (DATA_DIR, BACKUP_DIR, CACHE_DIR):
            if not os.path.isdir(scan_dir):
                continue
            for fn in os.listdir(scan_dir):
                if fn.endswith(".tmp"):
                    fp = os.path.join(scan_dir, fn)
                    if os.path.isfile(fp):
                        results.append(self._file_info(fp))
        return results

    def preview_old_logs(self, days: int = 7) -> list[dict]:
        """List rotated log files (bot.log.*) older than N days."""
        cutoff = time.time() - (days * 86400)
        results = []
        log_patterns = [
            os.path.join(BASE_DIR, "bot.log.*"),
            os.path.join(BASE_DIR, "logs", "bot.log.*"),
            os.path.join(BASE_DIR, "logs", "*.log.*"),
        ]
        seen = set()
        for pattern in log_patterns:
            for fp in glob.glob(pattern):
                if fp in seen or not os.path.isfile(fp):
                    continue
                seen.add(fp)
                try:
                    st = os.stat(fp)
                    if st.st_mtime < cutoff:
                        entry = self._file_info_with_mod(fp)
                        results.append(entry)
                except OSError:
                    pass
        return results

    def preview_old_backups(self, keep: int = 3) -> list[dict]:
        """List backup directories beyond the keep count (oldest first)."""
        if not os.path.isdir(BACKUP_DIR):
            return []
        entries = []
        for name in os.listdir(BACKUP_DIR):
            bpath = os.path.join(BACKUP_DIR, name)
            if not os.path.isdir(bpath):
                continue
            meta = load_json(os.path.join(bpath, "_backup_meta.json"), {})
            created = meta.get("created_at", "")
            entries.append({"name": name, "path": bpath, "created": created})
        entries.sort(key=lambda e: e.get("created") or "")
        if len(entries) <= keep:
            return []
        to_remove = entries[:-keep] if keep > 0 else entries
        results = []
        for entry in to_remove:
            results.append({
                "path": entry["path"],
                "size_bytes": _dir_size(entry["path"]),
            })
        return results

    async def preview_docker_containers(self) -> list[dict]:
        """List stopped/exited containers only - never running. Skips protected VPS."""
        protected = _load_protected_vps()
        code, out, err = await self._run_docker([
            "docker", "ps", "-a", "--format",
            "{{.Names}}\t{{.ID}}\t{{.Status}}\t{{.Size}}",
            "--filter", "status=exited",
            "--filter", "status=dead",
            "--filter", "status=created",
        ])
        if code != 0:
            logger.warning("docker ps failed: %s", err)
            return []
        results = []
        skipped = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 3:
                continue
            name, cid, status = parts[0], parts[1], parts[2]
            size = parts[3] if len(parts) > 3 else "N/A"
            if name in protected:
                skipped.append(name)
                bus.emit("audit", action="purge_skip_protected", name=name)
                continue
            results.append({
                "name": name,
                "id": cid,
                "status": status,
                "size": size,
            })
        if skipped:
            logger.info("Purge skipped %d protected VPS: %s", len(skipped), skipped)
        return results

    async def preview_docker_images(self) -> list[dict]:
        """List dangling images only (none referenced by active containers)."""
        code, out, err = await self._run_docker([
            "docker", "images", "--filter", "dangling=true",
            "--format", "{{.ID}}\t{{.Repository}}:{{.Tag}}\t{{.Size}}",
        ])
        if code != 0:
            logger.warning("docker images failed: %s", err)
            return []
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            image_id = parts[0]
            tag = parts[1] if len(parts) > 1 else "<none>"
            size = parts[2] if len(parts) > 2 else "N/A"
            results.append({
                "id": image_id,
                "tag": tag,
                "size": size,
            })
        return results

    async def preview_docker_volumes(self) -> list[dict]:
        """List unused volumes only."""
        code, out, err = await self._run_docker([
            "docker", "volume", "ls", "--filter", "dangling=true",
            "--format", "{{.Name}}\t{{.Size}}",
        ])
        if code != 0:
            logger.warning("docker volume ls failed: %s", err)
            return []
        results = []
        for line in out.splitlines():
            parts = line.split("\t")
            name = parts[0]
            size = parts[1] if len(parts) > 1 else "N/A"
            results.append({"name": name, "size": size})
        return results

    async def preview_docker_build_cache(self) -> dict:
        """Docker build cache usage summary."""
        code, out, err = await self._run_docker([
            "docker", "system", "df", "-v", "--format", "{{json .}}",
        ])
        if code != 0:
            logger.warning("docker system df failed: %s", err)
            return {"size": "N/A", "count": 0}
        try:
            lines = out.splitlines()
            for line in lines:
                entry = json.loads(line)
                if entry.get("Type") == "Build Cache":
                    return {
                        "size": entry.get("Size", "N/A"),
                        "count": entry.get("Reclaimable", 0) if isinstance(entry.get("Reclaimable"), int) else 0,
                    }
            for line in lines:
                entry = json.loads(line)
                if "Build" in entry.get("Type", ""):
                    return {
                        "size": entry.get("Size", "N/A"),
                        "count": entry.get("TotalCount", 0),
                    }
        except (json.JSONDecodeError, KeyError):
            pass
        total_count = 0
        total_size_str = "0B"
        try:
            code2, out2, _ = await self._run_docker([
                "docker", "builder", "du", "--format", "{{.Total}}",
            ])
            if code2 == 0 and out2.strip():
                total_size_str = out2.strip().splitlines()[-1].strip()
        except Exception:
            pass
        try:
            code3, out3, _ = await self._run_docker([
                "docker", "builder", "prune", "--all", "--force", "--dry-run",
            ])
            if code3 == 0:
                for ln in out3.splitlines():
                    if "Total" in ln:
                        parts = ln.split(":")
                        if len(parts) == 2:
                            total_size_str = parts[1].strip()
        except Exception:
            pass
        return {"size": total_size_str, "count": total_count}

    # ==================================================================
    # EXECUTION METHODS - perform the actual purge, return summary
    # ==================================================================

    def execute_cache(self) -> dict:
        """Remove all __pycache__ directories under BASE_DIR."""
        items = self.preview_cache()
        removed = 0
        freed = 0
        errors = []
        for item in items:
            path = item["path"]
            size = item["size_bytes"]
            if _safe_remove_path(path):
                removed += 1
                freed += size
                bus.emit("audit", action="purge_cache", path=path, size=size)
            else:
                errors.append(f"Failed to remove {path}")
        metrics.inc("purge.cache.removed", removed)
        metrics.inc("purge.cache.freed", freed)
        logger.info("Cache purge: removed %d dirs, freed %d bytes, %d errors", removed, freed, len(errors))
        return self._make_result(removed, freed, errors)

    def execute_temp(self) -> dict:
        """Remove .tmp files in data directories."""
        items = self.preview_temp()
        removed = 0
        freed = 0
        errors = []
        for item in items:
            path = item["path"]
            size = item["size_bytes"]
            basename = os.path.basename(path)
            if basename in PROTECTED_FILES or basename in PROTECTED_ROOT_FILES:
                errors.append(f"Protected: {path}")
                continue
            if _safe_remove_path(path):
                removed += 1
                freed += size
                bus.emit("audit", action="purge_temp", path=path, size=size)
            else:
                errors.append(f"Failed to remove {path}")
        metrics.inc("purge.temp.removed", removed)
        metrics.inc("purge.temp.freed", freed)
        logger.info("Temp purge: removed %d files, freed %d bytes, %d errors", removed, freed, len(errors))
        return self._make_result(removed, freed, errors)

    def execute_old_logs(self, days: int = 7) -> dict:
        """Remove rotated log files older than N days."""
        items = self.preview_old_logs(days)
        removed = 0
        freed = 0
        errors = []
        for item in items:
            path = item["path"]
            size = item["size_bytes"]
            basename = os.path.basename(path)
            if basename in PROTECTED_FILES or basename in PROTECTED_ROOT_FILES:
                errors.append(f"Protected: {path}")
                continue
            if _safe_remove_path(path):
                removed += 1
                freed += size
                bus.emit("audit", action="purge_old_logs", path=path, size=size, days=days)
            else:
                errors.append(f"Failed to remove {path}")
        metrics.inc("purge.logs.removed", removed)
        metrics.inc("purge.logs.freed", freed)
        logger.info("Old logs purge: removed %d files (>%d days), freed %d bytes", removed, days, freed)
        return self._make_result(removed, freed, errors)

    def execute_old_backups(self, keep: int = 3) -> dict:
        """Remove backup directories beyond the keep count. Never removes the last backup."""
        if not os.path.isdir(BACKUP_DIR):
            return self._make_result(0, 0, ["Backup directory does not exist"])
        all_dirs = []
        for name in os.listdir(BACKUP_DIR):
            bpath = os.path.join(BACKUP_DIR, name)
            if os.path.isdir(bpath):
                meta = load_json(os.path.join(bpath, "_backup_meta.json"), {})
                created = meta.get("created_at", "")
                all_dirs.append({"name": name, "path": bpath, "created": created})
        all_dirs.sort(key=lambda e: e.get("created") or "")
        remaining = len(all_dirs) - keep
        if remaining <= 0:
            return self._make_result(0, 0, [])
        actual_keep = max(keep, 1)
        if len(all_dirs) <= actual_keep:
            return self._make_result(0, 0, ["Refused: only one backup would remain"])
        to_remove = all_dirs[:-actual_keep]
        removed = 0
        freed = 0
        errors = []
        for entry in to_remove:
            path = entry["path"]
            size = _dir_size(path)
            if _safe_remove_path(path):
                removed += 1
                freed += size
                bus.emit("audit", action="purge_old_backup", path=path, name=entry["name"], size=size)
            else:
                errors.append(f"Failed to remove backup {entry['name']}")
        remaining_count = len(all_dirs) - removed
        if remaining_count < 1:
            freed_estimate = freed
            for entry in to_remove:
                _safe_remove_path(entry["path"])
            logger.error("Backup purge safety net triggered: would leave zero backups")
            return self._make_result(0, 0, ["Safety abort: cannot delete the last backup"])
        metrics.inc("purge.backups.removed", removed)
        metrics.inc("purge.backups.freed", freed)
        logger.info("Old backups purge: removed %d dirs, freed %d bytes", removed, freed)
        return self._make_result(removed, freed, errors)

    async def execute_docker_containers(self) -> dict:
        """Remove stopped/exited containers only. Skips protected VPS."""
        containers = await self.preview_docker_containers()
        protected = _load_protected_vps()
        removed = 0
        freed = 0
        errors = []
        for ct in containers:
            name = ct["name"]
            if name in protected:
                errors.append(f"Skipped protected VPS: {name}")
                continue
            cid = ct["id"]
            code, out, err = await self._run_docker(["docker", "rm", cid])
            if code == 0:
                removed += 1
                bus.emit("audit", action="purge_docker_container", name=name, id=cid, status=ct["status"])
            else:
                errors.append(f"docker rm {name}: {err or out}")
        metrics.inc("purge.docker.containers", removed)
        logger.info("Docker container purge: removed %d stopped containers, %d errors", removed, len(errors))
        return self._make_result(removed, 0, errors)

    async def execute_docker_images(self) -> dict:
        """Remove dangling images only."""
        images = await self.preview_docker_images()
        removed = 0
        freed = 0
        errors = []
        for img in images:
            image_id = img["id"]
            code, out, err = await self._run_docker(["docker", "rmi", image_id])
            if code == 0:
                removed += 1
                bus.emit("audit", action="purge_docker_image", id=image_id, tag=img["tag"])
            else:
                errors.append(f"docker rmi {image_id}: {err or out}")
        metrics.inc("purge.docker.images", removed)
        logger.info("Docker image purge: removed %d dangling images, %d errors", removed, len(errors))
        return self._make_result(removed, 0, errors)

    async def execute_docker_volumes(self) -> dict:
        """Remove unused volumes only."""
        volumes = await self.preview_docker_volumes()
        removed = 0
        freed = 0
        errors = []
        for vol in volumes:
            name = vol["name"]
            code, out, err = await self._run_docker(["docker", "volume", "rm", name])
            if code == 0:
                removed += 1
                bus.emit("audit", action="purge_docker_volume", name=name)
            else:
                errors.append(f"docker volume rm {name}: {err or out}")
        metrics.inc("purge.docker.volumes", removed)
        logger.info("Docker volume purge: removed %d unused volumes, %d errors", removed, len(errors))
        return self._make_result(removed, 0, errors)

    async def execute_docker_build_cache(self) -> dict:
        """Prune docker build cache."""
        code, out, err = await self._run_docker([
            "docker", "builder", "prune", "--all", "--force",
        ], timeout=120)
        removed = 0
        freed = 0
        errors = []
        if code == 0:
            for line in out.splitlines():
                lower = line.lower()
                if "total" in lower and "reclaimable" in lower:
                    continue
                if "total reclaimed space" in lower:
                    pass
                elif line.strip():
                    pass
            bus.emit("audit", action="purge_docker_build_cache", output=out[:500])
            removed = 1
        else:
            errors.append(f"docker builder prune: {err or out}")
        metrics.inc("purge.docker.build_cache")
        logger.info("Docker build cache purge: code=%d, %d errors", code, len(errors))
        return self._make_result(removed, freed, errors)

    # ------------------------------------------------------------------
    # Composite cleanup routines
    # ------------------------------------------------------------------

    def safe_cleanup(self) -> dict:
        """Run cache + temp + old logs purge, return combined result."""
        cache_res = self.execute_cache()
        temp_res = self.execute_temp()
        log_res = self.execute_old_logs()
        combined = self._merge_results([cache_res, temp_res, log_res])
        metrics.inc("purge.safe_cleanup_runs")
        logger.info(
            "Safe cleanup complete: %d items removed, %d bytes freed, %d errors",
            combined["removed"], combined["freed_bytes"], len(combined["errors"]),
        )
        return combined

    async def deep_cleanup(self) -> dict:
        """Run safe_cleanup + docker prune, return combined result."""
        safe_res = self.safe_cleanup()
        container_res = await self.execute_docker_containers()
        image_res = await self.execute_docker_images()
        volume_res = await self.execute_docker_volumes()
        build_res = await self.execute_docker_build_cache()
        combined = self._merge_results([
            safe_res, container_res, image_res, volume_res, build_res,
        ])
        metrics.inc("purge.deep_cleanup_runs")
        logger.info(
            "Deep cleanup complete: %d items removed, %d bytes freed, %d errors",
            combined["removed"], combined["freed_bytes"], len(combined["errors"]),
        )
        return combined

    @staticmethod
    def _merge_results(results: list[dict]) -> dict:
        total_removed = 0
        total_freed = 0
        all_errors = []
        for r in results:
            total_removed += r.get("removed", 0)
            total_freed += r.get("freed_bytes", 0)
            all_errors.extend(r.get("errors", []))
        return {
            "removed": total_removed,
            "freed_bytes": total_freed,
            "errors": all_errors,
        }

    # ==================================================================
    # SUMMARY
    # ==================================================================

    async def get_full_preview(self) -> dict:
        """Aggregate all previews into a single summary dict."""
        cache_items = self.preview_cache()
        temp_items = self.preview_temp()
        log_items = self.preview_old_logs()
        backup_items = self.preview_old_backups()
        docker_containers = await self.preview_docker_containers()
        docker_images = await self.preview_docker_images()
        docker_volumes = await self.preview_docker_volumes()
        docker_build = await self.preview_docker_build_cache()

        all_file_items = cache_items + temp_items + log_items + backup_items
        total_file_items = len(all_file_items)
        total_file_bytes = sum(i.get("size_bytes", 0) for i in all_file_items)

        docker_item_count = len(docker_containers) + len(docker_images) + len(docker_volumes)
        total_items = total_file_items + docker_item_count
        total_estimated_freed = total_file_bytes

        protected = _load_protected_vps()

        return {
            "cache": {
                "count": len(cache_items),
                "items": cache_items,
                "bytes": sum(i.get("size_bytes", 0) for i in cache_items),
            },
            "temp": {
                "count": len(temp_items),
                "items": temp_items,
                "bytes": sum(i.get("size_bytes", 0) for i in temp_items),
            },
            "old_logs": {
                "count": len(log_items),
                "items": log_items,
                "bytes": sum(i.get("size_bytes", 0) for i in log_items),
            },
            "old_backups": {
                "count": len(backup_items),
                "items": backup_items,
                "bytes": sum(i.get("size_bytes", 0) for i in backup_items),
            },
            "docker_containers": {
                "count": len(docker_containers),
                "items": docker_containers,
            },
            "docker_images": {
                "count": len(docker_images),
                "items": docker_images,
            },
            "docker_volumes": {
                "count": len(docker_volumes),
                "items": docker_volumes,
            },
            "docker_build_cache": docker_build,
            "protected_vps": {
                "count": len(protected),
                "names": sorted(protected),
            },
            "total_items": total_items,
            "total_estimated_bytes_freed": total_estimated_freed,
        }
