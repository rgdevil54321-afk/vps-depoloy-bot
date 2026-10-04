"""
🌐 Turtle Nodes Node API - REST control plane for the VPS manager.

Standalone, pure standard library. Run it on each host you want the Discord
bot to control remotely (e.g. a second VPS acting as a "node").

Endpoints (JSON):
    GET  /health                        → {status, docker}
    GET  /stats                         → host cpu/ram/disk + container count
    GET  /containers                    → running container names
    GET  /containers/<name>/state       → {running, uptime_sec}
    GET  /containers/<name>/stats       → docker stats for one container
    GET  /containers/<name>/disk        → disk usage for one container
    GET  /containers/<name>/ports       → published ports
    POST /containers/<name>/resize      → {memory_mb, cpus}  (docker update)
    POST /create                        → {name, image, ram_mb, cpu, disk_gb, password, ports[]}
    POST /start | /stop | /restart | /remove   → {name}
    POST /exec                          → {name, cmd, timeout} → {stdout, stderr, rc}

Auth:   Authorization: Bearer <token>
        Token read from API_TOKEN env var, or api_token.txt / .env next to this file.
        If no token is configured, binds to 127.0.0.1 only.

Usage:
    python api.py                # 127.0.0.1:8765
    python api.py --port 8765 --host 0.0.0.0
"""
import os
import sys
import json
import hmac
import shutil
import subprocess
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_IMAGE = "ubuntu:22.04"


