"""Session and Security Controls.

Provides secure sessions for sensitive admin operations with automatic expiry,
reauthorization for highly dangerous ops, session invalidation on suspicious
activity, and never stores credentials in Discord messages.
"""
import os
import uuid
import time
import threading
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.sessions")

SESSIONS_PATH = os.path.join(DATA_DIR, "admin_sessions.json")
SESSION_TTL = 1800
SENSITIVE_SESSION_TTL = 600
REAUTH_ACTIONS = {
    "emergency_lockdown", "full_lockdown", "emergency_restart",
    "db_wipe", "restore_backup", "delete_all_vps", "reinstall_os",
    "purge_deep", "node_isolate_all",
}


class SessionManager:
    def __init__(self):
        self._lock = threading.Lock()
        self._sessions: dict[str, dict] = load_json(SESSIONS_PATH, {})
        self._suspicious: dict[str, list] = {}
        self._cleanup()
        logger.info("SessionManager initialized with %d sessions", len(self._sessions))

    def _cleanup(self):
        now = time.time()
        expired = []
        for sid, sess in self._sessions.items():
            ttl = SESSION_TTL if not sess.get("reauth_required") else SESSION_TTL
            if now - sess.get("created_at", 0) > ttl:
                expired.append(sid)
        for sid in expired:
            del self._sessions[sid]
        if expired:
            self._save()

    def _save(self):
        save_json(SESSIONS_PATH, self._sessions)

    def create_session(self, user_id, action="", level="admin"):
        user_id = str(user_id)
        sid = str(uuid.uuid4())[:16]
        reauth_required = action in REAUTH_ACTIONS
        session = {
            "user_id": user_id,
            "created_at": time.time(),
            "last_active": time.time(),
            "action": action,
            "level": level,
            "reauth_required": reauth_required,
            "validated": False,
        }
        with self._lock:
            self._sessions[sid] = session
            self._save()
        metrics.inc("session.created")
        bus.emit("session.created", session_id=sid, user_id=user_id, action=action)
        logger.info("Session created: %s for user=%s action=%s", sid, user_id, action)
        return sid

    def validate_session(self, session_id, user_id):
        user_id = str(user_id)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False, "Session not found"
            if session["user_id"] != user_id:
                self._mark_suspicious(user_id, "session_user_mismatch",
                                      f"session={session_id}")
                return False, "Session belongs to another user"
            now = time.time()
            ttl = SESSION_TTL
            if now - session["created_at"] > ttl:
                del self._sessions[session_id]
                self._save()
                metrics.inc("session.expired")
                bus.emit("session.expired", session_id=session_id, user_id=user_id)
                return False, "Session expired"
            if now - session["last_active"] > SESSION_TTL:
                del self._sessions[session_id]
                self._save()
                metrics.inc("session.expired")
                return False, "Session expired due to inactivity"
            session["last_active"] = now
            self._save()
        return True, "ok"

    def reauthorize(self, session_id, user_id):
        user_id = str(user_id)
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return False, "Session not found"
            if session["user_id"] != user_id:
                return False, "Session belongs to another user"
            session["validated"] = True
            session["reauth_time"] = time.time()
            self._save()
        metrics.inc("session.reauthorized")
        return True, "ok"

    def needs_reauth(self, session_id):
        with self._lock:
            session = self._sessions.get(session_id)
            if not session:
                return True
            return session.get("reauth_required", False) and not session.get("validated", False)

    def invalidate_session(self, session_id, reason="manual"):
        with self._lock:
            session = self._sessions.pop(session_id, None)
            self._save()
        if session:
            metrics.inc("session.invalidated")
            bus.emit("session.invalidated", session_id=session_id,
                     user_id=session["user_id"], reason=reason)
            logger.warning("Session invalidated: %s reason=%s", session_id, reason)
        return session is not None

    def invalidate_user_sessions(self, user_id, reason="suspicious"):
        user_id = str(user_id)
        invalidated = 0
        with self._lock:
            to_remove = [sid for sid, s in self._sessions.items() if s["user_id"] == user_id]
            for sid in to_remove:
                del self._sessions[sid]
                invalidated += 1
            self._save()
        if invalidated:
            metrics.inc("session.bulk_invalidated", invalidated)
            bus.emit("session.bulk_invalidated", user_id=user_id,
                     count=invalidated, reason=reason)
            logger.warning("Invalidated %d sessions for user=%s reason=%s",
                           invalidated, user_id, reason)
        return invalidated

    def _mark_suspicious(self, user_id, event_type, detail):
        if user_id not in self._suspicious:
            self._suspicious[user_id] = []
        self._suspicious[user_id].append({
            "type": event_type, "detail": detail,
            "time": datetime.now(timezone.utc).isoformat(),
        })
        metrics.inc("session.suspicious")
        bus.emit("session.suspicious", user_id=user_id, event_type=event_type)
        if len(self._suspicious[user_id]) >= 3:
            self.invalidate_user_sessions(user_id, "repeated_suspicious")
            logger.critical("Repeated suspicious activity from user=%s (sessions invalidated)", user_id)

    def get_active_sessions(self, user_id=None):
        self._cleanup()
        with self._lock:
            if user_id:
                return {sid: s for sid, s in self._sessions.items()
                        if s["user_id"] == str(user_id)}
            return dict(self._sessions)

    def get_session_info(self, session_id):
        with self._lock:
            return self._sessions.get(session_id)


session_manager = SessionManager()
