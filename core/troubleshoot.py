import os
import asyncio
import logging
import time
import json
import subprocess
import shutil

try:
    import psutil
except ImportError:
    psutil = None

from core.services import metrics, bus, load_json, save_json, DATA_DIR, BASE_DIR

log = logging.getLogger("troubleshoot")

try:
    import aiohttp
except ImportError:
    aiohttp = None

BACKUP_DIR = os.path.join(DATA_DIR, "backups")
DATA_FILES = [
    "servers.json",
    "users.json",
    "templates.json",
    "deployments.json",
    "settings.json",
]


async def _docker(*args, timeout=15):
    cmd = ["docker"] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "docker not found"
    except Exception as exc:
        return -1, "", str(exc)


class DiagnosticCheck:
    __slots__ = (
        "name",
        "status",
        "detail",
        "icon",
        "recovery_action",
        "recovery_func",
    )

    ICONS = {
        "ok": "\u2705",
        "warning": "\u26a0\ufe0f",
        "critical": "\U0001f534",
        "error": "\u274c",
    }

    def __init__(self, name, status, detail, recovery_action=None, recovery_func=None):
        self.name = name
        self.status = status
        self.detail = detail
        self.icon = self.ICONS.get(status, "\u274c")
        self.recovery_action = recovery_action
        self.recovery_func = recovery_func

    def to_dict(self):
        return {
            "name": self.name,
            "status": self.status,
            "detail": self.detail,
            "icon": self.icon,
            "recovery_action": self.recovery_action,
        }