def load_token():
    env = os.environ.get("API_TOKEN", "").strip()
    if env:
        return env
    for path in (os.path.join(BASE_DIR, "api_token.txt"), os.path.join(BASE_DIR, ".env")):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("API_TOKEN="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
                    if "=" not in line:
                        return line
        except Exception:
            continue
    return ""


API_TOKEN = load_token()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def run(args, timeout=60):
    try:
        proc = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return proc.stdout, proc.stderr, proc.returncode
    except subprocess.TimeoutExpired:
        return "", "timeout after %ss" % timeout, -1
    except Exception as e:
        return "", str(e), -1


def container_exists(name):
    out, _, rc = run(["docker", "inspect", "--format", "{{.State.Running}}", name], 15)
    if rc != 0:
        return None
    return out.strip() == "true"


def utcnow():
    try:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


def uptime_sec(name):
    out, _, rc = run(["docker", "inspect", "--format", "{{.State.StartedAt}}", name], 15)
    if rc != 0 or not out.strip():
        return 0
    try:
        start = datetime.fromisoformat(out.strip()[:19])
        return max(int((utcnow() - start).total_seconds()), 0)
    except Exception:
        return 0


def host_cpu():
    try:
        out, _, _ = run(["top", "-bn1"], 10)
        for line in out.split("\n"):
            if "%Cpu(s):" in line and "id," in line:
                idle = float(line.split("id,")[0].split(",")[-1].strip().split("%")[0])
                return round(100.0 - idle, 1)
    except Exception:
        pass
    return None


def host_ram():
    try:
        out, _, _ = run(["free", "-b"], 10)
        for line in out.split("\n"):
            if line.startswith("Mem:"):
                parts = line.split()
                total, used = int(parts[1]), int(parts[2])
                return {"total": total, "used": used, "pct": round(used / total * 100, 1) if total else 0}
    except Exception:
        pass
    return None


def host_disk():
    try:
        out, _, _ = run(["df", "-k", "/"], 10)
        rows = out.split("\n")
        if len(rows) > 1 and rows[1].split():
            parts = rows[1].split()
            total_k, used_k = int(parts[1]) * 1024, int(parts[2]) * 1024
            return {"total": total_k, "used": used_k, "pct": round(used_k / total_k * 100, 1) if total_k else 0}
    except Exception:
        pass
    return None


def container_stats(name):
    out, _, rc = run(["docker", "stats", "--no-stream", "--format", "{{json .}}", name], 30)
    if rc != 0 or not out.strip():
        return None
    try:
        return json.loads(out.strip())
    except Exception:
        return None


def container_disk(name):
    out, _, rc = run(
        ["docker", "exec", name, "df", "-k", "/"], 20)
    if rc != 0:
        return None
    rows = out.split("\n")
    if len(rows) < 2:
        return None
    parts = rows[1].split()
    if len(parts) < 5:
        return None
    size_k, used_k, avail_k, pct = int(parts[1]), int(parts[2]), int(parts[3]), parts[4]
    return {"size": size_k * 1024, "used": used_k * 1024, "avail": avail_k * 1024, "pct": pct}


def container_ports(name):
    out, _, rc = run(["docker", "port", name], 15)
    result = []
    if rc == 0:
        for line in out.splitlines():
            parts = line.split(" -> ")
            if len(parts) == 2 and "/" in parts[0]:
                result.append(parts[1].split(":")[-1] + ":" + parts[0].split("/")[0])
    return result


def create_container(payload):
    name = payload.get("name")
    image = payload.get("image") or DEFAULT_IMAGE
    ram_mb = int(payload.get("ram_mb", 1024))
    cpu = str(payload.get("cpu", "1"))
    disk_gb = int(payload.get("disk_gb", 30))
    password = payload.get("password") or "Turtle Nodes"
    ports = payload.get("ports") or []

    if not name or not re_valid_name(name):
        raise ValueError("Invalid container name")
    if container_exists(name) is not None:
        raise ValueError(f"Container '{name}' already exists")

    pull, pull_err, pull_rc = run(["docker", "pull", image], 300)
    if pull_rc != 0:
        raise RuntimeError(f"image pull failed: {pull_err}")

    port_flags = "".join(f" -p {p}" for p in ports if ":" in p)
    create, err, rc = run(
        f"docker run -d --name {name} --memory={ram_mb}m --cpus={cpu} "
        f"--label cloud.disk_gb={disk_gb} --restart=unless-stopped {port_flags} {image} sleep infinity".split(),
        120)
    if rc != 0:
        raise RuntimeError(f"create failed: {err}")

    setup = (
        "apt-get update -qq && apt-get install -y openssh-server tmate curl -qq && "
        "mkdir -p /var/run/sshd && echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && "
        "echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config && "
        f"echo 'root:{password}' | chpasswd && /usr/sbin/sshd"
    )
    out, err, rc = run(["docker", "exec", name, "sh", "-c", setup], 300)
    if rc != 0 and "already" not in err.lower():
        run(["docker", "rm", "-f", name], 30)
        raise RuntimeError(f"setup failed: {err}")
    return {"status": "created", "name": name, "image": image, "ports": ports}


def re_valid_name(name):
    return bool(name) and len(name) <= 63 and all(c.isalnum() or c in "._-" for c in name)


def handle_op(op, payload):
    name = payload.get("name")
    if not name or not re_valid_name(name):
        raise ValueError("Invalid container name")
    if op == "start":
        out, err, rc = run(["docker", "start", name], 120)
        if rc != 0:
            raise RuntimeError(err)
        return {"status": "started", "name": name}
    if op == "stop":
        out, err, rc = run(["docker", "stop", "--time", "30", name], 120)
        if rc != 0 and "already stopped" not in err.lower():
            raise RuntimeError(err)
        return {"status": "stopped", "name": name}
    if op == "restart":
        out, err, rc = run(["docker", "restart", name], 120)
        if rc != 0:
            raise RuntimeError(err)
        try:
            run(["docker", "exec", name, "sh", "-c", "/usr/sbin/sshd || true"], 15)
        except Exception:
            pass
        return {"status": "restarted", "name": name}
    if op == "remove":
        run(["docker", "stop", "--time", "15", name], 60)
        out, err, rc = run(["docker", "rm", "-f", name], 60)
        if rc != 0:
            raise RuntimeError(err)
        return {"status": "removed", "name": name}
    raise ValueError("Unknown op")


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, payload, extra=None):
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        if extra:
            for k, v in extra.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        header = self.headers.get("Authorization", "")
        if API_TOKEN:
            expect = "Bearer " + API_TOKEN
            return hmac.compare_digest(header, expect)
        return self.client_address[0] in ("127.0.0.1", "::1")

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0))
        if length <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode())
        except Exception:
            return {}

    def do_GET(self):
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        path = self.path.split("?")[0].rstrip("/")
        try:
            if path == "/health":
                return self._send(200, {"status": "ok", "docker": bool(shutil.which("docker")), "ts": now_iso()})
            if path == "/stats":
                running = []
                out, _, rc = run(["docker", "ps", "-q"], 15)
                if rc == 0:
                    running = out.split()
                return self._send(200, {
                    "cpu": host_cpu(), "ram": host_ram(), "disk": host_disk(),
                    "containers_running": len(running), "ts": now_iso(),
                })
            if path == "/containers":
                out, _, rc = run(["docker", "ps", "--format", "{{.Names}}"], 15)
                names = [n for n in out.split() if n] if rc == 0 else []
                return self._send(200, {"containers": names})
            if path.startswith("/containers/"):
                name = path[len("/containers/"):]
                sub = ""
                if "/" in name:
                    name, sub = name.split("/", 1)
                if not re_valid_name(name):
                    return self._send(400, {"error": "invalid name"})
                if sub == "state":
                    running = container_exists(name)
                    if running is None:
                        return self._send(404, {"error": "container not found"})
                    return self._send(200, {"running": running, "uptime_sec": uptime_sec(name)})
                if sub == "stats":
                    return self._send(200, container_stats(name) or {"error": "no stats"})
                if sub == "disk":
                    return self._send(200, container_disk(name) or {"error": "no disk info"})
                if sub == "ports":
                    return self._send(200, {"ports": container_ports(name)})
                return self._send(400, {"error": "unknown subresource"})
            return self._send(404, {"error": "not found"})
        except Exception as e:
            return self._send(500, {"error": str(e)})

    def do_POST(self):
        if not self._auth_ok():
            return self._send(401, {"error": "unauthorized"})
        path = self.path.split("?")[0].rstrip("/")
        body = self._read_json()
        try:
            if path == "/create":
                return self._send(200, create_container(body))
            if path.startswith("/containers/"):
                rest = path[len("/containers/"):]
                name, _, sub = rest.partition("/")
                if sub == "resize":
                    if not re_valid_name(name):
                        return self._send(400, {"error": "invalid name"})
                    mem = body.get("memory_mb")
                    cpus = body.get("cpus")
                    cmd = ["docker", "update"]
                    if mem:
                        cmd += [f"--memory={int(mem)}m"]
                    if cpus:
                        cmd += [f"--cpus={cpus}"]
                    cmd += [name]
                    out, err, rc = run(cmd, 30)
                    if rc != 0:
                        return self._send(500, {"error": err})
                    return self._send(200, {"status": "resized", "name": name})
                return self._send(400, {"error": "unknown subresource"})
            if path in ("/start", "/stop", "/restart", "/remove"):
                return self._send(200, handle_op(path.lstrip("/"), body))
            if path == "/exec":
                name = body.get("name")
                cmd = body.get("cmd", "")
                timeout = int(body.get("timeout", 30))
                if not re_valid_name(name):
                    return self._send(400, {"error": "invalid name"})
                out, err, rc = run(["docker", "exec", name, "sh", "-c", cmd], timeout)
                return self._send(200, {"stdout": out, "stderr": err, "rc": rc})
            return self._send(404, {"error": "not found"})
        except ValueError as e:
            return self._send(400, {"error": str(e)})
        except Exception as e:
            return self._send(500, {"error": str(e)})


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    host = "127.0.0.1"
    port = 8765
    args = sys.argv[1:]
    for i, a in enumerate(args):
        if a == "--host" and i + 1 < len(args):
            host = args[i + 1]
        elif a == "--port" and i + 1 < len(args):
            port = int(args[i + 1])
    if not API_TOKEN and host != "127.0.0.1":
        print("WARNING: no API_TOKEN configured and binding to a non-loopback host, refusing to start.")
        print("Set API_TOKEN (env, api_token.txt, or .env) first.")
        raise SystemExit(1)
    if not API_TOKEN:
        print("WARNING: no API_TOKEN set, API will only accept requests from localhost.")
    print(f"🌐 Turtle Nodes Node API listening on http://{host}:{port}")
    print("    Auth: Bearer <token>" if API_TOKEN else "    Auth: localhost only (no token configured)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    main()
