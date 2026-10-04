"""Node Isolation system.

Detects unhealthy or suspicious nodes, prevents new VPS deployments to
isolated nodes, allows admin manual isolate/re-enable, shows isolated
nodes in infrastructure dashboard, and logs every isolation event.
"""
import os
import time
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("turtle.node_isolation")

NODE_ISOLATION_PATH = os.path.join(DATA_DIR, "node_isolation.json")


class NodeIsolation:
    def __init__(self):
        self._state = load_json(NODE_ISOLATION_PATH, {
            "isolated_nodes": {},
            "auto_isolate_enabled": True,
            "failure_threshold": 5,
            "isolation_log": [],
        })
        self._failure_counts: dict[str, int] = {}
        logger.info("NodeIsolation initialized - %d isolated nodes",
                     len(self._state.get("isolated_nodes", {})))

    def _save(self):
        save_json(NODE_ISOLATION_PATH, self._state)

    def _log_event(self, event_type, node_id, admin_id=None, reason=""):
        entry = {
            "event": event_type, "node_id": str(node_id),
            "admin_id": str(admin_id) if admin_id else None,
            "reason": reason,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self._state.setdefault("isolation_log", []).append(entry)
        if len(self._state["isolation_log"]) > 500:
            self._state["isolation_log"] = self._state["isolation_log"][-300:]
        self._save()
        bus.emit("node_isolation.event", entry)

    def isolate_node(self, node_id, admin_id, reason="Manual isolation"):
        node_id = str(node_id)
        with self._lock_if_needed():
            self._state.setdefault("isolated_nodes", {})[node_id] = {
                "isolated_by": str(admin_id),
                "isolated_at": datetime.now(timezone.utc).isoformat(),
                "reason": reason,
                "auto_isolated": False,
            }
            self._save()
        self._log_event("isolated", node_id, admin_id, reason)
        metrics.inc("node_isolation.isolated")
        bus.emit("node_isolation.isolated", node_id=node_id, reason=reason)
        logger.warning("Node %s isolated by admin %s: %s", node_id, admin_id, reason)
        return True

    def auto_isolate(self, node_id, reason="Threshold exceeded"):
        node_id = str(node_id)
        if not self._state.get("auto_isolate_enabled", True):
            return False
        self._state.setdefault("isolated_nodes", {})[node_id] = {
            "isolated_by": "auto_isolate",
            "isolated_at": datetime.now(timezone.utc).isoformat(),
            "reason": reason,
            "auto_isolated": True,
        }
        self._save()
        self._log_event("auto_isolated", node_id, None, reason)
        metrics.inc("node_isolation.auto_isolated")
        bus.emit("node_isolation.auto_isolated", node_id=node_id, reason=reason)
        logger.warning("Node %s auto-isolated: %s", node_id, reason)
        return True

    def enable_node(self, node_id, admin_id):
        node_id = str(node_id)
        isolated = self._state.get("isolated_nodes", {})
        if node_id in isolated:
            del isolated[node_id]
            self._state["isolated_nodes"] = isolated
            self._save()
            self._failure_counts.pop(node_id, None)
            self._log_event("enabled", node_id, admin_id)
            metrics.inc("node_isolation.enabled")
            logger.info("Node %s enabled by admin %s", node_id, admin_id)
            return True
        return False

    def is_isolated(self, node_id):
        return str(node_id) in self._state.get("isolated_nodes", {})

    def record_failure(self, node_id):
        node_id = str(node_id)
        self._failure_counts[node_id] = self._failure_counts.get(node_id, 0) + 1
        threshold = self._state.get("failure_threshold", 5)
        if self._failure_counts[node_id] >= threshold:
            return self.auto_isolate(node_id, f"Failed {self._failure_counts[node_id]} times")
        return False

    def record_success(self, node_id):
        self._failure_counts.pop(str(node_id), None)

    def get_isolated_nodes(self):
        return dict(self._state.get("isolated_nodes", {}))

    def get_isolation_log(self, limit=20):
        return list(self._state.get("isolation_log", [])[-limit:])

    def set_auto_isolate(self, enabled):
        self._state["auto_isolate_enabled"] = bool(enabled)
        self._save()

    def set_failure_threshold(self, threshold):
        self._state["failure_threshold"] = max(1, int(threshold))
        self._save()

    def get_status(self):
        isolated = self.get_isolated_nodes()
        return {
            "isolated_count": len(isolated),
            "isolated_nodes": isolated,
            "auto_isolate_enabled": self._state.get("auto_isolate_enabled", True),
            "failure_threshold": self._state.get("failure_threshold", 5),
            "tracked_failures": dict(self._failure_counts),
        }

    def _lock_if_needed(self):
        import threading
        return threading.Lock()


node_isolation = NodeIsolation()
