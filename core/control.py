import discord
import asyncio
import logging
import os
import json
import time
import sys
import shutil
import psutil
from datetime import datetime, timezone

from core.services import (
    metrics,
    bus,
    load_json,
    save_json,
    DATA_DIR,
    BASE_DIR,
    CONFIG_PATH,
)

logger = logging.getLogger("turtle.control")

ROTATION_FILE = os.path.join(DATA_DIR, "status_rotation.json")
EMERGENCY_FILE = os.path.join(DATA_DIR, "emergency.json")
RESTART_LOCK = asyncio.Lock()

STATUS_MAP = {
    "online": discord.Status.online,
    "idle": discord.Status.idle,
    "dnd": discord.Status.dnd,
    "invisible": discord.Status.invisible,
}

ACTIVITY_MAP = {
    "playing": discord.ActivityType.playing,
    "listening": discord.ActivityType.listening,
    "watching": discord.ActivityType.watching,
    "streaming": discord.ActivityType.streaming,
    "competing": discord.ActivityType.competing,
    "custom": discord.ActivityType.custom,
}

UPTIME_START = time.monotonic()


class BotController:
    DYNAMIC_TEMPLATES = {
        "vps_count": "Managing {vps_count} VPS",
        "vps_online": "{vps_online} VPS Online",
        "user_count": "{user_count} Users",
        "cpu_usage": "{cpu}% Host CPU",
        "ram_usage": "{ram}% Host RAM",
        "mixed": "{vps_count} VPS | {user_count} Users | {cpu}% CPU",
        "guild_count": "{guild_count} Servers",
        "latency": "{latency}ms Latency",
    }

    def __init__(self, bot):
        self.bot = bot
        self._rotation_task = None
        self._rotation_index = 0
        self._start_time = time.monotonic()
        logger.info("BotController initialised")

    # ------------------------------------------------------------------
    # Status & Presence
    # ------------------------------------------------------------------

    async def set_status(self, status_str: str) -> dict:
        status_str = status_str.lower().strip()
        if status_str not in STATUS_MAP:
            return {
                "success": False,
                "error": f"Unknown status '{status_str}'. Valid: {list(STATUS_MAP)}",
            }

        old_status = str(self.bot.status) if self.bot.status else "offline"
        new_discord_status = STATUS_MAP[status_str]

        try:
            await self.bot.change_presence(status=new_discord_status)
            bus.emit("status:changed", {"old": old_status, "new": status_str})
            metrics.inc("control.status_changes")
            return {
                "success": True,
                "old_status": old_status,
                "new_status": status_str,
            }
        except Exception as exc:
            logger.error("set_status failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def set_activity(self, activity_type: str, text: str) -> dict:
        activity_type = activity_type.lower().strip()
        if activity_type not in ACTIVITY_MAP:
            return {
                "success": False,
                "error": f"Unknown activity type '{activity_type}'. Valid: {list(ACTIVITY_MAP)}",
            }

        discord_type = ACTIVITY_MAP[activity_type]

        try:
            if activity_type == "streaming":
                activity = discord.Streaming(name=text, url="https://twitch.tv/turtle")
            elif activity_type == "custom":
                activity = discord.CustomActivity(name=text)
            else:
                activity = discord.Activity(type=discord_type, name=text)

            await self.bot.change_presence(activity=activity)
            bus.emit("activity:changed", {"type": activity_type, "text": text})
            metrics.inc("control.activity_changes")
            return {"success": True, "activity": {"type": activity_type, "text": text}}
        except Exception as exc:
            logger.error("set_activity failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def set_dynamic_activity(self, template: str) -> dict:
        rendered = await self._render_template(template)
        result = await self.set_activity("watching", rendered)
        if result.get("success"):
            result["rendered_text"] = rendered
        return result

    # ------------------------------------------------------------------
    # Status Querying
    # ------------------------------------------------------------------

    def get_current_status(self) -> dict:
        activity = self.bot.activity
        activity_type = None
        activity_text = None
        if activity:
            activity_type = getattr(activity, "type", None)
            if activity_type is not None:
                activity_type = activity_type.name
            activity_text = getattr(activity, "name", None)

        uptime_seconds = time.monotonic() - UPTIME_START
        return {
            "status": str(self.bot.status),
            "activity_type": activity_type,
            "activity_text": activity_text,
            "latency_ms": round(self.bot.latency * 1000, 1),
            "uptime_seconds": int(uptime_seconds),
            "uptime_human": _format_uptime(uptime_seconds),
            "ready": self.bot.is_ready(),
            "guild_count": len(self.bot.guilds),
            "user_count": sum(g.member_count or 0 for g in self.bot.guilds),
        }

    # ------------------------------------------------------------------
    # Dynamic Template Rendering
    # ------------------------------------------------------------------

    async def _render_template(self, template: str) -> str:
        replacements = {}

        vps_data = getattr(self.bot, "vps_data", {})
        if isinstance(vps_data, dict):
            replacements["vps_count"] = str(len(vps_data))
            replacements["vps_online"] = str(
                sum(1 for v in vps_data.values() if isinstance(v, dict) and v.get("status") == "running")
            )
        else:
            replacements["vps_count"] = "0"
            replacements["vps_online"] = "0"

        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)
        replacements["guild_count"] = str(guild_count)
        replacements["user_count"] = str(user_count)

        try:
            cpu = psutil.cpu_percent(interval=0.1)
            replacements["cpu"] = str(int(cpu))
        except Exception:
            replacements["cpu"] = "?"

        try:
            mem = psutil.virtual_memory()
            replacements["ram"] = str(int(mem.percent))
        except Exception:
            replacements["ram"] = "?"

        replacements["latency"] = str(round(self.bot.latency * 1000, 0))
        replacements["uptime"] = _format_uptime(time.monotonic() - UPTIME_START)

        rendered = template
        for key, value in replacements.items():
            rendered = rendered.replace("{" + key + "}", value)
        return rendered

    # ------------------------------------------------------------------
    # Restart / Reconnect / Shutdown
    # ------------------------------------------------------------------

    async def restart_bot(self) -> dict:
        async with RESTART_LOCK:
            try:
                save_json(EMERGENCY_FILE, {
                    "restarting": True,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                    "reason": "manual_restart",
                })
                logger.warning("Emergency file written, restarting in 2s")
                metrics.inc("control.restarts")

                await asyncio.sleep(2)
                await self.bot.close()

                python = sys.executable
                script = os.path.join(BASE_DIR, "bot.py")
                os.execv(python, [python, script])
                return {"success": True}
            except Exception as exc:
                logger.error("restart_bot failed: %s", exc)
                return {"success": False, "error": str(exc)}

    async def reconnect_bot(self) -> dict:
        try:
            metrics.inc("control.reconnects")
            logger.info("Manual reconnect requested")
            await self.bot.close()
            return {"success": True, "message": "Bot closed; discord.py will auto-reconnect"}
        except Exception as exc:
            logger.error("reconnect_bot failed: %s", exc)
            return {"success": False, "error": str(exc)}

    async def shutdown_bot(self) -> dict:
        try:
            logger.warning("Graceful shutdown initiated")
            save_json(EMERGENCY_FILE, {
                "restarting": False,
                "shutdown": True,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })

            for cog_name, cog in self.bot.cogs.items():
                if hasattr(cog, "cog_unload"):
                    try:
                        unload = cog.cog_unload()
                        if asyncio.iscoroutine(unload):
                            await unload
                    except Exception:
                        logger.debug("cog_unload error for %s", cog_name, exc_info=True)

            if hasattr(self.bot, "session") and self.bot.session and not self.bot.session.closed:
                await self.bot.session.close()

            if hasattr(self.bot, "_vps_db") and self.bot._vps_db:
                try:
                    await self.bot._vps_db.close()
                except Exception:
                    pass

            if self._rotation_task and not self._rotation_task.done():
                self._rotation_task.cancel()

            await self.bot.close()
            logger.info("Bot closed, exiting process")
            os._exit(0)
            return {"success": True}
        except Exception as exc:
            logger.error("shutdown_bot failed: %s", exc)
            os._exit(1)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Command Tree Sync
    # ------------------------------------------------------------------

    async def sync_commands(self) -> dict:
        try:
            synced = await self.bot.tree.sync()
            count = len(synced)
            logger.info("Synced %d slash commands", count)
            metrics.inc("control.command_syncs")
            bus.emit("commands:synced", {"count": count})
            return {"success": True, "count": count}
        except Exception as exc:
            logger.error("sync_commands failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Config Reload
    # ------------------------------------------------------------------

    async def reload_config(self) -> dict:
        try:
            new_config = load_json(CONFIG_PATH, default={})
            if not new_config:
                return {"success": False, "error": "Config file empty or unreadable"}

            if hasattr(self.bot, "_config"):
                self.bot._config = new_config
            if hasattr(self.bot, "config"):
                self.bot.config = new_config

            token = new_config.get("token", "")
            if token:
                self.bot.http.token = f"Bot {token}"

            for key, value in new_config.items():
                if key != "token":
                    setattr(self.bot, f"cfg_{key}", value)

            metrics.inc("control.config_reloads")
            bus.emit("config:reloaded", {"keys": list(new_config.keys())})
            logger.info("Config reloaded (%d keys)", len(new_config))
            return {"success": True, "keys": list(new_config.keys())}
        except Exception as exc:
            logger.error("reload_config failed: %s", exc)
            return {"success": False, "error": str(exc)}

    # ------------------------------------------------------------------
    # Cache Management
    # ------------------------------------------------------------------

    def clear_cache(self) -> dict:
        removed = 0
        for dirpath, dirnames, filenames in os.walk(BASE_DIR):
            if "__pycache__" in dirnames:
                cache_dir = os.path.join(dirpath, "__pycache__")
                try:
                    shutil.rmtree(cache_dir, ignore_errors=True)
                    removed += 1
                    dirnames.remove("__pycache__")
                except Exception:
                    pass

        logger.info("Cleared %d __pycache__ directories", removed)
        metrics.inc("control.cache_clears")
        return {"success": True, "directories_removed": removed}

    # ------------------------------------------------------------------
    # Health Check
    # ------------------------------------------------------------------

    def get_health(self) -> dict:
        gateway_ready = self.bot.is_ready()
        latency_ms = round(self.bot.latency * 1000, 1) if gateway_ready else -1
        uptime_seconds = time.monotonic() - UPTIME_START

        vps_data = getattr(self.bot, "vps_data", {})
        vps_count = len(vps_data) if isinstance(vps_data, dict) else 0

        try:
            proc = psutil.Process(os.getpid())
            mem_mb = round(proc.memory_info().rss / (1024 * 1024), 1)
        except Exception:
            mem_mb = -1.0

        try:
            cpu_pct = psutil.cpu_percent(interval=0.1)
        except Exception:
            cpu_pct = -1.0

        all_tasks = asyncio.all_tasks()
        background_tasks = len([t for t in all_tasks if not t.done()])

        guild_count = len(self.bot.guilds)
        user_count = sum(g.member_count or 0 for g in self.bot.guilds)

        health_status = "healthy"
        if not gateway_ready:
            health_status = "degraded"
        if latency_ms > 500:
            health_status = "degraded"
        if mem_mb > 512:
            health_status = "warning"

        return {
            "status": health_status,
            "gateway_ready": gateway_ready,
            "latency_ms": latency_ms,
            "guild_count": guild_count,
            "user_count": user_count,
            "vps_count": vps_count,
            "uptime_seconds": int(uptime_seconds),
            "uptime_human": _format_uptime(uptime_seconds),
            "memory_mb": mem_mb,
            "cpu_percent": cpu_pct,
            "background_tasks": background_tasks,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # Status Rotation
    # ------------------------------------------------------------------

    def get_status_rotation(self) -> dict:
        data = load_json(ROTATION_FILE, default={})
        return {
            "enabled": data.get("enabled", False),
            "templates": data.get("templates", []),
            "interval_seconds": data.get("interval_seconds", 30),
            "current_index": self._rotation_index,
        }

    def set_status_rotation(self, templates_list: list, interval_seconds: int = 30) -> dict:
        interval_seconds = max(10, min(interval_seconds, 3600))
        data = {
            "enabled": True,
            "templates": templates_list,
            "interval_seconds": interval_seconds,
        }
        save_json(ROTATION_FILE, data)
        logger.info("Status rotation set: %d templates, %ds interval", len(templates_list), interval_seconds)
        return {"success": True, **data}

    async def run_status_rotation(self):
        self._rotation_task = asyncio.create_task(self._rotation_loop())
        logger.info("Status rotation loop started")
        return {"success": True}

    async def _rotation_loop(self):
        while True:
            try:
                rotation = self.get_status_rotation()
                if not rotation.get("enabled") or not rotation.get("templates"):
                    await asyncio.sleep(10)
                    continue

                templates = rotation["templates"]
                interval = rotation["interval_seconds"]

                if self._rotation_index >= len(templates):
                    self._rotation_index = 0

                template = templates[self._rotation_index]
                rendered = await self._render_template(template)
                await self.set_activity("watching", rendered)
                metrics.inc("control.rotation_ticks")

                self._rotation_index = (self._rotation_index + 1) % len(templates)
                await asyncio.sleep(interval)

            except asyncio.CancelledError:
                logger.info("Status rotation loop cancelled")
                break
            except Exception:
                logger.error("Rotation loop error", exc_info=True)
                await asyncio.sleep(15)

    async def stop_rotation(self):
        if self._rotation_task and not self._rotation_task.done():
            self._rotation_task.cancel()
            try:
                await self._rotation_task
            except asyncio.CancelledError:
                pass
            self._rotation_task = None
            logger.info("Status rotation stopped")
            return {"success": True}
        return {"success": False, "error": "No rotation task running"}

    async def force_rotation_tick(self) -> dict:
        rotation = self.get_status_rotation()
        templates = rotation.get("templates", [])
        if not templates:
            return {"success": False, "error": "No templates configured"}

        idx = self._rotation_index % len(templates)
        template = templates[idx]
        rendered = await self._render_template(template)
        result = await self.set_activity("watching", rendered)
        self._rotation_index = (idx + 1) % len(templates)
        result["rendered_text"] = rendered
        result["template"] = template
        return result


def _format_uptime(seconds: float) -> str:
    seconds = int(seconds)
    days = seconds // 86400
    hours = (seconds % 86400) // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    parts = []
    if days > 0:
        parts.append(f"{days}d")
    if hours > 0:
        parts.append(f"{hours}h")
    if minutes > 0:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)
