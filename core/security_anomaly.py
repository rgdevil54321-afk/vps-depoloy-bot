"""Automatic Anomaly Detection system.

Detects unusual activity patterns including large numbers of admin actions,
repeated failures, sudden purge activity, unusual VPS changes, deployment
activity, abnormal API requests, and suspicious infrastructure behavior.
"""
import os
import time
import logging
import threading
from collections import defaultdict
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.anomaly_detect")

ANOMALY_LOG_PATH = os.path.join(DATA_DIR, "security_anomalies.json")

THRESHOLDS = {
    "admin_actions_per_minute": 15,
    "failures_per_minute": 5,
    "purges_per_hour": 3,
    "vps_changes_per_hour": 10,
    "deploys_per_hour": 5,
    "api_requests_per_minute": 60,
    "lockdown_attempts_per_hour": 3,
    "session_anomalies_per_hour": 5,
}


class SecurityAnomalyDetector:
    def __init__(self):
        self._lock = threading.Lock()
        self._events: dict[str, list[float]] = defaultdict(list)
        self._anomaly_log: list = load_json(ANOMALY_LOG_PATH, [])
        self._alerts: list = []
        logger.info("SecurityAnomalyDetector initialized with %d historical anomalies",
                     len(self._anomaly_log))

    def _save_log(self):
        save_json(ANOMALY_LOG_PATH, self._anomaly_log[-2000:])

    def _record_event(self, category):
        now = time.time()
        with self._lock:
            self._events[category].append(now)
            self._events[category] = [t for t in self._events[category] if now - t < 3600]

    def _check_threshold(self, category, window_seconds=60):
        now = time.time()
        with self._lock:
            events = self._events.get(category, [])
            recent = [t for t in events if now - t < window_seconds]
        threshold_key = f"{category}_per_minute" if window_seconds == 60 else f"{category}_per_hour"
        if window_seconds > 60:
            threshold_key = f"{category}_per_hour"
        threshold = THRESHOLDS.get(threshold_key, 10)
        return len(recent), threshold, len(recent) >= threshold

    def _anomaly(self, kind, severity, detail, source="system"):
        entry = {
            "type": kind, "severity": severity, "detail": detail,
            "source": source,
            "detected_at": datetime.now(timezone.utc).isoformat(),
        }
        with self._lock:
            self._anomaly_log.append(entry)
            if len(self._anomaly_log) > 2000:
                self._anomaly_log = self._anomaly_log[-1500:]
            self._save_log()
        metrics.inc(f"anomaly.{kind}")
        bus.emit("security.anomaly", entry)
        self._alerts.append(entry)
        if len(self._alerts) > 100:
            self._alerts = self._alerts[-50:]
        logger.warning("Anomaly detected: %s [%s] - %s", kind, severity, detail[:200])
        return entry

    def record_admin_action(self, admin_id, action):
        self._record_event("admin_actions")
        count, threshold, triggered = self._check_threshold("admin_actions")
        if triggered:
            self._anomaly("excessive_admin_actions", "warning",
                          f"Admin {admin_id} performed {count} actions/min (threshold: {threshold})",
                          source=f"admin:{admin_id}")

    def record_failure(self, action, user_id=None):
        self._record_event("failures")
        count, threshold, triggered = self._check_threshold("failures")
        if triggered:
            self._anomaly("repeated_failures", "warning",
                          f"{count} failures/min from user {user_id or 'unknown'}",
                          source=f"user:{user_id or 'system'}")

    def record_purge(self, admin_id, details=""):
        self._record_event("purges")
        count, threshold, triggered = self._check_threshold("purges", 3600)
        if triggered:
            self._anomaly("excessive_purge", "critical",
                          f"{count} purges/hour by admin {admin_id}: {details}",
                          source=f"admin:{admin_id}")

    def record_vps_change(self, admin_id, change_type):
        self._record_event("vps_changes")
        count, threshold, triggered = self._check_threshold("vps_changes", 3600)
        if triggered:
            self._anomaly("excessive_vps_changes", "warning",
                          f"{count} VPS changes/hour by admin {admin_id} (type: {change_type})",
                          source=f"admin:{admin_id}")

    def record_deploy(self, admin_id, details=""):
        self._record_event("deploys")
        count, threshold, triggered = self._check_threshold("deploys", 3600)
        if triggered:
            self._anomaly("excessive_deploys", "warning",
                          f"{count} deploys/hour by admin {admin_id}: {details}",
                          source=f"admin:{admin_id}")

    def record_api_request(self, source_ip="unknown"):
        self._record_event("api_requests")
        count, threshold, triggered = self._check_threshold("api_requests")
        if triggered:
            self._anomaly("abnormal_api_traffic", "warning",
                          f"{count} API requests/min from {source_ip}",
                          source=f"api:{source_ip}")

    def record_lockdown_attempt(self, admin_id):
        self._record_event("lockdown_attempts")
        count, threshold, triggered = self._check_threshold("lockdown_attempts", 3600)
        if triggered:
            self._anomaly("repeated_lockdown_attempts", "critical",
                          f"{count} lockdown attempts/hour by admin {admin_id}",
                          source=f"admin:{admin_id}")

    def record_suspicious_behavior(self, admin_id, behavior, detail):
        self._anomaly("suspicious_behavior", "critical",
                      f"Suspicious behavior by {admin_id}: {behavior} - {detail}",
                      source=f"admin:{admin_id}")

    def get_anomalies(self, limit=20, severity=None):
        with self._lock:
            anomalies = list(self._anomaly_log)
        if severity:
            anomalies = [a for a in anomalies if a.get("severity") == severity]
        return anomalies[-limit:]

    def get_stats(self):
        with self._lock:
            total = len(self._anomaly_log)
            by_type = defaultdict(int)
            by_severity = defaultdict(int)
            for a in self._anomaly_log:
                by_type[a.get("type", "unknown")] += 1
                by_severity[a.get("severity", "unknown")] += 1
        return {
            "total_anomalies": total,
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
        }

    def get_active_events(self):
        now = time.time()
        with self._lock:
            return {k: len([t for t in v if now - t < 300])
                    for k, v in self._events.items()}


security_anomaly_detector = SecurityAnomalyDetector()
