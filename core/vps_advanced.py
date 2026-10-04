import discord
import asyncio
import logging
import os
import json
import time
import secrets
import string
import subprocess
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("vps_advanced")



async def _docker(*args, timeout=30):
    cmd = ["docker"] + list(args)
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode, stdout.decode(errors="replace").strip(), stderr.decode(errors="replace").strip()
    except asyncio.TimeoutError:
        logger.error("Docker command timed out: %s", " ".join(cmd))
        proc.kill()
        return -1, "", "Command timed out"
    except FileNotFoundError:
        logger.error("Docker binary not found")
        return -1, "", "Docker not installed"
    except Exception as e:
        logger.exception("Docker command failed: %s", " ".join(cmd))
        return -1, "", str(e)


def _gen_password(length=16):
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
    return "".join(secrets.choice(alphabet) for _ in range(length))


class VPSController:
    def __init__(self, bot):
        self.bot = bot

    def _load_vps_data(self):
        return load_json("vps_data", {})

    def _save_vps_data(self, data):
        save_json("vps_data", data)

    def _get_vps(self, data, user_id, vps_index):
        users = data.get(str(user_id), [])
        if 0 <= vps_index < len(users):
            return users[vps_index]
        return None

    def _result(self, success, message, details=None):
        r = {"success": success, "message": message}
        if details:
            r["details"] = details
        return r

    def get_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        return self._get_vps(data, user_id, vps_index)

    def find_vps_by_container(self, container_name):
        data = self._load_vps_data()
        for uid, vpss in data.items():
            for idx, v in enumerate(vpss):
                if v.get("container_name") == container_name:
                    return uid, idx, v
        return None, None, None

    def get_all_vps_status(self):
        data = self._load_vps_data()
        counts = {"total": 0, "running": 0, "stopped": 0, "suspended": 0, "locked": 0}
        for uid, vpss in data.items():
            for v in vpss:
                counts["total"] += 1
                status = v.get("status", "stopped")
                if status in counts:
                    counts[status] += 1
                else:
                    counts["stopped"] += 1
                if v.get("locked"):
                    counts["locked"] += 1
        return counts

    async def start_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("locked"):
            return self._result(False, "VPS is locked. Cannot start a locked VPS.")

        container = vps["container_name"]

        if vps.get("status") == "suspended":
            return self._result(False, "VPS is suspended. Use unsuspend first.")

        rc, out, err = await _docker("start", container)
        if rc != 0:
            return self._result(False, f"Failed to start container: {err}")

        vps["status"] = "running"
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "start_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.start")
        return self._result(True, f"VPS started successfully.", {"container": container})

    async def stop_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("locked"):
            return self._result(False, "VPS is locked. Cannot stop a locked VPS.")

        container = vps["container_name"]

        rc, out, err = await _docker("stop", container, timeout=60)
        if rc != 0:
            return self._result(False, f"Failed to stop container: {err}")

        vps["status"] = "stopped"
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "stop_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.stop")
        return self._result(True, f"VPS stopped successfully.", {"container": container})

    async def restart_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("locked"):
            return self._result(False, "VPS is locked. Cannot restart a locked VPS.")

        container = vps["container_name"]

        rc, out, err = await _docker("restart", container, timeout=60)
        if rc != 0:
            return self._result(False, f"Failed to restart container: {err}")

        vps["status"] = "running"
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "restart_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.restart")
        return self._result(True, f"VPS restarted successfully.", {"container": container})

    async def reboot_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("locked"):
            return self._result(False, "VPS is locked. Cannot reboot a locked VPS.")

        container = vps["container_name"]

        rc, out, err = await _docker("exec", container, "reboot")
        if rc != 0:
            return self._result(False, f"Failed to reboot container: {err}")

        await asyncio.sleep(3)
        rc2, _, _ = await _docker("inspect", "-f", "{{.State.Running}}", container)
        if rc2 == 0 and "true" in _.lower():
            vps["status"] = "running"
            self._save_vps_data(data)

        bus.emit("audit", {
            "action": "reboot_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.reboot")
        return self._result(True, f"VPS rebooted successfully.", {"container": container})

    async def force_stop(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        container = vps["container_name"]

        rc, out, err = await _docker("kill", container)
        if rc != 0:
            return self._result(False, f"Failed to kill container: {err}")

        vps["status"] = "stopped"
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "force_stop_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.force_stop")
        return self._result(True, f"VPS force-stopped successfully.", {"container": container})

    async def suspend_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("status") == "stopped" or vps.get("status") == "suspended":
            vps["status"] = "suspended"
            vps["suspended_at"] = datetime.now(timezone.utc).isoformat()
            self._save_vps_data(data)
            return self._result(True, f"VPS marked as suspended.", {"container": vps["container_name"]})

        container = vps["container_name"]

        rc, out, err = await _docker("stop", container, timeout=60)
        if rc != 0:
            return self._result(False, f"Failed to stop container for suspension: {err}")

        vps["status"] = "suspended"
        vps["suspended_at"] = datetime.now(timezone.utc).isoformat()
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "suspend_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.suspend")
        return self._result(True, f"VPS suspended successfully.", {"container": container})

    async def unsuspend_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("status") != "suspended":
            return self._result(False, "VPS is not suspended.")

        container = vps["container_name"]

        rc, out, err = await _docker("start", container)
        if rc != 0:
            return self._result(False, f"Failed to start container: {err}")

        vps["status"] = "running"
        vps.pop("suspended_at", None)
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "unsuspend_vps",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        metrics.increment("vps.unsuspend")
        return self._result(True, f"VPS unsuspended successfully.", {"container": container})

    async def lock_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        vps["locked"] = True
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "lock_vps",
            "user_id": str(user_id),
            "container": vps["container_name"],
            "timestamp": time.time(),
        })
        return self._result(True, "VPS locked.", {"container": vps["container_name"]})

    async def unlock_vps(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        vps["locked"] = False
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "unlock_vps",
            "user_id": str(user_id),
            "container": vps["container_name"],
            "timestamp": time.time(),
        })
        return self._result(True, "VPS unlocked.", {"container": vps["container_name"]})

    def is_locked(self, user_id, vps_index):
        vps = self.get_vps(user_id, vps_index)
        if not vps:
            return False
        return vps.get("locked", False)

    async def rename_vps(self, user_id, vps_index, new_name):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        old_name = vps.get("name", vps["container_name"])
        vps["name"] = new_name
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "rename_vps",
            "user_id": str(user_id),
            "container": vps["container_name"],
            "old_name": old_name,
            "new_name": new_name,
            "timestamp": time.time(),
        })
        return self._result(True, f"VPS renamed to {new_name}.", {"container": vps["container_name"]})

    async def reset_ssh(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        container = vps["container_name"]
        new_pass = _gen_password()

        rc, out, err = await _docker(
            "exec", container, "bash", "-c",
            f"echo 'root:{new_pass}' | chpasswd"
        )
        if rc != 0:
            return self._result(False, f"Failed to reset SSH password: {err}")

        vps["ssh_password"] = new_pass
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "reset_ssh",
            "user_id": str(user_id),
            "container": container,
            "timestamp": time.time(),
        })
        return self._result(True, "SSH password reset.", {
            "container": container,
            "new_password": new_pass,
        })

    async def get_vps_health(self, user_id, vps_index):
        vps = self.get_vps(user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        container = vps["container_name"]
        health = {
            "container": container,
            "container_running": False,
            "cpu": None,
            "mem": None,
            "disk": None,
            "uptime": None,
            "status": vps.get("status", "unknown"),
            "name": vps.get("name", container),
            "locked": vps.get("locked", False),
            "suspended_at": vps.get("suspended_at"),
        }

        rc, out, err = await _docker("inspect", "-f", "{{.State.Running}}", container)
        if rc == 0:
            health["container_running"] = out.lower() == "true"
        else:
            health["container_running"] = False
            health["status"] = "not_found"
            return self._result(True, "Health check complete.", health)

        if not health["container_running"]:
            health["status"] = "stopped"
            health["uptime"] = "stopped"
            return self._result(True, "Health check complete.", health)

        rc, out, _ = await _docker(
            "stats", container, "--no-stream",
            "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}"
        )
        if rc == 0 and out:
            parts = out.split("|")
            if len(parts) >= 5:
                health["cpu"] = parts[0]
                health["mem"] = parts[1]
                health["mem_percent"] = parts[2]
                health["net_io"] = parts[3]
                health["block_io"] = parts[4]

        rc, out, _ = await _docker("exec", container, "uptime", "-p")
        if rc == 0 and out:
            health["uptime"] = out

        rc, out, _ = await _docker(
            "exec", container, "df", "-h", "/"
        )
        if rc == 0 and out:
            lines = out.strip().split("\n")
            if len(lines) >= 2:
                fields = lines[1].split()
                if len(fields) >= 5:
                    health["disk"] = {
                        "total": fields[1],
                        "used": fields[2],
                        "available": fields[3],
                        "percent": fields[4],
                    }

        if health["container_running"]:
            health["status"] = "running"

        return self._result(True, "Health check complete.", health)

    async def get_vps_uptime(self, user_id, vps_index):
        vps = self.get_vps(user_id, vps_index)
        if not vps:
            return None

        container = vps["container_name"]

        rc, out, _ = await _docker("inspect", "-f", "{{.State.Running}}", container)
        if rc != 0 or out.lower() != "true":
            return "stopped"

        rc, out, _ = await _docker("exec", container, "uptime", "-p")
        if rc != 0 or not out:
            return "unknown"

        return out

    async def recreate_container(self, user_id, vps_index):
        data = self._load_vps_data()
        vps = self._get_vps(data, user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        if vps.get("locked"):
            return self._result(False, "VPS is locked. Cannot recreate a locked VPS.")

        container = vps["container_name"]
        old_status = vps.get("status", "stopped")

        if old_status == "running":
            await _docker("stop", container, timeout=30)

        rc, _, _ = await _docker("rm", "-f", container)
        if rc != 0:
            return self._result(False, f"Failed to remove old container: {_}")

        new_pass = _gen_password()
        ram = vps.get("ram", "1g")
        cpu = vps.get("cpu", "1")
        storage = vps.get("storage", "10g")
        image = vps.get("image", "ubuntu:22.04")
        node = vps.get("node", "default")

        create_args = [
            "run", "-d",
            "--name", container,
            "--memory", str(ram),
            "--cpus", str(cpu),
            "--storage-opt", f"size={storage}",
            "-e", f"SSH_PASSWORD={new_pass}",
            "--restart", "unless-stopped",
            image,
        ]

        rc, out, err = await _docker(*create_args)
        if rc != 0:
            vps["status"] = "error"
            self._save_vps_data(data)
            return self._result(False, f"Failed to create new container: {err}")

        vps["status"] = "running"
        vps["ssh_password"] = new_pass
        vps["created_at"] = datetime.now(timezone.utc).isoformat()
        vps.pop("suspended_at", None)
        self._save_vps_data(data)

        bus.emit("audit", {
            "action": "recreate_vps",
            "user_id": str(user_id),
            "container": container,
            "old_status": old_status,
            "timestamp": time.time(),
        })
        metrics.increment("vps.recreate")
        return self._result(True, "VPS recreated successfully.", {
            "container": container,
            "new_password": new_pass,
        })

    async def get_vps_stats(self, user_id, vps_index):
        vps = self.get_vps(user_id, vps_index)
        if not vps:
            return self._result(False, "VPS not found")

        container = vps["container_name"]

        rc, out, _ = await _docker("inspect", "-f", "{{.State.Running}}", container)
        if rc != 0 or out.lower() != "true":
            return self._result(False, "Container is not running.")

        rc, out, err = await _docker(
            "stats", container, "--no-stream",
            "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}"
        )
        if rc != 0 or not out:
            return self._result(False, f"Failed to fetch stats: {err}")

        parts = out.split("|")
        if len(parts) < 5:
            return self._result(False, "Unexpected stats format.")

        stats = {
            "container": container,
            "cpu_percent": parts[0],
            "mem_usage": parts[1],
            "mem_percent": parts[2],
            "net_io": parts[3],
            "block_io": parts[4],
        }

        rc, out2, _ = await _docker("exec", container, "uptime", "-p")
        if rc == 0 and out2:
            stats["uptime"] = out2

        return self._result(True, "Stats fetched.", stats)
