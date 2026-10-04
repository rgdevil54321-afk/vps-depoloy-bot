"""Emergency Control Center - global feature toggles, lockdown, and restart controls."""
import os
import sys
import asyncio
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.emergency")

_EMERGENCY_PATH = os.path.join(DATA_DIR, "emergency.json")

_DEFAULT_STATE = {
    "maintenance_mode": False,
    "purchases_disabled": False,
    "vps_deployment_disabled": False,
    "terminal_disabled": False,
    "db_writes_disabled": False,
    "automation_stopped": False,
    "admin_locked": False,
}

_DISPLAY_NAMES = {
    "maintenance_mode": "Maintenance Mode",
    "purchases_disabled": "Purchases",
    "vps_deployment_disabled": "VPS Deployment",
    "terminal_disabled": "Terminal Access",
    "db_writes_disabled": "Database Writes",
    "automation_stopped": "Automation",
    "admin_locked": "Admin Panel",
}


class EmergencyManager:
    """Manages bot-wide emergency toggles, lockdown, and graceful restart."""

    def __init__(self, bot):
        self.bot = bot
        self._state: dict = load_json(_EMERGENCY_PATH, dict(_DEFAULT_STATE))

        for key in _DEFAULT_STATE:
            if key not in self._state:
                self._state[key] = False

        self._save()
        logger.info("EmergencyManager initialised - %d active blocks",
                     sum(1 for v in self._state.values() if v))

    # -- persistence --------------------------------------------------------

    def _save(self) -> None:
        save_json(_EMERGENCY_PATH, self._state)

    # -- state access -------------------------------------------------------

    def set_state(self, key: str, value: bool, admin_id) -> bool:
        """Set a single toggle. Returns True on success."""
        if key not in _DEFAULT_STATE:
            logger.warning("Unknown emergency key: %s", key)
            return False

        old = self._state.get(key, False)
        self._state[key] = bool(value)
        self._save()

        action = f"emergency_{key}_{'enable' if value else 'disable'}"
        metrics.inc(f"emergency.{key}")
        metrics.set(f"emergency.{key}", int(value))
        bus.emit("emergency_state_change",
                 key=key, value=value, admin_id=str(admin_id))

        logger.warning("Emergency toggle %s -> %s by admin %s",
                       key, value, admin_id)

        self._audit(action, admin_id, key, value)
        return True

    def get_state(self) -> dict:
        """Return a copy of all current states."""
        return dict(self._state)

    def is_enabled(self, action: str) -> bool:
        """Return True if *action* is NOT blocked by an emergency toggle.

        Convention: an action name like ``"vps_deployment"`` maps to the
        toggle ``"vps_deployment_disabled"``.  If no matching toggle exists
        the action is considered enabled.
        """
        toggle_key = None
        for key in _DEFAULT_STATE:
            if key == action or key.startswith(action):
                toggle_key = key
                break

        if toggle_key is None:
            return True

        return not self._state.get(toggle_key, False)

    # -- bulk operations ----------------------------------------------------

    def full_lockdown(self, admin_id) -> dict:
        """Disable every feature. Returns final state."""
        for key in _DEFAULT_STATE:
            self._state[key] = True
        self._save()

        metrics.inc("emergency.full_lockdown")
        bus.emit("emergency_lockdown", admin_id=str(admin_id))
        self._audit("emergency_full_lockdown", admin_id, "all", True)

        logger.critical("FULL LOCKDOWN activated by admin %s", admin_id)
        return self.get_state()

    def full_unlock(self, admin_id) -> dict:
        """Enable every feature. Returns final state."""
        for key in _DEFAULT_STATE:
            self._state[key] = False
        self._save()

        metrics.inc("emergency.full_unlock")
        bus.emit("emergency_unlock", admin_id=str(admin_id))
        self._audit("emergency_full_unlock", admin_id, "all", False)

        logger.info("FULL UNLOCK activated by admin %s", admin_id)
        return self.get_state()

    # -- restart ------------------------------------------------------------

    async def emergency_restart(self, admin_id) -> bool:
        """Gracefully restart the bot process. Returns True if initiated."""
        logger.critical("Emergency restart requested by admin %s", admin_id)
        self._audit("emergency_restart", admin_id, "bot", "graceful restart")

        metrics.inc("emergency.restarts")
        bus.emit("emergency_restart", admin_id=str(admin_id))

        self._state["maintenance_mode"] = True
        self._save()

        async def _do_restart():
            await asyncio.sleep(1.0)
            try:
                await self.bot.close()
            except Exception:
                pass
            python = sys.executable
            os.execv(python, [python] + sys.argv)

        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_do_restart())
            return True
        except Exception as e:
            logger.error("Failed to schedule restart: %s", e)
            return False

    # -- embed helpers ------------------------------------------------------

    def get_status_embed_fields(self) -> list:
        """Return a list of dicts suitable for ``embed.add_field``."""
        fields = []
        for key, display in _DISPLAY_NAMES.items():
            blocked = self._state.get(key, False)
            emoji = "\U0001f534" if blocked else "\U0001f7e2"
            status_text = "BLOCKED" if blocked else "Active"
            fields.append({
                "name": f"{emoji} {display}",
                "value": f"`{status_text}`",
                "inline": True,
            })
        return fields

    # -- audit helper -------------------------------------------------------

    def _audit(self, action: str, admin_id, target: str, value) -> None:
        """Write to the audit log if the module is available."""
        try:
            from core.security import audit_log
            audit_log.log(
                action=action,
                user_id=admin_id,
                target=target,
                details=f"value={value}",
                level="critical" if "lockdown" in action else "warning",
            )
        except Exception:
            pass


def is_maintenance_mode() -> bool:
    """Quick standalone check - loads directly from the JSON file."""
    state = load_json(_EMERGENCY_PATH, _DEFAULT_STATE)
    return bool(state.get("maintenance_mode", False))
