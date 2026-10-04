import os
import asyncio
import logging
import time
import json
import subprocess
import shutil
from datetime import datetime, timedelta
from core.services import metrics, bus, load_json, save_json, DATA_DIR, BASE_DIR, BACKUP_DIR, CACHE_DIR

logger = logging.getLogger("turtle.maintenance")


class MaintenanceCenter:
    def __init__(self, bot):
        self.bot = bot

    # ── Maintenance mode ──────────────────────────────────────────────

    def is_maintenance_mode(self) -> bool:
        try:
            from core.emergency import EmergencyManager
            mgr = EmergencyManager()
            return mgr.is_maintenance_mode()
        except Exception:
            data = load_json("emergency.json", {})
            return data.get("maintenance_mode", False)

    def enable_maintenance(self, reason: str, admin_id: int) -> dict:
        if self.is_maintenance_mode():
            return {"success": False, "message": "Already in maintenance mode."}
        try:
            from core.emergency import EmergencyManager
            mgr = EmergencyManager()
            return mgr.enable_maintenance(reason, admin_id)
        except Exception:
            data = load_json("emergency.json", {})
            data["maintenance_mode"] = True
            data["maintenance_reason"] = reason
            data["maintenance_enabled_by"] = admin_id
            data["maintenance_enabled_at"] = datetime.now().isoformat()
            save_json("emergency.json", data)
            logger.warning("Maintenance mode enabled by %s: %s", admin_id, reason)
            bus.emit("maintenance.enabled", {"reason": reason, "admin_id": admin_id})
            metrics.counter("maintenance.mode.enabled")
            return {"success": True, "message": f"Maintenance mode enabled: {reason}"}

    def disable_maintenance(self, admin_id: int) -> dict:
        if not self.is_maintenance_mode():
            return {"success": False, "message": "Not in maintenance mode."}
        try:
            from core.emergency import EmergencyManager
            mgr = EmergencyManager()
            return mgr.disable_maintenance(admin_id)
        except Exception:
            data = load_json("emergency.json", {})
            data["maintenance_mode"] = False
            data["maintenance_disabled_by"] = admin_id
            data["maintenance_disabled_at"] = datetime.now().isoformat()
            save_json("emergency.json", data)
            logger.info("Maintenance mode disabled by %s", admin_id)
            bus.emit("maintenance.disabled", {"admin_id": admin_id})
            metrics.counter("maintenance.mode.disabled")
            return {"success": True, "message": "Maintenance mode disabled."}

    # ── Service operations ────────────────────────────────────────────

    async def restart_docker(self) -> dict:
        try:
            for cmd in [
                ["sudo", "systemctl", "restart", "docker"],
                ["sudo", "service", "docker", "restart"],
            ]:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
                if proc.returncode == 0:
                    logger.info("Docker restarted successfully")
                    bus.emit("services.docker.restarted")
                    metrics.counter("maintenance.docker.restart")
                    return {"success": True, "message": "Docker restarted successfully."}
            return {"success": False, "message": "Failed to restart Docker."}
        except asyncio.TimeoutError:
            return {"success": False, "message": "Docker restart timed out."}
        except Exception as e:
            logger.error("Docker restart failed: %s", e)
            return {"success": False, "message": f"Error: {e}"}

    async def restart_bot(self) -> dict:
        try:
            self.enable_maintenance("Bot restart initiated", 0)
            logger.info("Bot restart initiated")
            bus.emit("services.bot.restarting")
            metrics.counter("maintenance.bot.restart")
            await asyncio.sleep(2)
            if self.bot and hasattr(self.bot, "loop") and self.bot.loop.is_running():
                asyncio.ensure_future(self._graceful_restart())
            else:
                os.execv(__import__("sys").executable, [__import__("sys").executable] + __import__("sys").argv)
            return {"success": True, "message": "Bot restart initiated."}
        except Exception as e:
            logger.error("Bot restart failed: %s", e)
            return {"success": False, "message": f"Error: {e}"}

    async def _graceful_restart(self):
        try:
            if self.bot and hasattr(self.bot, "close"):
                await self.bot.close()
        except Exception:
            pass
        await asyncio.sleep(1)
        import sys
        os.execv(sys.executable, [sys.executable] + sys.argv)

    async def reload_config(self) -> dict:
        try:
            config = load_json("config.json", {})
            if not config:
                return {"success": False, "message": "config.json not found or empty."}
            self.bot.config = config
            bus.emit("services.config.reloaded")
            metrics.counter("maintenance.config.reload")
            logger.info("Configuration reloaded")
            return {"success": True, "message": "Configuration reloaded successfully."}
        except Exception as e:
            return {"success": False, "message": f"Error: {e}"}

    def clear_cache(self) -> dict:
        removed = 0
        for root, dirs, files in os.walk(str(BASE_DIR)):
            for d in dirs:
                if d == "__pycache__":
                    path = os.path.join(root, d)
                    try:
                        shutil.rmtree(path)
                        removed += 1
                    except Exception:
                        pass
        metrics.counter("maintenance.cache.cleared")
        bus.emit("services.cache.cleared")
        return {"removed_count": removed}

    def clean_logs(self, keep_days: int = 7) -> dict:
        removed = 0
        freed = 0
        cutoff = datetime.now() - timedelta(days=keep_days)
        for root, dirs, files in os.walk(str(DATA_DIR)):
            for f in files:
                if f.endswith((".log", ".log.1")):
                    path = os.path.join(root, f)
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(path))
                        if mtime < cutoff:
                            size = os.path.getsize(path)
                            os.remove(path)
                            freed += size
                            removed += 1
                    except Exception:
                        pass
        metrics.counter("maintenance.logs.cleaned")
        return {"removed_count": removed, "freed_bytes": freed}

    async def run_diagnostics(self) -> dict:
        try:
            from core.diagnostics import DiagnosticsRunner
            runner = DiagnosticsRunner(self.bot)
            results = await runner.run_all()
            metrics.counter("maintenance.diagnostics.run")
            bus.emit("services.diagnostics.completed")
            return results
        except Exception as e:
            logger.error("Diagnostics failed: %s", e)
            return {"success": False, "error": str(e)}

    async def run_health_check(self) -> dict:
        try:
            from core.health_monitor import HealthMonitor
            monitor = HealthMonitor(self.bot)
            results = await monitor.run_checks()
            metrics.counter("maintenance.health_check.run")
            return results
        except Exception as e:
            logger.error("Health check failed: %s", e)
            return {"success": False, "error": str(e)}

    async def backup_database(self) -> dict:
        try:
            db_path = os.path.join(DATA_DIR, "turtle.db")
            if not os.path.exists(db_path):
                return {"success": False, "message": "Database file not found."}
            os.makedirs(str(BACKUP_DIR), exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"turtle_backup_{timestamp}.db"
            backup_path = os.path.join(str(BACKUP_DIR), backup_name)
            proc = await asyncio.create_subprocess_exec(
                "cp", db_path, backup_path,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()
            if proc.returncode == 0 and os.path.exists(backup_path):
                logger.info("Database backed up to %s", backup_name)
                bus.emit("services.database.backup", {"name": backup_name})
                metrics.counter("maintenance.database.backup")
                return {"success": True, "backup_name": backup_name}
            shutil.copy2(db_path, backup_path)
            if os.path.exists(backup_path):
                return {"success": True, "backup_name": backup_name}
            return {"success": False, "message": "Backup copy failed."}
        except Exception as e:
            logger.error("Database backup failed: %s", e)
            return {"success": False, "message": f"Error: {e}"}

    async def check_dependencies(self) -> dict:
        results = {"pip_packages": {}, "system_binaries": {}, "all_ok": True}
        required_bins = ["docker", "git", "python3", "pip3", "ffmpeg", "sqlite3"]
        for binary in required_bins:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "which", binary,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                found = proc.returncode == 0
                results["system_binaries"][binary] = {
                    "installed": found,
                    "path": stdout.decode().strip() if found else None,
                }
                if not found:
                    results["all_ok"] = False
            except Exception:
                results["system_binaries"][binary] = {"installed": False, "path": None}
                results["all_ok"] = False

        try:
            req_path = os.path.join(str(BASE_DIR), "requirements.txt")
            if os.path.exists(req_path):
                with open(req_path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        pkg_name = line.split("==")[0].split(">=")[0].split("<=")[0].split("!=")[0].strip()
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "pip3", "show", pkg_name,
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                            )
                            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
                            installed = proc.returncode == 0
                            version = None
                            if installed:
                                for ln in stdout.decode().splitlines():
                                    if ln.startswith("Version:"):
                                        version = ln.split(":", 1)[1].strip()
                            results["pip_packages"][pkg_name] = {"installed": installed, "version": version}
                            if not installed:
                                results["all_ok"] = False
                        except Exception:
                            results["pip_packages"][pkg_name] = {"installed": False, "version": None}
                            results["all_ok"] = False
        except Exception as e:
            results["pip_error"] = str(e)
            results["all_ok"] = False

        metrics.counter("maintenance.dependencies.checked")
        return results

    # ── Checklist ─────────────────────────────────────────────────────

    def get_maintenance_checklist(self) -> list:
        now = datetime.now().isoformat()
        return [
            {"name": "maintenance_mode", "description": "Current maintenance mode status", "status": "checked", "last_run": now, "action_name": "toggle_maintenance"},
            {"name": "docker", "description": "Docker daemon status", "status": "checked", "last_run": now, "action_name": "restart_docker"},
            {"name": "database", "description": "Database integrity", "status": "checked", "last_run": now, "action_name": "backup_database"},
            {"name": "cache", "description": "Cache size / cleanup needed", "status": "checked", "last_run": now, "action_name": "clear_cache"},
            {"name": "logs", "description": "Log file sizes", "status": "checked", "last_run": now, "action_name": "clean_logs"},
            {"name": "backups", "description": "Backup recency and count", "status": "checked", "last_run": now, "action_name": "backup_database"},
            {"name": "dependencies", "description": "Python packages up to date", "status": "checked", "last_run": now, "action_name": "check_dependencies"},
            {"name": "disk_space", "description": "Disk usage percentage", "status": "checked", "last_run": now, "action_name": None},
            {"name": "containers", "description": "All VPS containers running", "status": "checked", "last_run": now, "action_name": "restart_docker"},
            {"name": "background_tasks", "description": "All loops alive", "status": "checked", "last_run": now, "action_name": None},
        ]

    async def run_full_check(self) -> list:
        now = datetime.now().isoformat()
        items = []

        maintenance = self.is_maintenance_mode()
        items.append({
            "name": "maintenance_mode",
            "description": "Current maintenance mode status",
            "status": "on" if maintenance else "off",
            "last_run": now,
            "action_name": "toggle_maintenance",
        })

        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "docker",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            docker_active = stdout.decode().strip() == "active"
        except Exception:
            docker_active = False
        items.append({
            "name": "docker",
            "description": "Docker daemon status",
            "status": "running" if docker_active else "stopped",
            "last_run": now,
            "action_name": "restart_docker",
        })

        db_path = os.path.join(DATA_DIR, "turtle.db")
        db_ok = os.path.exists(db_path)
        if db_ok:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "sqlite3", db_path, "PRAGMA integrity_check;",
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=15)
                output = stdout.decode().strip().lower()
                db_ok = output == "ok"
            except Exception:
                db_ok = True
        items.append({
            "name": "database",
            "description": "Database integrity",
            "status": "ok" if db_ok else "error",
            "last_run": now,
            "action_name": "backup_database",
        })

        cache_size = 0
        cache_dirs = 0
        for root, dirs, _ in os.walk(str(BASE_DIR)):
            for d in dirs:
                if d == "__pycache__":
                    cache_dirs += 1
                    try:
                        for rf, _, ffiles in os.walk(os.path.join(root, d)):
                            for ff in ffiles:
                                cache_size += os.path.getsize(os.path.join(rf, ff))
                    except Exception:
                        pass
        items.append({
            "name": "cache",
            "description": f"Cache size / cleanup needed ({cache_dirs} dirs, {cache_size} bytes)",
            "status": "clean" if cache_dirs == 0 else "needs_cleanup",
            "last_run": now,
            "action_name": "clear_cache",
        })

        log_size = 0
        for root, _, files in os.walk(str(DATA_DIR)):
            for f in files:
                if f.endswith((".log", ".log.1")):
                    try:
                        log_size += os.path.getsize(os.path.join(root, f))
                    except Exception:
                        pass
        items.append({
            "name": "logs",
            "description": f"Log file sizes ({log_size} bytes)",
            "status": "ok" if log_size < 100 * 1024 * 1024 else "large",
            "last_run": now,
            "action_name": "clean_logs",
        })

        backups = []
        if os.path.isdir(str(BACKUP_DIR)):
            try:
                backups = sorted(os.listdir(str(BACKUP_DIR)))
            except Exception:
                pass
        backup_count = len(backups)
        if backups:
            try:
                latest_path = os.path.join(str(BACKUP_DIR), backups[-1])
                age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(latest_path))
                recent = age < timedelta(days=1)
            except Exception:
                recent = False
        else:
            recent = False
        items.append({
            "name": "backups",
            "description": f"Backup recency and count ({backup_count} backups)",
            "status": "ok" if recent and backup_count > 0 else "stale",
            "last_run": now,
            "action_name": "backup_database",
        })

        dep_results = await self.check_dependencies()
        dep_ok = dep_results.get("all_ok", False)
        items.append({
            "name": "dependencies",
            "description": "Python packages up to date",
            "status": "ok" if dep_ok else "issues_found",
            "last_run": now,
            "action_name": "check_dependencies",
        })

        try:
            usage = shutil.disk_usage(str(BASE_DIR))
            disk_pct = round((usage.used / usage.total) * 100, 1)
        except Exception:
            disk_pct = -1
        if disk_pct < 0:
            disk_status = "unknown"
        elif disk_pct >= 95:
            disk_status = "critical"
        elif disk_pct >= 85:
            disk_status = "warning"
        else:
            disk_status = "ok"
        items.append({
            "name": "disk_space",
            "description": f"Disk usage percentage ({disk_pct}% used)" if disk_pct >= 0 else "Disk usage unknown",
            "status": disk_status,
            "last_run": now,
            "action_name": None,
        })

        container_count = 0
        containers_running = 0
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a", "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            all_names = stdout.decode().strip().splitlines() if stdout else []
            container_count = len(all_names)

            proc2 = await asyncio.create_subprocess_exec(
                "docker", "ps", "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
            )
            stdout2, _ = await asyncio.wait_for(proc2.communicate(), timeout=10)
            running_names = stdout2.decode().strip().splitlines() if stdout2 else []
            containers_running = len(running_names)
        except Exception:
            pass
        items.append({
            "name": "containers",
            "description": f"All VPS containers running ({containers_running}/{container_count})",
            "status": "ok" if container_count > 0 and container_count == containers_running else "issues",
            "last_run": now,
            "action_name": "restart_docker",
        })

        loops_alive = False
        try:
            if self.bot and hasattr(self.bot, "loop") and self.bot.loop:
                loops_alive = self.bot.loop.is_running()
        except Exception:
            pass
        items.append({
            "name": "background_tasks",
            "description": "All loops alive",
            "status": "alive" if loops_alive else "dead",
            "last_run": now,
            "action_name": None,
        })

        metrics.counter("maintenance.full_check.run")
        return items

    async def get_maintenance_report(self) -> dict:
        checklist = await self.run_full_check()
        statuses = [item["status"] for item in checklist]
        critical_count = sum(1 for s in statuses if s in ("error", "critical", "dead"))
        warning_count = sum(1 for s in statuses if s in ("warning", "stale", "needs_cleanup", "stopped", "issues"))
        if critical_count > 0:
            overall = "critical"
        elif warning_count > 0:
            overall = "warning"
        else:
            overall = "healthy"

        recommendations = []
        for item in checklist:
            s = item["status"]
            if s == "stopped":
                recommendations.append(f"Docker daemon is stopped. Consider running '{item['action_name']}'.")
            elif s == "error":
                recommendations.append(f"{item['name']}: Error detected. Action: {item['action_name']}.")
            elif s == "needs_cleanup":
                recommendations.append(f"Cache cleanup recommended ({item['action_name']}).")
            elif s == "large":
                recommendations.append(f"Log files are large. Consider running '{item['action_name']}'.")
            elif s == "stale":
                recommendations.append(f"Backups are stale. Consider running '{item['action_name']}'.")
            elif s == "issues_found":
                recommendations.append(f"Dependency issues found. Check with '{item['action_name']}'.")
            elif s == "warning":
                recommendations.append("Disk usage is high. Free up space.")
            elif s == "critical":
                recommendations.append("Disk usage is critical. Immediate action required.")
            elif s == "dead":
                recommendations.append("Background tasks are not running. Restart may be needed.")
            elif s == "issues":
                recommendations.append("Not all containers are running. Check docker status.")

        report = {
            "checklist": checklist,
            "overall_status": overall,
            "critical_issues": critical_count,
            "warnings": warning_count,
            "recommendations": recommendations,
            "generated_at": datetime.now().isoformat(),
        }
        metrics.counter("maintenance.report.generated")
        return report

    def get_restartable_services(self) -> list:
        return [
            {
                "name": "docker",
                "description": "Docker daemon - manages all VPS containers and services",
                "can_restart": True,
            },
            {
                "name": "bot",
                "description": "Discord bot process - the main Turtle Nodes bot",
                "can_restart": True,
            },
            {
                "name": "api",
                "description": "API server - handles HTTP requests and webhooks",
                "can_restart": True,
            },
        ]
