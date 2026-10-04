import asyncio
import logging
import time
import os
import json
from core.services import metrics, bus, load_json, save_json, DATA_DIR, BASE_DIR

try:
    import psutil
    PSUTIL_AVAILABLE = True
except ImportError:
    PSUTIL_AVAILABLE = False
    logging.warning("psutil not installed - hardware monitors will return unavailable")

try:
    import urllib.request
except ImportError:
    urllib = None

try:
    import docker as docker_lib
except ImportError:
    docker_lib = None

logger = logging.getLogger(__name__)

DEFAULT_THRESHOLDS = {
    "cpu_warning": 90,
    "cpu_critical": 95,
    "ram_warning": 90,
    "ram_critical": 95,
    "swap_warning": 80,
    "disk_warning": 85,
    "disk_critical": 95,
    "disk_io_warning": 100,
    "load_warning": 4.0,
    "load_critical": 8.0,
    "latency_warning": 200,
    "latency_critical": 500,
    "container_stopped_action": "alert",
}

THRESHOLDS_PATH = os.path.join(DATA_DIR, "health_thresholds.json")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
CONTAINER_DATA_DIR = os.path.join(DATA_DIR, "vps_containers")
DB_FILES = [
    os.path.join(DATA_DIR, "vps_accounts.json"),
    os.path.join(DATA_DIR, "vps_config.json"),
    os.path.join(DATA_DIR, "deployments.json"),
    os.path.join(DATA_DIR, "orders.json"),
]
ALERT_COOLDOWN = 300


