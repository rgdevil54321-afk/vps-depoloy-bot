import asyncio
import time
import json
import os
import logging
import subprocess

from core.services import metrics, bus, load_json, DATA_DIR, BASE_DIR

logger = logging.getLogger("turtle.status_page")

STATUS_PATH = os.path.join(DATA_DIR, "status_page.json")
UPTIME_PATH = os.path.join(DATA_DIR, "uptime_history.json")

STATUS_EMOJI = {
    "operational": "\u2705",
    "degraded": "\U0001f7e1",
    "down": "\u274c",
    "unknown": "\u2753",
}

EMBED_COLORS = {
    "operational": 0x43B581,
    "partial_outage": 0xFAA61A,
    "major_outage": 0xF04747,
}


class StatusPage:
    def __init__(self, bot=None):
        self._bot = bot
        self._config = load_json(STATUS_PATH, {"services": [], "maintenance": None})
        self._uptime_history: list[dict] = load_json(UPTIME_PATH, [])
        self._last_check: dict[str, dict] = {}
        logger.info("StatusPage initialised")

    # ------------------------------------------------------------------
    # Overall status
    # ------------------------------------------------------------------
    def get_overall_status(self) -> dict:
        services = []
        for name in self._config.get("services", []):
            info = self.check_service(name)
            services.append(info)

        statuses = [s["status"] for s in services]
        if all(st == "operational" for st in statuses):
            overall = "operational"
        elif any(st == "down" for st in statuses):
            overall = "major_outage"
        else:
            overall = "partial_outage"

        if self._config.get("maintenance"):
            overall = "maintenance"

        return {"overall": overall, "services": services}

    # ------------------------------------------------------------------
    # Individual service check
    # ------------------------------------------------------------------
    def check_service(self, service_name: str) -> dict:
        handler = getattr(self, f"_check_{service_name}", None)
        if handler:
            result = handler()
        else:
            result = self._check_http(service_name)

        result.setdefault("name", service_name)
        result.setdefault("status", "unknown")
        result.setdefault("detail", "")
        self._last_check[service_name] = result
        return result

    # ---- Discord bot -------------------------------------------------
    def _check_discord(self) -> dict:
        if self._bot is None:
            return {"name": "discord", "status": "unknown", "detail": "Bot reference not set"}
        if self._bot.is_ready():
            latency_ms = round(self._bot.latency * 1000)
            status = "operational" if latency_ms < 300 else "degraded"
            return {
                "name": "discord",
                "status": status,
                "detail": f"Latency {latency_ms}ms",
            }
        return {"name": "discord", "status": "down", "detail": "Bot not ready"}

    # ---- API ---------------------------------------------------------
    def _check_api(self) -> dict:
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8000/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                if resp.status == 200:
                    return {"name": "api", "status": "operational", "detail": "HTTP 200"}
        except Exception as exc:
            return {"name": "api", "status": "down", "detail": str(exc)[:120]}
        return {"name": "api", "status": "down", "detail": "Unexpected response"}

    # ---- Database ----------------------------------------------------
    def _check_database(self) -> dict:
        try:
            for fname in ("servers.json", "users.json", "billing.json"):
                path = os.path.join(DATA_DIR, fname)
                if not os.path.isfile(path):
                    return {"name": "database", "status": "degraded", "detail": f"Missing {fname}"}
                load_json(path, None)
            return {"name": "database", "status": "operational", "detail": "All JSON stores readable"}
        except Exception as exc:
            return {"name": "database", "status": "down", "detail": str(exc)[:120]}

    # ---- Payments / Billing -----------------------------------------
    def _check_payments(self) -> dict:
        path = os.path.join(DATA_DIR, "billing.json")
        if not os.path.isfile(path):
            return {"name": "payments", "status": "degraded", "detail": "billing.json missing"}
        try:
            data = load_json(path, {})
            if isinstance(data, dict) and len(data) > 0:
                return {"name": "payments", "status": "operational", "detail": "Billing data present"}
            return {"name": "payments", "status": "degraded", "detail": "Billing data empty"}
        except Exception as exc:
            return {"name": "payments", "status": "down", "detail": str(exc)[:120]}

    # ---- Docker ------------------------------------------------------
    def _check_docker(self) -> dict:
        try:
            result = subprocess.run(
                ["docker", "ps", "--format", "{{.Names}}"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                containers = [c for c in result.stdout.strip().splitlines() if c]
                return {
                    "name": "docker",
                    "status": "operational",
                    "detail": f"{len(containers)} container(s) running",
                }
            return {"name": "docker", "status": "down", "detail": result.stderr[:120]}
        except FileNotFoundError:
            return {"name": "docker", "status": "down", "detail": "docker binary not found"}
        except Exception as exc:
            return {"name": "docker", "status": "down", "detail": str(exc)[:120]}

    # ---- Generic node ------------------------------------------------
    def _check_node(self, service_name: str) -> dict:
        node_id = service_name.replace("node_", "")
        node_cfg = load_json(os.path.join(DATA_DIR, "nodes.json"), {})
        node_info = node_cfg.get(node_id, {})
        url = node_info.get("stats_url")
        if not url:
            return {"name": service_name, "status": "unknown", "detail": "No stats URL configured"}
        try:
            import urllib.request
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                if resp.status == 200:
                    return {"name": service_name, "status": "operational", "detail": "Stats endpoint OK"}
                return {"name": service_name, "status": "degraded", "detail": f"HTTP {resp.status}"}
        except Exception as exc:
            return {"name": service_name, "status": "down", "detail": str(exc)[:120]}

    # ---- Fallback HTTP check ----------------------------------------
    def _check_http(self, service_name: str) -> dict:
        if service_name.startswith("node_"):
            return self._check_node(service_name)
        return {"name": service_name, "status": "unknown", "detail": "No check handler"}

    # ------------------------------------------------------------------
    # Embed builder
    # ------------------------------------------------------------------
    def get_status_embed(self):
        import discord

        status_data = self.get_overall_status()
        overall = status_data["overall"]
        services = status_data["services"]

        color = EMBED_COLORS.get(overall, EMBED_COLORS["major_outage"])
        embed = discord.Embed(color=color, timestamp=discord.utils.utcnow())

        if overall == "maintenance":
            maint = self._config.get("maintenance", {})
            embed.title = "\u2699\ufe0f SCHEDULED MAINTENANCE"
            embed.description = maint.get("message", "Maintenance in progress.")
        elif overall == "operational":
            embed.title = "\u2705 ALL SYSTEMS OPERATIONAL"
            embed.description = "All services are running normally."
        elif overall == "partial_outage":
            embed.title = "\U0001f7e1 PARTIAL OUTAGE"
            embed.description = "One or more services are experiencing issues."
        else:
            embed.title = "\u274c MAJOR OUTAGE"
            embed.description = "Critical services are currently down."

        for svc in services:
            emoji = STATUS_EMOJI.get(svc["status"], "\u2753")
            embed.add_field(
                name=f"{emoji} {svc['name'].replace('_', ' ').title()}",
                value=svc.get("detail", svc["status"]),
                inline=True,
            )

        uptime_pct = self.get_uptime_percent(hours=24)
        embed.set_footer(text=f"Uptime (24h): {uptime_pct:.2f}%")
        return embed

    # ------------------------------------------------------------------
    # Uptime tracking
    # ------------------------------------------------------------------
    def record_status_snapshot(self):
        status_data = self.get_overall_status()
        entry = {
            "timestamp": time.time(),
            "overall_status": status_data["overall"],
            "services": {
                s["name"]: s["status"] for s in status_data["services"]
            },
        }
        self._uptime_history.append(entry)

        cutoff = time.time() - (7 * 86400)
        self._uptime_history = [e for e in self._uptime_history if e["timestamp"] > cutoff]

        try:
            with open(UPTIME_PATH, "w", encoding="utf-8") as fh:
                json.dump(self._uptime_history, fh, indent=2)
        except OSError as exc:
            logger.error("Failed to write uptime history: %s", exc)

    def get_uptime_history(self, hours: int = 24) -> list[dict]:
        cutoff = time.time() - (hours * 3600)
        return [
            {"timestamp": e["timestamp"], "overall_status": e["overall_status"]}
            for e in self._uptime_history
            if e["timestamp"] > cutoff
        ]

    def get_uptime_percent(self, hours: int = 24) -> float:
        history = self.get_uptime_history(hours)
        if not history:
            return 100.0
        up_count = sum(1 for e in history if e["overall_status"] == "operational")
        return (up_count / len(history)) * 100.0

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------
    def format_status_for_embed(self, services: list[dict]) -> str:
        lines = []
        for svc in services:
            emoji = STATUS_EMOJI.get(svc.get("status", "unknown"), "\u2753")
            name = svc.get("name", "unknown").replace("_", " ").title()
            detail = svc.get("detail", "")
            lines.append(f"{emoji} **{name}** - {detail}" if detail else f"{emoji} **{name}**")
        return "\n".join(lines)
