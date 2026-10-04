"""Advanced Maintenance Mode - scoping, scheduling, persistence, history, announcements."""
import os
import asyncio
import logging
import time
import json
import uuid
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.maintenance.adv")

MAINT_STATE_PATH = os.path.join(DATA_DIR, "maintenance_state.json")
MAINT_HISTORY_PATH = os.path.join(DATA_DIR, "maintenance_history.json")
MAINT_SCHEDULE_PATH = os.path.join(DATA_DIR, "maintenance_schedule.json")

MAINTAINABLE_SCOPES = {
    "bot": "Entire Bot",
    "user_commands": "User Commands",
    "vps_deploy": "VPS Deployment",
    "vps_management": "VPS Management",
    "purchases": "Purchases",
    "billing": "Billing",
    "backups": "Backups",
    "terminal": "Terminal",
    "api": "API Operations",
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(ts_str):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


class AdvancedMaintenance:
    """Extended maintenance system with scoped control, scheduling, and persistence."""

    def __init__(self, bot):
        self.bot = bot
        self.state = self.load_state()
        logger.info("AdvancedMaintenance initialised - active=%s, scopes=%s",
                     self.state["active"], self.state["scopes_disabled"])

    # ── persistence ────────────────────────────────────────────────────

    def load_state(self):
        data = load_json(MAINT_STATE_PATH, None)
        if data and isinstance(data, dict):
            defaults = self._default_state()
            defaults.update(data)
            return defaults
        return self._default_state()

    def save_state(self):
        save_json(MAINT_STATE_PATH, self.state)

    @staticmethod
    def _default_state():
        return {
            "active": False,
            "reason": "",
            "message": "",
            "started_by": "",
            "started_at": "",
            "estimated_end": "",
            "actual_end": "",
            "scopes_disabled": [],
            "is_scheduled": False,
            "announcement_channel_id": None,
        }

    # ── state accessors ────────────────────────────────────────────────

    def is_active(self):
        return bool(self.state.get("active", False))

    def is_scope_disabled(self, scope):
        return scope in self.state.get("scopes_disabled", [])

    def get_state(self):
        return dict(self.state)

    def get_scopes_status(self):
        disabled = set(self.state.get("scopes_disabled", []))
        return {name: name in disabled for name in MAINTAINABLE_SCOPES}

    # ── enable / disable ───────────────────────────────────────────────

    def enable(self, reason, message, admin_id, scopes=None,
               estimated_minutes=None, announcement_channel=None):
        if self.is_active():
            return {"success": False, "message": "Maintenance mode is already active."}

        if scopes:
            for s in scopes:
                if s not in MAINTAINABLE_SCOPES:
                    return {"success": False, "message": f"Unknown scope: {s}"}

        now = _now_iso()
        est_end = ""
        if estimated_minutes:
            from datetime import timedelta
            est_dt = datetime.now(timezone.utc) + timedelta(minutes=estimated_minutes)
            est_end = est_dt.isoformat()

        self.state.update({
            "active": True,
            "reason": reason or "",
            "message": message or "",
            "started_by": str(admin_id),
            "started_at": now,
            "estimated_end": est_end,
            "actual_end": "",
            "scopes_disabled": list(scopes) if scopes else list(MAINTAINABLE_SCOPES.keys()),
            "is_scheduled": False,
            "announcement_channel_id": announcement_channel,
        })
        self.save_state()

        self.record_history({
            "action": "enable",
            "reason": reason,
            "message": message,
            "admin_id": str(admin_id),
            "scopes": self.state["scopes_disabled"],
            "started_at": now,
            "estimated_end": est_end,
        })

        bus.emit("maintenance_started",
                 reason=reason, admin_id=str(admin_id),
                 scopes=self.state["scopes_disabled"])
        metrics.counter("maintenance.adv.enabled")
        metrics.set("maintenance.adv.active", 1)

        logger.warning("Advanced maintenance enabled by %s: %s (scopes=%s)",
                       admin_id, reason, self.state["scopes_disabled"])

        return {
            "success": True,
            "message": f"Maintenance mode enabled: {reason}",
            "scopes": self.state["scopes_disabled"],
        }

    def disable(self, admin_id):
        if not self.is_active():
            return {"success": False, "message": "Maintenance mode is not active."}

        now = _now_iso()
        started_at = self.state.get("started_at", "")
        duration_str = self._calc_duration(started_at, now)

        self.record_history({
            "action": "disable",
            "admin_id": str(admin_id),
            "started_at": started_at,
            "ended_at": now,
            "duration": duration_str,
            "reason": self.state.get("reason", ""),
        })

        self.state.update({
            "active": False,
            "actual_end": now,
            "scopes_disabled": [],
        })
        self.save_state()

        bus.emit("maintenance_disabled", admin_id=str(admin_id))
        metrics.counter("maintenance.adv.disabled")
        metrics.set("maintenance.adv.active", 0)

        if self.state.get("announcement_channel_id"):
            asyncio.ensure_future(self.send_announcement(
                self.state["announcement_channel_id"],
                self._completion_embed(duration_str),
            ))

        logger.info("Advanced maintenance disabled by %s (duration=%s)", admin_id, duration_str)
        self.state["announcement_channel_id"] = None
        self.save_state()

        return {"success": True, "duration_str": duration_str}

    def _calc_duration(self, start_iso, end_iso):
        start = _parse_iso(start_iso)
        end = _parse_iso(end_iso)
        if not start or not end:
            return "unknown"
        delta = end - start
        total_secs = int(delta.total_seconds())
        hours, remainder = divmod(total_secs, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours}h {minutes}m {seconds}s"
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    # ── scope management ───────────────────────────────────────────────

    def set_scope(self, scope, disabled, admin_id):
        if scope not in MAINTAINABLE_SCOPES:
            return {"success": False, "message": f"Unknown scope: {scope}"}

        scopes = self.state.get("scopes_disabled", [])
        if disabled and scope not in scopes:
            scopes.append(scope)
        elif not disabled and scope in scopes:
            scopes.remove(scope)

        self.state["scopes_disabled"] = scopes
        self.save_state()

        bus.emit("maintenance.scope_changed",
                 scope=scope, disabled=disabled, admin_id=str(admin_id))
        metrics.counter("maintenance.adv.scope_changed")
        logger.info("Scope %s set to disabled=%s by %s", scope, disabled, admin_id)
        return {"success": True}

    # ── embed helpers ──────────────────────────────────────────────────

    def get_maintenance_embed_fields(self):
        if not self.is_active():
            return [{"name": "\U0001f7e2 Status", "value": "All systems operational", "inline": False}]

        started = self.state.get("started_at", "")
        estimated_end = self.state.get("estimated_end", "")
        elapsed = self._calc_duration(started, _now_iso()) if started else "unknown"

        scopes = self.state.get("scopes_disabled", [])
        scope_labels = [MAINTAINABLE_SCOPES.get(s, s) for s in scopes]
        scopes_text = ", ".join(scope_labels) if scope_labels else "None"

        est_remaining = ""
        if estimated_end:
            est_dt = _parse_iso(estimated_end)
            now = datetime.now(timezone.utc)
            if est_dt and est_dt > now:
                diff = est_dt - now
                secs = int(diff.total_seconds())
                h, rem = divmod(secs, 3600)
                m, s = divmod(rem, 60)
                est_remaining = f"{h}h {m}m" if h else f"{m}m {s}s"
            else:
                est_remaining = "Overdue"

        fields = [
            {"name": "\U0001f534 Status", "value": "MAINTENANCE MODE ACTIVE", "inline": False},
            {"name": "Reason", "value": self.state.get("reason") or "N/A", "inline": True},
            {"name": "Message", "value": self.state.get("message") or "N/A", "inline": True},
            {"name": "Started By", "value": str(self.state.get("started_by", "Unknown")), "inline": True},
            {"name": "Elapsed", "value": elapsed, "inline": True},
            {"name": "Scopes Disabled", "value": scopes_text, "inline": False},
        ]

        if est_remaining:
            fields.append({"name": "Est. Remaining", "value": est_remaining, "inline": True})

        return fields

    def _completion_embed(self, duration_str):
        return {
            "title": "Maintenance Complete",
            "description": (
                f"**Reason:** {self.state.get('reason') or 'N/A'}\n"
                f"**Duration:** {duration_str}\n\n"
                "All systems are back online."
            ),
            "color": 0x2ECC71,
        }

    # ── scheduling ─────────────────────────────────────────────────────

    def schedule(self, start_time_iso, end_time_iso, reason, message,
                 admin_id, scopes=None):
        schedules = load_json(MAINT_SCHEDULE_PATH, [])
        if not isinstance(schedules, list):
            schedules = []

        if scopes:
            for s in scopes:
                if s not in MAINTAINABLE_SCOPES:
                    return {"success": False, "message": f"Unknown scope: {s}"}

        start_dt = _parse_iso(start_time_iso)
        end_dt = _parse_iso(end_time_iso)
        if not start_dt or not end_dt:
            return {"success": False, "message": "Invalid time format. Use ISO 8601."}
        if end_dt <= start_dt:
            return {"success": False, "message": "End time must be after start time."}

        schedule_id = str(uuid.uuid4())[:8]
        entry = {
            "id": schedule_id,
            "start_time": start_time_iso,
            "end_time": end_time_iso,
            "reason": reason,
            "message": message,
            "admin_id": str(admin_id),
            "scopes": list(scopes) if scopes else list(MAINTAINABLE_SCOPES.keys()),
            "created_at": _now_iso(),
            "activated": False,
        }
        schedules.append(entry)
        save_json(MAINT_SCHEDULE_PATH, schedules)

        bus.emit("maintenance.scheduled", schedule_id=schedule_id, start=start_time_iso)
        metrics.counter("maintenance.adv.scheduled")
        logger.info("Maintenance scheduled: %s starting at %s", schedule_id, start_time_iso)

        return {"success": True, "schedule_id": schedule_id}

    def cancel_schedule(self, schedule_id):
        schedules = load_json(MAINT_SCHEDULE_PATH, [])
        if not isinstance(schedules, list):
            return {"success": False, "message": "No schedules found."}

        new_schedules = [s for s in schedules if s.get("id") != schedule_id]
        if len(new_schedules) == len(schedules):
            return {"success": False, "message": f"Schedule {schedule_id} not found."}

        save_json(MAINT_SCHEDULE_PATH, new_schedules)
        bus.emit("maintenance.schedule_cancelled", schedule_id=schedule_id)
        metrics.counter("maintenance.adv.schedule_cancelled")
        logger.info("Schedule %s cancelled", schedule_id)
        return {"success": True, "message": f"Schedule {schedule_id} cancelled."}

    def get_schedules(self):
        schedules = load_json(MAINT_SCHEDULE_PATH, [])
        if not isinstance(schedules, list):
            return []
        now = datetime.now(timezone.utc)
        upcoming = []
        for s in schedules:
            end_dt = _parse_iso(s.get("end_time", ""))
            if end_dt and end_dt < now and s.get("activated"):
                continue
            upcoming.append(s)
        return upcoming

    def check_schedules(self):
        schedules = load_json(MAINT_SCHEDULE_PATH, [])
        if not isinstance(schedules, list):
            return

        now = datetime.now(timezone.utc)
        modified = False

        for entry in schedules:
            if entry.get("activated"):
                end_dt = _parse_iso(entry.get("end_time", ""))
                if end_dt and now >= end_dt and self.is_active():
                    logger.info("Scheduled maintenance %s end time reached - disabling", entry["id"])
                    self.disable("scheduler_auto")
                    entry["completed"] = True
                    modified = True
            else:
                start_dt = _parse_iso(entry.get("start_time", ""))
                if start_dt and now >= start_dt:
                    logger.info("Scheduled maintenance %s start time reached - activating", entry["id"])
                    result = self.enable(
                        reason=entry.get("reason", "Scheduled maintenance"),
                        message=entry.get("message", ""),
                        admin_id=entry.get("admin_id", "scheduler"),
                        scopes=entry.get("scopes"),
                    )
                    if result.get("success"):
                        entry["activated"] = True
                        modified = True

        if modified:
            save_json(MAINT_SCHEDULE_PATH, schedules)

    # ── auto-end check ─────────────────────────────────────────────────

    def auto_end_check(self):
        if not self.is_active():
            return

        est_end = self.state.get("estimated_end", "")
        if not est_end:
            return

        est_dt = _parse_iso(est_end)
        now = datetime.now(timezone.utc)
        if est_dt and now >= est_dt:
            logger.warning("Maintenance estimated_end reached - auto-disabling")
            result = self.disable("auto_end")
            if result.get("success") and self.state.get("announcement_channel_id"):
                asyncio.ensure_future(self.send_announcement(
                    self.state["announcement_channel_id"],
                    self._completion_embed(result.get("duration_str", "unknown")),
                ))
            metrics.counter("maintenance.adv.auto_end")

    # ── history ────────────────────────────────────────────────────────

    def get_history(self, n=20):
        history = load_json(MAINT_HISTORY_PATH, [])
        if not isinstance(history, list):
            return []
        return list(reversed(history[-n:]))

    def record_history(self, entry):
        history = load_json(MAINT_HISTORY_PATH, [])
        if not isinstance(history, list):
            history = []

        entry["timestamp"] = _now_iso()
        history.append(entry)
        if len(history) > 100:
            history = history[-100:]
        save_json(MAINT_HISTORY_PATH, history)

    # ── announcements ──────────────────────────────────────────────────

    async def send_announcement(self, channel_id, embed_dict):
        if not channel_id or not self.bot:
            return
        try:
            channel = self.bot.get_channel(int(channel_id))
            if not channel:
                channel = await self.bot.fetch_channel(int(channel_id))
            if channel:
                from discord import Embed
                embed = Embed.from_dict(embed_dict)
                await channel.send(embed=embed)
                metrics.counter("maintenance.adv.announcement_sent")
                logger.info("Announcement sent to channel %s", channel_id)
        except Exception as e:
            logger.error("Failed to send maintenance announcement to %s: %s", channel_id, e)
            metrics.counter("maintenance.adv.announcement_failed")
