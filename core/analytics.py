"""Analytics Engine - aggregates data from JSON stores and metrics for reporting."""
import json
import os
import time
import logging
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from core.services import metrics, load_json, DATA_DIR

logger = logging.getLogger("turtle.analytics")

USER_DATA_PATH = os.path.join(DATA_DIR, "user_data.json")
VPS_DATA_PATH = os.path.join(DATA_DIR, "vps_data.json")
TXN_PATH = os.path.join(DATA_DIR, "transactions.json")
NODES_PATH = os.path.join(DATA_DIR, "nodes.json")


def _load_user_data() -> dict:
    return load_json(USER_DATA_PATH, {})


def _load_vps_data() -> dict:
    return load_json(VPS_DATA_PATH, {})


def _load_transactions() -> list:
    raw = load_json(TXN_PATH, [])
    return raw if isinstance(raw, list) else []


def _load_nodes() -> dict:
    return load_json(NODES_PATH, {})


def _parse_iso(s: str) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None


class AnalyticsEngine:
    """Reads raw JSON data files and metrics counters to produce analytics."""

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Overview
    # ------------------------------------------------------------------

    def get_overview(self, bot) -> dict:
        user_data = _load_user_data()
        vps_data = _load_vps_data()
        now = datetime.now(timezone.utc)

        all_vps: list[dict] = []
        for vlist in vps_data.values():
            if isinstance(vlist, list):
                all_vps.extend(vlist)

        total_users = len(user_data)
        total_vps = len(all_vps)

        running = sum(1 for v in all_vps if str(v.get("status", "")).lower() == "running")
        stopped = sum(1 for v in all_vps if str(v.get("status", "")).lower() in ("stopped", "offline", "halted"))

        expired = 0
        for v in all_vps:
            exp_raw = v.get("expires") or v.get("expiry") or v.get("expires_at", "")
            exp_dt = _parse_iso(str(exp_raw)) if exp_raw else None
            if exp_dt and exp_dt < now:
                expired += 1

        total_credits = 0.0
        for uid, uinfo in user_data.items():
            if isinstance(uinfo, dict):
                total_credits += float(uinfo.get("credits", 0) or 0)

        counters = metrics.all_counters()
        total_commands = counters.get("commands_executed", 0)
        total_errors = counters.get("errors_count", 0)

        start_ts = metrics._start if hasattr(metrics, "_start") else time.time()
        uptime_secs = time.time() - start_ts

        return {
            "total_users": total_users,
            "total_vps": total_vps,
            "running_vps": running,
            "stopped_vps": stopped,
            "expired_vps": expired,
            "total_credits": round(total_credits, 2),
            "total_commands_executed": total_commands,
            "total_errors": total_errors,
            "bot_uptime": _format_uptime(uptime_secs),
            "bot_uptime_seconds": round(uptime_secs, 1),
        }

    # ------------------------------------------------------------------
    # VPS stats
    # ------------------------------------------------------------------

    def get_vps_stats(self, bot) -> dict:
        vps_data = _load_vps_data()
        now = datetime.now(timezone.utc)

        all_vps: list[dict] = []
        for vlist in vps_data.values():
            if isinstance(vlist, list):
                all_vps.extend(vlist)

        by_plan = Counter()
        by_os = Counter()
        by_processor = Counter()
        by_node = Counter()
        ages: list[float] = []
        expired_count = 0

        for v in all_vps:
            plan = v.get("plan") or v.get("plan_name") or v.get("plan_id") or "unknown"
            by_plan[str(plan)] += 1

            os_name = v.get("image") or v.get("os") or v.get("os_image") or "unknown"
            by_os[str(os_name)] += 1

            proc = v.get("processor") or v.get("cpu") or v.get("cpu_count") or "unknown"
            by_processor[str(proc)] += 1

            node = v.get("node") or v.get("node_id") or v.get("hostname") or "unknown"
            by_node[str(node)] += 1

            created_raw = v.get("created_at") or v.get("created") or v.get("deployed_at", "")
            created_dt = _parse_iso(str(created_raw)) if created_raw else None
            if created_dt:
                age_days = (now - created_dt).total_seconds() / 86400.0
                ages.append(age_days)

            exp_raw = v.get("expires") or v.get("expiry") or v.get("expires_at", "")
            exp_dt = _parse_iso(str(exp_raw)) if exp_raw else None
            if exp_dt and exp_dt < now:
                expired_count += 1

        avg_age = round(sum(ages) / len(ages), 1) if ages else 0

        return {
            "by_plan": dict(by_plan),
            "by_os": dict(by_os),
            "by_processor": dict(by_processor),
            "by_node": dict(by_node),
            "avg_age_days": avg_age,
            "expired_count": expired_count,
            "total_count": len(all_vps),
        }

    # ------------------------------------------------------------------
    # Revenue stats
    # ------------------------------------------------------------------

    def get_revenue_stats(self, bot) -> dict:
        txns = _load_transactions()

        total_spent = 0.0
        for tx in txns:
            if not isinstance(tx, dict):
                continue
            tx_type = str(tx.get("type", "")).lower()
            if tx_type in ("purchase", "payment", "topup", "credit_purchase"):
                total_spent += float(tx.get("amount", 0) or 0)

        counters = metrics.all_counters()
        total_earned = counters.get("credits_earned", 0)

        sorted_txns = sorted(
            txns,
            key=lambda t: t.get("timestamp") or t.get("created_at") or t.get("date") or "",
            reverse=True,
        )
        recent = sorted_txns[:20]

        return {
            "total_spent": round(total_spent, 2),
            "total_earned": total_earned,
            "transaction_count": len(txns),
            "recent_transactions": recent,
        }

    # ------------------------------------------------------------------
    # Growth data
    # ------------------------------------------------------------------

    def get_growth_data(self, bot) -> dict:
        user_data = _load_user_data()
        vps_data = _load_vps_data()
        now = datetime.now(timezone.utc)
        cutoff = now - timedelta(days=30)

        users_per_day: Counter = Counter()
        for uid, uinfo in user_data.items():
            if not isinstance(uinfo, dict):
                continue
            join_raw = uinfo.get("joined_at") or uinfo.get("created_at") or uinfo.get("join_date", "")
            join_dt = _parse_iso(str(join_raw)) if join_raw else None
            if join_dt and join_dt >= cutoff:
                day_key = join_dt.strftime("%Y-%m-%d")
                users_per_day[day_key] += 1

        vps_per_day: Counter = Counter()
        all_vps: list[dict] = []
        for vlist in vps_data.values():
            if isinstance(vlist, list):
                all_vps.extend(vlist)

        for v in all_vps:
            created_raw = v.get("created_at") or v.get("created") or v.get("deployed_at", "")
            created_dt = _parse_iso(str(created_raw)) if created_raw else None
            if created_dt and created_dt >= cutoff:
                day_key = created_dt.strftime("%Y-%m-%d")
                vps_per_day[day_key] += 1

        result_users = {}
        result_vps = {}
        for i in range(30):
            day = (cutoff + timedelta(days=i + 1)).strftime("%Y-%m-%d")
            result_users[day] = users_per_day.get(day, 0)
            result_vps[day] = vps_per_day.get(day, 0)

        return {
            "users_joined_per_day": result_users,
            "vps_created_per_day": result_vps,
            "total_users_30d": sum(result_users.values()),
            "total_vps_30d": sum(result_vps.values()),
        }

    # ------------------------------------------------------------------
    # Command stats
    # ------------------------------------------------------------------

    def get_command_stats(self) -> dict:
        counters = metrics.all_counters()
        timers = metrics.all_timers()

        cmd_counts: dict[str, int] = {}
        for key, val in counters.items():
            if key.startswith("cmd_"):
                cmd_name = key[4:]
                cmd_counts[cmd_name] = val
            elif key == "commands_executed":
                cmd_counts["_total"] = val

        sorted_cmds = sorted(
            cmd_counts.items(),
            key=lambda kv: kv[1],
            reverse=True,
        )
        top = [
            {"command": k, "count": v}
            for k, v in sorted_cmds
            if k != "_total"
        ][:15]

        error_counts: dict[str, int] = {}
        for key, val in counters.items():
            if key.startswith("error_"):
                err_name = key[6:]
                error_counts[err_name] = val

        return {
            "total_commands": cmd_counts.get("_total", 0),
            "top_commands": top,
            "error_counts": error_counts,
            "unique_commands_tracked": len([k for k in cmd_counts if k != "_total"]),
            "command_timers": {
                k: v for k, v in timers.items()
                if k.startswith("cmd_")
            },
        }

    # ------------------------------------------------------------------
    # Resource usage
    # ------------------------------------------------------------------

    def get_resource_usage(self, bot) -> dict:
        vps_data = _load_vps_data()

        total_ram = 0.0
        total_cpu = 0
        total_disk = 0.0
        total_bandwidth = 0.0

        for vlist in vps_data.values():
            if not isinstance(vlist, list):
                continue
            for v in vlist:
                if not isinstance(v, dict):
                    continue

                ram = v.get("ram") or v.get("memory") or v.get("memory_mb") or 0
                try:
                    total_ram += float(ram)
                except (ValueError, TypeError):
                    pass

                cpu = v.get("cpu") or v.get("cpu_count") or v.get("vcpu") or 0
                try:
                    total_cpu += int(float(cpu))
                except (ValueError, TypeError):
                    pass

                disk = v.get("disk") or v.get("disk_gb") or v.get("storage") or 0
                try:
                    total_disk += float(disk)
                except (ValueError, TypeError):
                    pass

                bw = v.get("bandwidth") or v.get("bandwidth_gb") or 0
                try:
                    total_bandwidth += float(bw)
                except (ValueError, TypeError):
                    pass

        return {
            "total_ram_allocated": round(total_ram, 1),
            "total_ram_allocated_display": self.format_bytes(total_ram * 1024 * 1024),
            "total_cpu_allocated": total_cpu,
            "total_disk_allocated": round(total_disk, 1),
            "total_disk_allocated_display": self.format_bytes(total_disk * 1024 * 1024 * 1024),
            "total_bandwidth_allocated": round(total_bandwidth, 1),
        }

    # ------------------------------------------------------------------
    # Formatting helpers
    # ------------------------------------------------------------------

    @staticmethod
    def format_number(n) -> str:
        try:
            return f"{int(n):,}"
        except (ValueError, TypeError):
            return str(n)

    @staticmethod
    def format_bytes(b) -> str:
        try:
            b = float(b)
        except (ValueError, TypeError):
            return "0 B"

        if b < 1024:
            return f"{b:.0f} B"
        elif b < 1024 ** 2:
            return f"{b / 1024:.1f} KB"
        elif b < 1024 ** 3:
            return f"{b / (1024 ** 2):.1f} MB"
        elif b < 1024 ** 4:
            return f"{b / (1024 ** 3):.2f} GB"
        else:
            return f"{b / (1024 ** 4):.2f} TB"


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
