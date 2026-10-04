"""Live Monitoring - real-time dashboards, alerting, and health checks for Turtle Nodes."""
import asyncio
import logging
import os
import time
from collections import deque
from datetime import datetime, timezone

import discord
from discord.ext import commands

from core.services import metrics, health, bus, load_json, save_json, DATA_DIR, BASE_DIR
from core.bot_core import get_bot_status, get_full_status
from core.infrastructure import get_host_stats, get_docker_status, get_service_status

logger = logging.getLogger("turtle.monitoring")


def _progress_bar(percent: float, length: int = 10) -> str:
    filled = round(percent / 100 * length)
    filled = max(0, min(length, filled))
    return f"{'█' * filled}{'░' * (length - filled)} {percent:.1f}%"


def _health_emoji(status: str) -> str:
    mapping = {
        "healthy": "\U0001f7e2",
        "ok": "\U0001f7e2",
        "online": "\U0001f7e2",
        "connected": "\U0001f7e2",
        "running": "\U0001f7e2",
        "warning": "\U0001f7e1",
        "degraded": "\U0001f7e1",
        "critical": "\U0001f534",
        "error": "\U0001f534",
        "offline": "\u26ab",
        "disconnected": "\u26ab",
        "unknown": "\u26ab",
    }
    return mapping.get(str(status).lower(), "\u26ab")


def _color_for_overall(status: str) -> int:
    mapping = {
        "healthy": 0x00FF88,
        "warning": 0xFFAA00,
        "critical": 0xFF3366,
        "offline": 0x2C2F33,
    }
    return mapping.get(str(status).lower(), 0x5865F2)


# ---------------------------------------------------------------------------
# Monitoring Dashboard
# ---------------------------------------------------------------------------

