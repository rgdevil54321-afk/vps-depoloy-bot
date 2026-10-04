import os
import time
import uuid
import threading
from datetime import datetime, timezone
from discord.ext import commands
from core.services import metrics, bus, load_json, save_json, DATA_DIR


class PermissionManager:
    LEVELS = {
        "Owner": 100,
        "Super Admin": 90,
        "VPS Manager": 70,
        "Developer": 60,
        "Support": 50,
        "Billing": 40,
        "Moderator": 30,
    }

    PERMISSIONS = {
        100: ["*"],
        90: [
            "admin.*", "vps.*", "server.*", "user.*", "billing.*",
            "config.*", "security.*", "deploy.*", "backup.*",
        ],
        70: [
            "vps.*", "server.*", "user.view", "user.manage_basic",
            "billing.view", "deploy.vps", "backup.create", "backup.restore",
        ],
        60: [
            "server.*", "deploy.*", "backup.create", "user.view",
            "config.view", "config.edit",
        ],
        50: [
            "server.view", "server.restart", "user.view",
            "support.ticket.*", "deploy.status",
        ],
        40: [
            "billing.view", "billing.invoice.*", "user.view",
            "server.view",
        ],
        30: [
            "server.view", "user.view", "chat.moderate",
        ],
    }

    @staticmethod
    def has_permission(user_level: int, action: str) -> bool:
        if user_level >= 100:
            return True
        allowed = PermissionManager.PERMISSIONS.get(user_level, [])
        for pattern in allowed:
            if pattern == "*":
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if action == prefix or action.startswith(prefix + "."):
                    return True
            elif pattern == action:
                return True
        return False

    @staticmethod
    def get_level_name(num: int) -> str:
        for name, level in sorted(PermissionManager.LEVELS.items(), key=lambda x: -x[1]):
            if num >= level:
                return name
        return "Unprivileged"


class AuditLog:
    _lock = threading.Lock()

    def __init__(self):
        self._path = os.path.join(DATA_DIR, "audit.json")
        self._entries = load_json(self._path, [])

    def _save(self):
        save_json(self._path, self._entries)

    def log(self, action: str, user_id: str, target: str, details: str = "", level: str = "info"):
        entry = {
            "op_id": str(uuid.uuid4())[:12],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "action": action,
            "user_id": str(user_id),
            "target": str(target),
            "details": details,
            "level": level,
        }
        with self._lock:
            self._entries.append(entry)
            if len(self._entries) > 5000:
                self._entries = self._entries[-3000:]
            self._save()
        try:
            metrics.counter("audit_events", tags={"action": action, "level": level})
        except Exception:
            pass
        bus.emit("audit.logged", entry)
        return entry

    def search(self, action: str = None, user_id: str = None, limit: int = 50, level: str = None):
        with self._lock:
            results = list(self._entries)
        if action:
            results = [e for e in results if e["action"] == action or e["action"].startswith(action)]
        if user_id:
            results = [e for e in results if e["user_id"] == str(user_id)]
        if level:
            results = [e for e in results if e["level"] == level]
        results.reverse()
        return results[:limit]

    def get_recent(self, n: int = 20):
        with self._lock:
            return list(self._entries[-n:])

    def get_stats(self):
        with self._lock:
            entries = list(self._entries)
        stats = {"total": len(entries), "by_level": {}, "by_action": {}}
        for e in entries:
            lvl = e.get("level", "info")
            stats["by_level"][lvl] = stats["by_level"].get(lvl, 0) + 1
            act = e.get("action", "unknown")
            stats["by_action"][act] = stats["by_action"].get(act, 0) + 1
        return stats


class SecurityManager:
    def __init__(self):
        self.lockdown_mode = False
        self._lockdown_reason = ""
        self._lockdown_admin = ""
        self._lockdown_time = None
        self.rate_limits: dict[str, list[float]] = {}
        self._lock = threading.Lock()
        self._config_path = os.path.join(DATA_DIR, "security.json")
        cfg = load_json(self._config_path, {})
        self.lockdown_mode = cfg.get("lockdown_mode", False)
        self._lockdown_reason = cfg.get("lockdown_reason", "")
        self._lockdown_admin = cfg.get("lockdown_admin", "")
        self._lockdown_time = cfg.get("lockdown_time")

    def _save_config(self):
        save_json(self._config_path, {
            "lockdown_mode": self.lockdown_mode,
            "lockdown_reason": self._lockdown_reason,
            "lockdown_admin": self._lockdown_admin,
            "lockdown_time": self._lockdown_time,
        })

    def check_rate_limit(self, user_id: str, action: str, limit_per_min: int = 30) -> bool:
        now = time.time()
        key = f"{user_id}:{action}"
        with self._lock:
            timestamps = self.rate_limits.get(key, [])
            timestamps = [t for t in timestamps if now - t < 60]
            if len(timestamps) >= limit_per_min:
                self.rate_limits[key] = timestamps
                return False
            timestamps.append(now)
            self.rate_limits[key] = timestamps
            stale = [k for k, v in self.rate_limits.items() if not v or now - v[-1] > 120]
            for k in stale:
                del self.rate_limits[k]
        return True

    def set_lockdown(self, state: bool, reason: str = "", admin_id: str = ""):
        with self._lock:
            self.lockdown_mode = state
            self._lockdown_reason = reason if state else ""
            self._lockdown_admin = str(admin_id) if state else ""
            self._lockdown_time = datetime.now(timezone.utc).isoformat() if state else None
            self._save_config()
        bus.emit("security.lockdown", {
            "state": state, "reason": reason, "admin_id": admin_id,
        })

    def get_security_report(self) -> dict:
        with self._lock:
            rl_count = len(self.rate_limits)
            lockdown = self.lockdown_mode
            reason = self._lockdown_reason
            admin = self._lockdown_admin
            ltime = self._lockdown_time
        return {
            "lockdown_active": lockdown,
            "lockdown_reason": reason,
            "lockdown_admin": admin,
            "lockdown_time": ltime,
            "active_rate_limits": rl_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }


audit = AuditLog()
security = SecurityManager()


def PERMISSION_CHECK(required_action: str):
    def predicate(ctx):
        if ctx.author.id == ctx.bot.owner_id:
            return True
        config_path = os.path.join(DATA_DIR, "admin_levels.json")
        admin_levels = load_json(config_path, {})
        user_id_str = str(ctx.author.id)
        user_level = admin_levels.get(user_id_str, 0)
        if not security.check_rate_limit(user_id_str, required_action):
            audit.log("rate_limited", user_id_str, required_action, level="warning")
            return False
        if security.lockdown_mode and user_level < 70:
            audit.log("lockdown_blocked", user_id_str, required_action, level="warning")
            return False
        allowed = PermissionManager.has_permission(user_level, required_action)
        if not allowed:
            audit.log("permission_denied", user_id_str, required_action, level="warning")
        return allowed
    return commands.check(predicate)
