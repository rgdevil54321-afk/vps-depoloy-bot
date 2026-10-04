"""Diagnostics Center - comprehensive system health checks and recommendations."""
import os
import sys
import shutil
import platform
import logging
import asyncio
import json
from datetime import datetime, timezone

from core.services import health, metrics, DATA_DIR, BASE_DIR, BACKUP_DIR, CONFIG_PATH, load_json
from core.infrastructure import get_host_stats, get_docker_status

logger = logging.getLogger("turtle.diagnostics")

_ICON_OK = "\U0001f7e2"
_ICON_WARN = "\U0001f7e1"
_ICON_ERR = "\U0001f534"

REQUIRED_PACKAGES = ["discord", "psutil", "aiohttp"]


def _check_import(module_name: str) -> tuple[bool, str]:
    try:
        mod = __import__(module_name)
        ver = getattr(mod, "__version__", getattr(mod, "version", "?"))
        return True, str(ver)
    except ImportError:
        return False, "not installed"


class DiagnosticsRunner:
    """Runs a full suite of diagnostic checks and produces reports."""

    def __init__(self, bot):
        self.bot = bot

    async def run_full_diagnostics(self) -> dict:
        results = {}

        results["discord"] = self._check_discord()
        results["database"] = await self._check_database()
        results["docker"] = await self._check_docker()
        results["filesystem"] = self._check_filesystem()
        results["environment"] = self._check_environment()
        results["memory"] = self._check_memory()
        results["disk"] = self._check_disk()
        results["cpu"] = self._check_cpu()
        results["network"] = await self._check_network()
        results["dependencies"] = self._check_dependencies()
        results["backups"] = self._check_backups()
        results["config"] = self._check_config()

        return results

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------

    def _check_discord(self) -> dict:
        try:
            if not hasattr(self.bot, "is_ready"):
                return {
                    "name": "Discord Bot",
                    "status": "error",
                    "detail": "Bot instance has no is_ready method",
                    "icon": _ICON_ERR,
                }
            if self.bot.is_ready():
                latency = round(self.bot.latency * 1000, 1) if self.bot.latency else 0
                guilds = len(getattr(self.bot, "guilds", []))
                return {
                    "name": "Discord Bot",
                    "status": "ok",
                    "detail": f"Connected - {guilds} guild(s), {latency}ms latency",
                    "icon": _ICON_OK,
                }
            return {
                "name": "Discord Bot",
                "status": "warning",
                "detail": "Bot connected but not yet ready",
                "icon": _ICON_WARN,
            }
        except Exception as e:
            return {
                "name": "Discord Bot",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    async def _check_database(self) -> dict:
        try:
            if not os.path.isdir(DATA_DIR):
                return {
                    "name": "Database",
                    "status": "error",
                    "detail": f"DATA_DIR missing: {DATA_DIR}",
                    "icon": _ICON_ERR,
                }

            json_files = [f for f in os.listdir(DATA_DIR) if f.endswith(".json")]
            parse_errors = []
            for fname in json_files:
                fpath = os.path.join(DATA_DIR, fname)
                try:
                    with open(fpath, "r", encoding="utf-8") as fh:
                        json.load(fh)
                except json.JSONDecodeError as exc:
                    parse_errors.append(f"{fname}: {exc}")

            if parse_errors:
                return {
                    "name": "Database",
                    "status": "warning",
                    "detail": f"{len(json_files)} file(s), {len(parse_errors)} corrupt: {', '.join(parse_errors[:3])}",
                    "icon": _ICON_WARN,
                }

            total_size = sum(
                os.path.getsize(os.path.join(DATA_DIR, f))
                for f in json_files
            )
            return {
                "name": "Database",
                "status": "ok",
                "detail": f"{len(json_files)} file(s), {total_size / 1024:.1f} KB",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Database",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    async def _check_docker(self) -> dict:
        try:
            docker_bin = shutil.which("docker")
            if docker_bin is None:
                return {
                    "name": "Docker",
                    "status": "warning",
                    "detail": "Docker binary not found in PATH",
                    "icon": _ICON_WARN,
                }

            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a", "--format", "{{.Names}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
                code = proc.returncode or 0
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                return {
                    "name": "Docker",
                    "status": "error",
                    "detail": "docker ps timed out after 10s",
                    "icon": _ICON_ERR,
                }

            if code != 0:
                err_text = stderr.decode(errors="replace").strip()[:120]
                return {
                    "name": "Docker",
                    "status": "error",
                    "detail": f"docker ps failed: {err_text}",
                    "icon": _ICON_ERR,
                }

            lines = [l.strip() for l in stdout.decode(errors="replace").splitlines() if l.strip()]
            return {
                "name": "Docker",
                "status": "ok",
                "detail": f"Docker daemon responsive - {len(lines)} container(s) total",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Docker",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_filesystem(self) -> dict:
        try:
            test_dir = os.path.join(BASE_DIR, ".write_test")
            can_write = False
            try:
                os.makedirs(test_dir, exist_ok=True)
                test_file = os.path.join(test_dir, "probe.tmp")
                with open(test_file, "w") as fh:
                    fh.write("ok")
                os.remove(test_file)
                os.rmdir(test_dir)
                can_write = True
            except (OSError, PermissionError):
                try:
                    os.rmdir(test_dir)
                except Exception:
                    pass

            if can_write:
                return {
                    "name": "Filesystem",
                    "status": "ok",
                    "detail": f"BASE_DIR writable: {BASE_DIR}",
                    "icon": _ICON_OK,
                }
            return {
                "name": "Filesystem",
                "status": "warning",
                "detail": f"BASE_DIR not writable: {BASE_DIR}",
                "icon": _ICON_WARN,
            }
        except Exception as e:
            return {
                "name": "Filesystem",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_environment(self) -> dict:
        try:
            py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
            os_name = platform.system()
            os_release = platform.release()
            arch = platform.machine()
            return {
                "name": "Environment",
                "status": "ok",
                "detail": f"Python {py_ver} | {os_name} {os_release} ({arch})",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Environment",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_memory(self) -> dict:
        try:
            import psutil
            vm = psutil.virtual_memory()
            used_gb = vm.used / (1024 ** 3)
            total_gb = vm.total / (1024 ** 3)
            pct = vm.percent
            status = "ok" if pct < 80 else ("warning" if pct < 92 else "error")
            icon = _ICON_OK if status == "ok" else (_ICON_WARN if status == "warning" else _ICON_ERR)
            return {
                "name": "Memory",
                "status": status,
                "detail": f"{used_gb:.1f}GB / {total_gb:.1f}GB ({pct}%)",
                "icon": icon,
            }
        except ImportError:
            return {
                "name": "Memory",
                "status": "warning",
                "detail": "psutil not available - cannot check memory",
                "icon": _ICON_WARN,
            }
        except Exception as e:
            return {
                "name": "Memory",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_disk(self) -> dict:
        try:
            import psutil
            usage = psutil.disk_usage("/")
            free_gb = usage.free / (1024 ** 3)
            total_gb = usage.total / (1024 ** 3)
            pct = usage.percent
            status = "ok" if pct < 85 else ("warning" if pct < 95 else "error")
            icon = _ICON_OK if status == "ok" else (_ICON_WARN if status == "warning" else _ICON_ERR)
            return {
                "name": "Disk",
                "status": status,
                "detail": f"{free_gb:.1f}GB free / {total_gb:.1f}GB total ({pct}% used)",
                "icon": icon,
            }
        except ImportError:
            return {
                "name": "Disk",
                "status": "warning",
                "detail": "psutil not available - cannot check disk",
                "icon": _ICON_WARN,
            }
        except Exception as e:
            return {
                "name": "Disk",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_cpu(self) -> dict:
        try:
            import psutil
            count = psutil.cpu_count(logical=True) or 0
            pct = psutil.cpu_percent(interval=0.5)
            status = "ok" if pct < 80 else ("warning" if pct < 95 else "error")
            icon = _ICON_OK if status == "ok" else (_ICON_WARN if status == "warning" else _ICON_ERR)
            return {
                "name": "CPU",
                "status": status,
                "detail": f"{count} core(s), {pct}% usage",
                "icon": icon,
            }
        except ImportError:
            return {
                "name": "CPU",
                "status": "warning",
                "detail": "psutil not available - cannot check CPU",
                "icon": _ICON_WARN,
            }
        except Exception as e:
            return {
                "name": "CPU",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    async def _check_network(self) -> dict:
        try:
            import socket as _sock
            loop = asyncio.get_running_loop()
            def _probe():
                with _sock.create_connection(("1.1.1.1", 53), timeout=3):
                    return True
            await loop.run_in_executor(None, _probe)
            return {
                "name": "Network",
                "status": "ok",
                "detail": "Outbound connectivity to 1.1.1.1:53 OK",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Network",
                "status": "warning",
                "detail": f"Connectivity check failed: {e}",
                "icon": _ICON_WARN,
            }

    def _check_dependencies(self) -> dict:
        results = {}
        for pkg in REQUIRED_PACKAGES:
            ok, ver = _check_import(pkg)
            results[pkg] = {"installed": ok, "version": ver}

        missing = [p for p, r in results.items() if not r["installed"]]
        if missing:
            return {
                "name": "Dependencies",
                "status": "warning",
                "detail": f"Missing: {', '.join(missing)}",
                "icon": _ICON_WARN,
            }
        detail_parts = [f"{p} {r['version']}" for p, r in results.items()]
        return {
            "name": "Dependencies",
            "status": "ok",
            "detail": ", ".join(detail_parts),
            "icon": _ICON_OK,
        }

    def _check_backups(self) -> dict:
        try:
            if not os.path.isdir(BACKUP_DIR):
                return {
                    "name": "Backups",
                    "status": "warning",
                    "detail": f"BACKUP_DIR missing: {BACKUP_DIR}",
                    "icon": _ICON_WARN,
                }

            files = [f for f in os.listdir(BACKUP_DIR) if os.path.isfile(os.path.join(BACKUP_DIR, f))]
            if not files:
                return {
                    "name": "Backups",
                    "status": "warning",
                    "detail": "Backup directory empty - no backups found",
                    "icon": _ICON_WARN,
                }

            return {
                "name": "Backups",
                "status": "ok",
                "detail": f"{len(files)} backup file(s) in {BACKUP_DIR}",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Backups",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    def _check_config(self) -> dict:
        try:
            if not os.path.isfile(CONFIG_PATH):
                return {
                    "name": "Config",
                    "status": "error",
                    "detail": f"config.json not found at {CONFIG_PATH}",
                    "icon": _ICON_ERR,
                }
            cfg = load_json(CONFIG_PATH, None)
            if cfg is None:
                return {
                    "name": "Config",
                    "status": "error",
                    "detail": "config.json exists but could not be parsed (invalid JSON)",
                    "icon": _ICON_ERR,
                }
            keys = list(cfg.keys())
            return {
                "name": "Config",
                "status": "ok",
                "detail": f"config.json parsed - {len(keys)} key(s): {', '.join(keys[:5])}",
                "icon": _ICON_OK,
            }
        except Exception as e:
            return {
                "name": "Config",
                "status": "error",
                "detail": str(e),
                "icon": _ICON_ERR,
            }

    # ------------------------------------------------------------------
    # Report formatting
    # ------------------------------------------------------------------

    def get_report_embed_fields(self, results: dict) -> list[dict]:
        fields = []
        for key, check in results.items():
            icon = check.get("icon", "\u26ab")
            name = check.get("name", key)
            status = check.get("status", "unknown")
            detail = check.get("detail", "N/A")
            fields.append({
                "name": f"{icon} {name}",
                "value": f"**Status:** `{status.upper()}`\n**Detail:** {detail}",
                "inline": True,
            })
        return fields

    @staticmethod
    def get_overall_status(results: dict) -> str:
        statuses = [r.get("status", "ok") for r in results.values()]
        if "error" in statuses:
            return "critical"
        if statuses.count("warning") >= 3:
            return "degraded"
        if "warning" in statuses:
            return "degraded"
        return "healthy"

    @staticmethod
    def get_recommendations(results: dict) -> list[str]:
        recs = []
        for key, check in results.items():
            status = check.get("status", "ok")
            detail = check.get("detail", "")
            name = check.get("name", key)

            if status == "error":
                if name == "Docker":
                    recs.append("Install or start the Docker daemon to enable VPS deployment.")
                elif name == "Database":
                    recs.append("Ensure the data directory exists and JSON files are valid.")
                elif name == "Discord Bot":
                    recs.append("Restart the bot process to re-establish the Discord connection.")
                elif name == "Filesystem":
                    recs.append("Check file permissions on the project directory.")
                elif name == "Config":
                    recs.append("Restore config.json from a backup or regenerate it with default values.")
                else:
                    recs.append(f"Investigate {name}: {detail}")

            elif status == "warning":
                if name == "Memory":
                    recs.append("Memory usage is high - consider stopping unused services or upgrading RAM.")
                elif name == "Disk":
                    recs.append("Disk space is low - clean up old logs, backups, or unused Docker images.")
                elif name == "CPU":
                    recs.append("CPU usage is elevated - check for runaway processes.")
                elif name == "Docker":
                    recs.append("Docker is not responding - verify the docker service is running.")
                elif name == "Backups":
                    recs.append("Set up automated backups to prevent data loss.")
                elif name == "Dependencies":
                    recs.append("Install missing Python packages: pip install -r requirements.txt")
                else:
                    recs.append(f"Review {name}: {detail}")

        if not recs:
            recs.append("All systems operating normally. No action required.")

        return recs
