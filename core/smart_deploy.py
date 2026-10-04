import asyncio
import logging
import time
import json
import os
import aiohttp

from core.services import metrics, bus, load_json, save_json, DATA_DIR, BASE_DIR
from core.infrastructure import get_host_stats, get_docker_status

logger = logging.getLogger("turtle.smart_deploy")

DEPLOY_TIMEOUT = 180
STATS_TIMEOUT = 10
HEALTH_DECAY_INTERVAL = 300


class NodeCandidate:
    def __init__(
        self, name, host, port, cpu_percent, ram_percent, disk_percent,
        vps_count, total_ram_gb, used_ram_gb, total_cpu, available_cpu,
        health_score, online, docker_running
    ):
        self.name = name
        self.host = host
        self.port = port
        self.cpu_percent = cpu_percent
        self.ram_percent = ram_percent
        self.disk_percent = disk_percent
        self.vps_count = vps_count
        self.total_ram_gb = total_ram_gb
        self.used_ram_gb = used_ram_gb
        self.total_cpu = total_cpu
        self.available_cpu = available_cpu
        self.health_score = health_score
        self.online = online
        self.docker_running = docker_running

    @property
    def available_ram_gb(self):
        return max(0.0, self.total_ram_gb - self.used_ram_gb)

    @property
    def available_disk_gb(self):
        return max(0.0, self.total_ram_gb * (1 - self.disk_percent / 100) * 0.8)

    def to_dict(self):
        return {
            "name": self.name,
            "host": self.host,
            "port": self.port,
            "cpu_percent": round(self.cpu_percent, 1),
            "ram_percent": round(self.ram_percent, 1),
            "disk_percent": round(self.disk_percent, 1),
            "vps_count": self.vps_count,
            "total_ram_gb": round(self.total_ram_gb, 2),
            "used_ram_gb": round(self.used_ram_gb, 2),
            "available_ram_gb": round(self.available_ram_gb, 2),
            "total_cpu": self.total_cpu,
            "available_cpu": round(self.available_cpu, 2),
            "health_score": round(self.health_score, 1),
            "online": self.online,
            "docker_running": self.docker_running,
        }


async def _docker(*args, timeout=15):
    cmd = ["docker"] + list(args)
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        return -1, "", "timeout"
    return proc.returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")


