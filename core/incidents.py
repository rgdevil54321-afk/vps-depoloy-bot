"""Incident System for Turtle Nodes Discord hosting bot.

Tracks incidents detected during troubleshooting, records actions taken,
and stores a queryable history for dashboards and notifications.
"""
import os
import logging
import time
import json
import uuid
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.incidents")

INCIDENTS_PATH = os.path.join(DATA_DIR, "incidents.json")

VALID_SEVERITIES = ("info", "warning", "critical")
VALID_STATUSES = ("open", "investigating", "recovered", "failed")
ARCHIVE_STATUSES = ("recovered", "failed")


class Incident:
    """Single incident record."""

    def __init__(self, service: str, severity: str, problem: str):
        self.id: str = uuid.uuid4().hex[:10]
        self.service: str = service
        self.severity: str = severity if severity in VALID_SEVERITIES else "info"
        self.problem: str = problem
        self.status: str = "open"
        self.created_at: float = time.time()
        self.closed_at: float | None = None
        self.duration_seconds: float | None = None
        self.actions: list[dict] = []

    # -- Actions -------------------------------------------------------------

    def add_action(self, action: str, result: str):
        self.actions.append({
            "timestamp": time.time(),
            "action": action,
            "result": result,
        })

    def close(self, status: str = "recovered"):
        if status not in VALID_STATUSES:
            status = "failed"
        self.status = status
        self.closed_at = time.time()
        self.duration_seconds = round(self.closed_at - self.created_at, 2)

    # -- Serialisation -------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "service": self.service,
            "severity": self.severity,
            "problem": self.problem,
            "status": self.status,
            "created_at": self.created_at,
            "closed_at": self.closed_at,
            "duration_seconds": self.duration_seconds,
            "actions": list(self.actions),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Incident":
        inc = cls(d.get("service", "unknown"), d.get("severity", "info"), d.get("problem", ""))
        inc.id = d.get("id", inc.id)
        inc.status = d.get("status", "open")
        inc.created_at = d.get("created_at", inc.created_at)
        inc.closed_at = d.get("closed_at")
        inc.duration_seconds = d.get("duration_seconds")
        inc.actions = d.get("actions", [])
        return inc

    # -- Formatting helpers --------------------------------------------------

    def get_duration_str(self) -> str:
        if self.duration_seconds is None:
            secs = round(time.time() - self.created_at)
        else:
            secs = int(self.duration_seconds)
        if secs < 60:
            return f"{secs} seconds"
        minutes = secs // 60
        remainder = secs % 60
        if remainder == 0:
            return f"{minutes} minutes" if minutes != 1 else "1 minute"
        return f"{minutes} minutes {remainder} seconds"

    def get_actions_text(self) -> str:
        if not self.actions:
            return "No actions recorded"
        lines = []
        for entry in self.actions:
            action = entry.get("action", "Unknown")
            result = entry.get("result", "")
            lines.append(f"\u2713 {action}" + (f" - {result}" if result else ""))
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Manager
# ---------------------------------------------------------------------------

