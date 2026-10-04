import asyncio
import logging
import os
import time
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, save_json, DATA_DIR

logger = logging.getLogger("vps_health")

BACKUP_DIR = os.path.join(DATA_DIR, "backups")
VPS_DIR = os.path.join(DATA_DIR, "vps")


async def _docker(*args, timeout: int = 15) -> tuple[int, str, str]:
    cmd = ["docker", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    except asyncio.TimeoutError:
        logger.warning("docker command timed out: %s", " ".join(cmd))
        return -1, "", "timeout"
    except FileNotFoundError:
        logger.error("docker binary not found")
        return -2, "", "docker not installed"
    except Exception as exc:
        logger.error("docker command failed: %s", exc)
        return -3, "", str(exc)


class VPSHealthScorer:

    def __init__(self, bot):
        self.bot = bot

    # ------------------------------------------------------------------
    # Individual scoring methods
    # ------------------------------------------------------------------

    def _threshold(self, pct: float) -> dict:
        if pct < 50:
            return {"score": 100, "status": "ok", "detail": f"{pct:.1f}% - nominal"}
        if pct < 75:
            return {"score": 80, "status": "ok", "detail": f"{pct:.1f}% - elevated"}
        if pct < 90:
            return {"score": 60, "status": "warning", "detail": f"{pct:.1f}% - high"}
        if pct < 95:
            return {"score": 30, "status": "warning", "detail": f"{pct:.1f}% - critical"}
        return {"score": 10, "status": "critical", "detail": f"{pct:.1f}% - overloaded"}

    async def score_cpu(self, container_name: str) -> dict:
        rc, out, err = await _docker(
            "stats", "--no-stream", "--format", "{{.CPUPerc}}", container_name,
        )
        if rc != 0:
            return {"score": 0, "status": "critical", "detail": f"docker stats failed: {err.strip()}"}
        raw = out.strip().replace("%", "").strip()
        try:
            pct = float(raw)
        except ValueError:
            return {"score": 0, "status": "critical", "detail": f"unparseable CPU: {raw!r}"}
        return self._threshold(pct)

    async def score_ram(self, container_name: str) -> dict:
        rc, out, err = await _docker(
            "stats", "--no-stream", "--format", "{{.MemPerc}}", container_name,
        )
        if rc != 0:
            return {"score": 0, "status": "critical", "detail": f"docker stats failed: {err.strip()}"}
        raw = out.strip().replace("%", "").strip()
        try:
            pct = float(raw)
        except ValueError:
            return {"score": 0, "status": "critical", "detail": f"unparseable RAM: {raw!r}"}
        return self._threshold(pct)

    async def score_disk(self, container_name: str) -> dict:
        rc, out, err = await _docker("exec", container_name, "df", "/")
        if rc != 0:
            return {"score": 0, "status": "critical", "detail": f"df failed: {err.strip()}"}
        lines = out.strip().splitlines()
        if len(lines) < 2:
            return {"score": 0, "status": "critical", "detail": "df output malformed"}
        parts = lines[1].split()
        try:
            pct = float(parts[4].replace("%", ""))
        except (IndexError, ValueError):
            return {"score": 0, "status": "critical", "detail": f"df parse error: {lines[1]!r}"}
        return self._threshold(pct)

    async def score_network(self, container_name: str) -> dict:
        rc, out, err = await _docker(
            "stats", "--no-stream", "--format", "{{.NetIO}}", container_name,
        )
        if rc != 0:
            return {"score": 30, "status": "critical", "detail": f"net stats failed: {err.strip()}"}
        raw = out.strip()
        if not raw or "/" not in raw:
            return {"score": 30, "status": "warning", "detail": f"unexpected net format: {raw!r}"}
        try:
            parts = raw.split("/")
            inbound = parts[0].strip()
            outbound = parts[1].strip()

            def _parse_size(s: str) -> float:
                s = s.strip()
                multiplier = 1.0
                if s.endswith("GB"):
                    multiplier = 1_073_741_824
                    s = s[:-2]
                elif s.endswith("MB"):
                    multiplier = 1_048_576
                    s = s[:-2]
                elif s.endswith("kB"):
                    multiplier = 1_024
                    s = s[:-2]
                elif s.endswith("B"):
                    s = s[:-1]
                return float(s) * multiplier

            total = _parse_size(inbound) + _parse_size(outbound)
            if total > 10 * 1_073_741_824:
                return {"score": 30, "status": "warning", "detail": f"very high I/O: {raw}"}
            if total > 5 * 1_073_741_824:
                return {"score": 70, "status": "warning", "detail": f"high I/O: {raw}"}
            return {"score": 100, "status": "ok", "detail": f"normal: {raw}"}
        except Exception as exc:
            return {"score": 30, "status": "warning", "detail": f"net parse error: {exc}"}

    async def score_ssh(self, container_name: str) -> dict:
        rc, out, _ = await _docker(
            "exec", container_name, "bash", "-c",
            "sshd -t 2>/dev/null && echo OK || (pgrep -x sshd >/dev/null && echo OK || echo FAIL)",
        )
        if rc == 0 and "OK" in out:
            return {"score": 100, "status": "ok", "detail": "sshd running"}
        return {"score": 0, "status": "critical", "detail": "sshd not running or misconfigured"}

    async def score_container(self, container_name: str) -> dict:
        rc, out, err = await _docker(
            "inspect", "--format",
            "{{.State.Running}}|{{.State.OOMKilled}}|{{.RestartCount}}",
            container_name,
        )
        if rc != 0:
            return {"score": 0, "status": "critical", "detail": f"inspect failed: {err.strip()}"}
        parts = out.strip().split("|")
        if len(parts) < 3:
            return {"score": 0, "status": "critical", "detail": f"inspect parse: {out.strip()!r}"}
        running = parts[0].lower() == "true"
        oom = parts[1].lower() == "true"
        restarts = int(parts[2]) if parts[2].isdigit() else 0

        if not running:
            return {"score": 0, "status": "critical", "detail": "container stopped"}
        if oom:
            return {"score": 25, "status": "critical", "detail": "OOM killed recently"}
        if restarts > 5:
            return {"score": 50, "status": "warning", "detail": f"container restarting ({restarts} restarts)"}
        return {"score": 100, "status": "ok", "detail": f"running, {restarts} restarts"}

    async def score_uptime(self, vps_info: dict) -> dict:
        created = vps_info.get("created_at") or vps_info.get("last_restart")
        if not created:
            return {"score": 50, "status": "ok", "detail": "no timestamp available"}
        try:
            if isinstance(created, (int, float)):
                age_s = time.time() - created
            else:
                dt = datetime.fromisoformat(str(created))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                age_s = (datetime.now(timezone.utc) - dt).total_seconds()
        except Exception:
            return {"score": 50, "status": "ok", "detail": "could not parse timestamp"}

        if age_s > 7 * 86400:
            return {"score": 100, "status": "ok", "detail": f"uptime >7d ({age_s / 86400:.1f}d)"}
        if age_s > 86400:
            return {"score": 90, "status": "ok", "detail": f"uptime >1d ({age_s / 86400:.1f}d)"}
        if age_s > 43200:
            return {"score": 70, "status": "ok", "detail": f"uptime >12h ({age_s / 3600:.1f}h)"}
        if age_s > 3600:
            return {"score": 50, "status": "warning", "detail": f"uptime >1h ({age_s / 3600:.1f}h)"}
        return {"score": 30, "status": "warning", "detail": f"uptime <1h ({age_s / 60:.0f}m)"}

    async def score_backups(self, vps_info: dict) -> dict:
        vps_id = vps_info.get("id") or vps_info.get("container_name", "unknown")
        backup_pattern = os.path.join(BACKUP_DIR, f"{vps_id}*")
        rc, out, _ = await _docker(
            "run", "--rm", "-v", f"{BACKUP_DIR}:/backups:ro", "alpine",
            "sh", "-c", f"ls -t /backups/{vps_id}* 2>/dev/null | head -1",
        )
        try:
            rc2, out2, _ = await asyncio.create_subprocess_exec(
                "sh", "-c", f"ls -t {backup_pattern} 2>/dev/null | head -1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, _ = await asyncio.wait_for(rc2.communicate() if hasattr(rc2, "communicate") else asyncio.sleep(0), timeout=5)
        except Exception:
            pass

        import glob as _glob
        matches = sorted(_glob.glob(backup_pattern), key=lambda p: _glob.os.path.getmtime(p) if _glob.os.path.exists(p) else 0, reverse=True)

        if not matches:
            return {"score": 30, "status": "warning", "detail": "no backups found"}

        try:
            mtime = _glob.os.path.getmtime(matches[0])
            age_days = (time.time() - mtime) / 86400
        except Exception:
            return {"score": 70, "status": "ok", "detail": "backup exists (age unknown)"}

        if age_days < 7:
            return {"score": 100, "status": "ok", "detail": f"backup {age_days:.1f}d old"}
        return {"score": 70, "status": "ok", "detail": f"backup {age_days:.1f}d old (stale)"}

    async def score_crashes(self, container_name: str) -> dict:
        rc, out, err = await _docker(
            "inspect", "--format", "{{.RestartCount}}", container_name,
        )
        if rc != 0:
            return {"score": 0, "status": "critical", "detail": f"inspect failed: {err.strip()}"}
        try:
            count = int(out.strip())
        except ValueError:
            return {"score": 0, "status": "critical", "detail": f"bad RestartCount: {out.strip()!r}"}

        if count == 0:
            return {"score": 100, "status": "ok", "detail": "no crashes"}
        if count < 3:
            return {"score": 80, "status": "ok", "detail": f"{count} restarts (minor)"}
        if count < 10:
            return {"score": 50, "status": "warning", "detail": f"{count} restarts (frequent)"}
        return {"score": 20, "status": "critical", "detail": f"{count} restarts (crash loop?)"}

    # ------------------------------------------------------------------
    # Composite health calculation
    # ------------------------------------------------------------------

    WEIGHTS = {
        "cpu": 15,
        "ram": 15,
        "disk": 15,
        "network": 10,
        "ssh": 10,
        "container": 15,
        "uptime": 5,
        "backups": 10,
        "crashes": 5,
    }

    async def calculate_health(self, user_id: int, vps_index: int) -> dict:
        user_data = load_json(f"{user_id}.json", subdir="users")
        if not user_data:
            return {"score": 0, "grade": "F", "status": "critical", "breakdown": {}, "progress_bar": score_to_bar(0)}
        vps_list = user_data.get("vps", [])
        if vps_index < 0 or vps_index >= len(vps_list):
            return {"score": 0, "grade": "F", "status": "critical", "breakdown": {}, "progress_bar": score_to_bar(0)}
        vps_info = vps_list[vps_index]
        container_name = vps_info.get("container_name", f"vps_{user_id}_{vps_index}")

        results = {}
        results["cpu"] = await self.score_cpu(container_name)
        results["ram"] = await self.score_ram(container_name)
        results["disk"] = await self.score_disk(container_name)
        results["network"] = await self.score_network(container_name)
        results["ssh"] = await self.score_ssh(container_name)
        results["container"] = await self.score_container(container_name)
        results["uptime"] = await self.score_uptime(vps_info)
        results["backups"] = await self.score_backups(vps_info)
        results["crashes"] = await self.score_crashes(container_name)

        total_weight = sum(self.WEIGHTS.values())
        weighted_sum = 0.0
        for factor, weight in self.WEIGHTS.items():
            factor_score = results.get(factor, {}).get("score", 0)
            weighted_sum += factor_score * weight

        final_score = int(round(weighted_sum / total_weight))
        final_score = max(0, min(100, final_score))
        grade = score_to_grade(final_score)

        if final_score >= 80:
            status = "healthy"
        elif final_score >= 50:
            status = "warning"
        else:
            status = "critical"

        try:
            metrics.gauge("vps_health_score", final_score, tags={
                "user_id": str(user_id),
                "vps_index": str(vps_index),
            })
        except Exception:
            pass

        return {
            "score": final_score,
            "grade": grade,
            "status": status,
            "breakdown": results,
            "progress_bar": score_to_bar(final_score),
        }

    # ------------------------------------------------------------------
    # Batch scoring
    # ------------------------------------------------------------------

    async def get_all_scores(self) -> dict:
        all_scores: dict = {}
        users_dir = os.path.join(DATA_DIR, "users")
        if not os.path.isdir(users_dir):
            return all_scores

        for user_file in users_dir.glob("*.json"):
            try:
                user_id = int(user_file.stem)
            except ValueError:
                continue
            user_data = load_json(user_file.name, subdir="users")
            if not user_data:
                continue
            vps_list = user_data.get("vps", [])
            entries = []
            for idx in range(len(vps_list)):
                health = await self.calculate_health(user_id, idx)
                entries.append({
                    "index": idx,
                    "score": health["score"],
                    "grade": health["grade"],
                })
            if entries:
                all_scores[user_id] = entries
        return all_scores


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def score_to_grade(score: int) -> str:
    if score >= 90:
        return "A"
    if score >= 75:
        return "B"
    if score >= 60:
        return "C"
    if score >= 40:
        return "D"
    return "F"


def score_to_bar(score: int, length: int = 10) -> str:
    filled = round(score / 100 * length)
    filled = max(0, min(length, filled))
    bar = "\u2588" * filled + "\u2591" * (length - filled)
    return f"{bar} {score}/100"
