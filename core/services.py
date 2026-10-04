"""Shared services, metrics collector, event bus, and data access layer."""
import os
import json
import time
import threading
import logging
from collections import deque, defaultdict
from datetime import datetime, timezone

logger = logging.getLogger("turtle.services")

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")

for _d in (DATA_DIR, BACKUP_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)


def load_json(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, default=str)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error("save_json %s failed: %s", path, e)
        return False


def utcnow():
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Metrics collector
# ---------------------------------------------------------------------------

class Metrics:
    """Thread-safe in-memory metrics collector."""

    def __init__(self):
        self._lock = threading.Lock()
        self._counters = defaultdict(int)
        self._gauges = {}
        self._timers = defaultdict(list)
        self._events = deque(maxlen=5000)
        self._start = time.time()

    def inc(self, key, n=1):
        with self._lock:
            self._counters[key] += n

    def dec(self, key, n=1):
        with self._lock:
            self._counters[key] = max(0, self._counters[key] - n)

    def set(self, key, value):
        with self._lock:
            self._gauges[key] = value

    def get(self, key, default=0):
        with self._lock:
            return self._counters.get(key, self._gauges.get(key, default))

    def timer(self, key, duration):
        with self._lock:
            buf = self._timers[key]
            buf.append(duration)
            if len(buf) > 200:
                self._timers[key] = buf[-200:]

    def timer_avg(self, key):
        with self._lock:
            buf = self._timers.get(key, [])
            return sum(buf) / len(buf) if buf else 0

    def timer_p99(self, key):
        with self._lock:
            buf = sorted(self._timers.get(key, []))
            if not buf:
                return 0
            return buf[int(len(buf) * 0.99)] if len(buf) > 1 else buf[0]

    def event(self, level, category, message, details=None):
        entry = {
            "ts": utcnow().isoformat(),
            "level": level,
            "cat": category,
            "msg": message,
            "details": details,
        }
        self._events.appendleft(entry)

    def snapshot(self):
        with self._lock:
            return {
                "uptime": int(time.time() - self._start),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "timers_avg": {k: round(sum(v) / len(v), 3) if v else 0
                               for k, v in self._timers.items()},
                "recent_events": list(self._events)[:20],
            }

    def all_counters(self):
        with self._lock:
            return dict(self._counters)

    def all_gauges(self):
        with self._lock:
            return dict(self._gauges)

    def all_timers(self):
        with self._lock:
            return {k: {"avg": round(sum(v) / len(v), 3) if v else 0,
                         "p99": round(sorted(v)[int(len(v) * 0.99)] if len(v) > 1 else (v[0] if v else 0), 3),
                         "count": len(v)}
                    for k, v in self._timers.items()}

    def recent_events(self, n=50):
        return list(self._events)[:n]


metrics = Metrics()


# ---------------------------------------------------------------------------
# Event bus (lightweight pub/sub)
# ---------------------------------------------------------------------------

class EventBus:
    def __init__(self):
        self._subs = defaultdict(list)
        self._lock = threading.Lock()

    def on(self, event, callback):
        with self._lock:
            self._subs[event].append(callback)

    def emit(self, event, *args, **kwargs):
        with self._lock:
            cbs = list(self._subs.get(event, []))
        for cb in cbs:
            try:
                cb(*args, **kwargs)
            except Exception as e:
                logger.error("EventBus handler error for %s: %s", event, e)


bus = EventBus()


# ---------------------------------------------------------------------------
# Health registry
# ---------------------------------------------------------------------------

class HealthRegistry:
    """Centralized health check registry for all services."""

    def __init__(self):
        self._checks = {}
        self._results = {}
        self._status = {}
        self._lock = threading.Lock()

    def register(self, name, check_fn, critical=False):
        with self._lock:
            self._checks[name] = {"fn": check_fn, "critical": critical}

    def set(self, name, status, detail=""):
        with self._lock:
            self._status[name] = {"status": status, "detail": detail}

    def get(self, name):
        with self._lock:
            return self._status.get(name, {"status": "unknown", "detail": "not checked"})

    def run_all(self):
        results = {}
        for name, info in self._checks.items():
            try:
                ok, detail = info["fn"]()
                results[name] = {
                    "ok": ok,
                    "detail": detail,
                    "critical": info["critical"],
                    "status": "healthy" if ok else ("critical" if info["critical"] else "warning"),
                }
            except Exception as e:
                results[name] = {
                    "ok": False,
                    "detail": str(e),
                    "critical": info["critical"],
                    "status": "error",
                }
        with self._lock:
            self._results = results
        return results

    def summary(self):
        with self._lock:
            results = dict(self._results)
        if not results:
            return self.run_all()
        return results

    def overall_ok(self):
        results = self.summary()
        return all(r["ok"] or not r["critical"] for r in results.values())


health = HealthRegistry()