class IncidentManager:
    """Manages the lifecycle of all incidents."""

    def __init__(self):
        self.incidents: list[Incident] = []
        self._load()

    # -- CRUD ----------------------------------------------------------------

    def create_incident(self, service: str, severity: str, problem: str) -> Incident:
        inc = Incident(service, severity, problem)
        inc.status = "open"
        self.incidents.append(inc)
        self._save()
        metrics.inc("incidents_created")
        metrics.inc(f"incidents_severity_{severity}")
        logger.warning("Incident %s created: [%s] %s on %s", inc.id, severity, problem, service)
        bus.emit("incident_created", incident=inc.to_dict())
        return inc

    def update_incident(self, incident_id: str, action: str, result: str):
        inc = self.get_incident(incident_id)
        if inc is None:
            logger.warning("update_incident: unknown id %s", incident_id)
            return
        inc.add_action(action, result)
        if inc.status == "open":
            inc.status = "investigating"
        self._save()
        metrics.inc("incidents_actions")
        logger.info("Incident %s action: %s -> %s", incident_id, action, result)

    def close_incident(self, incident_id: str, status: str = "recovered"):
        inc = self.get_incident(incident_id)
        if inc is None:
            logger.warning("close_incident: unknown id %s", incident_id)
            return
        inc.close(status)
        self._save()
        metrics.inc(f"incidents_closed_{status}")
        logger.info("Incident %s closed as %s (duration: %s)", incident_id, status, inc.get_duration_str())
        bus.emit("incident_closed", incident=inc.to_dict())

    # -- Queries -------------------------------------------------------------

    def get_recent(self, n: int = 20) -> list[dict]:
        sorted_incidents = sorted(self.incidents, key=lambda i: i.created_at, reverse=True)
        return [inc.to_dict() for inc in sorted_incidents[:n]]

    def get_open_incidents(self) -> list[Incident]:
        return [inc for inc in self.incidents if inc.status in ("open", "investigating")]

    def get_incident(self, incident_id: str) -> Incident | None:
        for inc in self.incidents:
            if inc.id == incident_id:
                return inc
        return None

    def get_stats(self) -> dict:
        stats = {
            "total": len(self.incidents),
            "open": 0,
            "recovered": 0,
            "failed": 0,
            "by_severity": {},
            "by_service": {},
        }
        for inc in self.incidents:
            if inc.status in ("open", "investigating"):
                stats["open"] += 1
            elif inc.status == "recovered":
                stats["recovered"] += 1
            elif inc.status == "failed":
                stats["failed"] += 1
            stats["by_severity"][inc.severity] = stats["by_severity"].get(inc.severity, 0) + 1
            stats["by_service"][inc.service] = stats["by_service"].get(inc.service, 0) + 1
        return stats

    # -- Maintenance ---------------------------------------------------------

    def cleanup_old(self, days: int = 30):
        cutoff = time.time() - days * 86400
        before = len(self.incidents)
        self.incidents = [
            inc for inc in self.incidents
            if inc.created_at > cutoff or inc.status not in ARCHIVE_STATUSES
        ]
        removed = before - len(self.incidents)
        if removed:
            self._save()
            metrics.inc("incidents_cleaned", removed)
            logger.info("Cleaned up %d incidents older than %d days", removed, days)

    # -- Persistence ---------------------------------------------------------

    def _save(self):
        data = [inc.to_dict() for inc in self.incidents]
        save_json(INCIDENTS_PATH, data)

    def _load(self):
        raw = load_json(INCIDENTS_PATH, [])
        self.incidents = [Incident.from_dict(d) for d in raw if isinstance(d, dict)]
        logger.info("Loaded %d incidents", len(self.incidents))

    # -- Integration helper --------------------------------------------------

    async def record_troubleshoot_event(self, troubleshooter_result: dict, recovery_result: dict) -> Incident:
        """Create and fully record an incident from troubleshooter outputs.

        Parameters
        ----------
        troubleshooter_result : dict
            Expected keys: service, severity, problem, checks (list of {name, ok, detail}).
        recovery_result : dict
            Expected keys: success (bool), actions (list of {action, result}).

        Returns
        -------
        Incident
            The created (or updated) incident record.
        """
        service = troubleshooter_result.get("service", "unknown")
        severity = troubleshooter_result.get("severity", "warning")
        problem = troubleshooter_result.get("problem", "Unspecified issue")

        if severity != "critical":
            inc = self.create_incident(service, severity, problem)
            inc.add_action("Troubleshoot run", f"Severity {severity} - no incident escalation")
            self._save()
            return inc

        inc = self.create_incident(service, severity, problem)

        # Record diagnostic checks
        checks = troubleshooter_result.get("checks", [])
        for check in checks:
            name = check.get("name", "unknown check")
            ok = check.get("ok", False)
            detail = check.get("detail", "")
            status_str = "passed" if ok else "FAILED"
            inc.add_action(f"Diagnostic: {name}", f"{status_str} - {detail}")

        # Record recovery attempts
        recovery_actions = recovery_result.get("actions", [])
        for ra in recovery_actions:
            action_text = ra.get("action", "recovery step")
            result_text = ra.get("result", "no result")
            inc.add_action(action_text, result_text)

        # Close based on recovery outcome
        success = recovery_result.get("success", False)
        if success:
            inc.close("recovered")
        else:
            inc.close("failed")

        self._save()
        metrics.inc("troubleshoot_events_recorded")
        bus.emit("troubleshoot_recorded", incident=inc.to_dict())
        return inc