class MonitoringDashboard:
    """Renders and maintains the live monitoring embed in Discord."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def render(self) -> discord.Embed:
        full = await get_full_status(self.bot)
        st = full["status"]
        met = full["metrics"]
        svc = await get_service_status(self.bot)
        host = await get_host_stats()
        docker = await get_docker_status()

        discord_status = st.get("status_text", "unknown")
        gateway = st.get("gateway_status", "unknown")
        latency = st.get("latency_ms", 0)

        db_info = svc.get("database", {})
        docker_svc = svc.get("docker", {})
        api_info = svc.get("api", {})
        nodes_info = svc.get("nodes", {})

        cpu = host.get("cpu_percent", 0)
        ram = host.get("ram_percent", 0)
        disk = host.get("disk_percent", 0)

        overall = "healthy"
        if any(s.get("status") == "critical" for s in svc.values() if isinstance(s, dict)):
            overall = "critical"
        elif any(s.get("status") in ("warning", "offline") for s in svc.values() if isinstance(s, dict)):
            overall = "warning"
        if cpu > 90 or ram > 90 or disk > 90:
            overall = "critical"
        elif (cpu > 75 or ram > 75 or disk > 80) and overall == "healthy":
            overall = "warning"

        embed_color = _color_for_overall(overall)

        embed = discord.Embed(
            title="\u26a1 Turtle Nodes \u2014 Live Monitoring Dashboard",
            color=embed_color,
            timestamp=datetime.now(timezone.utc),
        )

        embed.set_footer(
            text="Turtle Nodes Control Panel \u2022 Auto-refreshes every 30s",
            icon_url="https://cdn-icons-png.flaticon.com/512/1827/1827422.png",
        )

        discord_emoji = _health_emoji(gateway)
        embed.add_field(
            name=f"{discord_emoji} Discord Gateway",
            value=(
                f"**Status:** {discord_emoji} `{discord_status.title()}`\n"
                f"**Gateway:** `{gateway.title()}`\n"
                f"**Latency:** `{latency}ms`"
            ),
            inline=True,
        )

        db_emoji = _health_emoji(db_info.get("status", "unknown"))
        embed.add_field(
            name=f"{db_emoji} Database",
            value=(
                f"**Status:** {db_emoji} `{db_info.get('status', 'unknown').title()}`\n"
                f"**Detail:** `{db_info.get('detail', 'N/A')}`"
            ),
            inline=True,
        )

        docker_emoji = _health_emoji(docker_svc.get("status", "unknown"))
        container_count = docker.get("container_count", 0)
        docker_running = docker.get("running", False)
        embed.add_field(
            name=f"{docker_emoji} Docker",
            value=(
                f"**Status:** {docker_emoji} `{'Running' if docker_running else 'Offline'}`\n"
                f"**Daemon:** `{docker_svc.get('detail', 'N/A')}`\n"
                f"**Containers:** `{container_count}`"
            ),
            inline=True,
        )

        cpu_bar = _progress_bar(cpu, 12)
        ram_used = host.get("ram_used_gb", 0)
        ram_total = host.get("ram_total_gb", 0)
        ram_bar = _progress_bar(ram, 12)
        disk_used = host.get("disk_used_gb", 0)
        disk_total = host.get("disk_total_gb", 0)
        disk_bar = _progress_bar(disk, 12)

        cpu_emoji = _health_emoji("healthy" if cpu < 75 else ("warning" if cpu < 90 else "critical"))
        ram_emoji = _health_emoji("healthy" if ram < 75 else ("warning" if ram < 90 else "critical"))
        disk_emoji = _health_emoji("healthy" if disk < 80 else ("warning" if disk < 90 else "critical"))

        host_value = (
            f"**CPU** {cpu_emoji} `{cpu_bar}`\n"
            f"\u2003\u2003 Cores: `{host.get('cpu_count', '?')}` | Load: `{host.get('load_1', 0)}` / `{host.get('load_5', 0)}` / `{host.get('load_15', 0)}`\n\n"
            f"**RAM** {ram_emoji} `{ram_bar}`\n"
            f"\u2003\u2003 Used: `{ram_used}GB` / `{ram_total}GB`\n\n"
            f"**Disk** {disk_emoji} `{disk_bar}`\n"
            f"\u2003\u2003 Used: `{disk_used}GB` / `{disk_total}GB`"
        )
        embed.add_field(name="\U0001f4bb Host Resources", value=host_value, inline=False)

        uptime = st.get("uptime", "N/A")
        guilds = st.get("guild_count", 0)
        users = st.get("user_count", 0)
        cmds_exec = met.get("commands_executed", 0)
        errors = met.get("errors_count", 0)
        vps_dep = met.get("vps_deployed", 0)
        vps_del = met.get("vps_deleted", 0)
        procs = host.get("process_count", 0)
        threads = st.get("thread_count", 0)

        embed.add_field(
            name="\U0001f4ca Statistics",
            value=(
                f"**Uptime:** `{uptime}`\n"
                f"**Guilds:** `{guilds}` | **Users:** `{users}`\n"
                f"**Commands Executed:** `{cmds_exec}`\n"
                f"**Errors:** `{errors}`"
            ),
            inline=True,
        )

        embed.add_field(
            name="\U0001f5a5\u200d\U0001f4bb Infrastructure",
            value=(
                f"**Processes:** `{procs}` | **Threads:** `{threads}`\n"
                f"**VPS Deployed:** `{vps_dep}` | **VPS Deleted:** `{vps_del}`\n"
                f"**API Server:** {_health_emoji(api_info.get('status', 'unknown'))} `{api_info.get('status', 'unknown').title()}`\n"
                f"**Nodes:** {_health_emoji(nodes_info.get('status', 'unknown'))} `{nodes_info.get('detail', 'N/A')}`"
            ),
            inline=True,
        )

        container_lines = []
        for c in docker.get("containers", [])[:5]:
            c_state = c.get("state", "unknown")
            c_emoji = _health_emoji("running" if c_state == "running" else c_state)
            container_lines.append(
                f"{c_emoji} `{c.get('name', '?')}` \u2014 "
                f"CPU `{c.get('cpu_percent', '0.00%')}` | "
                f"MEM `{c.get('mem_percent', '0.00%')}`"
            )
        if container_lines:
            embed.add_field(
                name="\U0001f4e6 Containers",
                value="\n".join(container_lines),
                inline=False,
            )

        embed.set_thumbnail(url="https://cdn-icons-png.flaticon.com/512/1827/1827422.png")

        return embed

    async def update(self, message: discord.Message) -> None:
        embed = await self.render()
        try:
            await message.edit(embed=embed)
        except discord.HTTPException as e:
            logger.error("Failed to update monitoring message: %s", e)


# ---------------------------------------------------------------------------
# Alert Manager
# ---------------------------------------------------------------------------

class AlertManager:
    """Manages threshold-based alerts and alert history."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.alert_channel_id: int | None = None
        self.alert_history: deque = deque(maxlen=100)

    def set_channel(self, channel_id: int) -> None:
        self.alert_channel_id = channel_id
        logger.info("Alert channel set to %s", channel_id)

    def get_history(self, n: int = 20) -> list[dict]:
        return list(self.alert_history)[:n]

    async def send_alert(self, level: str, title: str, description: str) -> bool:
        color_map = {
            "info": 0x00FF88,
            "warning": 0xFFAA00,
            "critical": 0xFF3366,
        }
        emoji_map = {
            "info": "\u2139\ufe0f",
            "warning": "\u26a0\ufe0f",
            "critical": "\U0001f6a8",
        }
        color = color_map.get(level.lower(), 0x5865F2)
        emoji = emoji_map.get(level.lower(), "\u2753")

        embed = discord.Embed(
            title=f"{emoji} {title}",
            description=description,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Turtle Nodes \u2022 {level.upper()} Alert")

        alert_record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "level": level,
            "title": title,
            "description": description,
        }
        self.alert_history.appendleft(alert_record)

        if self.alert_channel_id is None:
            logger.warning("Alert channel not set, alert stored but not sent: %s", title)
            return False

        channel = self.bot.get_channel(self.alert_channel_id)
        if channel is None:
            logger.error("Alert channel %s not found", self.alert_channel_id)
            return False

        try:
            await channel.send(embed=embed)
            return True
        except discord.Forbidden:
            logger.error("Missing permissions to send alert to channel %s", self.alert_channel_id)
            return False
        except discord.HTTPException as e:
            logger.error("Failed to send alert: %s", e)
            return False

    async def check_thresholds(self) -> list[dict]:
        host = await get_host_stats()
        triggered = []

        cpu = host.get("cpu_percent", 0)
        ram = host.get("ram_percent", 0)
        disk = host.get("disk_percent", 0)

        if cpu > 90:
            alert = {
                "metric": "CPU",
                "value": cpu,
                "threshold": 90,
                "level": "critical",
            }
            triggered.append(alert)
            await self.send_alert(
                "critical",
                "CPU Usage Critical",
                f"CPU usage has exceeded **90%**.\n\n**Current:** `{cpu:.1f}%`\n**Threshold:** `90%`\n\nImmediate action may be required.",
            )

        if ram > 90:
            ram_used = host.get("ram_used_gb", 0)
            ram_total = host.get("ram_total_gb", 0)
            alert = {
                "metric": "RAM",
                "value": ram,
                "threshold": 90,
                "level": "critical",
            }
            triggered.append(alert)
            await self.send_alert(
                "critical",
                "Memory Usage Critical",
                f"RAM usage has exceeded **90%**.\n\n**Current:** `{ram:.1f}%` (`{ram_used}GB` / `{ram_total}GB`)\n**Threshold:** `90%`\n\nServices may become unstable.",
            )

        if disk > 90:
            disk_used = host.get("disk_used_gb", 0)
            disk_total = host.get("disk_total_gb", 0)
            alert = {
                "metric": "Disk",
                "value": disk,
                "threshold": 90,
                "level": "critical",
            }
            triggered.append(alert)
            await self.send_alert(
                "critical",
                "Disk Usage Critical",
                f"Disk usage has exceeded **90%**.\n\n**Current:** `{disk:.1f}%` (`{disk_used}GB` / `{disk_total}GB`)\n**Threshold:** `90%`\n\nLogs and backups may fail. Free space urgently.",
            )

        for a in triggered:
            metrics.event("alert", "threshold", f"{a['metric']} at {a['value']:.1f}%")

        return triggered