class HealthMonitor:
    def __init__(self, bot):
        self.bot = bot
        self.thresholds = self.load_thresholds()
        self._state = {"last_alert_times": {}, "previous_net_io": None}
        self._task = None
        logger.info("HealthMonitor initialized")

    # -- Threshold persistence -------------------------------------------------

    def load_thresholds(self):
        data = load_json(THRESHOLDS_PATH, {})
        merged = {**DEFAULT_THRESHOLDS, **data}
        return merged

    def save_thresholds(self):
        save_json(THRESHOLDS_PATH, self.thresholds)

    def set_threshold(self, key, value):
        self.thresholds[key] = value
        self.save_thresholds()
        logger.info("Threshold %s set to %s", key, value)

    def get_thresholds(self):
        return dict(self.thresholds)

    # -- Helpers ---------------------------------------------------------------

    def _severity(self, value, warning_key, critical_key=None, invert=False):
        warning = self.thresholds.get(warning_key, 0)
        if critical_key:
            critical = self.thresholds.get(critical_key, 0)
        else:
            critical = warning * 1.1
        if invert:
            if value <= critical:
                return "critical"
            if value <= warning:
                return "warning"
            return "ok"
        else:
            if value >= critical:
                return "critical"
            if value >= warning:
                return "warning"
            return "ok"

    def _should_alert(self, metric, cooldown=None):
        cooldown = cooldown or ALERT_COOLDOWN
        last = self._state["last_alert_times"].get(metric, 0)
        return (time.time() - last) >= cooldown

    def _record_alert(self, metric):
        self._state["last_alert_times"][metric] = time.time()

    def _ok(self, name, value, unit=""):
        return {"name": name, "value": value, "unit": unit, "status": "ok", "threshold_breached": False}

    def _result(self, name, value, status, unit="", breached=False, detail=""):
        return {"name": name, "value": value, "unit": unit, "status": status, "threshold_breached": breached, "detail": detail}

    # -- Individual monitors ---------------------------------------------------

    def monitor_cpu(self):
        if not PSUTIL_AVAILABLE:
            return self._result("cpu", -1, "unavailable", unit="%", detail="psutil not installed")
        value = psutil.cpu_percent(interval=1)
        severity = self._severity(value, "cpu_warning", "cpu_critical")
        return self._result("cpu", value, severity, unit="%", breached=severity != "ok")

    def monitor_ram(self):
        if not PSUTIL_AVAILABLE:
            return self._result("ram", -1, "unavailable", unit="%", detail="psutil not installed")
        mem = psutil.virtual_memory()
        value = mem.percent
        severity = self._severity(value, "ram_warning", "ram_critical")
        return self._result(
            "ram", value, severity, unit="%", breached=severity != "ok",
            detail=f"{mem.used // (1024**3)}G / {mem.total // (1024**3)}G"
        )

    def monitor_swap(self):
        if not PSUTIL_AVAILABLE:
            return self._result("swap", -1, "unavailable", unit="%", detail="psutil not installed")
        swap = psutil.swap_memory()
        value = swap.percent
        severity = self._severity(value, "swap_warning")
        return self._result(
            "swap", value, severity, unit="%", breached=severity != "ok",
            detail=f"{swap.used // (1024**3)}G / {swap.total // (1024**3)}G"
        )

    def monitor_disk(self):
        if not PSUTIL_AVAILABLE:
            return self._result("disk", -1, "unavailable", unit="%", detail="psutil not installed")
        usage = psutil.disk_usage("/")
        value = usage.percent
        severity = self._severity(value, "disk_warning", "disk_critical")
        return self._result(
            "disk", value, severity, unit="%", breached=severity != "ok",
            detail=f"{usage.used // (1024**3)}G / {usage.total // (1024**3)}G"
        )

    def monitor_disk_io(self):
        if not PSUTIL_AVAILABLE:
            return self._result("disk_io", -1, "unavailable", unit="MB/s", detail="psutil not installed")
        try:
            io = psutil.disk_io_counters()
            if io is None:
                return self._result("disk_io", 0, "ok", unit="MB/s")
            bytes_per_sec = (io.read_bytes + io.write_bytes)
            prev = self._state.get("previous_disk_io")
            now = time.time()
            if prev:
                elapsed = now - prev["time"]
                if elapsed > 0:
                    rate = (bytes_per_sec - prev["bytes"]) / elapsed / (1024 * 1024)
                else:
                    rate = 0
            else:
                rate = 0
            self._state["previous_disk_io"] = {"bytes": bytes_per_sec, "time": now}
            severity = self._severity(rate, "disk_io_warning")
            return self._result("disk_io", round(rate, 2), severity, unit="MB/s", breached=severity != "ok")
        except Exception as e:
            return self._result("disk_io", -1, "error", detail=str(e))

    def monitor_load(self):
        try:
            load1, load5, load15 = os.getloadavg()
        except (OSError, AttributeError):
            return self._result("load", -1, "unavailable", unit="", detail="loadavg not available on this OS")
        severity = self._severity(load1, "load_warning", "load_critical")
        return self._result(
            "load", round(load1, 2), severity, breached=severity != "ok",
            detail=f"1m={load1:.2f} 5m={load5:.2f} 15m={load15:.2f}"
        )

    def monitor_network(self):
        if not PSUTIL_AVAILABLE:
            return self._result("network", -1, "unavailable", detail="psutil not installed")
        try:
            io = psutil.net_io_counters()
            total = io.bytes_sent + io.bytes_recv
            prev = self._state.get("previous_net_io")
            now = time.time()
            if prev:
                elapsed = now - prev["time"]
                if elapsed > 0:
                    rate = (total - prev["bytes"]) / elapsed / (1024 * 1024)
                else:
                    rate = 0
            else:
                rate = 0
            self._state["previous_net_io"] = {"bytes": total, "time": now}
            return self._result("network", round(rate, 2), "ok", unit="MB/s",
                                detail=f"sent={io.bytes_sent // (1024**2)}M recv={io.bytes_recv // (1024**2)}M")
        except Exception as e:
            return self._result("network", -1, "error", detail=str(e))

    def monitor_docker(self):
        if docker_lib is None:
            return self._result("docker", -1, "unavailable", detail="docker SDK not installed")
        try:
            client = docker_lib.from_env()
            containers = client.containers.list(all=True)
            running = sum(1 for c in containers if c.status == "running")
            total = len(containers)
            stopped = total - running
            status = "ok" if stopped == 0 else "warning"
            return self._result("docker", running, status, unit="containers",
                                detail=f"{running}/{total} running", breached=stopped > 0)
        except Exception as e:
            return self._result("docker", -1, "error", detail=str(e))

    def monitor_containers(self):
        if not os.path.isdir(CONTAINER_DATA_DIR):
            return self._result("containers", 0, "ok", detail="No container configs")
        issues = []
        count = 0
        for fname in os.listdir(CONTAINER_DATA_DIR):
            if not fname.endswith(".json"):
                continue
            count += 1
            fpath = os.path.join(CONTAINER_DATA_DIR, fname)
            data = load_json(fpath, {})
            status = data.get("status", "unknown")
            name = data.get("name", fname)
            if status not in ("running", "stopped"):
                issues.append(f"{name}: unexpected status '{status}'")
            elif status == "stopped" and self.thresholds.get("container_stopped_action") == "alert":
                issues.append(f"{name}: stopped")
        if issues:
            return self._result("containers", len(issues), "warning", detail="; ".join(issues), breached=True)
        return self._result("containers", count, "ok", detail=f"{count} configured")

    def monitor_database(self):
        issues = []
        ok_count = 0
        for fpath in DB_FILES:
            fname = os.path.basename(fpath)
            if not os.path.exists(fpath):
                issues.append(f"{fname}: missing")
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    json.load(f)
                ok_count += 1
            except (json.JSONDecodeError, IOError) as e:
                issues.append(f"{fname}: {e}")
        total = len(DB_FILES)
        if issues:
            return self._result("database", ok_count, "warning", breached=True,
                                detail=f"{ok_count}/{total} OK; " + "; ".join(issues))
        return self._result("database", total, "ok", detail=f"{total}/{total} OK")

    def monitor_api(self):
        if urllib is None:
            return self._result("api", -1, "unavailable", detail="urllib not available")
        try:
            req = urllib.request.Request("http://127.0.0.1:8000/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                code = resp.getcode()
                status = "ok" if code == 200 else "warning"
                return self._result("api", code, status, detail=f"HTTP {code}", breached=code != 200)
        except Exception as e:
            return self._result("api", -1, "critical", detail=str(e), breached=True)

    def monitor_bot_latency(self):
        if not hasattr(self.bot, "latency") or self.bot.latency is None:
            return self._result("latency", -1, "unavailable", unit="ms", detail="Bot not connected")
        value = self.bot.latency * 1000
        severity = self._severity(value, "latency_warning", "latency_critical")
        return self._result("latency", round(value, 1), severity, unit="ms", breached=severity != "ok")

    def monitor_tasks(self):
        try:
            import asyncio as _aio
            loop = _aio.get_running_loop()
            tasks = [t for t in _aio.all_tasks(loop) if not t.done()]
            return self._result("tasks", len(tasks), "ok", detail=f"{len(tasks)} running")
        except Exception as e:
            return self._result("tasks", -1, "error", detail=str(e))

    def monitor_backups(self):
        if not os.path.isdir(BACKUP_DIR):
            return self._result("backups", 0, "warning", detail="Backup directory missing", breached=True)
        now = time.time()
        recent = 0
        for fname in os.listdir(BACKUP_DIR):
            fpath = os.path.join(BACKUP_DIR, fname)
            try:
                age = now - os.path.getmtime(fpath)
                if age < 86400:
                    recent += 1
            except OSError:
                continue
        status = "ok" if recent > 0 else "warning"
        return self._result("backups", recent, status, unit="recent",
                            detail=f"{recent} backup(s) in last 24h", breached=recent == 0)

    # -- Aggregation -----------------------------------------------------------

    def run_all_monitors(self):
        monitors = [
            self.monitor_cpu,
            self.monitor_ram,
            self.monitor_swap,
            self.monitor_disk,
            self.monitor_disk_io,
            self.monitor_load,
            self.monitor_network,
            self.monitor_docker,
            self.monitor_containers,
            self.monitor_database,
            self.monitor_api,
            self.monitor_bot_latency,
            self.monitor_tasks,
            self.monitor_backups,
        ]
        results = []
        for fn in monitors:
            try:
                results.append(fn())
            except Exception as e:
                logger.exception("Monitor %s crashed", fn.__name__)
                results.append({"name": fn.__name__, "value": -1, "status": "error",
                                "threshold_breached": True, "detail": str(e)})
        return results

    def check_and_alert(self):
        results = self.run_all_monitors()
        alerts = []
        for r in results:
            if not r.get("threshold_breached"):
                continue
            name = r["name"]
            if not self._should_alert(name):
                continue
            severity = r.get("status", "warning")
            value = r.get("value")
            msg = f"[{severity.upper()}] {name}: {r.get('detail', value)}"
            alert = {
                "metric": name,
                "value": value,
                "threshold": self.thresholds.get(f"{name}_warning", "?"),
                "severity": severity,
                "message": msg,
                "time": time.time(),
            }
            alerts.append(alert)
            self._record_alert(name)
            try:
                bus.emit("health_alert", **alert)
            except Exception:
                logger.exception("Failed to emit health_alert for %s", name)
            logger.warning("Health alert: %s", msg)
        return alerts

    def get_dashboard_data(self):
        results = self.run_all_monitors()
        dashboard = {}
        for r in results:
            name = r["name"]
            dashboard[name] = {
                "value": r.get("value"),
                "unit": r.get("unit", ""),
                "status": r.get("status", "unknown"),
                "detail": r.get("detail", ""),
            }
        return dashboard

    def get_system_overview(self):
        results = self.run_all_monitors()
        statuses = [r.get("status", "error") for r in results]
        if any(s == "critical" for s in statuses):
            overall = "critical"
        elif any(s == "warning" for s in statuses):
            overall = "warning"
        elif any(s in ("error", "unavailable") for s in statuses):
            overall = "degraded"
        else:
            overall = "healthy"

        uptime = 0
        if PSUTIL_AVAILABLE:
            try:
                uptime = time.time() - psutil.boot_time()
            except Exception:
                pass

        metrics_map = {}
        for r in results:
            metrics_map[r["name"]] = {
                "value": r.get("value"),
                "unit": r.get("unit", ""),
                "status": r.get("status", "unknown"),
                "detail": r.get("detail", ""),
            }

        return {
            "overall": overall,
            "uptime_seconds": int(uptime),
            "metrics": metrics_map,
            "thresholds": dict(self.thresholds),
        }

    # -- Background loop -------------------------------------------------------

    async def start_monitoring(self, interval=60):
        logger.info("Health monitoring started (interval=%ds)", interval)
        self._task = asyncio.get_running_loop().create_task(self._monitor_loop(interval))
        return self._task

    async def _monitor_loop(self, interval):
        while True:
            try:
                alerts = self.check_and_alert()
                if alerts:
                    logger.info("Monitor loop: %d alert(s) triggered", len(alerts))
                try:
                    dashboard = self.get_system_overview()
                    metrics.gauge("health.overall", 1 if dashboard["overall"] == "healthy" else 0)
                except Exception:
                    pass
            except asyncio.CancelledError:
                logger.info("Health monitor loop cancelled")
                raise
            except Exception:
                logger.exception("Error in health monitor loop")
            await asyncio.sleep(interval)

    def stop(self):
        if self._task and not self._task.done():
            self._task.cancel()
            logger.info("Health monitor stopped")
