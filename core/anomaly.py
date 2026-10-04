import os
import time
import json
import asyncio
import logging
from datetime import datetime, timedelta, timezone
from collections import defaultdict

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("anomaly")

ANOMALIES_PATH = os.path.join(DATA_DIR, "anomaly_log.json")


def _host_busy_processes(threshold=0.0):
    """Return host processes using notable CPU or memory.

    Blocking: call from an executor. Returns a list of dicts with
    pid, name, cpu and mem percentage.
    """
    try:
        import psutil
    except ImportError:
        logger.warning("psutil not available - large process detection disabled")
        return []

    found = []
    try:
        procs = list(psutil.process_iter(["pid", "name", "cpu_percent", "memory_percent"]))
    except Exception as exc:
        logger.warning("Could not enumerate processes: %s", exc)
        return []

    for proc in procs:
        try:
            info = proc.info
            cpu = float(info.get("cpu_percent") or 0.0)
            mem = float(info.get("memory_percent") or 0.0)
        except Exception:
            continue
        if cpu > threshold or mem >= 80:
            found.append({
                "pid": info.get("pid"),
                "name": info.get("name") or "?",
                "cpu": round(cpu, 1),
                "mem": round(mem, 1),
            })
    found.sort(key=lambda p: max(p["cpu"], p["mem"]), reverse=True)
    return found[:5]