# ---------------------------------------------------------------------------
# Health Monitor
# ---------------------------------------------------------------------------

class HealthMonitor:
    """Runs periodic health checks and maintains status."""

    def __init__(self):
        self.last_check: float = 0
        self.check_interval: int = 60
        self._last_results: dict = {}
        self._lock = asyncio.Lock()

    async def run_checks(self, bot: commands.Bot) -> dict:
        async with self._lock:
            results = await asyncio.to_thread(health.run_all)

            services = await get_service_status(bot)
            for svc_name, svc_data in services.items():
                results[f"service_{svc_name}"] = {
                    "ok": svc_data.get("status") in ("healthy", "online"),
                    "detail": svc_data.get("detail", ""),
                    "critical": svc_data.get("status") == "critical",
                    "status": svc_data.get("status", "unknown"),
                }

            host = await get_host_stats()
            cpu = host.get("cpu_percent", 0)
            ram = host.get("ram_percent", 0)
            disk = host.get("disk_percent", 0)

            results["host_cpu"] = {
                "ok": cpu < 90,
                "detail": f"{cpu:.1f}%",
                "critical": cpu > 90,
                "status": "healthy" if cpu < 75 else ("warning" if cpu < 90 else "critical"),
            }
            results["host_ram"] = {
                "ok": ram < 90,
                "detail": f"{ram:.1f}%",
                "critical": ram > 90,
                "status": "healthy" if ram < 75 else ("warning" if ram < 90 else "critical"),
            }
            results["host_disk"] = {
                "ok": disk < 90,
                "detail": f"{disk:.1f}%",
                "critical": disk > 90,
                "status": "healthy" if disk < 80 else ("warning" if disk < 90 else "critical"),
            }

            self._last_results = results
            self.last_check = time.time()

            snapshot = await asyncio.to_thread(metrics.snapshot)
            for name, res in results.items():
                metrics.set(f"health_{name}_ok", 1 if res.get("ok") else 0)

            bus.emit("health_check_complete", results=results)
            return results

    def get_status(self) -> dict:
        return self._last_results

    def is_healthy(self) -> bool:
        if not self._last_results:
            return False
        return all(
            r.get("ok") or not r.get("critical", False)
            for r in self._last_results.values()
        )


