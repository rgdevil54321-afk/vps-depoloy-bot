"""Bot Core Management - lifecycle, status metrics, and core operations for Turtle Nodes."""
import os
import sys
import time
import shutil
import asyncio
import logging
import platform

from discord.ext import commands

from core.services import metrics, health, bus, load_json, save_json, DATA_DIR, BASE_DIR

logger = logging.getLogger("turtle.bot_core")

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

try:
    import psutil as _psutil
    _HAVE_PSUTIL = True
except ImportError:
    _HAVE_PSUTIL = False
    logger.warning("psutil not installed - host metrics will be limited")

BOT_START_TIME = time.time()


def _get_uptime() -> float:
    return time.time() - BOT_START_TIME


def _format_uptime(seconds: float) -> str:
    days = int(seconds) // 86400
    hours = (int(seconds) % 86400) // 3600
    minutes = (int(seconds) % 3600) // 60
    secs = int(seconds) % 60
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{secs}s")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Host metric helpers (blocking - run in thread)
# ---------------------------------------------------------------------------

def _cpu_percent_sync() -> float:
    if _HAVE_PSUTIL:
        try:
            return _psutil.cpu_percent(interval=0.3)
        except Exception:
            pass
    return 0.0


def _ram_percent_sync() -> float:
    if _HAVE_PSUTIL:
        try:
            return _psutil.virtual_memory().percent
        except Exception:
            pass
    return 0.0


def _thread_count_sync() -> int:
    if _HAVE_PSUTIL:
        try:
            proc = _psutil.Process(os.getpid())
            return proc.num_threads()
        except Exception:
            pass
    return 0


def _disk_free_percent() -> float:
    if _HAVE_PSUTIL:
        try:
            return _psutil.disk_usage("/").percent
        except Exception:
            pass
    return 0.0


def _mem_free_percent() -> float:
    if _HAVE_PSUTIL:
        try:
            return _psutil.virtual_memory().percent
        except Exception:
            pass
    return 0.0


# ---------------------------------------------------------------------------
# Core async functions - called by bot command handlers
# ---------------------------------------------------------------------------

async def get_bot_status(bot: commands.Bot) -> dict:
    """Return live bot status dict for the dashboard."""
    uptime_secs = _get_uptime()

    cpu_percent = await asyncio.to_thread(_cpu_percent_sync)
    ram_percent = await asyncio.to_thread(_ram_percent_sync)
    thread_count = await asyncio.to_thread(_thread_count_sync)

    loop_healthy = True
    try:
        loop = asyncio.get_running_loop()
        loop_healthy = loop.is_running()
    except RuntimeError:
        loop_healthy = False

    gateway_ok = False
    try:
        gateway_ok = bot.is_ready()
    except Exception:
        gateway_ok = False

    api_latency_ms = 0.0
    try:
        api_latency_ms = round(bot.latency * 1000, 2) if bot.is_ready() else 0.0
    except Exception:
        api_latency_ms = 0.0

    guild_count = 0
    user_count = 0
    command_count = 0
    try:
        guild_count = len(bot.guilds)
        user_count = sum(g.member_count or 0 for g in bot.guilds)
        command_count = len(bot.commands)
    except Exception:
        pass

    status_map = {
        "online": "online",
        "idle": "idle",
        "dnd": "dnd",
        "invisible": "invisible",
        "offline": "offline",
    }
    status_text = "offline"
    try:
        if bot.user and bot.user.status:
            status_text = status_map.get(str(bot.user.status), str(bot.user.status))
        elif gateway_ok:
            status_text = "online"
    except Exception:
        status_text = "unknown"

    return {
        "uptime": _format_uptime(uptime_secs),
        "uptime_seconds": uptime_secs,
        "latency_ms": api_latency_ms,
        "guild_count": guild_count,
        "user_count": user_count,
        "command_count": command_count,
        "cpu_percent": cpu_percent,
        "ram_percent": ram_percent,
        "thread_count": thread_count,
        "event_loop_healthy": loop_healthy,
        "status_text": status_text,
        "gateway_status": "connected" if gateway_ok else "disconnected",
        "api_latency": api_latency_ms,
    }


async def get_bot_metrics(bot: commands.Bot) -> dict:
    """Return aggregated metrics from the metrics collector."""
    counters = await asyncio.to_thread(metrics.all_counters)
    uptime_secs = _get_uptime()
    events_count = await asyncio.to_thread(metrics.recent_events, 9999)
    events_processed = len(events_count)

    return {
        "commands_executed": counters.get("commands_executed", 0),
        "errors_count": counters.get("errors_count", 0),
        "vps_deployed": counters.get("vps_deployed", 0),
        "vps_deleted": counters.get("vps_deleted", 0),
        "uptime_seconds": uptime_secs,
        "events_processed": events_processed,
    }


