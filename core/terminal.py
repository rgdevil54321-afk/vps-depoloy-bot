import asyncio
import datetime
import os
import shutil
import time
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.services import (
    metrics,
    bus,
    load_json,
    save_json,
    DATA_DIR,
    BASE_DIR,
)


class AdminTerminal:
    def __init__(self, bot) -> None:
        self.bot = bot
        self.command_history: deque = deque(maxlen=500)
        self.command_log: List[dict] = []
        self.COMMANDS: Dict[str, dict] = self._build_command_map()

    # ------------------------------------------------------------------
    # command map
    # ------------------------------------------------------------------
    def _build_command_map(self) -> Dict[str, dict]:
        mapping: Dict[str, dict] = {}
        raw: List[Tuple[str, str, str, bool, str]] = [
            ("bot status",        "Show bot status overview",           "cmd_bot_status",        False, "bot"),
            ("bot restart",       "Schedule a bot restart",            "cmd_bot_restart",       True,  "bot"),
            ("bot reload",        "Reload bot configuration",          "cmd_bot_reload",        True,  "bot"),
            ("bot sync",          "Sync slash commands with Discord",   "cmd_bot_sync",          True,  "bot"),
            ("bot cache-clear",   "Clear __pycache__ directories",     "cmd_bot_cache_clear",   True,  "bot"),

            ("db status",         "Show database status",              "cmd_db_status",         True,  "db"),
            ("db backup",         "Create a database backup",          "cmd_db_backup",         True,  "db"),
            ("db restore",        "Restore database from backup",      "cmd_db_restore",        True,  "db"),
            ("db health",         "Run database health check",         "cmd_db_health",         True,  "db"),
            ("db integrity",      "Run database integrity check",      "cmd_db_integrity",      True,  "db"),

            ("docker status",     "Show Docker daemon status",         "cmd_docker_status",     True,  "docker"),
            ("docker containers", "List all Docker containers",        "cmd_docker_containers", True,  "docker"),
            ("docker stats",      "Show Docker container stats",       "cmd_docker_stats",      True,  "docker"),

            ("vps status",        "Show VPS count and summary",        "cmd_vps_status",        True,  "vps"),
            ("vps sync",          "Resync VPS data from disk",         "cmd_vps_sync",          True,  "vps"),
            ("vps health",        "Health-check all VPS containers",   "cmd_vps_health",        True,  "vps"),

            ("system cpu",        "Show CPU usage",                    "cmd_system_cpu",        True,  "system"),
            ("system memory",     "Show memory usage",                 "cmd_system_memory",     True,  "system"),
            ("system disk",       "Show disk usage",                   "cmd_system_disk",       True,  "system"),
            ("system uptime",     "Show system uptime",                "cmd_system_uptime",     True,  "system"),

            ("logs live",         "Tail bot.log (last 50 lines)",      "cmd_logs_live",         True,  "logs"),
            ("logs errors",       "Filter ERROR lines from log",       "cmd_logs_errors",       True,  "logs"),
            ("logs warnings",     "Filter WARNING lines from log",     "cmd_logs_warnings",     True,  "logs"),

            ("maintenance enable",  "Enable maintenance mode",         "cmd_maintenance_enable",  True,  "maintenance"),
            ("maintenance disable", "Disable maintenance mode",        "cmd_maintenance_disable", True,  "maintenance"),
        ]

        for name, desc, handler_name, admin_only, category in raw:
            mapping[name] = {
                "handler": getattr(self, handler_name),
                "description": desc,
                "admin_only": admin_only,
                "category": category,
            }
        return mapping

    # ------------------------------------------------------------------
    # public API
    # ------------------------------------------------------------------
    async def execute(self, command_str: str) -> dict:
        start = time.perf_counter()
        ts = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None).isoformat()
        cmd = command_str.strip().lower()

        entry = self.COMMANDS.get(cmd)
        if entry is None:
            return {
                "success": False,
                "output": f"Unknown command: `{command_str}`",
                "execution_time_ms": round((time.perf_counter() - start) * 1000, 2),
                "command": command_str,
                "timestamp": ts,
            }

        if entry["admin_only"] and not self._is_admin():
            result = {"success": False, "output": "Permission denied."}
        else:
            try:
                output = await entry["handler"](cmd)
                result = {"success": True, "output": output}
            except Exception as exc:
                result = {"success": False, "output": f"Error: {exc}"}

        elapsed = round((time.perf_counter() - start) * 1000, 2)
        record = {
            "success": result["success"],
            "output": result["output"],
            "execution_time_ms": elapsed,
            "command": command_str,
            "timestamp": ts,
        }
        self.command_history.append(record)
        self.command_log.append(record)
        try:
            bus.emit("terminal:executed", record)
        except Exception:
            pass
        return record

    def get_history(self, n: int = 20) -> List[dict]:
        return list(self.command_history)[-n:]

    def get_command_list(self) -> Dict[str, List[dict]]:
        grouped: Dict[str, List[dict]] = {}
        for name, meta in self.COMMANDS.items():
            grouped.setdefault(meta["category"], []).append({
                "command": name,
                "description": meta["description"],
                "admin_only": meta["admin_only"],
            })
        return grouped

    def search_commands(self, query: str) -> List[dict]:
        q = query.lower()
        return [
            {"command": name, "description": meta["description"], "category": meta["category"]}
            for name, meta in self.COMMANDS.items()
            if q in name or q in meta["description"].lower()
        ]

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------
    def _is_admin(self) -> bool:
        from core.services import load_json as _lj, DATA_DIR as _DD
        cfg = _lj(os.path.join(_DD, "config.json"), {})
        admin_id = cfg.get("MAIN_ADMIN_ID")
        user_id = getattr(self.bot, "_last_interaction_user_id", None)
        return user_id is not None and admin_id is not None and int(user_id) == int(admin_id)

    async def _run(self, *args: str, timeout: int = 30) -> str:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        out = stdout.decode(errors="replace").strip()
        err = stderr.decode(errors="replace").strip()
        if proc.returncode != 0:
            return f"[rc={proc.returncode}] {err or out}"
        return out or err or "(no output)"

    # ------------------------------------------------------------------
    # BOT commands
    # ------------------------------------------------------------------
    async def cmd_bot_status(self, _: str) -> str:
        latency_ms = round(self.bot.latency * 1000, 1) if hasattr(self.bot, "latency") else "N/A"
        guilds = len(self.bot.guilds) if hasattr(self.bot, "guilds") else 0
        users = sum(g.member_count or 0 for g in self.bot.guilds) if hasattr(self.bot, "guilds") else 0
        uptime = "N/A"
        if hasattr(self.bot, "uptime"):
            delta = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) - self.bot.uptime
            hours, rem = divmod(int(delta.total_seconds()), 3600)
            mins, secs = divmod(rem, 60)
            uptime = f"{hours}h {mins}m {secs}s"
        lines = [
            f"**Bot Status**",
            f"Latency: {latency_ms} ms",
            f"Guilds: {guilds}",
            f"Users: {users}",
            f"Uptime: {uptime}",
        ]
        return "\n".join(lines)

    async def cmd_bot_restart(self, _: str) -> str:
        bus.emit("bot:restart_requested", {})
        return "Restart scheduled. The bot will come back online shortly."

    async def cmd_bot_reload(self, _: str) -> str:
        try:
            bus.emit("bot:reload_config", {})
            return "Configuration reload signal sent."
        except Exception as exc:
            return f"Reload failed: {exc}"

    async def cmd_bot_sync(self, _: str) -> str:
        try:
            if hasattr(self.bot, "tree"):
                synced = await self.bot.tree.sync()
                return f"Synced {len(synced)} slash commands."
            return "Bot tree not available."
        except Exception as exc:
            return f"Sync failed: {exc}"

    async def cmd_bot_cache_clear(self, _: str) -> str:
        cleared = 0
        for root, dirs, files in os.walk(BASE_DIR):
            if "__pycache__" in dirs:
                pycache = os.path.join(root, "__pycache__")
                shutil.rmtree(pycache, ignore_errors=True)
                cleared += 1
        return f"Cleared {cleared} __pycache__ directories."

    # ------------------------------------------------------------------
    # DB commands
    # ------------------------------------------------------------------
    async def cmd_db_status(self, _: str) -> str:
        try:
            from core.db import DBManager
            status = await DBManager.get_status()
            return str(status)
        except Exception as exc:
            return f"DB status error: {exc}"

    async def cmd_db_backup(self, _: str) -> str:
        try:
            from core.db import DBManager
            name = await DBManager.backup()
            return f"Backup created: {name}"
        except Exception as exc:
            return f"Backup failed: {exc}"

    async def cmd_db_restore(self, cmd: str) -> str:
        parts = cmd.split(maxsplit=2)
        backup_name = parts[2] if len(parts) > 2 else None
        if not backup_name:
            return "Usage: `db restore <backup_name>`"
        try:
            from core.db import DBManager
            await DBManager.restore(backup_name)
            return f"Restored from {backup_name}."
        except Exception as exc:
            return f"Restore failed: {exc}"

    async def cmd_db_health(self, _: str) -> str:
        try:
            from core.db import DBManager
            result = await DBManager.health_check()
            return str(result)
        except Exception as exc:
            return f"Health check failed: {exc}"

    async def cmd_db_integrity(self, _: str) -> str:
        try:
            from core.db import DBManager
            result = await DBManager.integrity_check()
            return str(result)
        except Exception as exc:
            return f"Integrity check failed: {exc}"

    # ------------------------------------------------------------------
    # DOCKER commands
    # ------------------------------------------------------------------
    async def cmd_docker_status(self, _: str) -> str:
        return await self._run("docker", "info", "--format", "{{.ServerVersion}}")

    async def cmd_docker_containers(self, _: str) -> str:
        raw = await self._run(
            "docker", "ps", "-a",
            "--format", "table {{.Names}}\t{{.Status}}\t{{.Ports}}",
        )
        return f"```\n{raw}\n```"

    async def cmd_docker_stats(self, _: str) -> str:
        raw = await self._run("docker", "stats", "--no-stream",
                               "--format", "table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}",
                               timeout=15)
        return f"```\n{raw}\n```"

    # ------------------------------------------------------------------
    # VPS commands
    # ------------------------------------------------------------------
    async def cmd_vps_status(self, _: str) -> str:
        vps_data = load_json(os.path.join(DATA_DIR, "vps_data.json"), {})
        containers = vps_data.get("containers", {})
        total = len(containers)
        running = sum(1 for c in containers.values() if c.get("status") == "running")
        return f"Total VPS: {total}\nRunning: {running}\nStopped: {total - running}"

    async def cmd_vps_sync(self, _: str) -> str:
        bus.emit("vps:resync", {})
        return "VPS resync signal emitted."

    async def cmd_vps_health(self, _: str) -> str:
        vps_data = load_json(os.path.join(DATA_DIR, "vps_data.json"), {})
        containers = vps_data.get("containers", {})
        results: List[str] = []
        for name, info in containers.items():
            cid = info.get("container_id", "")
            if not cid:
                results.append(f"{name}: no container id")
                continue
            raw = await self._run("docker", "inspect", "--format", "{{.State.Health.Status}}", cid, timeout=10)
            results.append(f"{name}: {raw}")
        return "\n".join(results) if results else "No VPS containers found."

    # ------------------------------------------------------------------
    # SYSTEM commands
    # ------------------------------------------------------------------
    async def cmd_system_cpu(self, _: str) -> str:
        try:
            import psutil
            usage = psutil.cpu_percent(interval=1)
            return f"CPU usage: {usage}%"
        except ImportError:
            return await self._run("wmic", "cpu", "get", "loadpercentage")

    async def cmd_system_memory(self, _: str) -> str:
        try:
            import psutil
            mem = psutil.virtual_memory()
            return f"Total: {mem.total // (1024**2)} MB\nUsed: {mem.used // (1024**2)} MB\nAvailable: {mem.available // (1024**2)} MB\nPercent: {mem.percent}%"
        except ImportError:
            return await self._run("systeminfo")

    async def cmd_system_disk(self, _: str) -> str:
        try:
            import psutil
            parts = []
            for part in psutil.disk_partitions():
                usage = psutil.disk_usage(part.mountpoint)
                parts.append(f"{part.mountpoint}: {usage.used // (1024**3)}/{usage.total // (1024**3)} GB ({usage.percent}%)")
            return "\n".join(parts)
        except ImportError:
            return await self._run("wmic", "logicaldisk", "get", "size,freespace,caption")

    async def cmd_system_uptime(self, _: str) -> str:
        try:
            import psutil
            boot = datetime.datetime.fromtimestamp(psutil.boot_time())
            delta = datetime.datetime.now() - boot
            days = delta.days
            hours, rem = divmod(delta.seconds, 3600)
            mins, secs = divmod(rem, 60)
            return f"Uptime: {days}d {hours}h {mins}m {secs}s\nBoot: {boot.isoformat()}"
        except ImportError:
            return await self._run("net", "stats", "srv")

    # ------------------------------------------------------------------
    # LOGS commands
    # ------------------------------------------------------------------
    async def cmd_logs_live(self, _: str) -> str:
        log_path = os.path.join(BASE_DIR, "bot.log")
        if not os.path.isfile(log_path):
            return "bot.log not found."
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = fh.readlines()
            tail = lines[-50:]
            return "```\n" + "".join(tail) + "\n```"
        except Exception as exc:
            return f"Failed to read log: {exc}"

    async def cmd_logs_errors(self, _: str) -> str:
        return await self._filter_log("ERROR")

    async def cmd_logs_warnings(self, _: str) -> str:
        return await self._filter_log("WARNING")

    async def _filter_log(self, level: str) -> str:
        log_path = os.path.join(BASE_DIR, "bot.log")
        if not os.path.isfile(log_path):
            return "bot.log not found."
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as fh:
                lines = [l for l in fh if level in l]
            tail = lines[-50:]
            return f"Last {len(tail)} `{level}` lines:\n```\n" + "".join(tail) + "\n```"
        except Exception as exc:
            return f"Failed to read log: {exc}"

    # ------------------------------------------------------------------
    # MAINTENANCE commands
    # ------------------------------------------------------------------
    async def cmd_maintenance_enable(self, _: str) -> str:
        cfg_path = os.path.join(DATA_DIR, "config.json")
        cfg = load_json(cfg_path, {})
        old = cfg.get("MAINTENANCE_MODE", False)
        cfg["MAINTENANCE_MODE"] = True
        save_json(cfg_path, cfg)
        bus.emit("config:updated", {"key": "MAINTENANCE_MODE", "old": old, "new": True})
        return "Maintenance mode **enabled**."

    async def cmd_maintenance_disable(self, _: str) -> str:
        cfg_path = os.path.join(DATA_DIR, "config.json")
        cfg = load_json(cfg_path, {})
        old = cfg.get("MAINTENANCE_MODE", False)
        cfg["MAINTENANCE_MODE"] = False
        save_json(cfg_path, cfg)
        bus.emit("config:updated", {"key": "MAINTENANCE_MODE", "old": old, "new": False})
        return "Maintenance mode **disabled**."