# ---------------------------------------------------------------------------
# Background Loops
# ---------------------------------------------------------------------------

async def _metrics_collector(bot: commands.Bot) -> None:
    """Collect host and docker metrics every 30 seconds."""
    while not bot.is_closed():
        try:
            host = await get_host_stats()
            docker = await get_docker_status()

            metrics.set("host_cpu_percent", host.get("cpu_percent", 0))
            metrics.set("host_ram_percent", host.get("ram_percent", 0))
            metrics.set("host_disk_percent", host.get("disk_percent", 0))
            metrics.set("host_ram_used_gb", host.get("ram_used_gb", 0))
            metrics.set("host_ram_total_gb", host.get("ram_total_gb", 0))
            metrics.set("host_disk_used_gb", host.get("disk_used_gb", 0))
            metrics.set("host_disk_total_gb", host.get("disk_total_gb", 0))
            metrics.set("host_load_1", host.get("load_1", 0))
            metrics.set("host_load_5", host.get("load_5", 0))
            metrics.set("host_load_15", host.get("load_15", 0))
            metrics.set("host_process_count", host.get("process_count", 0))

            metrics.set("docker_installed", 1 if docker.get("installed") else 0)
            metrics.set("docker_running", 1 if docker.get("running") else 0)
            metrics.set("docker_container_count", docker.get("container_count", 0))

            bus.emit("metrics_collected", ts=time.time())
        except Exception as e:
            logger.error("Metrics collector error: %s", e)
            metrics.inc("errors_count")

        await asyncio.sleep(30)


async def _health_checker(bot: commands.Bot) -> None:
    """Run health checks every 60 seconds."""
    monitor = HealthMonitor()
    while not bot.is_closed():
        try:
            results = await monitor.run_checks(bot)
            unhealthy = [n for n, r in results.items() if not r.get("ok") and r.get("critical")]
            if unhealthy:
                logger.warning("Unhealthy critical services: %s", ", ".join(unhealthy))
        except Exception as e:
            logger.error("Health checker error: %s", e)
            metrics.inc("errors_count")

        await asyncio.sleep(60)


async def _alert_checker(bot: commands.Bot) -> None:
    """Check alert thresholds every 120 seconds."""
    alert_mgr = AlertManager(bot)
    threshold_path = os.path.join(str(BASE_DIR), "config.json")

    while not bot.is_closed():
        try:
            config = await asyncio.to_thread(load_json, threshold_path, {})
            alert_channel = config.get("ALERT_CHANNEL_ID")
            if alert_channel:
                alert_mgr.set_channel(int(alert_channel))

            triggered = await alert_mgr.check_thresholds()
            if triggered:
                logger.info("Alert thresholds triggered: %d", len(triggered))
        except Exception as e:
            logger.error("Alert checker error: %s", e)
            metrics.inc("errors_count")

        await asyncio.sleep(120)


def start_monitoring_loops(bot: commands.Bot) -> list[asyncio.Task]:
    """Start all background monitoring loops and return the tasks."""
    tasks = [
        asyncio.create_task(_metrics_collector(bot), name="turtle-metrics-collector"),
        asyncio.create_task(_health_checker(bot), name="turtle-health-checker"),
        asyncio.create_task(_alert_checker(bot), name="turtle-alert-checker"),
    ]
    logger.info("Started %d monitoring background tasks", len(tasks))
    return tasks
