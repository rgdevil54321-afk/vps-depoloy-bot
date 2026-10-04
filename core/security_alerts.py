"""Security Alerts system.

Sends alerts for critical admin actions, repeated failures, suspicious activity,
emergency lockdown, node isolation, dangerous purge attempts, backup operations,
session anomalies, and infrastructure failures.
"""
import os
import time
import logging
import threading
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.sec_alerts")

ALERTS_PATH = os.path.join(DATA_DIR, "security_alerts.json")

ALERT_CATEGORIES = {
    "critical_action": {"severity": "critical", "emoji": "\U0001f525"},
    "repeated_failure": {"severity": "warning", "emoji": "\u26a0\ufe0f"},
    "suspicious_activity": {"severity": "critical", "emoji": "\U0001f6a8"},
    "emergency_lockdown": {"severity": "critical", "emoji": "\U0001f6d1"},
    "node_isolation": {"severity": "warning", "emoji": "\U0001f517"},
    "dangerous_purge": {"severity": "critical", "emoji": "\U0001f5d1"},
    "backup_operation": {"severity": "info", "emoji": "\U0001f4be"},
    "session_anomaly": {"severity": "warning", "emoji": "\U0001f510"},
    "infra_failure": {"severity": "critical", "emoji": "\U0001f527"},
    "auth_anomaly": {"severity": "warning", "emoji": "\U0001f512"},
    "rate_limit": {"severity": "warning", "emoji": "\u23f3"},
    "db_anomaly": {"severity": "warning", "emoji": "\U0001f4c4"},
}


class SecurityAlertSystem:
    def __init__(self):
        self._lock = threading.Lock()
        self._alerts: list = load_json(ALERTS_PATH, [])
        self._alert_counts: dict[str, int] = {}
        self._cooldowns: dict[str, float] = {}
        ALERT_COOLDOWN = 300
        logger.info("SecurityAlertSystem initialized with %d alerts", len(self._alerts))

    def _save(self):
        save_json(ALERTS_PATH, self._alerts[-2000:])

    def _in_cooldown(self, category, detail_hash):
        key = f"{category}:{detail_hash}"
        now = time.time()
        last = self._cooldowns.get(key, 0)
        return now - last < 300

    def _set_cooldown(self, category, detail_hash):
        key = f"{category}:{detail_hash}"
        self._cooldowns[key] = time.time()

    def send_alert(self, category, title, message, op_id=None, resource=None,
                   admin_id=None, extra=None):
        if category not in ALERT_CATEGORIES:
            return None
        detail_hash = f"{category}:{resource or ''}:{admin_id or ''}"
        if self._in_cooldown(category, detail_hash):
            return None
        cat_info = ALERT_CATEGORIES[category]
        alert = {
            "id": str(int(time.time() * 1000))[-12:],
            "category": category,
            "severity": cat_info["severity"],
            "emoji": cat_info["emoji"],
            "title": title,
            "message": str(message)[:500],
            "op_id": op_id,
            "resource": str(resource) if resource else None,
            "admin_id": str(admin_id) if admin_id else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "acknowledged": False,
        }
        if extra:
            alert["extra"] = extra
        with self._lock:
            self._alerts.append(alert)
            self._alert_counts[category] = self._alert_counts.get(category, 0) + 1
            if len(self._alerts) > 2000:
                self._alerts = self._alerts[-1500:]
            self._save()
        self._set_cooldown(category, detail_hash)
        metrics.inc(f"sec_alert.{category}")
        bus.emit("security.alert", alert)
        logger.warning("SECURITY ALERT [%s]: %s - %s (op=%s, resource=%s)",
                       category, title, message[:100], op_id, resource)
        return alert

    def acknowledge(self, alert_id):
        with self._lock:
            for alert in reversed(self._alerts):
                if alert.get("id") == alert_id:
                    alert["acknowledged"] = True
                    alert["acknowledged_at"] = datetime.now(timezone.utc).isoformat()
                    self._save()
                    return True
        return False

    def get_alerts(self, limit=20, category=None, severity=None, unack_only=False):
        with self._lock:
            alerts = list(self._alerts)
        if category:
            alerts = [a for a in alerts if a.get("category") == category]
        if severity:
            alerts = [a for a in alerts if a.get("severity") == severity]
        if unack_only:
            alerts = [a for a in alerts if not a.get("acknowledged")]
        return alerts[-limit:]

    def get_stats(self):
        with self._lock:
            total = len(self._alerts)
            unacked = sum(1 for a in self._alerts if not a.get("acknowledged"))
            by_severity = {}
            for a in self._alerts:
                sev = a.get("severity", "unknown")
                by_severity[sev] = by_severity.get(sev, 0) + 1
        return {
            "total_alerts": total,
            "unacknowledged": unacked,
            "by_severity": by_severity,
            "by_category": dict(self._alert_counts),
        }

    def clear_acknowledged(self):
        with self._lock:
            self._alerts = [a for a in self._alerts if not a.get("acknowledged")]
            self._save()


security_alerts = SecurityAlertSystem()
