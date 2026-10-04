"""Full System Scan - One-click comprehensive system scan for Turtle Nodes."""
import asyncio
import os
import time
import json
import logging
import shutil
import subprocess

from core.services import metrics, bus, load_json, save_json, DATA_DIR, BASE_DIR, BACKUP_DIR, CONFIG_PATH

logger = logging.getLogger("turtle.full_scan")

_ICON_OK = "\u2705"
_ICON_WARN = "\u26a0\ufe0f"
_ICON_CRIT = "\U0001f534"
_ICON_ERR = "\u274c"

DB_FILES = [
    "vps_accounts.json",
    "vps_config.json",
    "deployments.json",
    "orders.json",
]


async def _docker(*args, timeout=15):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        return -1, "", "timeout"
    except FileNotFoundError:
        return -1, "", "docker not found"
    except Exception as e:
        return -1, "", str(e)


class FullSystemScan:
    def __init__(self, bot):
        self.bot = bot
        self.results = []
        self.start_time = time.time()

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_discord(self):
        try:
            if not hasattr(self.bot, "is_ready") or not self.bot.is_ready():
                return {"name": "Discord", "status": "error", "detail": "Bot not connected", "icon": _ICON_ERR, "recommendation": "Restart the bot to re-establish Discord connection."}
            latency = round(self.bot.latency * 1000, 1) if self.bot.latency else 0
            guilds = len(getattr(self.bot, "guilds", []))
            status = "ok"
            rec = None
            if latency > 500:
                status = "warning"
                rec = "High latency detected. Check network or Discord status."
            return {"name": "Discord", "status": status, "detail": f"Connected - {guilds} guild(s), {latency}ms", "icon": _ICON_OK if status == "ok" else _ICON_WARN, "recommendation": rec}
        except Exception as e:
            return {"name": "Discord", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Restart the bot process."}

    async def _check_database(self):
        issues = []
        ok_count = 0
        for fname in DB_FILES:
            fpath = os.path.join(DATA_DIR, fname)
            if not os.path.exists(fpath):
                issues.append(f"{fname}: missing")
                continue
            data = load_json(fpath)
            if data is None:
                issues.append(f"{fname}: corrupt JSON")
                continue
            ok_count += 1
        if issues:
            return {"name": "Database", "status": "warning", "detail": f"{ok_count}/{len(DB_FILES)} OK - {', '.join(issues[:3])}", "icon": _ICON_WARN, "recommendation": "Restore missing/corrupt data files from backup."}
        return {"name": "Database", "status": "ok", "detail": f"{ok_count}/{len(DB_FILES)} files valid", "icon": _ICON_OK, "recommendation": None}

    async def _check_docker(self):
        code, stdout, stderr = await _docker("info", timeout=10)
        if code == -1 and "not found" in stderr:
            return {"name": "Docker", "status": "error", "detail": "Docker binary not found", "icon": _ICON_ERR, "recommendation": "Install Docker on this VPS."}
        if code != 0:
            err = stderr.strip()[:120] if stderr else "daemon not responding"
            return {"name": "Docker", "status": "error", "detail": f"Docker daemon error: {err}", "icon": _ICON_ERR, "recommendation": "Start Docker daemon: systemctl start docker"}
        return {"name": "Docker", "status": "ok", "detail": "Docker daemon running", "icon": _ICON_OK, "recommendation": None}

    async def _check_nodes(self):
        config = load_json(CONFIG_PATH, {})
        nodes = config.get("nodes", [])
        if not nodes:
            return {"name": "Nodes", "status": "warning", "detail": "No nodes configured", "icon": _ICON_WARN, "recommendation": "Add at least one node in config.json."}
        unreachable = []
        for node in nodes:
            host = node.get("host", "")
            name = node.get("name", host)
            try:
                proc = await asyncio.create_subprocess_exec(
                    "ping", "-n", "1", "-w", "2000", host,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode != 0:
                    unreachable.append(name)
            except Exception:
                unreachable.append(name)
        if unreachable:
            return {"name": "Nodes", "status": "warning", "detail": f"{len(unreachable)}/{len(nodes)} unreachable: {', '.join(unreachable[:3])}", "icon": _ICON_WARN, "recommendation": f"Check network/firewall for: {', '.join(unreachable[:3])}"}
        return {"name": "Nodes", "status": "ok", "detail": f"{len(nodes)} node(s) reachable", "icon": _ICON_OK, "recommendation": None}

    async def _check_vps_containers(self):
        code, stdout, stderr = await _docker("ps", "-a", "--format", "{{.Names}}|{{.Status}}", timeout=10)
        if code != 0:
            return {"name": "VPS Containers", "status": "error", "detail": f"docker ps failed: {stderr.strip()[:100]}", "icon": _ICON_ERR, "recommendation": "Verify Docker daemon is running."}
        lines = [l.strip() for l in stdout.splitlines() if l.strip()]
        stopped = []
        total = len(lines)
        for line in lines:
            parts = line.split("|", 1)
            name = parts[0]
            status = parts[1] if len(parts) > 1 else ""
            if "Up" not in status:
                stopped.append(name)
        if stopped:
            return {"name": "VPS Containers", "status": "warning", "detail": f"{total} total, {len(stopped)} stopped: {', '.join(stopped[:3])}", "icon": _ICON_WARN, "recommendation": f"Restart stopped containers: {', '.join(stopped[:3])}"}
        return {"name": "VPS Containers", "status": "ok", "detail": f"{total} container(s) running", "icon": _ICON_OK, "recommendation": None}

    async def _check_api(self):
        try:
            import urllib.request
            req = urllib.request.Request("http://127.0.0.1:8000/health", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                code = resp.getcode()
                if code == 200:
                    return {"name": "API", "status": "ok", "detail": f"HTTP {code} on :8000", "icon": _ICON_OK, "recommendation": None}
                return {"name": "API", "status": "warning", "detail": f"HTTP {code}", "icon": _ICON_WARN, "recommendation": "Check API server configuration."}
        except ConnectionRefusedError:
            return {"name": "API", "status": "warning", "detail": "API not listening on :8000", "icon": _ICON_WARN, "recommendation": "Start the API server if needed."}
        except Exception as e:
            return {"name": "API", "status": "error", "detail": str(e)[:120], "icon": _ICON_ERR, "recommendation": "Verify API server is running."}

    async def _check_network(self):
        try:
            import urllib.request
            req = urllib.request.Request("https://www.google.com", method="HEAD")
            with urllib.request.urlopen(req, timeout=5) as resp:
                code = resp.getcode()
                return {"name": "Network", "status": "ok", "detail": f"Outbound HTTP OK (code {code})", "icon": _ICON_OK, "recommendation": None}
        except Exception:
            pass
        try:
            import socket
            loop = asyncio.get_running_loop()
            def _dns():
                socket.getaddrinfo("1.1.1.1", 53)
            await loop.run_in_executor(None, _dns)
            return {"name": "Network", "status": "ok", "detail": "DNS resolution OK", "icon": _ICON_OK, "recommendation": None}
        except Exception as e:
            return {"name": "Network", "status": "error", "detail": f"No outbound connectivity: {e}", "icon": _ICON_ERR, "recommendation": "Check firewall rules and network config."}

    def _check_filesystem(self):
        try:
            usage = shutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            used_pct = round((usage.used / usage.total) * 100, 1)
            test_path = os.path.join(BASE_DIR, ".write_test")
            can_write = False
            try:
                with open(test_path, "w") as f:
                    f.write("ok")
                os.remove(test_path)
                can_write = True
            except (OSError, PermissionError):
                pass
            if used_pct >= 95:
                return {"name": "Filesystem", "status": "critical", "detail": f"{free_gb:.1f}GB free / {total_gb:.1f}GB ({used_pct}% used), writable={can_write}", "icon": _ICON_CRIT, "recommendation": "Disk critically full! Delete old logs/backups immediately."}
            if used_pct >= 85 or not can_write:
                return {"name": "Filesystem", "status": "warning", "detail": f"{free_gb:.1f}GB free / {total_gb:.1f}GB ({used_pct}% used), writable={can_write}", "icon": _ICON_WARN, "recommendation": "Free disk space or check permissions."}
            return {"name": "Filesystem", "status": "ok", "detail": f"{free_gb:.1f}GB free / {total_gb:.1f}GB ({used_pct}% used)", "icon": _ICON_OK, "recommendation": None}
        except Exception as e:
            return {"name": "Filesystem", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Check filesystem health."}

    def _check_backups(self):
        if not os.path.isdir(BACKUP_DIR):
            return {"name": "Backups", "status": "warning", "detail": "Backup directory missing", "icon": _ICON_WARN, "recommendation": "Create backups directory and configure automated backups."}
        files = []
        now = time.time()
        for fname in os.listdir(BACKUP_DIR):
            fpath = os.path.join(BACKUP_DIR, fname)
            if os.path.isfile(fpath):
                try:
                    age = now - os.path.getmtime(fpath)
                    files.append({"name": fname, "age_hours": round(age / 3600, 1)})
                except OSError:
                    continue
        count = len(files)
        if count == 0:
            return {"name": "Backups", "status": "warning", "detail": "No backup files found", "icon": _ICON_WARN, "recommendation": "Create an initial backup immediately."}
        newest = min(f["age_hours"] for f in files)
        if newest > 168:
            return {"name": "Backups", "status": "warning", "detail": f"{count} file(s), newest is {newest:.0f}h old", "icon": _ICON_WARN, "recommendation": "Backups are stale - run a fresh backup."}
        return {"name": "Backups", "status": "ok", "detail": f"{count} file(s), newest {newest:.1f}h ago", "icon": _ICON_OK, "recommendation": None}

    def _check_billing(self):
        billing_path = os.path.join(DATA_DIR, "billing.json")
        billing = load_json(billing_path, None)
        if billing is None:
            return {"name": "Billing", "status": "warning", "detail": "billing.json missing or invalid", "icon": _ICON_WARN, "recommendation": "Initialize billing.json with default structure."}
        coupons = billing.get("coupons", [])
        invalid = [c for c in coupons if isinstance(c, dict) and not c.get("code")]
        if invalid:
            return {"name": "Billing", "status": "warning", "detail": f"Billing OK, {len(invalid)} coupon(s) invalid", "icon": _ICON_WARN, "recommendation": "Remove or fix invalid coupon entries."}
        return {"name": "Billing", "status": "ok", "detail": f"Billing config valid, {len(coupons)} coupon(s)", "icon": _ICON_OK, "recommendation": None}

    def _check_automation(self):
        jobs_path = os.path.join(DATA_DIR, "automation_jobs.json")
        jobs = load_json(jobs_path, [])
        if not jobs:
            return {"name": "Automation", "status": "warning", "detail": "No scheduler jobs configured", "icon": _ICON_WARN, "recommendation": "Configure automation jobs for scheduled tasks."}
        enabled = [j for j in jobs if j.get("enabled", True)]
        failing = [j for j in enabled if j.get("fail_count", 0) > 3]
        if failing:
            names = [j.get("name", "unnamed") for j in failing[:3]]
            return {"name": "Automation", "status": "warning", "detail": f"{len(enabled)}/{len(jobs)} enabled, {len(failing)} failing: {', '.join(names)}", "icon": _ICON_WARN, "recommendation": f"Fix failing jobs: {', '.join(names)}"}
        return {"name": "Automation", "status": "ok", "detail": f"{len(enabled)}/{len(jobs)} jobs enabled", "icon": _ICON_OK, "recommendation": None}

    def _check_dependencies(self):
        deps = {"discord.py": False, "psutil": False}
        versions = {}
        for pkg in deps:
            try:
                mod = __import__(pkg.replace(".", "_") if "." in pkg else pkg)
                deps[pkg] = True
                versions[pkg] = getattr(mod, "__version__", getattr(mod, "version", "?"))
            except ImportError:
                try:
                    mod = __import__(pkg.replace(".", "_"))
                    deps[pkg] = True
                    versions[pkg] = getattr(mod, "__version__", "?")
                except ImportError:
                    pass
        missing = [p for p, ok in deps.items() if not ok]
        if missing:
            return {"name": "Dependencies", "status": "error", "detail": f"Missing: {', '.join(missing)}", "icon": _ICON_ERR, "recommendation": f"Install: pip install {' '.join(missing)}"}
        detail = ", ".join(f"{p} {v}" for p, v in versions.items())
        return {"name": "Dependencies", "status": "ok", "detail": detail, "icon": _ICON_OK, "recommendation": None}

    def _check_permissions(self):
        admin_path = os.path.join(DATA_DIR, "admin_levels.json")
        admins = load_json(admin_path, None)
        if admins is None:
            return {"name": "Permissions", "status": "warning", "detail": "admin_levels.json missing", "icon": _ICON_WARN, "recommendation": "Create admin_levels.json with at least one admin."}
        if isinstance(admins, dict):
            main_admin = admins.get("main") or admins.get("owner")
            levels = admins.get("levels", admins)
        else:
            main_admin = None
            levels = admins
        if not main_admin and not levels:
            return {"name": "Permissions", "status": "warning", "detail": "No admin configured", "icon": _ICON_WARN, "recommendation": "Set a main admin in admin_levels.json."}
        return {"name": "Permissions", "status": "ok", "detail": "Admin config valid", "icon": _ICON_OK, "recommendation": None}

    def _check_storage(self):
        try:
            total_size = 0
            file_count = 0
            for dirpath, dirnames, filenames in os.walk(DATA_DIR):
                for fname in filenames:
                    fpath = os.path.join(dirpath, fname)
                    try:
                        total_size += os.path.getsize(fpath)
                        file_count += 1
                    except OSError:
                        continue
            size_mb = round(total_size / (1024 * 1024), 2)
            if size_mb > 500:
                return {"name": "Storage", "status": "warning", "detail": f"{size_mb:.1f}MB across {file_count} files", "icon": _ICON_WARN, "recommendation": "Consider archiving old data to reduce storage."}
            return {"name": "Storage", "status": "ok", "detail": f"{size_mb:.1f}MB across {file_count} files", "icon": _ICON_OK, "recommendation": None}
        except Exception as e:
            return {"name": "Storage", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Check DATA_DIR accessibility."}

    def _check_cpu(self):
        try:
            import psutil
            pct = psutil.cpu_percent(interval=0.5)
            cores = psutil.cpu_count(logical=True) or 0
            if pct >= 95:
                return {"name": "CPU", "status": "critical", "detail": f"{pct}% usage, {cores} cores", "icon": _ICON_CRIT, "recommendation": "CPU critically high - check for runaway processes."}
            if pct >= 80:
                return {"name": "CPU", "status": "warning", "detail": f"{pct}% usage, {cores} cores", "icon": _ICON_WARN, "recommendation": "CPU usage elevated - monitor for spikes."}
            return {"name": "CPU", "status": "ok", "detail": f"{pct}% usage, {cores} cores", "icon": _ICON_OK, "recommendation": None}
        except ImportError:
            return {"name": "CPU", "status": "warning", "detail": "psutil not installed", "icon": _ICON_WARN, "recommendation": "Install psutil for hardware monitoring."}
        except Exception as e:
            return {"name": "CPU", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Check system health."}

    def _check_memory(self):
        try:
            import psutil
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024 ** 3)
            total_gb = vm.total / (1024 ** 3)
            pct = vm.percent
            if pct >= 95:
                return {"name": "Memory", "status": "critical", "detail": f"{used_gb:.1f}GB / {total_gb:.1f}GB ({pct}%)", "icon": _ICON_CRIT, "recommendation": "Memory critically low - kill unused processes or add RAM."}
            if pct >= 85:
                return {"name": "Memory", "status": "warning", "detail": f"{used_gb:.1f}GB / {total_gb:.1f}GB ({pct}%)", "icon": _ICON_WARN, "recommendation": "Memory usage high - monitor for leaks."}
            return {"name": "Memory", "status": "ok", "detail": f"{used_gb:.1f}GB / {total_gb:.1f}GB ({pct}%)", "icon": _ICON_OK, "recommendation": None}
        except ImportError:
            return {"name": "Memory", "status": "warning", "detail": "psutil not installed", "icon": _ICON_WARN, "recommendation": "Install psutil for memory monitoring."}
        except Exception as e:
            return {"name": "Memory", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Check system health."}

    def _check_maintenance(self):
        maint_path = os.path.join(DATA_DIR, "maintenance.json")
        maint = load_json(maint_path, {})
        enabled = maint.get("enabled", False)
        reason = maint.get("reason", "")
        if enabled:
            return {"name": "Maintenance", "status": "warning", "detail": f"Maintenance mode ON: {reason or 'no reason given'}", "icon": _ICON_WARN, "recommendation": "Disable maintenance mode when done: set enabled=false in maintenance.json"}
        return {"name": "Maintenance", "status": "ok", "detail": "Maintenance mode OFF", "icon": _ICON_OK, "recommendation": None}

    def _check_logs(self):
        log_path = os.path.join(BASE_DIR, "bot.log")
        if not os.path.isfile(log_path):
            return {"name": "Logs", "status": "ok", "detail": "No bot.log found (may use stdout logging)", "icon": _ICON_OK, "recommendation": None}
        try:
            size = os.path.getsize(log_path)
            size_mb = round(size / (1024 * 1024), 2)
            error_count = 0
            try:
                with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                    lines = f.readlines()
                    recent = lines[-200:] if len(lines) > 200 else lines
                    for line in recent:
                        if "ERROR" in line or "CRITICAL" in line:
                            error_count += 1
            except Exception:
                pass
            if size_mb > 50:
                return {"name": "Logs", "status": "warning", "detail": f"bot.log is {size_mb:.1f}MB, {error_count} recent errors", "icon": _ICON_WARN, "recommendation": "Rotate or truncate bot.log to save disk space."}
            if error_count > 10:
                return {"name": "Logs", "status": "warning", "detail": f"{size_mb:.1f}MB, {error_count} recent errors", "icon": _ICON_WARN, "recommendation": "Investigate recent error spike in bot.log."}
            return {"name": "Logs", "status": "ok", "detail": f"{size_mb:.1f}MB, {error_count} recent errors", "icon": _ICON_OK, "recommendation": None}
        except Exception as e:
            return {"name": "Logs", "status": "error", "detail": str(e), "icon": _ICON_ERR, "recommendation": "Check log file permissions."}

    # ------------------------------------------------------------------
    # Main scan runner
    # ------------------------------------------------------------------

    async def run_full_scan(self):
        self.start_time = time.time()
        self.results = []

        checks = [
            ("Discord", self._check_discord()),
            ("Database", await self._check_database()),
            ("Docker", await self._check_docker()),
            ("Nodes", await self._check_nodes()),
            ("VPS Containers", await self._check_vps_containers()),
            ("API", await self._check_api()),
            ("Network", await self._check_network()),
            ("Filesystem", self._check_filesystem()),
            ("Backups", self._check_backups()),
            ("Billing", self._check_billing()),
            ("Automation", self._check_automation()),
            ("Dependencies", self._check_dependencies()),
            ("Permissions", self._check_permissions()),
            ("Storage", self._check_storage()),
            ("CPU", self._check_cpu()),
            ("Memory", self._check_memory()),
            ("Maintenance", self._check_maintenance()),
            ("Logs", self._check_logs()),
        ]

        for name, result in checks:
            self.results.append(result)
            try:
                metrics.inc(f"scan.check.{name.lower().replace(' ', '_')}")
            except Exception:
                pass

        duration_ms = int((time.time() - self.start_time) * 1000)
        overall = self.get_overall_status(self.results)

        try:
            metrics.set("scan.last_overall", overall)
            metrics.set("scan.last_duration_ms", duration_ms)
            bus.emit("full_scan_complete", overall=overall, duration_ms=duration_ms, results=self.results)
        except Exception:
            pass

        summary = self._build_summary(self.results, overall, duration_ms)

        return {
            "results": self.results,
            "overall": overall,
            "duration_ms": duration_ms,
            "summary": summary,
        }

    # ------------------------------------------------------------------
    # Analysis helpers
    # ------------------------------------------------------------------

    def get_overall_status(self, results):
        statuses = [r.get("status", "ok") for r in results]
        if "critical" in statuses or statuses.count("error") >= 2:
            return "critical"
        if "error" in statuses or statuses.count("warning") >= 3:
            return "degraded"
        if "warning" in statuses:
            return "degraded"
        return "healthy"

    def get_recommendations(self, results):
        recs = []
        for r in results:
            rec = r.get("recommendation")
            if rec:
                recs.append(rec)
        if not recs:
            recs.append("All systems healthy - no action needed.")
        return recs

    def get_embed_fields(self, results):
        fields = []
        for r in results:
            icon = r.get("icon", "\u26ab")
            name = r.get("name", "Unknown")
            status = r.get("status", "unknown").upper()
            detail = r.get("detail", "N/A")
            rec = r.get("recommendation")
            value = f"**Status:** `{status}`\n**Detail:** {detail}"
            if rec:
                value += f"\n**Fix:** {rec}"
            fields.append({"name": f"{icon} {name}", "value": value, "inline": True})
        return fields

    def get_report_embed(self, results, overall, duration_ms):
        import discord

        status_colors = {
            "healthy": discord.Color.green(),
            "degraded": discord.Color.gold(),
            "critical": discord.Color.red(),
        }
        status_emoji = {
            "healthy": "\u2705",
            "degraded": "\u26a0\ufe0f",
            "critical": "\U0001f534",
        }

        color = status_colors.get(overall, discord.Color.greyple())
        emoji = status_emoji.get(overall, "\u26ab")

        ok_count = sum(1 for r in results if r.get("status") == "ok")
        warn_count = sum(1 for r in results if r.get("status") == "warning")
        err_count = sum(1 for r in results if r.get("status") in ("error", "critical"))

        embed = discord.Embed(
            title=f"{emoji} System Scan Report - {overall.upper()}",
            description=(
                f"**{ok_count}** passed \u2022 **{warn_count}** warnings \u2022 **{err_count}** errors\n"
                f"Scan completed in **{duration_ms}ms**"
            ),
            color=color,
            timestamp=discord.utils.utcnow(),
        )

        fields = self.get_embed_fields(results)
        for field in fields[:25]:
            embed.add_field(
                name=field["name"],
                value=field["value"][:1024],
                inline=field.get("inline", True),
            )

        recs = self.get_recommendations(results)
        if recs and len(recs) <= 10:
            rec_text = "\n".join(f"\u2022 {r}" for r in recs[:10])
            embed.add_field(name="\U0001f4a1 Recommendations", value=rec_text[:1024], inline=False)

        embed.set_footer(text="Turtle Nodes Full System Scan")
        return embed

    def _build_summary(self, results, overall, duration_ms):
        ok = sum(1 for r in results if r.get("status") == "ok")
        warn = sum(1 for r in results if r.get("status") == "warning")
        err = sum(1 for r in results if r.get("status") in ("error", "critical"))
        total = len(results)
        return f"Scan {overall.upper()} - {ok}/{total} passed, {warn} warnings, {err} errors ({duration_ms}ms)"