async def get_full_status(bot: commands.Bot) -> dict:
    """Return comprehensive status combining live status + metrics for the dashboard."""
    status = await get_bot_status(bot)
    bot_metrics = await get_bot_metrics(bot)

    health_summary = await asyncio.to_thread(health.run_all)
    overall_ok = await asyncio.to_thread(health.overall_ok)

    snapshot = await asyncio.to_thread(metrics.snapshot)

    return {
        "status": status,
        "metrics": bot_metrics,
        "health": health_summary,
        "health_ok": overall_ok,
        "metrics_snapshot": snapshot,
        "collection_time": time.time(),
    }


async def reload_bot_config() -> dict:
    """Re-read config.json from disk and return the new config dict."""
    cfg = await asyncio.to_thread(load_json, CONFIG_PATH, None)
    if cfg is None:
        cfg = {
            "MAIN_ADMIN_ID": 1251119503492775956,
            "VPS_USER_ROLE_ID": 1431499643698544720,
            "DOCKER_IMAGE": "ubuntu:22.04",
            "LOG_CHANNEL_ID": None,
            "CPU_THRESHOLD": 90,
            "CHECK_INTERVAL": 60,
            "SSH_PORT_START": 10000,
        }
        await asyncio.to_thread(save_json, CONFIG_PATH, cfg)
        logger.warning("config.json was missing/invalid - regenerated with defaults")

    bus.emit("config_reloaded", config=cfg)
    logger.info("Configuration reloaded from disk")
    return cfg


async def clear_cache() -> int:
    """Walk the project tree and remove __pycache__ directories. Returns count cleared."""
    count = 0

    def _clear():
        nonlocal count
        for root, dirs, files in os.walk(BASE_DIR):
            if "__pycache__" in dirs:
                cache_path = os.path.join(root, "__pycache__")
                try:
                    shutil.rmtree(cache_path)
                    count += 1
                except Exception as e:
                    logger.error("Failed to remove %s: %s", cache_path, e)
        for root, dirs, files in os.walk(BASE_DIR):
            for f in files:
                if f.endswith(".pyc"):
                    try:
                        os.remove(os.path.join(root, f))
                    except Exception:
                        pass

    await asyncio.to_thread(_clear)
    bus.emit("cache_cleared", count=count)
    logger.info("Cache cleared - %d __pycache__ dirs removed", count)
    return count


async def get_system_info() -> dict:
    """Return static system/platform information."""
    hostname = ""
    pid = os.getpid()
    cwd = os.getcwd()
    py_version = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    def _hostname():
        nonlocal hostname
        try:
            hostname = platform.node()
        except Exception:
            hostname = "unknown"

    await asyncio.to_thread(_hostname)

    return {
        "platform": platform.platform(),
        "python_version": py_version,
        "pid": pid,
        "cwd": cwd,
        "hostname": hostname or "unknown",
        "os_name": platform.system(),
        "os_release": platform.release(),
        "architecture": platform.machine(),
        "psutil_available": _HAVE_PSUTIL,
    }


# ---------------------------------------------------------------------------
# Health checks - registered at module level with core.services.health
# ---------------------------------------------------------------------------

_bot_ref = None


def _check_bot_gateway() -> tuple:
    """Verify the Discord bot gateway connection is alive.

    Uses a module-level ``_bot_ref`` that is populated by
    :func:`set_bot_reference` once the bot instance exists.
    """
    if _bot_ref is None:
        return False, "Bot instance not set"
    try:
        if _bot_ref.is_ready():
            return True, "Gateway connected"
        return False, "Gateway not ready"
    except Exception as e:
        return False, str(e)


def _check_event_loop() -> tuple:
    """Verify the asyncio event loop is running."""
    try:
        loop = asyncio.get_running_loop()
        if loop.is_running():
            return True, "Event loop running"
        return False, "Event loop exists but not running"
    except RuntimeError:
        return False, "No running event loop"


def _check_disk_space() -> tuple:
    """Verify disk has more than 10% free space."""
    free_pct = 100.0 - _disk_free_percent()
    if free_pct > 10.0:
        return True, f"{free_pct:.1f}% free"
    return False, f"Low disk: {free_pct:.1f}% free (threshold: 10%)"


def _check_memory() -> tuple:
    """Verify memory has more than 20% free."""
    mem_pct = _mem_free_percent()
    free_pct = 100.0 - mem_pct
    if free_pct > 20.0:
        return True, f"{free_pct:.1f}% free"
    return False, f"Low memory: {free_pct:.1f}% free (threshold: 20%)"


# Register all checks at module level so they are available immediately.
health.register("bot_gateway", _check_bot_gateway, critical=True)
health.register("event_loop", _check_event_loop, critical=True)
health.register("disk_space", _check_disk_space, critical=False)
health.register("memory", _check_memory, critical=False)

logger.info("Health checks registered at module level (bot_gateway, event_loop, disk_space, memory)")


def set_bot_reference(bot: commands.Bot):
    """Store the bot instance so the gateway health check can inspect it.

    Call this once during bot startup (e.g. in on_ready).
    """
    global _bot_ref
    _bot_ref = bot
    logger.info("Bot reference set for health checks")