class AnomalyDetector:

    def __init__(self, bot):
        self.bot = bot
        self.anomaly_log = load_json(ANOMALIES_PATH, [])
        logger.info("AnomalyDetector initialised - %d historical anomalies loaded", len(self.anomaly_log))

    async def _docker(self, *args, timeout=15):
        try:
            proc = await self.bot.loop.run_in_executor(
                None,
                lambda: __import__("subprocess").run(
                    ["docker", *args],
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                ),
            )
            return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
        except Exception as exc:
            logger.error("docker %s failed: %s", " ".join(args), exc)
            return -1, "", str(exc)

    def _ts(self):
        return datetime.now(timezone.utc).replace(tzinfo=None).isoformat()

    def _anomaly(self, kind, severity, detail, vps="unknown"):
        return {
            "type": kind,
            "severity": severity,
            "detail": detail,
            "vps": vps,
            "detected_at": self._ts(),
        }

    # ── detection methods ──────────────────────────────────────────────

    async def detect_high_cpu(self, threshold=95, duration_minutes=5):
        anomalies = []
        rc, out, _ = await self._docker(
            "stats", "--no-stream", "--format",
            '{{.Name}} {{.CPUPerc}}', timeout=20,
        )
        if rc != 0:
            return anomalies

        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 2:
                continue
            name = parts[0]
            try:
                cpu = float(parts[1].rstrip("%"))
            except ValueError:
                continue

            if cpu >= threshold:
                severity = "critical" if cpu >= 99 else "warning"
                anomalies.append(
                    self._anomaly(
                        "high_cpu",
                        severity,
                        f"{name} CPU at {cpu:.1f}% (threshold {threshold}%)",
                        vps=name,
                    )
                )
        return anomalies

    async def detect_high_ram(self, threshold=95):
        anomalies = []
        rc, out, _ = await self._docker(
            "stats", "--no-stream", "--format",
            '{{.Name}} {{.MemPerc}} {{.MemUsage}}', timeout=20,
        )
        if rc != 0:
            return anomalies

        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[0]
            try:
                mem_pct = float(parts[1].rstrip("%"))
            except ValueError:
                continue

            usage = f"{parts[2]} {parts[3]}" if len(parts) >= 4 else parts[2]

            if mem_pct >= threshold:
                severity = "critical" if mem_pct >= 99 else "warning"
                anomalies.append(
                    self._anomaly(
                        "high_ram",
                        severity,
                        f"{name} RAM at {mem_pct:.1f}% ({usage}) - threshold {threshold}%",
                        vps=name,
                    )
                )
        return anomalies

    async def detect_disk_growth(self, rate_gb_per_day=5):
        anomalies = []
        disk_snap_path = os.path.join(DATA_DIR, "disk_snapshots.json")
        snapshots = load_json(disk_snap_path, {})

        rc, out, _ = await self._docker(
            "run", "--rm", "-v", "/:/host:ro", "busybox",
            "df", "-BG", "/host", timeout=20,
        )
        if rc != 0:
            return anomalies

        used_gb = None
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[-1] == "/host":
                try:
                    used_gb = int(parts[2].rstrip("G"))
                except ValueError:
                    used_gb = None
                break
        if used_gb is None:
            return anomalies

        now = time.time()
        container = "host"

        if container in snapshots:
            prev = snapshots[container]
            elapsed_days = max((now - prev["time"]) / 86400, 1 / 1440)
            growth = used_gb - prev["used_gb"]
            rate = growth / elapsed_days

            if rate >= rate_gb_per_day:
                severity = "critical" if rate >= rate_gb_per_day * 2 else "warning"
                anomalies.append(
                    self._anomaly(
                        "disk_growth",
                        severity,
                        f"{container} disk growing at {rate:.1f} GB/day "
                        f"(threshold {rate_gb_per_day} GB/day) - currently {used_gb} GB used",
                        vps=container,
                    )
                )

        snapshots[container] = {"used_gb": used_gb, "time": now}
        save_json(disk_snap_path, snapshots)
        return anomalies

    async def detect_high_network(self, gb_threshold=10):
        anomalies = []
        rc, out, _ = await self._docker(
            "stats", "--no-stream", "--format",
            '{{.Name}} {{.NetIO}}', timeout=20,
        )
        if rc != 0:
            return anomalies

        def parse_gb(s):
            s = s.strip().upper()
            if s.endswith("GB"):
                return float(s[:-2])
            if s.endswith("MB"):
                return float(s[:-2]) / 1024
            if s.endswith("KB"):
                return float(s[:-2]) / (1024 * 1024)
            return 0.0

        for line in out.splitlines():
            parts = line.split()
            if len(parts) < 3:
                continue
            name = parts[0]
            net_str = f"{parts[1]} {parts[2]}"
            net_parts = net_str.split("/")
            if len(net_parts) < 2:
                continue
            try:
                inbound = parse_gb(net_parts[0])
                outbound = parse_gb(net_parts[1])
                total = inbound + outbound
            except ValueError:
                continue

            if total >= gb_threshold:
                severity = "critical" if total >= gb_threshold * 2 else "warning"
                anomalies.append(
                    self._anomaly(
                        "high_network",
                        severity,
                        f"{name} network I/O {total:.2f} GB "
                        f"(in {inbound:.2f} / out {outbound:.2f}) - threshold {gb_threshold} GB",
                        vps=name,
                    )
                )
        return anomalies

    async def detect_crash_loop(self, min_restarts=5, window_hours=1):
        anomalies = []
        rc, out, _ = await self._docker("ps", "-a", "--format", "{{.Names}}", timeout=10)
        if rc != 0:
            return anomalies

        containers = [n for n in out.splitlines() if n.strip()]
        for name in containers:
            rc2, info, _ = await self._docker(
                "inspect", "--format",
                "{{.RestartCount}} {{.State.StartedAt}}",
                name,
                timeout=10,
            )
            if rc2 != 0:
                continue

            parts = info.split()
            if len(parts) < 2:
                continue

            try:
                restart_count = int(parts[0])
            except ValueError:
                continue

            try:
                started_str = parts[1].replace("Z", "+00:00")
                started_at = datetime.fromisoformat(started_str)
                if started_at.tzinfo is None:
                    started_at = started_at.replace(tzinfo=__import__("datetime").timezone.utc)
                window_start = datetime.now(tz=__import__("datetime").timezone.utc) - timedelta(hours=window_hours)
                if started_at > window_start:
                    if restart_count >= min_restarts:
                        severity = "critical" if restart_count >= min_restarts * 2 else "warning"
                        anomalies.append(
                            self._anomaly(
                                "crash_loop",
                                severity,
                                f"{name} restarted {restart_count} times since startup "
                                f"(threshold {min_restarts} in {window_hours}h window)",
                                vps=name,
                            )
                        )
            except Exception:
                if restart_count >= min_restarts * 2:
                    anomalies.append(
                        self._anomaly(
                            "crash_loop",
                            "critical",
                            f"{name} has {restart_count} total restarts - possible crash loop",
                            vps=name,
                        )
                    )
        return anomalies

    async def detect_port_abuse(self, max_ports=50):
        anomalies = []
        rc, out, _ = await self._docker(
            "ps", "--format", "{{.Names}} {{.Ports}}", timeout=10,
        )
        if rc != 0:
            return anomalies

        port_count = 0
        for line in out.splitlines():
            port_count += line.count(",")
            port_count += 1 if line.strip() else 0

        total_rc, total_out, _ = await self._docker(
            "ps", "-q", timeout=10,
        )
        if total_rc == 0:
            total_ports = total_out.count("\n") + (1 if total_out else 0)
        else:
            total_ports = port_count

        rc2, out2, _ = await self._docker(
            "ps", "--format", "{{.Names}}", timeout=10,
        )
        if rc2 == 0:
            names = [n.strip() for n in out2.splitlines() if n.strip()]
            port_mapping = {}
            for name in names:
                rc3, ports_out, _ = await self._docker(
                    "port", name, timeout=10,
                )
                if rc3 == 0 and ports_out:
                    port_mapping[name] = len(ports_out.splitlines())
                else:
                    port_mapping[name] = 0

            for name, count in port_mapping.items():
                if count >= max_ports:
                    anomalies.append(
                        self._anomaly(
                            "port_abuse",
                            "warning",
                            f"{name} exposes {count} port mappings (threshold {max_ports})",
                            vps=name,
                        )
                    )
        return anomalies

    async def detect_large_process(self):
        anomalies = []
        try:
            procs = await asyncio.get_running_loop().run_in_executor(
                None, _host_busy_processes
            )
        except Exception:
            return anomalies

        for proc in procs:
            resource_detail = []
            if proc["cpu"] >= 80:
                resource_detail.append(f"CPU {proc['cpu']}%")
            if proc["mem"] >= 80:
                resource_detail.append(f"MEM {proc['mem']}%")
            if not resource_detail:
                continue
            severity = "critical" if (proc["cpu"] >= 95 or proc["mem"] >= 95) else "warning"
            anomalies.append(
                self._anomaly(
                    "large_process",
                    severity,
                    f"PID {proc['pid']} ({proc['name']}): "
                    f"{', '.join(resource_detail)}",
                    vps="host",
                )
            )
        return anomalies

    async def detect_failed_operations(self):
        anomalies = []
        audit_log_path = os.path.join(DATA_DIR, "audit_log.json")
        entries = load_json(audit_log_path, [])

        if not entries:
            return anomalies

        now = datetime.now(timezone.utc).replace(tzinfo=None)
        window = now - timedelta(hours=1)

        failure_counts = defaultdict(lambda: {"count": 0, "operations": defaultdict(int), "latest": None})

        for entry in entries:
            try:
                entry_time = datetime.fromisoformat(entry.get("timestamp", "").replace("Z", "+00:00"))
                if entry_time.tzinfo is None:
                    entry_time = entry_time.replace(tzinfo=__import__("datetime").timezone.utc)
            except (ValueError, AttributeError):
                continue

            if not entry.get("success", True):
                op_type = entry.get("operation", "unknown")
                user = entry.get("user", "system")
                key = f"{user}:{op_type}"
                failure_counts[key]["count"] += 1
                failure_counts[key]["operations"][op_type] += 1
                failure_counts[key]["latest"] = entry.get("timestamp", "unknown")

        for key, data in failure_counts.items():
            if data["count"] >= 3:
                severity = "critical" if data["count"] >= 10 else "warning"
                user, op_type = key.split(":", 1)
                anomalies.append(
                    self._anomaly(
                        "failed_operations",
                        severity,
                        f"{user} had {data['count']} failed {op_type} operations "
                        f"in last hour - latest: {data['latest']}",
                        vps="global",
                    )
                )
        return anomalies

    # ── aggregation ────────────────────────────────────────────────────

    async def scan_all(self):
        detectors = [
            self.detect_high_cpu(),
            self.detect_high_ram(),
            self.detect_disk_growth(),
            self.detect_high_network(),
            self.detect_crash_loop(),
            self.detect_port_abuse(),
            self.detect_large_process(),
            self.detect_failed_operations(),
        ]

        results = await __import__("asyncio").gather(*detectors, return_exceptions=True)
        all_anomalies = []

        for result in results:
            if isinstance(result, Exception):
                logger.error("Detection method failed: %s", result)
                continue
            all_anomalies.extend(result)

        for anomaly in all_anomalies:
            self.record_anomaly(anomaly)

        return all_anomalies

    def get_anomaly_summary(self):
        total = len(self.anomaly_log)
        by_severity = defaultdict(int)
        by_type = defaultdict(int)

        for a in self.anomaly_log:
            by_severity[a.get("severity", "unknown")] += 1
            by_type[a.get("type", "unknown")] += 1

        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=24)).isoformat()
        recent = [a for a in self.anomaly_log if a.get("detected_at", "") >= cutoff]

        return {
            "total": total,
            "by_severity": dict(by_severity),
            "by_type": dict(by_type),
            "recent": len(recent),
            "recent_anomalies": recent,
        }

    def record_anomaly(self, anomaly):
        self.anomaly_log.append(anomaly)

        if len(self.anomaly_log) > 5000:
            self.anomaly_log = self.anomaly_log[-5000:]

        save_json(ANOMALIES_PATH, self.anomaly_log)
        logger.warning("Anomaly recorded: [%s] %s", anomaly["severity"], anomaly["detail"])

        try:
            bus.emit("anomaly_detected", anomaly)
        except Exception as exc:
            logger.error("Failed to emit anomaly event: %s", exc)

    def get_history(self, hours=24, severity=None, vps=None):
        cutoff = (datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=hours)).isoformat()
        results = []

        for a in reversed(self.anomaly_log):
            detected = a.get("detected_at", "")
            if detected < cutoff:
                break
            if severity and a.get("severity") != severity:
                continue
            if vps and a.get("vps") != vps:
                continue
            results.append(a)

        return results

    def get_stats(self):
        by_type = defaultdict(int)
        by_severity = defaultdict(int)

        for a in self.anomaly_log:
            by_type[a.get("type", "unknown")] += 1
            by_severity[a.get("severity", "unknown")] += 1

        return {
            "total": len(self.anomaly_log),
            "by_type": dict(by_type),
            "by_severity": dict(by_severity),
        }

    def get_recommendation(self, anomaly):
        kind = anomaly.get("type", "")
        recommendations = {
            "high_cpu": "Consider upgrading RAM or limiting processes",
            "high_ram": "Increase memory allocation or identify memory leak",
            "disk_growth": "Investigate large files, consider disk upgrade",
            "crash_loop": "Check logs for OOM kills or application errors",
            "port_abuse": "Review port mappings, close unused ports",
            "high_network": "Monitor traffic sources, consider bandwidth upgrade",
            "large_process": "Investigate runaway process, consider container limits",
            "failed_operations": "Review user permissions and operation audit trail",
        }
        return recommendations.get(kind, "Review the anomaly details and check system logs")

    def format_anomaly(self, anomaly):
        severity_emoji = {
            "critical": "🔴",
            "warning": "🟡",
            "info": "🔵",
        }
        emoji = severity_emoji.get(anomaly.get("severity", ""), "⚪")
        kind = anomaly.get("type", "unknown").replace("_", " ").title()
        detail = anomaly.get("detail", "No details available")
        vps = anomaly.get("vps", "unknown")
        detected = anomaly.get("detected_at", "unknown")
        recommendation = self.get_recommendation(anomaly)

        lines = [
            f"{emoji} **{kind}** - `{vps}`",
            f"> {detail}",
            f"**Detected:** {detected}",
            f"**Recommendation:** {recommendation}",
        ]
        return "\n".join(lines)