class SmartDeployer:
    def __init__(self, bot):
        self.bot = bot
        self._cache = {}
        self._cache_ttl = 15
        self._last_health_update = 0
        self._health_scores = {}

    async def _fetch_remote_stats(self, host, port):
        url = f"http://{host}:{port}/stats"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=STATS_TIMEOUT)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return True, data
                    return False, None
        except Exception as e:
            logger.warning("Failed to fetch stats from %s:%s - %s", host, port, e)
            return False, None

    async def _get_local_stats(self):
        cache_key = "local_stats"
        now = time.time()
        if cache_key in self._cache:
            ts, data = self._cache[cache_key]
            if now - ts < self._cache_ttl:
                return data

        rc, stdout, _ = await _docker("stats", "--no-stream", "--format", "{{.CPUPerc}}|{{.MemPerc}}|{{.MemUsage}}")
        containers = {}
        if rc == 0:
            for line in stdout.strip().splitlines():
                parts = line.split("|")
                if len(parts) == 3:
                    cpu_str = parts[0].strip().replace("%", "")
                    ram_str = parts[1].strip().replace("%", "")
                    ram_parts = parts[2].split("/")
                    containers.setdefault("running", []).append({
                        "cpu_percent": float(cpu_str) if cpu_str else 0.0,
                        "ram_percent": float(ram_str) if ram_str else 0.0,
                    })

        rc2, out2, _ = await _docker("ps", "-a", "--format", "{{.Names}}")
        total_vps = len(out2.strip().splitlines()) if rc2 == 0 and out2.strip() else 0

        try:
            host_stats = await get_host_stats()
        except Exception:
            host_stats = {"cpu_percent": 0, "ram_total_gb": 4, "ram_used_gb": 0, "disk_total_gb": 80, "disk_used_gb": 0}

        docker_ok = False
        try:
            docker_ok = await get_docker_status()
        except Exception:
            pass

        result = {
            "cpu_percent": host_stats.get("cpu_percent", 0),
            "ram_total_gb": host_stats.get("ram_total_gb", 4),
            "ram_used_gb": host_stats.get("ram_used_gb", 0),
            "ram_percent": (host_stats.get("ram_used_gb", 0) / max(host_stats.get("ram_total_gb", 1), 1)) * 100,
            "disk_total_gb": host_stats.get("disk_total_gb", 80),
            "disk_used_gb": host_stats.get("disk_used_gb", 0),
            "disk_percent": (host_stats.get("disk_used_gb", 0) / max(host_stats.get("disk_total_gb", 1), 1)) * 100,
            "total_cpu": host_stats.get("total_cpu", os.cpu_count() or 1),
            "available_cpu": max(0, (host_stats.get("total_cpu", os.cpu_count() or 1)) * (1 - host_stats.get("cpu_percent", 0) / 100)),
            "vps_count": total_vps,
            "docker_running": docker_ok,
            "containers": containers,
        }
        self._cache[cache_key] = (now, result)
        return result

    async def _build_candidate(self, node_cfg):
        name = node_cfg.get("name", "unknown")
        host = node_cfg.get("host", "127.0.0.1")
        port = node_cfg.get("port", 2375)
        is_local = host in ("127.0.0.1", "localhost", "::1")

        if is_local:
            stats = await self._get_local_stats()
            online = stats.get("docker_running", False)
            docker_running = stats.get("docker_running", False)
            cpu_percent = stats.get("cpu_percent", 0)
            ram_percent = stats.get("ram_percent", 0)
            disk_percent = stats.get("disk_percent", 0)
            total_ram_gb = stats.get("ram_total_gb", 4)
            used_ram_gb = stats.get("ram_used_gb", 0)
            total_cpu = stats.get("total_cpu", os.cpu_count() or 1)
            available_cpu = stats.get("available_cpu", total_cpu * 0.5)
            vps_count = stats.get("vps_count", 0)
        else:
            online, data = await self._fetch_remote_stats(host, port)
            if online and data:
                docker_running = data.get("docker_running", True)
                cpu_percent = data.get("cpu_percent", 0)
                ram_percent = data.get("ram_percent", 0)
                disk_percent = data.get("disk_percent", 0)
                total_ram_gb = data.get("ram_total_gb", 4)
                used_ram_gb = data.get("ram_used_gb", 0)
                total_cpu = data.get("total_cpu", 2)
                available_cpu = data.get("available_cpu", total_cpu * 0.5)
                vps_count = data.get("vps_count", 0)
            else:
                docker_running = False
                cpu_percent = 100
                ram_percent = 100
                disk_percent = 100
                total_ram_gb = 0
                used_ram_gb = 0
                total_cpu = 0
                available_cpu = 0
                vps_count = 0

        health = self._get_health_score(name)
        candidate = NodeCandidate(
            name=name, host=host, port=port,
            cpu_percent=cpu_percent, ram_percent=ram_percent, disk_percent=disk_percent,
            vps_count=vps_count, total_ram_gb=total_ram_gb, used_ram_gb=used_ram_gb,
            total_cpu=total_cpu, available_cpu=available_cpu,
            health_score=health, online=online, docker_running=docker_running,
        )
        self._update_health(name, candidate)
        return candidate

    def _get_health_score(self, node_name):
        if node_name in self._health_scores:
            entry = self._health_scores[node_name]
            age = time.time() - entry.get("last_update", 0)
            decay = min(age / HEALTH_DECAY_INTERVAL, 1.0)
            return entry["score"] * (1 - decay * 0.1)
        return 50.0

    def _update_health(self, node_name, candidate):
        score = self.calculate_node_score(candidate)
        now = time.time()
        prev = self._health_scores.get(node_name, {"score": score, "last_update": now, "history": []})
        history = prev.get("history", [])
        history.append((now, score))
        history = history[-20:]
        avg_score = sum(s for _, s in history) / max(len(history), 1)
        self._health_scores[node_name] = {
            "score": avg_score,
            "last_update": now,
            "history": history,
        }

    def calculate_node_score(self, candidate):
        if not candidate.online or not candidate.docker_running:
            return 0.0
        cpu_score = max(0, 100 - candidate.cpu_percent)
        ram_score = max(0, 100 - candidate.ram_percent)
        disk_score = max(0, 100 - candidate.disk_percent)
        max_vps = max(candidate.total_ram_gb * 2, 5)
        load_score = max(0, 100 - (candidate.vps_count / max(max_vps, 1)) * 100)
        weighted = (cpu_score * 0.3) + (ram_score * 0.3) + (disk_score * 0.2) + (load_score * 0.2)
        return round(min(100, max(0, weighted)), 2)

    async def evaluate_nodes(self):
        nodes_path = os.path.join(DATA_DIR, "nodes.json")
        nodes_reg = load_json(nodes_path, default=[])
        if not nodes_reg:
            logger.warning("No nodes found in nodes.json")
            return []

        tasks = [self._build_candidate(n) for n in nodes_reg]
        candidates = await asyncio.gather(*tasks, return_exceptions=True)
        valid = []
        for c in candidates:
            if isinstance(c, Exception):
                logger.error("Error building candidate: %s", c)
                continue
            valid.append(c)
        valid.sort(key=lambda c: c.health_score, reverse=True)
        return valid

    def select_best_node(self, candidates, ram_gb, cpu_cores, disk_gb):
        filtered = [
            c for c in candidates
            if c.online and c.docker_running
            and c.available_ram_gb >= ram_gb
            and c.available_cpu >= cpu_cores
            and c.available_disk_gb >= disk_gb
        ]
        if not filtered:
            return None

        for c in filtered:
            c._composite = (
                c.health_score * 0.40
                + self._normalize(c.available_ram_gb, 64) * 25
                + self._normalize(c.available_cpu, 32) * 20
                + self._normalize(max(0, 20 - c.vps_count), 20) * 15
            )
        filtered.sort(key=lambda c: c._composite, reverse=True)
        return filtered[0]

    def _normalize(self, value, max_val):
        return min(100, (value / max(max_val, 1)) * 100)

    async def deploy_smart(self, user_id, ram_gb, cpu_cores, disk_gb, os_image, password):
        logger.info("Smart deploy requested: user=%s ram=%s cpu=%s disk=%s os=%s", user_id, ram_gb, cpu_cores, disk_gb, os_image)
        candidates = await self.evaluate_nodes()
        if not candidates:
            return {
                "success": False,
                "reason": "No nodes registered. Add nodes to nodes.json first.",
                "node": None,
            }

        node = self.select_best_node(candidates, ram_gb, cpu_cores, disk_gb)
        if not node:
            return {
                "success": False,
                "reason": f"No nodes with sufficient capacity (need {ram_gb}GB RAM, {cpu_cores} CPU, {disk_gb}GB disk).",
                "node": None,
                "candidates": [c.to_dict() for c in candidates],
            }

        container_name = f"vps_{user_id}_{int(time.time())}"
        try:
            result = await self._deploy_container(node, container_name, ram_gb, cpu_cores, disk_gb, os_image, password)
            if result.get("success"):
                bus.emit("vps:deployed", {
                    "user_id": user_id,
                    "node": node.name,
                    "container": container_name,
                    "ram_gb": ram_gb,
                    "cpu_cores": cpu_cores,
                    "disk_gb": disk_gb,
                })
                metrics.increment("deployments.success")
            else:
                metrics.increment("deployments.failed")
            return result
        except Exception as e:
            logger.error("Deployment failed on %s: %s", node.name, e)
            metrics.increment("deployments.failed")
            return {
                "success": False,
                "reason": str(e),
                "node": node.name,
            }

    async def _deploy_container(self, node, container_name, ram_gb, cpu_cores, disk_gb, os_image, password):
        ram_bytes = int(ram_gb * 1024 * 1024 * 1024)
        cpu_quota = int(cpu_cores * 100000)
        disk_bytes = int(disk_gb * 1024 * 1024 * 1024)

        cmd = [
            "run", "-d",
            "--name", container_name,
            "--hostname", container_name,
            "-m", str(ram_bytes),
            "--cpu-quota", str(cpu_quota),
            "--storage-opt", f"size={disk_bytes}",
            "-e", f"ROOT_PASS={password}",
            "-e", f"OS_IMAGE={os_image}",
            "--restart", "unless-stopped",
            "-d", os_image,
        ]

        is_local = node.host in ("127.0.0.1", "localhost", "::1")
        if is_local:
            rc, stdout, stderr = await _docker(*cmd, timeout=DEPLOY_TIMEOUT)
            if rc != 0:
                return {
                    "success": False,
                    "reason": f"Docker error: {stderr.strip()}",
                    "node": node.name,
                }
            container_id = stdout.strip()[:12]
        else:
            url = f"http://{node.host}:{node.port}/containers/{container_name}/create"
            payload = {
                "Image": os_image,
                "Hostname": container_name,
                "HostConfig": {
                    "Memory": ram_bytes,
                    "NanoCpus": int(cpu_cores * 1e9),
                    "StorageOpt": {"size": f"{disk_gb}GB"},
                    "RestartPolicy": {"Name": "unless-stopped"},
                },
                "Env": [f"ROOT_PASS={password}", f"OS_IMAGE={os_image}"],
            }
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=DEPLOY_TIMEOUT)) as resp:
                        if resp.status not in (200, 201):
                            body = await resp.text()
                            return {
                                "success": False,
                                "reason": f"Remote API error ({resp.status}): {body[:200]}",
                                "node": node.name,
                            }
                        data = await resp.json()
                        container_id = data.get("Id", "")[:12]
            except Exception as e:
                return {
                    "success": False,
                    "reason": f"Connection error to {node.host}:{node.port} - {e}",
                    "node": node.name,
                }

        start_url = (
            f"http://{node.host}:{node.port}/containers/{container_name}/start"
            if not is_local else None
        )
        if is_local:
            rc2, _, err2 = await _docker("start", container_name, timeout=30)
            if rc2 != 0:
                await _docker("rm", "-f", container_name, timeout=10)
                return {
                    "success": False,
                    "reason": f"Container created but failed to start: {err2.strip()}",
                    "node": node.name,
                }
        else:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(start_url, timeout=aiohttp.ClientTimeout(total=30)) as resp:
                        if resp.status != 204:
                            return {
                                "success": False,
                                "reason": f"Container started failed (HTTP {resp.status})",
                                "node": node.name,
                            }
            except Exception as e:
                return {
                    "success": False,
                    "reason": f"Failed to start container: {e}",
                    "node": node.name,
                }

        save_json(os.path.join(DATA_DIR, "deployments.json"), {
            container_name: {
                "user_id": None,
                "node": node.name,
                "node_host": node.host,
                "ram_gb": ram_gb,
                "cpu_cores": cpu_cores,
                "disk_gb": disk_gb,
                "os_image": os_image,
                "created_at": time.time(),
                "container_id": container_id,
            }
        }, mode="append")

        return {
            "success": True,
            "node": node.name,
            "container_name": container_name,
            "container_id": container_id,
            "message": f"VPS deployed on {node.name} - {ram_gb}GB RAM, {cpu_cores} CPU, {disk_gb}GB disk.",
        }

    async def get_deployment_preview(self, ram_gb, cpu_cores, disk_gb):
        try:
            candidates = await self.evaluate_nodes()
        except Exception:
            candidates = []

        if not candidates:
            return {
                "available": False,
                "reason": "No nodes registered or reachable.",
                "selected_node": None,
            }

        node = self.select_best_node(candidates, ram_gb, cpu_cores, disk_gb)
        if not node:
            return {
                "available": False,
                "reason": "No node has sufficient resources for this deployment.",
                "candidates": [
                    {"name": c.name, "available_ram": round(c.available_ram_gb, 2), "health": c.health_score}
                    for c in candidates
                ],
                "selected_node": None,
            }

        impact = {
            "current_ram_used": round(node.used_ram_gb, 2),
            "after_ram_used": round(node.used_ram_gb + ram_gb, 2),
            "current_ram_percent": round(node.ram_percent, 1),
            "after_ram_percent": round(((node.used_ram_gb + ram_gb) / max(node.total_ram_gb, 1)) * 100, 1),
            "current_cpu_percent": round(node.cpu_percent, 1),
            "current_vps_count": node.vps_count,
            "after_vps_count": node.vps_count + 1,
        }

        return {
            "available": True,
            "selected_node": node.to_dict(),
            "reason": f"Best match: {node.name} (health={node.health_score:.0f}, available RAM={node.available_ram_gb:.1f}GB)",
            "resource_impact": impact,
        }

    async def get_node_capacity_summary(self):
        try:
            candidates = await self.evaluate_nodes()
        except Exception:
            return []

        summaries = []
        for c in candidates:
            utilization = (c.ram_percent + c.cpu_percent + c.disk_percent) / 3
            if utilization > 90:
                recommendation = "OVERLOADED - Do not deploy"
            elif utilization > 70:
                recommendation = "HEAVY - Deploy with caution"
            elif utilization > 40:
                recommendation = "MODERATE - Good for small VPS"
            else:
                recommendation = "LIGHT - Ideal for new deployments"

            if not c.online:
                recommendation = "OFFLINE - Unreachable"
            elif not c.docker_running:
                recommendation = "DOCKER DOWN - Restart required"

            summaries.append({
                "name": c.name,
                "host": c.host,
                "online": c.online,
                "docker_running": c.docker_running,
                "health_score": round(c.health_score, 1),
                "vps_count": c.vps_count,
                "total_resources": {
                    "ram_gb": round(c.total_ram_gb, 2),
                    "cpu_cores": c.total_cpu,
                },
                "used_resources": {
                    "ram_gb": round(c.used_ram_gb, 2),
                    "cpu_percent": round(c.cpu_percent, 1),
                    "disk_percent": round(c.disk_percent, 1),
                },
                "available_resources": {
                    "ram_gb": round(c.available_ram_gb, 2),
                    "cpu_cores": round(c.available_cpu, 2),
                    "disk_gb": round(c.available_disk_gb, 2),
                },
                "recommendation": recommendation,
            })
        return summaries
