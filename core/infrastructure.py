import asyncio
import os
import shutil
import socket
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil

from core.services import (
    DATA_DIR,
    BASE_DIR,
    bus,
    health,
    load_json,
    save_json,
)


def _bytes_to_gb(b: float) -> float:
    return round(b / (1024 ** 3), 2)


def _bytes_to_mb(b: float) -> float:
    return round(b / (1024 ** 2), 2)


def _port_is_listening(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex((host, port)) == 0


async def _run(cmd: str, *args: str, timeout: float = 10.0) -> tuple[int, str, str]:
    proc = await asyncio.create_subprocess_exec(
        cmd, *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return -1, "", "timeout"
    return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")


async def get_host_stats() -> dict:
    loop = asyncio.get_running_loop()

    def _gather() -> dict:
        cpu_percent = psutil.cpu_percent(interval=1)
        cpu_count = psutil.cpu_count(logical=True) or 1

        ram = psutil.virtual_memory()
        swap = psutil.swap_memory()
        disk = psutil.disk_usage("/")
        disk_io = psutil.disk_io_counters() or psutil.disk_io_counters(perdisk=False)
        net_io = psutil.net_io_counters()
        try:
            load = os.getloadavg()
        except (AttributeError, OSError):
            load = (cpu_percent / 100.0, 0.0, 0.0)
        boot = psutil.boot_time()
        uptime = time.time() - boot
        proc_count = len(psutil.pids())

        return {
            "cpu_percent": round(cpu_percent, 1),
            "cpu_count": cpu_count,
            "ram_total_gb": _bytes_to_gb(ram.total),
            "ram_used_gb": _bytes_to_gb(ram.used),
            "ram_percent": round(ram.percent, 1),
            "swap_total_gb": _bytes_to_gb(swap.total),
            "swap_used_gb": _bytes_to_gb(swap.used),
            "swap_percent": round(swap.percent, 1),
            "disk_total_gb": _bytes_to_gb(disk.total),
            "disk_used_gb": _bytes_to_gb(disk.used),
            "disk_percent": round(disk.percent, 1),
            "disk_io_read_bytes": disk_io.read_bytes if disk_io else 0,
            "disk_io_write_bytes": disk_io.write_bytes if disk_io else 0,
            "net_sent_bytes": net_io.bytes_sent,
            "net_recv_bytes": net_io.bytes_recv,
            "load_1": round(load[0], 2),
            "load_5": round(load[1], 2),
            "load_15": round(load[2], 2),
            "uptime_seconds": int(uptime),
            "process_count": proc_count,
        }

    stats = await loop.run_in_executor(None, _gather)

    cpu = stats["cpu_percent"]
    ram = stats["ram_percent"]
    disk = stats["disk_percent"]

    if cpu > 90 or ram > 90 or disk > 90:
        health.set("host_cpu", "critical", f"CPU {cpu}%")
        health.set("host_ram", "critical", f"RAM {ram}%")
        health.set("host_disk", "critical", f"Disk {disk}%")
        bus.emit("infra:critical", {"host": stats})
    elif cpu > 75 or ram > 75 or disk > 80:
        health.set("host_cpu", "warning", f"CPU {cpu}%")
        health.set("host_ram", "warning", f"RAM {ram}%")
        health.set("host_disk", "warning", f"Disk {disk}%")
    else:
        health.set("host_cpu", "healthy", f"CPU {cpu}%")
        health.set("host_ram", "healthy", f"RAM {ram}%")
        health.set("host_disk", "healthy", f"Disk {disk}%")

    return stats


async def get_docker_status() -> dict:
    installed = shutil.which("docker") is not None
    if not installed:
        health.set("docker_daemon", "offline", "Docker binary not found")
        return {
            "installed": False,
            "running": False,
            "container_count": 0,
            "containers": [],
        }

    code, out, err = await _run("docker", "info", "--format", "{{.ServerVersion}}", timeout=5)
    running = code == 0

    if not running:
        health.set("docker_daemon", "critical", f"Docker daemon not responding: {err.strip()}")
        bus.emit("infra:docker_down", {"error": err.strip()})
        return {
            "installed": True,
            "running": False,
            "container_count": 0,
            "containers": [],
        }

    health.set("docker_daemon", "healthy", "Docker daemon responsive")

    code, ps_out, _ = await _run(
        "docker", "ps", "-a",
        "--format", "{{.Names}}|{{.Image}}|{{.Status}}|{{.State}}|{{.Ports}}",
        timeout=10,
    )
    if code != 0:
        return {
            "installed": True,
            "running": True,
            "container_count": 0,
            "containers": [],
        }

    containers_raw = [
        line.strip() for line in ps_out.strip().splitlines() if line.strip()
    ]

    code, stats_out, _ = await _run(
        "docker", "stats", "--no-stream",
        "--format", "{{.Name}}|{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}",
        timeout=15,
    )

    stats_map: dict[str, dict] = {}
    if code == 0:
        for line in stats_out.strip().splitlines():
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 4:
                name = parts[0]
                stats_map[name] = {
                    "cpu_percent": parts[1],
                    "mem_usage": parts[2],
                    "mem_percent": parts[3],
                }

    containers = []
    for line in containers_raw:
        parts = [p.strip() for p in line.split("|")]
        name = parts[0] if len(parts) > 0 else "unknown"
        image = parts[1] if len(parts) > 1 else ""
        status_str = parts[2] if len(parts) > 2 else ""
        state = parts[3] if len(parts) > 3 else ""
        ports = parts[4] if len(parts) > 4 else ""

        cstats = stats_map.get(name, {})

        uptime_str = ""
        if "Up " in status_str:
            uptime_str = status_str.split("Up ", 1)[-1]
        elif "Up" in status_str:
            uptime_str = status_str

        containers.append({
            "name": name,
            "image": image,
            "status": status_str,
            "state": state,
            "ports": ports,
            "cpu_percent": cstats.get("cpu_percent", "0.00%"),
            "mem_usage": cstats.get("mem_usage", "0B / 0B"),
            "mem_percent": cstats.get("mem_percent", "0.00%"),
            "uptime": uptime_str,
        })

    return {
        "installed": True,
        "running": True,
        "container_count": len(containers),
        "containers": containers,
    }


async def get_docker_container_stats(container_name: str) -> dict | None:
    installed = shutil.which("docker") is not None
    if not installed:
        return None

    code, inspect_out, _ = await _run(
        "docker", "inspect", "--format",
        "{{.Name}}|{{.Config.Image}}|{{.State.Status}}|{{.State.StartedAt}}|{{.HostConfig.RestartPolicy.Name}}",
        container_name,
        timeout=5,
    )
    if code != 0:
        return None

    parts = [p.strip() for p in inspect_out.strip().split("|")]
    real_name = parts[0].lstrip("/") if len(parts) > 0 else container_name
    image = parts[1] if len(parts) > 1 else ""
    state = parts[2] if len(parts) > 2 else ""
    started_at = parts[3] if len(parts) > 3 else ""
    restart_policy = parts[4] if len(parts) > 4 else "no"

    code, stats_out, _ = await _run(
        "docker", "stats", "--no-stream",
        "--format", "{{.CPUPerc}}|{{.MemUsage}}|{{.MemPerc}}|{{.NetIO}}|{{.BlockIO}}|{{.PIDs}}",
        container_name,
        timeout=10,
    )

    cpu_percent = "0.00%"
    mem_usage = "0B / 0B"
    mem_percent = "0.00%"
    net_io = "0B / 0B"
    block_io = "0B / 0B"
    pids = "0"

    if code == 0 and stats_out.strip():
        s_parts = [p.strip() for p in stats_out.strip().split("|")]
        cpu_percent = s_parts[0] if len(s_parts) > 0 else cpu_percent
        mem_usage = s_parts[1] if len(s_parts) > 1 else mem_usage
        mem_percent = s_parts[2] if len(s_parts) > 2 else mem_percent
        net_io = s_parts[3] if len(s_parts) > 3 else net_io
        block_io = s_parts[4] if len(s_parts) > 4 else block_io
        pids = s_parts[5] if len(s_parts) > 5 else pids

    code, log_out, _ = await _run(
        "docker", "logs", "--tail", "10", "--timestamps", container_name,
        timeout=5,
    )
    recent_logs = log_out.strip().splitlines() if code == 0 else []

    uptime = ""
    if started_at and state == "running":
        try:
            start_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
            delta = datetime.now(timezone.utc) - start_dt
            days = delta.days
            hours, remainder = divmod(delta.seconds, 3600)
            minutes, _ = divmod(remainder, 60)
            parts_uptime = []
            if days > 0:
                parts_uptime.append(f"{days}d")
            if hours > 0:
                parts_uptime.append(f"{hours}h")
            parts_uptime.append(f"{minutes}m")
            uptime = " ".join(parts_uptime)
        except (ValueError, AttributeError):
            uptime = started_at

    return {
        "name": real_name,
        "image": image,
        "state": state,
        "started_at": started_at,
        "uptime": uptime,
        "restart_policy": restart_policy,
        "cpu_percent": cpu_percent,
        "mem_usage": mem_usage,
        "mem_percent": mem_percent,
        "net_io": net_io,
        "block_io": block_io,
        "pids": pids,
        "recent_logs": recent_logs[-5:],
    }


async def get_service_status(bot=None) -> dict:
    services = {}

    async def _check_discord() -> dict:
        if bot is None:
            return {
                "name": "Discord Bot",
                "status": "offline",
                "detail": "Bot instance not provided",
            }
        if not hasattr(bot, "is_ready"):
            return {
                "name": "Discord Bot",
                "status": "critical",
                "detail": "Bot instance invalid",
            }
        if bot.is_ready():
            guilds = getattr(bot, "guilds", [])
            latency_ms = round(bot.latency * 1000) if bot.latency else 0
            users = sum(g.member_count or 0 for g in guilds)
            return {
                "name": "Discord Bot",
                "status": "healthy",
                "detail": f"Connected: {len(guilds)} guild(s), {users} user(s), {latency_ms}ms latency",
            }
        return {
            "name": "Discord Bot",
            "status": "critical",
            "detail": "Bot connected but not ready",
        }

    async def _check_docker() -> dict:
        if shutil.which("docker") is None:
            return {
                "name": "Docker",
                "status": "offline",
                "detail": "Docker binary not found in PATH",
            }
        code, out, err = await _run("docker", "ps", "--format", "{{.Names}}", timeout=5)
        if code == 0:
            count = len([l for l in out.strip().splitlines() if l.strip()])
            return {
                "name": "Docker",
                "status": "healthy",
                "detail": f"{count} running container(s)",
            }
        return {
            "name": "Docker",
            "status": "critical",
            "detail": f"Docker daemon error: {err.strip()[:100]}",
        }

    async def _check_database() -> dict:
        data_path = Path(DATA_DIR)
        if not data_path.exists():
            return {
                "name": "Database",
                "status": "critical",
                "detail": f"Data directory missing: {DATA_DIR}",
            }
        json_files = list(data_path.glob("*.json"))
        total_size = sum(f.stat().st_size for f in json_files if f.exists())
        return {
            "name": "Database",
            "status": "healthy",
            "detail": f"{len(json_files)} file(s), {_bytes_to_mb(total_size)}MB total",
        }

    async def _check_api() -> dict:
        api_port = 8000
        api_host = "127.0.0.1"
        api_path = os.path.join(BASE_DIR, "api.py")
        if not os.path.isfile(api_path):
            return {
                "name": "API Server",
                "status": "critical",
                "detail": "api.py not found",
            }
        if _port_is_listening(api_port, api_host):
            return {
                "name": "API Server",
                "status": "healthy",
                "detail": f"Listening on {api_host}:{api_port}",
            }
        return {
            "name": "API Server",
            "status": "warning",
            "detail": f"Not listening on port {api_port}",
        }

    async def _check_nodes() -> dict:
        nodes_path = Path(DATA_DIR) / "nodes.json"
        if not nodes_path.exists():
            return {
                "name": "Nodes Registry",
                "status": "warning",
                "detail": "nodes.json not found",
            }
        nodes_data = await asyncio.get_running_loop().run_in_executor(
            None, load_json, str(nodes_path), {}
        )
        if not nodes_data:
            return {
                "name": "Nodes Registry",
                "status": "warning",
                "detail": "No nodes registered",
            }
        if isinstance(nodes_data, dict):
            nodes_list = nodes_data.get("nodes", [])
        elif isinstance(nodes_data, list):
            nodes_list = nodes_data
        else:
            nodes_list = []

        if not nodes_list:
            return {
                "name": "Nodes Registry",
                "status": "warning",
                "detail": "No nodes registered",
            }

        reachable = 0
        unreachable = 0
        for node in nodes_list:
            host = node.get("host") or node.get("ip") or node.get("address", "")
            port = int(node.get("port", 22))
            if host:
                try:
                    _, writer = await asyncio.wait_for(
                        asyncio.open_connection(host, port),
                        timeout=3,
                    )
                    writer.close()
                    await writer.wait_closed()
                    reachable += 1
                except (asyncio.TimeoutError, OSError, ConnectionRefusedError):
                    unreachable += 1

        total = reachable + unreachable
        if unreachable == 0:
            status = "healthy"
        elif reachable > 0:
            status = "warning"
        else:
            status = "critical"

        return {
            "name": "Nodes Registry",
            "status": status,
            "detail": f"{reachable}/{total} node(s) reachable",
        }

    services["discord"] = await _check_discord()
    services["docker"] = await _check_docker()
    services["database"] = await _check_database()
    services["api"] = await _check_api()
    services["nodes"] = await _check_nodes()

    for svc in services.values():
        if svc["status"] == "critical":
            bus.emit("infra:service_critical", {"service": svc["name"], "detail": svc["detail"]})
        elif svc["status"] == "offline":
            bus.emit("infra:service_offline", {"service": svc["name"], "detail": svc["detail"]})

    return services


async def get_infrastructure_dashboard(bot=None) -> dict:
    host_stats, docker_status, services = await asyncio.gather(
        get_host_stats(),
        get_docker_status(),
        get_service_status(bot),
        return_exceptions=True,
    )

    if isinstance(host_stats, Exception):
        host_stats = {"error": str(host_stats)}
    if isinstance(docker_status, Exception):
        docker_status = {"error": str(docker_status)}
    if isinstance(services, Exception):
        services = {"error": str(services)}

    overall = "healthy"
    if isinstance(services, dict):
        for svc in services.values():
            if isinstance(svc, dict):
                if svc.get("status") == "critical":
                    overall = "critical"
                    break
                elif svc.get("status") == "warning" and overall != "critical":
                    overall = "warning"
                elif svc.get("status") == "offline" and overall not in ("critical", "warning"):
                    overall = "offline"

    if isinstance(host_stats, dict):
        cpu = host_stats.get("cpu_percent", 0)
        ram = host_stats.get("ram_percent", 0)
        disk = host_stats.get("disk_percent", 0)
        if cpu > 90 or ram > 90 or disk > 90:
            overall = "critical"
        elif (cpu > 75 or ram > 75 or disk > 80) and overall == "healthy":
            overall = "warning"

    bus.emit("infra:dashboard_refresh", {
        "overall": overall,
        "timestamp": time.time(),
    })

    return {
        "overall_status": overall,
        "host": host_stats,
        "docker": docker_status,
        "services": services,
        "timestamp": time.time(),
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
