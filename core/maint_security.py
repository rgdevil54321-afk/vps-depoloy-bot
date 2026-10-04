"""Maintenance Security Integration.

Allows Maintenance Mode to be activated during security incidents with scoped
maintenance, prevents normal user bypass, keeps emergency admin recovery,
and logs all maintenance security actions.
"""
import os
import time
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.maint_security")

MAINT_SECURITY_PATH = os.path.join(DATA_DIR, "maint_security.json")

SCOPED_MAINTENANCE = {
    "deployments": {"display": "VPS Deployments", "block_user": True},
    "terminal": {"display": "Terminal Access", "block_user": True},
    "purchases": {"display": "Purchases", "block_user": True},
    "configs": {"display": "Config Changes", "block_user": False},
    "backups": {"display": "Backup Operations", "block_user": True},
    "admin_panel": {"display": "Admin Panel", "block_user": False},
}


class MaintenanceSecurity:
    def __init__(self):
        self._state = load_json(MAINT_SECURITY_PATH, {
            "active": False,
            "reason": "",
            "activated_by": None,
            "activated_at": None,
            "scope": {},
            "authorized_recoverers": [],
            "actions_log": [],
        })
        logger.info("MaintenanceSecurity initialized - active=%s",
                     self._state.get("active", False))

    def _save(self):
        save_json(MAINT_SECURITY_PATH, self._state)

    def _log_action(self, action, admin_id, details=""):
        entry = {
            "action": action,
            "admin_id": str(admin_id),
            "details": details,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state.setdefault("actions_log", []).append(entry)
        if len(self._state["actions_log"]) > 500:
            self._state["actions_log"] = self._state["actions_log"][-300:]
        self._save()
        bus.emit("maint_security.action", entry)

    def activate(self, admin_id, reason="Security incident", scope=None):
        if self._state.get("active"):
            return False, "Already in maintenance security mode"
        self._state["active"] = True
        self._state["reason"] = reason
        self._state["activated_by"] = str(admin_id)
        self._state["activated_at"] = datetime.now(timezone.utc).isoformat()
        self._state["scope"] = {}
        if scope:
            for s in scope:
                if s in SCOPED_MAINTENANCE:
                    self._state["scope"][s] = True
        else:
            for s in SCOPED_MAINTENANCE:
                self._state["scope"][s] = True
        self._save()
        self._log_action("activated", admin_id, reason)
        metrics.inc("maint_security.activated")
        bus.emit("maint_security.activated", admin_id=str(admin_id), reason=reason)
        logger.warning("Maintenance security activated by %s: %s", admin_id, reason)
        return True, "Maintenance security mode activated"

    def deactivate(self, admin_id):
        admin_id = str(admin_id)
        if not self._can_recover(admin_id):
            return False, "Not authorized to deactivate maintenance"
        self._state["active"] = False
        self._state["reason"] = ""
        self._state["scope"] = {}
        self._state["deactivated_by"] = admin_id
        self._state["deactivated_at"] = datetime.now(timezone.utc).isoformat()
        self._save()
        self._log_action("deactivated", admin_id)
        metrics.inc("maint_security.deactivated")
        bus.emit("maint_security.deactivated", admin_id=admin_id)
        logger.info("Maintenance security deactivated by %s", admin_id)
        return True, "Maintenance security mode deactivated"

    def is_active(self):
        return self._state.get("active", False)

    def is_scope_blocked(self, scope):
        if not self._state.get("active", False):
            return False
        return self._state.get("scope", {}).get(scope, False)

    def can_user_bypass(self, user_id):
        if not self._state.get("active", False):
            return True
        return str(user_id) in self._state.get("authorized_recoverers", [])

    def _can_recover(self, user_id):
        return str(user_id) in self._state.get("authorized_recoverers", [])

    def add_recoverer(self, user_id):
        recoverers = self._state.setdefault("authorized_recoverers", [])
        uid = str(user_id)
        if uid not in recoverers:
            recoverers.append(uid)
            self._save()
            self._log_action("recoverer_added", uid)

    def remove_recoverer(self, user_id):
        recoverers = self._state.get("authorized_recoverers", [])
        uid = str(user_id)
        if uid in recoverers:
            recoverers.remove(uid)
            self._save()

    def get_status(self):
        return {
            "active": self._state.get("active", False),
            "reason": self._state.get("reason", ""),
            "activated_by": self._state.get("activated_by"),
            "activated_at": self._state.get("activated_at"),
            "scope": dict(self._state.get("scope", {})),
            "recoverers": self._state.get("authorized_recoverers", []),
        }

    def get_actions_log(self, limit=20):
        return list(self._state.get("actions_log", [])[-limit:])


maint_security = MaintenanceSecurity()
