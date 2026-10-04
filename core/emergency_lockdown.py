"""Emergency Lockdown system.

Provides a comprehensive emergency security control that can immediately:
- Disable VPS deployments, purchases, terminal access, destructive operations
- Pause automation, lock sensitive admin operations
- Enable maintenance mode
Requires high-level authorization and confirmation.
"""
import os
import time
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.lockdown")

LOCKDOWN_PATH = os.path.join(DATA_DIR, "lockdown.json")

LOCKDOWN_FEATURES = {
    "vps_deployment": {"display": "VPS Deployment", "default": False},
    "purchases": {"display": "Purchases", "default": False},
    "terminal": {"display": "Terminal Access", "default": False},
    "destructive_ops": {"display": "Destructive Operations", "default": False},
    "automation": {"display": "Automation", "default": False},
    "admin_operations": {"display": "Admin Operations", "default": False},
    "maintenance_mode": {"display": "Maintenance Mode", "default": False},
    "api_access": {"display": "API Access", "default": False},
    "backup_ops": {"display": "Backup Operations", "default": False},
}

FULL_LOCKDOWN_FEATURES = list(LOCKDOWN_FEATURES.keys())

HIGH_LEVEL_USERS_KEY = "high_level_users"


class EmergencyLockdown:
    def __init__(self):
        self._state = load_json(LOCKDOWN_PATH, {
            "active": False,
            "features": {k: False for k in LOCKDOWN_FEATURES},
            "activated_by": None,
            "activated_at": None,
            "reason": "",
            "high_level_users": [],
        })
        self._alerts: list = []
        logger.info("EmergencyLockdown initialized - active=%s", self._state.get("active"))

    def _save(self):
        save_json(LOCKDOWN_PATH, self._state)

    def activate_lockdown(self, admin_id, reason="Emergency lockdown", features=None):
        admin_id = str(admin_id)
        if not self._is_high_level(admin_id):
            metrics.inc("lockdown.unauthorized_attempt")
            bus.emit("lockdown.unauthorized", admin_id=admin_id)
            logger.critical("Unauthorized lockdown attempt by user=%s", admin_id)
            return False, "Insufficient authorization"
        self._state["active"] = True
        self._state["activated_by"] = admin_id
        self._state["activated_at"] = datetime.now(timezone.utc).isoformat()
        self._state["reason"] = reason
        if features:
            for f in features:
                if f in LOCKDOWN_FEATURES:
                    self._state["features"][f] = True
        else:
            for f in FULL_LOCKDOWN_FEATURES:
                self._state["features"][f] = True
        self._save()
        metrics.inc("lockdown.activated")
        bus.emit("lockdown.activated", admin_id=admin_id, reason=reason,
                 features=list(self._state["features"].keys()))
        logger.critical("LOCKDOWN activated by admin=%s reason=%s", admin_id, reason)
        self._send_alert("emergency_lockdown", f"Lockdown activated by {admin_id}: {reason}")
        return True, "Lockdown activated"

    def deactivate_lockdown(self, admin_id, reason="Manual unlock"):
        admin_id = str(admin_id)
        if not self._is_high_level(admin_id):
            return False, "Insufficient authorization"
        self._state["active"] = False
        self._state["features"] = {k: False for k in LOCKDOWN_FEATURES}
        self._state["deactivated_by"] = admin_id
        self._state["deactivated_at"] = datetime.now(timezone.utc).isoformat()
        self._state["reason"] = ""
        self._save()
        metrics.inc("lockdown.deactivated")
        bus.emit("lockdown.deactivated", admin_id=admin_id, reason=reason)
        logger.info("Lockdown deactivated by admin=%s reason=%s", admin_id, reason)
        return True, "Lockdown deactivated"

    def is_feature_blocked(self, feature):
        if not self._state.get("active", False):
            return False
        return self._state.get("features", {}).get(feature, False)

    def is_any_blocked(self):
        return self._state.get("active", False) and any(self._state.get("features", {}).values())

    def get_status(self):
        return {
            "active": self._state.get("active", False),
            "features": dict(self._state.get("features", {})),
            "activated_by": self._state.get("activated_by"),
            "activated_at": self._state.get("activated_at"),
            "reason": self._state.get("reason", ""),
            "blocked_count": sum(1 for v in self._state.get("features", {}).values() if v),
        }

    def _is_high_level(self, user_id):
        high_level = self._state.get(HIGH_LEVEL_USERS_KEY, [])
        return str(user_id) in high_level

    def set_high_level_users(self, user_ids):
        self._state[HIGH_LEVEL_USERS_KEY] = [str(uid) for uid in user_ids]
        self._save()

    def add_high_level_user(self, user_id):
        users = self._state.setdefault(HIGH_LEVEL_USERS_KEY, [])
        uid = str(user_id)
        if uid not in users:
            users.append(uid)
            self._save()

    def remove_high_level_user(self, user_id):
        users = self._state.get(HIGH_LEVEL_USERS_KEY, [])
        uid = str(user_id)
        if uid in users:
            users.remove(uid)
            self._save()

    def _send_alert(self, alert_type, message):
        alert = {
            "type": alert_type, "message": message,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._alerts.append(alert)
        if len(self._alerts) > 100:
            self._alerts = self._alerts[-50:]
        bus.emit("security.alert", alert)

    def get_alerts(self, limit=20):
        return list(self._alerts[-limit:])


emergency_lockdown = EmergencyLockdown()