class Troubleshooter:
    def __init__(self, bot):
        self.bot = bot

    async def check_bot_connection(self):
        name = "Bot Connection"
        if not self.bot.is_ready():
            return DiagnosticCheck(
                name,
                "critical",
                "Bot is not ready / not logged in.",
                recovery_action=None,
            )
        latency_ms = round(self.bot.latency * 1000, 1)
        if latency_ms > 5000:
            return DiagnosticCheck(
                name,
                "warning",
                f"High latency: {latency_ms}ms",
            )
        if latency_ms > 2000:
            return DiagnosticCheck(
                name,
                "warning",
                f"Elevated latency: {latency_ms}ms",
            )
        return DiagnosticCheck(name, "ok", f"Latency: {latency_ms}ms")

    async def check_discord_gateway(self):
        name = "Discord Gateway"
        ws = getattr(self.bot, "ws", None)
        if ws is None:
            return DiagnosticCheck(
                name,
                "critical",
                "WebSocket object is None.",
            )
        heartbeat_ack = getattr(ws, "_discord_ws_heartbeat_ack", None)
        if heartbeat_ack is False:
            return DiagnosticCheck(
                name,
                "warning",
                "Heartbeat ACK not received recently.",
            )
        heartbeat_latency = getattr(ws, "heartbeat_acknowledged", None)
        if heartbeat_latency is not None and heartbeat_latency > 5:
            return DiagnosticCheck(
                name,
                "warning",
                f"Heartbeat RTT elevated: {heartbeat_latency:.1f}s",
            )
        return DiagnosticCheck(name, "ok", "Gateway connected, heartbeat OK.")

    async def check_database(self):
        name = "Database (JSON)"
        issues = []
        for fname in DATA_FILES:
            fpath = os.path.join(DATA_DIR, fname)
            if not os.path.isfile(fpath):
                issues.append(f"{fname}: missing")
                continue
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    json.load(f)
            except json.JSONDecodeError as exc:
                issues.append(f"{fname}: corrupt ({exc})")
            except Exception as exc:
                issues.append(f"{fname}: unreadable ({exc})")
        if not issues:
            return DiagnosticCheck(name, "ok", f"All {len(DATA_FILES)} data files valid.")
        if len(issues) == len(DATA_FILES):
            return DiagnosticCheck(
                name,
                "critical",
                "All data files missing or corrupt.",
                recovery_action="recover_database",
                recovery_func=self.recover_database,
            )
        return DiagnosticCheck(
            name,
            "warning",
            f"Issues: {'; '.join(issues)}",
            recovery_action="recover_database",
            recovery_func=self.recover_database,
        )

    async def check_docker(self):
        name = "Docker"
        rc, stdout, stderr = await _docker("info", timeout=10)
        if rc != 0:
            if "not found" in stderr.lower():
                return DiagnosticCheck(
                    name,
                    "error",
                    "Docker is not installed.",
                )
            return DiagnosticCheck(
                name,
                "critical",
                f"Docker daemon issue: {stderr[:200]}",
                recovery_action="recover_docker",
                recovery_func=self.recover_docker,
            )
        rc2, stdout2, _ = await _docker("ps", "-q", timeout=10)
        if rc2 != 0:
            return DiagnosticCheck(
                name,
                "warning",
                "Docker info OK but 'docker ps' failed.",
                recovery_action="recover_docker",
                recovery_func=self.recover_docker,
            )
        running = len(stdout2.strip().splitlines()) if stdout2.strip() else 0
        return DiagnosticCheck(name, "ok", f"Docker OK. {running} container(s) running.")

    async def check_vps_containers(self):
        name = "VPS Containers"
        rc, stdout, stderr = await _docker(
            "ps", "-a", "--format", "{{.Names}}\t{{.Status}}\t{{.Image}}", timeout=10
        )
        if rc != 0:
            return DiagnosticCheck(
                name,
                "error",
                f"Cannot list containers: {stderr[:200]}",
            )
        if not stdout.strip():
            return DiagnosticCheck(name, "ok", "No containers found (expected if none deployed).")
        stopped = []
        running = 0
        unhealthy = []
        for line in stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            cname, cstatus = parts[0], parts[1]
            if "Up" in cstatus:
                running += 1
                if "unhealthy" in cstatus.lower():
                    unhealthy.append(cname)
            else:
                stopped.append(cname)
        details = []
        if running:
            details.append(f"{running} running")
        if stopped:
            details.append(f"{len(stopped)} stopped: {', '.join(stopped)}")
        if unhealthy:
            details.append(f"{len(unhealthy)} unhealthy: {', '.join(unhealthy)}")
        detail_str = "; ".join(details)
        if stopped or unhealthy:
            severity = "warning" if not unhealthy else "critical"
            return DiagnosticCheck(
                name,
                severity,
                detail_str,
                recovery_action="recover_container",
                recovery_func=self.recover_container,
            )
        return DiagnosticCheck(name, "ok", detail_str)

    async def check_ssh(self):
        name = "SSH / tmate"
        tmate_bin = shutil.which("tmate")
        sshd_bin = shutil.which("sshd")
        sshd_service = False
        try:
            proc = await asyncio.create_subprocess_exec(
                "systemctl", "is-active", "sshd",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            sshd_service = out.decode().strip() == "active"
        except Exception:
            pass
        if tmate_bin or sshd_bin or sshd_service:
            parts = []
            if tmate_bin:
                parts.append("tmate available")
            if sshd_bin:
                parts.append("sshd binary found")
            if sshd_service:
                parts.append("sshd service active")
            return DiagnosticCheck(name, "ok", "; ".join(parts))
        return DiagnosticCheck(
            name,
            "warning",
            "No SSH/tmate found. Remote access may be limited.",
        )

    async def check_network(self):
        name = "Network"
        if aiohttp is None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python", "-c",
                    "import urllib.request; urllib.request.urlopen('https://www.google.com', timeout=5)",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                rc = await asyncio.wait_for(proc.wait(), timeout=8)
                if rc == 0:
                    return DiagnosticCheck(name, "ok", "Internet reachable (urllib).")
                return DiagnosticCheck(
                    name,
                    "critical",
                    "Cannot reach internet.",
                    recovery_action="recover_network",
                )
            except Exception as exc:
                return DiagnosticCheck(
                    name,
                    "critical",
                    f"Network check failed: {exc}",
                )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://www.google.com",
                    timeout=aiohttp.ClientTimeout(total=8),
                ) as resp:
                    if resp.status == 200:
                        return DiagnosticCheck(name, "ok", "Internet reachable (aiohttp).")
                    return DiagnosticCheck(
                        name,
                        "warning",
                        f"Google returned status {resp.status}.",
                    )
        except Exception as exc:
            return DiagnosticCheck(
                name,
                "critical",
                f"Cannot reach internet: {exc}",
            )

    async def check_cpu(self):
        name = "CPU"
        if psutil is None:
            return DiagnosticCheck(name, "ok", "psutil not installed; skipping CPU check.")
        try:
            usage = psutil.cpu_percent(interval=1)
        except Exception as exc:
            return DiagnosticCheck(name, "error", f"Cannot read CPU: {exc}")
        if usage > 90:
            return DiagnosticCheck(
                name,
                "critical",
                f"CPU usage: {usage}%",
                recovery_action=None,
            )
        if usage > 75:
            return DiagnosticCheck(name, "warning", f"CPU usage: {usage}%")
        return DiagnosticCheck(name, "ok", f"CPU usage: {usage}%")

    async def check_ram(self):
        name = "RAM"
        if psutil is None:
            return DiagnosticCheck(name, "ok", "psutil not installed; skipping RAM check.")
        try:
            mem = psutil.virtual_memory()
        except Exception as exc:
            return DiagnosticCheck(name, "error", f"Cannot read RAM: {exc}")
        pct = mem.percent
        used_gb = mem.used / (1024 ** 3)
        total_gb = mem.total / (1024 ** 3)
        if pct > 90:
            return DiagnosticCheck(
                name,
                "critical",
                f"RAM usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)",
            )
        if pct > 75:
            return DiagnosticCheck(
                name,
                "warning",
                f"RAM usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)",
            )
        return DiagnosticCheck(
            name, "ok", f"RAM usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)"
        )

    async def check_disk(self):
        name = "Disk"
        if psutil is None:
            return DiagnosticCheck(name, "ok", "psutil not installed; skipping disk check.")
        try:
            usage = psutil.disk_usage("/")
        except Exception:
            try:
                usage = psutil.disk_usage("C:\\")
            except Exception as exc:
                return DiagnosticCheck(name, "error", f"Cannot read disk: {exc}")
        pct = usage.percent
        used_gb = usage.used / (1024 ** 3)
        total_gb = usage.total / (1024 ** 3)
        if pct > 95:
            return DiagnosticCheck(
                name,
                "critical",
                f"Disk usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)",
                recovery_action="recover_disk",
            )
        if pct > 85:
            return DiagnosticCheck(
                name,
                "warning",
                f"Disk usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)",
            )
        return DiagnosticCheck(
            name, "ok", f"Disk usage: {pct}% ({used_gb:.1f}/{total_gb:.1f} GB)"
        )

    async def check_api(self):
        name = "API Health"
        if aiohttp is None:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "python", "-c",
                    "import urllib.request; urllib.request.urlopen('http://localhost:8000/health', timeout=3)",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                rc = await asyncio.wait_for(proc.wait(), timeout=6)
                if rc == 0:
                    return DiagnosticCheck(name, "ok", "API responding on :8000.")
                return DiagnosticCheck(
                    name,
                    "critical",
                    "API not responding on :8000.",
                    recovery_action="recover_api",
                    recovery_func=self.recover_api,
                )
            except Exception as exc:
                return DiagnosticCheck(
                    name, "error", f"API check failed: {exc}"
                )
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://localhost:8000/health",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        return DiagnosticCheck(name, "ok", "API responding on :8000.")
                    return DiagnosticCheck(
                        name,
                        "warning",
                        f"API returned status {resp.status}.",
                    )
        except Exception:
            return DiagnosticCheck(
                name,
                "critical",
                "API not responding on :8000.",
                recovery_action="recover_api",
                recovery_func=self.recover_api,
            )

    async def check_background_tasks(self):
        name = "Background Tasks"
        tasks = asyncio.all_tasks()
        bot_tasks = [
            t for t in tasks
            if not t.done() and not t.cancelled()
        ]
        if not bot_tasks:
            return DiagnosticCheck(
                name,
                "warning",
                "No active background tasks found.",
                recovery_action="recover_background_tasks",
                recovery_func=self.recover_background_tasks,
            )
        running = [t.get_name() for t in bot_tasks]
        return DiagnosticCheck(
            name, "ok", f"{len(running)} task(s) active: {', '.join(running[:5])}"
        )

    async def check_backup_system(self):
        name = "Backup System"
        if not os.path.isdir(BACKUP_DIR):
            try:
                os.makedirs(BACKUP_DIR, exist_ok=True)
            except Exception as exc:
                return DiagnosticCheck(
                    name,
                    "critical",
                    f"Cannot create backup dir: {exc}",
                )
        try:
            test_file = os.path.join(BACKUP_DIR, ".write_test")
            with open(test_file, "w") as f:
                f.write("ok")
            os.remove(test_file)
        except Exception as exc:
            return DiagnosticCheck(
                name,
                "critical",
                f"Backup dir not writable: {exc}",
            )
        existing = [f for f in os.listdir(BACKUP_DIR) if f.endswith(".json")]
        return DiagnosticCheck(
            name, "ok", f"Backup dir OK. {len(existing)} backup file(s)."
        )

    async def run_all_checks(self):
        check_methods = [
            self.check_bot_connection,
            self.check_discord_gateway,
            self.check_database,
            self.check_docker,
            self.check_vps_containers,
            self.check_ssh,
            self.check_network,
            self.check_cpu,
            self.check_ram,
            self.check_disk,
            self.check_api,
            self.check_background_tasks,
            self.check_backup_system,
        ]
        results = []
        for method in check_methods:
            try:
                result = await method()
                results.append(result)
            except Exception as exc:
                results.append(
                    DiagnosticCheck(
                        method.__name__,
                        "error",
                        f"Check crashed: {exc}",
                    )
                )
                log.exception("Diagnostic check %s failed", method.__name__)
        return results

    def get_overall_status(self, checks):
        statuses = [c.status for c in checks]
        if "critical" in statuses or "error" in statuses:
            return "critical"
        if "warning" in statuses:
            return "degraded"
        return "healthy"

    def get_summary_embed_fields(self, checks):
        fields = []
        overall = self.get_overall_status(checks)
        status_icons = {
            "healthy": "\u2705 Healthy",
            "degraded": "\u26a0\ufe0f Degraded",
            "critical": "\U0001f534 Critical",
        }
        fields.append({
            "name": "Overall Status",
            "value": status_icons.get(overall, overall),
            "inline": False,
        })
        for check in checks:
            value = check.detail
            if check.recovery_action and check.status in ("critical", "warning", "error"):
                value += f"\n*Recovery available: {check.recovery_action}*"
            fields.append({
                "name": f"{check.icon} {check.name}",
                "value": value[:1024],
                "inline": True,
            })
        return fields

    # --- Recovery Methods ---

    async def recover_docker(self):
        actions = []
        rc, stdout, _ = await _docker("--version", timeout=5)
        if "not found" in stdout.lower() or rc != 0:
            return {
                "success": False,
                "message": "Docker is not installed. Cannot recover.",
                "actions_taken": actions,
            }
        actions.append("Attempting systemctl restart docker...")
        for cmd in (
            ["systemctl", "restart", "docker"],
            ["service", "docker", "restart"],
        ):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=20)
                if proc.returncode == 0:
                    actions.append(f"Restarted docker via {' '.join(cmd)}")
                    break
            except Exception as exc:
                actions.append(f"{' '.join(cmd)} failed: {exc}")
        await asyncio.sleep(2)
        rc2, _, _ = await _docker("ps", timeout=10)
        if rc2 == 0:
            return {
                "success": True,
                "message": "Docker restarted and responding.",
                "actions_taken": actions,
            }
        return {
            "success": False,
            "message": "Docker restart attempted but not responding.",
            "actions_taken": actions,
        }

    async def recover_container(self, container_name=None):
        actions = []
        if container_name is None:
            rc, stdout, _ = await _docker(
                "ps", "-a", "--filter", "status=exited",
                "--format", "{{.Names}}", timeout=10,
            )
            if rc != 0 or not stdout.strip():
                return {
                    "success": False,
                    "message": "No stopped containers found or cannot list.",
                    "actions_taken": actions,
                }
            candidates = stdout.strip().splitlines()
            container_name = candidates[0]
            actions.append(f"Auto-selected stopped container: {container_name}")
        actions.append(f"Starting container {container_name}...")
        rc, stdout, stderr = await _docker("start", container_name, timeout=15)
        if rc != 0:
            return {
                "success": False,
                "message": f"Failed to start {container_name}: {stderr[:200]}",
                "actions_taken": actions,
            }
        actions.append(f"Container {container_name} started")
        await asyncio.sleep(2)
        rc2, stdout2, _ = await _docker(
            "inspect", "--format", "{{.State.Running}}", container_name, timeout=10
        )
        if rc2 == 0 and stdout2.strip() == "true":
            return {
                "success": True,
                "message": f"Container {container_name} is now running.",
                "actions_taken": actions,
            }
        return {
            "success": False,
            "message": f"Container {container_name} start issued but not confirmed running.",
            "actions_taken": actions,
        }

    async def recover_database(self):
        actions = []
        recovered = 0
        os.makedirs(BACKUP_DIR, exist_ok=True)
        for fname in DATA_FILES:
            fpath = os.path.join(DATA_DIR, fname)
            valid = False
            if os.path.isfile(fpath):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        json.load(f)
                    valid = True
                except (json.JSONDecodeError, Exception):
                    pass
            if valid:
                continue
            backup_files = []
            for bname in sorted(os.listdir(BACKUP_DIR), reverse=True):
                if fname.replace(".json", "") in bname and bname.endswith(".json"):
                    backup_files.append(bname)
            restored = False
            for bname in backup_files:
                bpath = os.path.join(BACKUP_DIR, bname)
                try:
                    with open(bpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    actions.append(f"Restoring {fname} from backup {bname}")
                    if os.path.isfile(fpath):
                        corrupt_backup = fpath + ".corrupt"
                        try:
                            shutil.move(fpath, corrupt_backup)
                            actions.append(f"Moved corrupt {fname} to {corrupt_backup}")
                        except Exception:
                            pass
                    save_json(fname, data)
                    restored = True
                    recovered += 1
                    break
                except Exception as exc:
                    actions.append(f"Backup {bname} unusable: {exc}")
                    continue
            if not restored:
                if not os.path.isfile(fpath):
                    save_json(fname, {})
                    actions.append(f"Created empty {fname} (no backup available)")
                    recovered += 1
        if recovered > 0:
            return {
                "success": True,
                "message": f"Recovered {recovered} database file(s).",
                "actions_taken": actions,
            }
        return {
            "success": True,
            "message": "All database files already valid.",
            "actions_taken": actions,
        }

    async def recover_api(self):
        return {
            "success": False,
            "message": "API must be restarted externally. Use systemctl or supervisorctl.",
            "actions_taken": ["Reported: API restart required server-side"],
        }

    async def recover_background_tasks(self):
        actions = []
        recovered = 0
        loop = asyncio.get_running_loop()
        for task_name, task_obj in self.bot._custom_tasks.items():
            if task_obj.done() or task_obj.cancelled():
                actions.append(f"Task {task_name} is dead, restarting...")
                try:
                    if hasattr(self.bot, f"_restart_{task_name}"):
                        restart_fn = getattr(self.bot, f"_restart_{task_name}")
                        await restart_fn()
                        recovered += 1
                        actions.append(f"Restarted {task_name}")
                except Exception as exc:
                    actions.append(f"Failed to restart {task_name}: {exc}")
        if recovered > 0:
            return {
                "success": True,
                "message": f"Restarted {recovered} background task(s).",
                "actions_taken": actions,
            }
        return {
            "success": False,
            "message": "No restartable background tasks found.",
            "actions_taken": actions,
        }


class AutoRecovery:
    def __init__(self, bot, max_retries=3, cooldown_seconds=60, timeout_seconds=30):
        self.bot = bot
        self.max_retries = max_retries
        self.cooldown_seconds = cooldown_seconds
        self.timeout_seconds = timeout_seconds
        self._retries = {}
        self._cooldowns = {}

    async def attempt_recovery(self, check_result):
        action = check_result.recovery_action
        if not action:
            return {
                "success": False,
                "message": f"No recovery action defined for '{check_result.name}'.",
                "attempts": 0,
                "actions_taken": [],
            }
        service = check_result.name
        now = time.time()
        retries = self._retries.get(service, 0)
        last_attempt = self._cooldowns.get(service, 0)
        if retries >= self.max_retries:
            msg = (
                f"Recovery for '{service}' exhausted "
                f"({retries}/{self.max_retries} attempts)."
            )
            log.warning(msg)
            return {
                "success": False,
                "message": msg,
                "attempts": retries,
                "actions_taken": [],
            }
        elapsed = now - last_attempt
        if elapsed < self.cooldown_seconds:
            remaining = round(self.cooldown_seconds - elapsed, 1)
            msg = f"Cooldown active for '{service}'. Retry in {remaining}s."
            log.info(msg)
            return {
                "success": False,
                "message": msg,
                "attempts": retries,
                "actions_taken": [],
            }
        recovery_func = check_result.recovery_func
        if recovery_func is None:
            return {
                "success": False,
                "message": f"No recovery function for '{action}'.",
                "attempts": retries,
                "actions_taken": [],
            }
        bus.emit("recovery:attempted", {
            "service": service,
            "action": action,
            "attempt": retries + 1,
        })
        try:
            result = await asyncio.wait_for(
                recovery_func(), timeout=self.timeout_seconds
            )
        except asyncio.TimeoutError:
            result = {
                "success": False,
                "message": f"Recovery for '{service}' timed out ({self.timeout_seconds}s).",
                "actions_taken": [],
            }
        except Exception as exc:
            log.exception("Recovery for %s raised exception", service)
            result = {
                "success": False,
                "message": f"Recovery error: {exc}",
                "actions_taken": [],
            }
        self._cooldowns[service] = time.time()
        attempts = retries + 1
        self._retries[service] = attempts
        if result.get("success"):
            self._retries[service] = 0
            bus.emit("recovery:succeeded", {
                "service": service,
                "action": action,
                "attempt": attempts,
                "result": result,
            })
        else:
            bus.emit("recovery:failed", {
                "service": service,
                "action": action,
                "attempt": attempts,
                "result": result,
            })
        result["attempts"] = attempts
        return result

    def reset_counters(self):
        self._retries.clear()
        self._cooldowns.clear()
        log.info("Recovery counters reset.")

    def get_recovery_status(self):
        status = {}
        for service in set(list(self._retries.keys()) + list(self._cooldowns.keys())):
            retries = self._retries.get(service, 0)
            last_attempt_ts = self._cooldowns.get(service, 0)
            if last_attempt_ts:
                last_attempt = time.strftime(
                    "%Y-%m-%d %H:%M:%S", time.localtime(last_attempt_ts)
                )
            else:
                last_attempt = None
            exhausted = retries >= self.max_retries
            cooldown_remaining = max(
                0, self.cooldown_seconds - (time.time() - last_attempt_ts)
            )
            status[service] = {
                "retries": retries,
                "max_retries": self.max_retries,
                "last_attempt": last_attempt,
                "cooldown_remaining": round(cooldown_remaining, 1),
                "exhausted": exhausted,
            }
        return status
