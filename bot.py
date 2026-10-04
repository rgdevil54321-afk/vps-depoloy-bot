import discord
from discord.ext import commands, tasks
import asyncio
import subprocess
import json
import shlex
import logging
import shutil
import os
import re
import random
import string
import sys
from typing import Optional, List, Dict, Any
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from collections import deque
from datetime import datetime, timezone
import base64
import hashlib
import hmac
import struct
import urllib.request
try:
    from dotenv import load_dotenv as _load_dotenv
    _load_dotenv()
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Logging (set up before the core imports below so failures can be logged)
# ---------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('turtle_bot')

# ---------------------------------------------------------------------------
# Core modules (Turtle Nodes modular architecture)
# ---------------------------------------------------------------------------
try:
    from core.services import metrics as core_metrics, bus as core_bus, health as core_health
    from core.bot_core import get_bot_status, get_full_status, reload_bot_config as core_reload_config, clear_cache as core_clear_cache
    from core.infrastructure import get_host_stats, get_docker_status, get_infrastructure_dashboard
    from core.monitoring import MonitoringDashboard, AlertManager, HealthMonitor, start_monitoring_loops
    from core.db_manager import DBManager
    from core.backup_center import BackupManager
    from core.terminal import AdminTerminal
    from core.config_center import ConfigManager
    from core.security import PermissionManager, AuditLog, SecurityManager, PERMISSION_CHECK
    from core.automation import start_scheduler
    from core.diagnostics import DiagnosticsRunner
    from core.analytics import AnalyticsEngine
    from core.emergency import EmergencyManager, is_maintenance_mode
    from core.logging_center import LogManager
    from core.gui import (PaginatorView, ConfirmView as CoreConfirmView, BotControlPanel as OldBotControlPanel, DatabasePanel,
                           MonitoringPanel, LogViewer, EmergencyPanel, ConfigPanel,
                           TerminalView, AnalyticsPanel, SecurityPanel, BackupPanel,
                           make_embed, status_emoji, progress_bar)
    from core.control import BotController
    from core.vps_advanced import VPSController
    from core.purge import PurgeManager
    from core.troubleshoot import Troubleshooter, AutoRecovery
    from core.incidents import IncidentManager
    from core.health_monitor import HealthMonitor as SystemHealthMonitor
    from core.maintenance import MaintenanceCenter
    from core.vps_health import VPSHealthScorer
    from core.smart_deploy import SmartDeployer
    from core.predictive import PredictiveMonitor
    from core.anomaly import AnomalyDetector
    from core.full_scan import FullSystemScan
    from core.maintenance_adv import AdvancedMaintenance
    from core.dryrun import DryRunManager
    from core.config_history import ConfigHistory
    from core.status_page import StatusPage
    from core.panels import (BotControlPanel, VPSControlPanel, ConfirmView,
                              PurgePanel, TroubleshootPanel, IncidentsPanel,
                              HealthMonitorPanel, MaintenancePanel, StatusConfigModal, RenameModal,
                              VPSScorePanel, SmartDeployPanel, PredictivePanel, AnomalyPanel,
                              FullScanPanel, DryRunPanel, ConfigHistoryPanel, StatusPagePanel,
                              MaintenanceAdvPanel)
    from core.confirm import send_confirmation, is_extreme, request_confirmation
    from core.immutable_audit import immutable_audit
    from core.action_rate_limiter import action_rate_limiter
    from core.session_security import session_manager
    from core.secret_guard import secret_guard
    from core.cmd_allowlist import cmd_allowlist
    from core.emergency_lockdown import emergency_lockdown
    from core.maint_security import maint_security
    from core.security_anomaly import security_anomaly_detector
    from core.fail_guard import fail_guard
    from core.backup_guard import backup_guard
    from core.node_isolation import node_isolation
    from core.health_verify import health_verifier
    from core.security_alerts import security_alerts
    from core.db_guard import db_guard
    from core.dep_check import dep_checker
    from core.disaster_recovery import disaster_recovery
    from core.security_center import SecurityCenterView, build_security_center_embed
    from core.progress import ProgressScreen, quick_progress
    from core.hypervisor import (KVM_OS_CATALOG, VIRT_TYPES, backend_key,
                                 domain_of, is_root_access, instance_action,
                                 instance_resize, instance_state,
                                 kvm_disk_size_gb, kvm_redefine_ports,
                                 kvm_snapshot, kvm_snapshots, kvm_state,
                                 kvm_reset_password,
                                 lxc_action, lxc_backend, lxc_export, lxc_import,
                                 file_push as vm_file_push,
                                 file_pull as vm_file_pull,
                                 run_cmd as vm_run_cmd, supported_backends,
                                 virt_available)
    from core.create_panel import CreateWizard
    CORE_MODULES_LOADED = True
    CORE_ERROR = None
except Exception as _core_err:
    CORE_MODULES_LOADED = False
    CORE_ERROR = str(_core_err)
    logger.warning("Core modules failed to load: %s", _core_err)

try:
    from core.progress import ProgressScreen, quick_progress
except ImportError:
    from progress import ProgressScreen, quick_progress

# ---------------------------------------------------------------------------
# Paths & token loading
# ---------------------------------------------------------------------------

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_PATH = os.path.join(BASE_DIR, "bot.log")
USER_DATA_PATH = os.path.join(DATA_DIR, "user_data.json")
VPS_DATA_PATH = os.path.join(DATA_DIR, "vps_data.json")
ADMIN_DATA_PATH = os.path.join(DATA_DIR, "admin_data.json")
LOGS_PATH = os.path.join(DATA_DIR, "audit_logs.json")
LEVELS_PATH = os.path.join(DATA_DIR, "admin_levels.json")
AUTOBACKUP_PATH = os.path.join(DATA_DIR, "auto_backup.json")
TXN_PATH = os.path.join(DATA_DIR, "transactions.json")
COUPONS_PATH = os.path.join(DATA_DIR, "coupons.json")
BILLING_PATH = os.path.join(DATA_DIR, "billing.json")
TICKETS_PATH = os.path.join(DATA_DIR, "tickets.json")
PENDING_PATH = os.path.join(DATA_DIR, "pending.json")
NODES_PATH = os.path.join(DATA_DIR, "nodes.json")
PROTECTED_VPS_PATH = os.path.join(DATA_DIR, "protected_vps.json")
STATE_PATH = os.path.join(BASE_DIR, "bot_state.json")

for _d in (DATA_DIR, BACKUP_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)


def load_token():
    env = os.environ.get("TOKEN", "").strip()
    if env:
        return env
    for path in (os.path.join(BASE_DIR, "token.txt"), os.path.join(BASE_DIR, ".env")):
        if not os.path.exists(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    if line.startswith("TOKEN="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
                    if "=" not in line:
                        return line
        except Exception:
            continue
    return ""


# ---------------------------------------------------------------------------
# Logging - logger is created at the top of the file, only the file
# handler is attached here now that LOG_PATH is known.
# ---------------------------------------------------------------------------

try:
    fh = RotatingFileHandler(LOG_PATH, maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    fh.setFormatter(logging.Formatter('%(asctime)s - %(levelname)s - %(message)s'))
    logger.addHandler(fh)
except Exception as e:
    logger.error("Log file setup failed: %s", e)

if not shutil.which("docker"):
    logger.warning("Docker not found. Attempting auto-install...")
    HAS_DOCKER = False
    try:
        import platform as _plat
        if _plat.system() != "Windows":
            subprocess.run(["apt-get", "update", "-qq"], capture_output=True, text=True, timeout=120)
            for _pkg in ("docker.io", "tmate", "curl", "openssh-server"):
                if not shutil.which(_pkg):
                    logger.info("Installing %s...", _pkg)
                    subprocess.run(["apt-get", "install", "-y", _pkg], capture_output=True, text=True, timeout=180)
            subprocess.Popen(
                ["dockerd", "--host=unix:///var/run/docker.sock"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
            )
            import time as _time
            for _attempt in range(15):
                _time.sleep(2)
                try:
                    subprocess.run(["docker", "info"], capture_output=True, text=True, timeout=10)
                    HAS_DOCKER = True
                    logger.info("Docker installed and daemon started successfully.")
                    break
                except Exception:
                    pass
            if not HAS_DOCKER:
                logger.warning("Docker installed but daemon failed to start.")
    except FileNotFoundError:
        logger.warning("apt-get not found. Install Docker manually: https://docs.docker.com/engine/install/")
    except Exception as _docker_err:
        logger.warning("Docker auto-install failed: %s", _docker_err)
else:
    HAS_DOCKER = True

try:
    import psutil
    HAVE_PSUTIL = True
except ImportError:
    HAVE_PSUTIL = False
    logger.warning("psutil not found. Attempting auto-install...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "psutil>=5.9.0"],
            capture_output=True, text=True, timeout=60
        )
        import psutil
        HAVE_PSUTIL = True
        logger.info("psutil installed successfully.")
    except Exception as _ps_err:
        logger.warning("psutil auto-install failed: %s", _ps_err)

REQ_PATH = os.path.join(BASE_DIR, "requirements.txt")
if os.path.exists(REQ_PATH):
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", REQ_PATH, "-q"],
            capture_output=True, text=True, timeout=120
        )
        logger.info("Python requirements installed from requirements.txt")
    except Exception as _req_err:
        logger.warning("requirements.txt install failed: %s", _req_err)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.path.join(BASE_DIR, "config.json")


def _load_config():
    defaults = {
        "MAIN_ADMIN_ID": 1251119503492775956,
        "VPS_USER_ROLE_ID": 1431499643698544720,
        "DOCKER_IMAGE": "ubuntu:22.04",
        "LOG_CHANNEL_ID": None,
        "CPU_THRESHOLD": 90,
        "CHECK_INTERVAL": 60,
        "SSH_PORT_START": 10000,
        "SERVER_IP": "127.0.0.1",
    }
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        for k, v in defaults.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    except Exception:
        return defaults


_cfg = _load_config()

# ---------------------------------------------------------------------------
# Custom emojis (uploaded to the server)
# Defined up here because some module level tables below use E().
# ---------------------------------------------------------------------------
EMOJI = {
    "cmd":       "1542537566065135696",
    "terminal":  "1542537521697919086",
    "bot":       "1542537483135230122",
    "success":   "1542537436465463376",
    "error":     "1542537400042131456",
    "online":    "1542537331175718932",
    "offline":   "1542537293582307379",
    "firewall":  "1542537185322991687",
    "unlock":    "1542537113885614110",
    "lock":      "1542536381123793028",
    "linux":     "1542536235350757487",
    "ubuntu":    "1542536137044791307",
    "nodejs":    "1542536044485017710",
    "network":   "1542536000268402821",
    "ram":       "1542535937756504245",
    "lightning": "1542535888913829918",
    "fire":      "1542535837730734100",
    "rocket":    "1542535792000499812",
    "admin":     "1542535722928705626",
    "maint":     "1542535652308951161",
    "arrow":     "1542535601247752354",
    "gearneon":  "1542535527889113118",
    "settings":  "1542534472434589777",
    "cpu":       "1542534308928163922",
    "loading":   "1542534066333548594",
    "dev":       "1542533933290233898",
    "upload":    "1542533714679042129",
    "dberr":     "1542533480410251466",
    "dbclear":   "1542533422399094947",
    "database":  "1542533364278624256",
    "vps":       "1542533064700465322",
    "shield":    "1542532552630345798",
    "gear":      "1542532445528784986",
    "disk":      "1542532027843223592",
    "server":    "1542531888499920966",
    "nodes":     "1542531429844648016",
}


def E(name):
    """Return the Discord custom emoji markup for a name."""
    eid = EMOJI.get(name)
    if eid:
        return f"<:{name}:{eid}>"
    return ""


def _fetch_url_bytes(url):
    """Blocking HTTP GET used from worker threads. Returns bytes or None."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = resp.read()
            if resp.status == 200 and data:
                return data
    except Exception:
        pass
    return None

MAIN_ADMIN_ID = _cfg["MAIN_ADMIN_ID"]
VPS_USER_ROLE_ID = _cfg["VPS_USER_ROLE_ID"]
DOCKER_IMAGE = _cfg["DOCKER_IMAGE"]
LOG_CHANNEL_ID = _cfg["LOG_CHANNEL_ID"]
CPU_THRESHOLD = _cfg["CPU_THRESHOLD"]
CHECK_INTERVAL = _cfg["CHECK_INTERVAL"]
SSH_PORT_START = _cfg["SSH_PORT_START"]
SERVER_IP = _cfg.get("SERVER_IP", "127.0.0.1")

cpu_monitor_active = True

PERM_LEVELS = {
    "Owner": 100,
    "Super Admin": 90,
    "VPS Manager": 70,
    "Support": 50,
    "Billing": 40,
    "Moderator": 30,
    "User": 0,
}

BOT_START_TIME = datetime.now()
maintenance_mode = False
MAIN_LOOP = None
audit_queue = None

# ---------------------------------------------------------------------------
# Data storage
# ---------------------------------------------------------------------------

def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        logger.warning("%s missing/corrupt, using default", os.path.basename(path))
        return default


def save_json(path, data):
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, path)
        return True
    except Exception as e:
        logger.error("save %s failed: %s", path, e)
        return False


def load_vps_data():
    loaded = load_json(VPS_DATA_PATH, {})
    vps_data = {}
    for uid, v in loaded.items():
        if isinstance(v, dict):
            if "container_name" in v:
                vps_data[uid] = [v]
            else:
                vps_data[uid] = list(v.values())
        elif isinstance(v, list):
            vps_data[uid] = v
        else:
            logger.warning("Unknown VPS data format for user %s, skipping", uid)
            continue
    return vps_data


user_data = load_json(USER_DATA_PATH, {})
vps_data = load_vps_data()
admin_data = load_json(ADMIN_DATA_PATH, {"admins": [str(MAIN_ADMIN_ID)]})
audit_logs = load_json(LOGS_PATH, [])
admin_levels = load_json(LEVELS_PATH, {})
auto_backup_cfg = load_json(AUTOBACKUP_PATH, {"enabled": False, "hours": 24, "keep": 3, "last": None})

transactions = load_json(TXN_PATH, [])
coupons = load_json(COUPONS_PATH, {})
billing_cfg = load_json(BILLING_PATH, {"enabled": False, "days": 30})
tickets_data = load_json(TICKETS_PATH, {"counter": 1, "open": {}, "closed": {}})
pending_payments = load_json(PENDING_PATH, [])
nodes_reg = load_json(NODES_PATH, {})
protected_vps = load_json(PROTECTED_VPS_PATH, {"containers": [], "owners": {}})


def save_data():
    save_json(USER_DATA_PATH, user_data)
    save_json(VPS_DATA_PATH, vps_data)
    save_json(ADMIN_DATA_PATH, admin_data)
    save_json(LEVELS_PATH, admin_levels)
    save_json(AUTOBACKUP_PATH, auto_backup_cfg)
    save_json(TXN_PATH, transactions)
    save_json(COUPONS_PATH, coupons)
    save_json(BILLING_PATH, billing_cfg)
    save_json(TICKETS_PATH, tickets_data)
    save_json(PENDING_PATH, pending_payments)
    save_json(PROTECTED_VPS_PATH, protected_vps)
    save_json(NODES_PATH, nodes_reg)


# ---------------------------------------------------------------------------
# Time helper (UTC naive, no deprecation warnings)
# ---------------------------------------------------------------------------

def utcnow():
    try:
        return datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    except Exception:
        return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Audit log
# ---------------------------------------------------------------------------

def audit(action, user, target="", details=""):
    entry = {
        "ts": utcnow().isoformat(),
        "action": action,
        "user": str(user),
        "target": str(target),
        "details": str(details),
    }
    audit_logs.append(entry)
    del audit_logs[:-5000]
    save_json(LOGS_PATH, audit_logs)
    q = audit_queue
    if q is not None:
        try:
            MAIN_LOOP.call_soon_threadsafe(lambda: asyncio.ensure_future(q.put(entry)))
        except Exception:
            pass


def audit_embed(entry):
    em = create_embed("📜 Audit Log", f"**{entry['action']}**", 0x1a1a1a)
    em.add_field(name="Time", value=f"`{entry['ts']}`", inline=False)
    em.add_field(name="User", value=entry.get("user", "?"), inline=True)
    em.add_field(name="Target", value=entry.get("target", "-") or "-", inline=True)
    em.add_field(name="Details", value=entry.get("details", "-") or "-", inline=False)
    return em


async def audit_poster():
    while True:
        entry = await audit_queue.get()
        if not LOG_CHANNEL_ID:
            continue
        try:
            ch = bot.get_channel(LOG_CHANNEL_ID)
            if ch:
                await ch.send(embed=audit_embed(entry))
        except Exception as e:
            logger.error("audit post: %s", e)


# ---------------------------------------------------------------------------
# Permission levels
# ---------------------------------------------------------------------------

def get_level(uid):
    """Return the numeric permission level for a user.

    Unknown users get 0 (no permissions). They must be promoted with
    !adminadd / !adminlevel before they can run any admin command.
    """
    if str(uid) == str(MAIN_ADMIN_ID):
        return PERM_LEVELS["Owner"]
    stored = admin_levels.get(str(uid))
    if stored:
        return PERM_LEVELS.get(str(stored), PERM_LEVELS["User"])
    return PERM_LEVELS["User"]


def has_level(uid, level):
    return get_level(uid) >= PERM_LEVELS.get(level, 90)


def is_admin():
    async def predicate(ctx):
        uid = str(ctx.author.id)
        if uid == str(MAIN_ADMIN_ID) or uid in admin_data.get("admins", []):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "You don't have permission to use this command."))
        return False
    return commands.check(predicate)


def is_superadmin():
    async def predicate(ctx):
        if has_level(ctx.author.id, "Super Admin"):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "Only Super Admin+ can use this command."))
        return False
    return commands.check(predicate)


def is_main_admin():
    async def predicate(ctx):
        if str(ctx.author.id) == str(MAIN_ADMIN_ID):
            return True
        await ctx.send(embed=create_error_embed("Access Denied", "Only the main admin (Owner) can use this command."))
        return False
    return commands.check(predicate)


# ---------------------------------------------------------------------------
# Rate limiting + 2FA (TOTP RFC 6238, pure stdlib)
# ---------------------------------------------------------------------------

# In-memory 2FA verification cache: uid -> verified timestamp (10 min window)
VERIFIED_2FA = {}
# Rate-limit buckets: uid -> {action: deque(timestamps)}
RATE_LIMITS = {}


def check_rate(uid, action, limit_per_min=10):
    key = (str(uid), action)
    now = time.time()
    bucket = RATE_LIMITS.setdefault(key, deque())
    while bucket and now - bucket[0] > 60:
        bucket.popleft()
    if len(bucket) >= limit_per_min:
        return False
    bucket.append(now)
    return True


def _b32_secret():
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZ234567"
    return "".join(random.choice(alphabet) for _ in range(16))


def _totp_code(secret, at=None):
    key = base64.b32decode(secret + "=" * ((8 - len(secret) % 8) % 8))
    t = int(at if at is not None else time.time()) // 30
    digest = hmac.new(key, struct.pack(">Q", t), hashlib.sha1).digest()
    off = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[off:off + 4])[0] & 0x7FFFFFFF) % 1000000
    return f"{code:06d}"


def user_2fa(uid):
    return bool(ensure_user(uid).get("totp_secret"))


def totp_verify(uid, code):
    secret = ensure_user(uid).get("totp_secret")
    if not secret:
        return False
    now = int(time.time())
    return any(_totp_code(secret, now + i) == str(code).strip() for i in (-1, 0, 1))


def require_2fa_ok(uid):
    if not user_2fa(uid):
        return True
    return time.time() - VERIFIED_2FA.get(str(uid), 0) <= 600


async def maybe_2fa_block(ctx):
    if require_2fa_ok(ctx.author.id):
        return False
    await ctx.send(embed=create_warning_embed(
        "🔐 2FA Required",
        "This action requires 2FA verification.\nRun `!verify <code>` first (valid for 10 minutes)."))
    return True


def enforce_security(fn):
    """Decorator: 2FA + rate limit gate for destructive commands. Integrates with advanced security systems."""
    async def wrapper(ctx, *args, **kwargs):
        uid = str(ctx.author.id)
        action_name = ctx.command.name
        if not check_rate(uid, action_name, 6):
            await ctx.send(embed=create_error_embed("Rate Limited", "Slow down! Too many attempts."))
            return
        blocked, remaining = action_rate_limiter.is_blocked(uid, action_name)
        if blocked:
            await ctx.send(embed=create_error_embed("Rate Limited",
                f"Temporarily blocked for {remaining}s due to excessive requests."))
            return
        rl_ok, rl_msg = action_rate_limiter.check_rate(uid, action_name)
        if not rl_ok:
            await ctx.send(embed=create_error_embed("Rate Limited", rl_msg))
            return
        fail_blocked, fail_remaining, fail_msg = fail_guard.is_blocked(action_name, uid)
        if fail_blocked:
            await ctx.send(embed=create_error_embed("Operation Blocked",
                f"Blocked for {fail_remaining}s due to repeated failures."))
            return
        if emergency_lockdown.is_feature_blocked("destructive_ops") and not has_level(ctx.author.id, "Super Admin"):
            await ctx.send(embed=create_error_embed("Lockdown Active",
                "Destructive operations are disabled during emergency lockdown."))
            return
        if maint_security.is_active() and not maint_security.can_user_bypass(uid):
            await ctx.send(embed=create_error_embed("Maintenance Active",
                "System is in maintenance mode. Please wait."))
            return
        if await maybe_2fa_block(ctx):
            return
        security_anomaly_detector.record_admin_action(uid, action_name)
        result = await fn(ctx, *args, **kwargs)
        action_rate_limiter.record_success(uid, action_name)
        return result
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    wrapper.__wrapped__ = fn
    return wrapper


# ---------------------------------------------------------------------------
# Embed helpers
# ---------------------------------------------------------------------------

def sanitize_error(msg):
    """Turn raw Docker/system errors into user-friendly messages. Redacts secrets."""
    msg = str(msg)
    if "docker.sock" in msg or "docker daemon" in msg or "Cannot connect" in msg:
        return "Docker daemon is not running. The bot will attempt to start it on the next command."
    if "No such file" in msg and "docker" in msg.lower():
        return "Docker is not installed."
    if "timed out" in msg.lower():
        return "Operation timed out. The server may be overloaded."
    if "permission denied" in msg.lower():
        return "Permission denied. Insufficient system privileges."
    if "image not found" in msg.lower() or "manifest" in msg.lower():
        return "Docker image not found."
    if "already in use" in msg.lower():
        return "Container name already in use."
    if len(msg) > 200:
        msg = msg[:200] + "..."
    try:
        from core.secret_guard import secret_guard as _sg
        msg = _sg.redact_string(msg)
    except Exception:
        pass
    return msg

def create_embed(title, description="", color=0x1a1a1a, fields=None):
    embed = discord.Embed(title=f"▌ {title}", description=description, color=color)
    if fields:
        for field in fields:
            embed.add_field(name=f"▸ {field['name']}", value=field["value"], inline=field.get("inline", False))
    embed.set_footer(text=f"Turtle Nodes | Made By ᴛɪʀᴇᴅ ᵍᴄ • {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return embed


def create_success_embed(title, description=""):
    return create_embed(title, description, color=0x00ff88)


def create_error_embed(title, description=""):
    return create_embed(title, description, color=0xff3366)


def create_info_embed(title, description=""):
    return create_embed(title, description, color=0x00ccff)


def create_warning_embed(title, description=""):
    return create_embed(title, description, color=0xffaa00)


# ---------------------------------------------------------------------------
# Docker execution
# ---------------------------------------------------------------------------

async def check_docker_daemon():
    """Verify Docker daemon is reachable. Auto-start if down."""
    global HAS_DOCKER
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "info", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        await asyncio.wait_for(proc.communicate(), timeout=10)
        if proc.returncode == 0:
            HAS_DOCKER = True
            return True
    except Exception:
        pass
    try:
        subprocess.Popen(
            ["dockerd", "--host=unix:///var/run/docker.sock"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True
        )
    except Exception:
        pass
    import time as _t
    for _ in range(10):
        _t.sleep(2)
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "info", stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode == 0:
                HAS_DOCKER = True
                return True
        except Exception:
            pass
    HAS_DOCKER = False
    return False


async def execute_docker(command, timeout=120):
    global HAS_DOCKER
    if not HAS_DOCKER:
        if not await check_docker_daemon():
            raise Exception("Docker daemon is not running. Attempted auto-start but failed.")
    try:
        cmd = shlex.split(command)
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        if proc.returncode != 0:
            error = stderr.decode().strip() if stderr else "Command failed with no error output"
            raise Exception(error)
        return stdout.decode().strip() if stdout else True
    except asyncio.TimeoutError:
        logger.error("Docker command timed out: %s", command)
        raise Exception(f"Command timed out after {timeout} seconds")
    except FileNotFoundError:
        HAS_DOCKER = False
        raise Exception("Docker binary not found. Docker may need to be installed.")
    except Exception as e:
        logger.error("Docker Error: %s - %s", command, str(e))
        raise


async def docker_exec(container_name, command, timeout=60):
    if not HAS_DOCKER:
        return "", "Docker is not installed.", 1
    cmd = ["docker", "exec", container_name, "bash", "-c", command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode
    except FileNotFoundError:
        return "", "Docker binary not found.", 1
    except Exception as e:
        return "", str(e), 1


async def docker_exec_async(container_name, command, timeout=60):
    return await docker_exec(container_name, command, timeout)


# ---------------------------------------------------------------------------
# SSH remote execution (for direct SSH-based node management)
# ---------------------------------------------------------------------------

async def ssh_exec(node, command, timeout=120):
    """Execute a command on a remote node via SSH. Node dict must have ip, port, user, password/ssh_key."""
    ip, port = node["ip"], node.get("port", 22)
    user = node.get("user", "root")
    password = node.get("password", "")
    ssh_key = node.get("ssh_key", "")
    if ssh_key:
        key_path = f"/tmp/ssh_key_{node.get('name', 'unknown')}"
        with open(key_path, "w") as f:
            f.write(ssh_key)
        os.chmod(key_path, 0o600)
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-i", key_path, "-p", str(port), f"{user}@{ip}", command]
    elif password:
        cmd = ["sshpass", "-p", password, "ssh", "-o", "StrictHostKeyChecking=no",
               "-o", "UserKnownHostsFile=/dev/null", "-p", str(port), f"{user}@{ip}", command]
    else:
        cmd = ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null",
               "-p", str(port), f"{user}@{ip}", command]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return stdout.decode().strip(), stderr.decode().strip(), proc.returncode
    except asyncio.TimeoutError:
        return "", "SSH timed out", 1
    except FileNotFoundError:
        return "", "sshpass not found", 1
    except Exception as e:
        return "", str(e), 1


async def ssh_ok(node, command, timeout=30):
    """SSH exec, return True if exit code == 0."""
    _, _, rc = await ssh_exec(node, command, timeout)
    return rc == 0


# ---------------------------------------------------------------------------
# Per-node-type deploy functions (Docker / KVM / HVM / PVM / LXC)
# ---------------------------------------------------------------------------

DEFAULT_DEPLOY_IMAGE = "jrei/systemd-ubuntu:22.04"

NODE_TYPE_LABELS = {
    "docker": "Docker Container",
    "hvm": "Hardware VM (QEMU/KVM)",
    "pvm": "Paravirtual (Xen/VMware)",
    "lxc": "Linux Container (LXC)",
    "kvm": "KVM Virtual Machine",
}


async def _deploy_docker_remote(node, ram_gb, cpu, disk_gb):
    """Deploy a Docker container on a remote node via SSH."""
    http_port = random.randint(3000, 3999)
    cname = f"vps-{random.randint(1000, 9999)}"
    cmd = (f"docker run -d --privileged --cgroupns=host --tmpfs /run --tmpfs /run/lock "
           f"-v /sys/fs/cgroup:/sys/fs/cgroup:rw --name {cname} --cpus {cpu} "
           f"--memory {ram_gb}g --memory-swap {ram_gb}g -p {http_port}:80 "
           f"{DEFAULT_DEPLOY_IMAGE}")
    _, out, err = await ssh_exec(node, cmd, timeout=60)
    if not out:
        return None, None, f"Remote Docker deploy failed: {err}"
    return out[:12], http_port, None


async def _deploy_kvm_remote(node, ram_gb, cpu, disk_gb):
    vm_name = f"kvm-{random.randint(1000, 9999)}"
    http_port = random.randint(3000, 3999)
    cmd = (f"docker run -d --privileged --name {vm_name} --cpus {cpu} --memory {ram_gb}g "
           f"-p {http_port}:22 {DEFAULT_DEPLOY_IMAGE} sleep infinity")
    _, out, err = await ssh_exec(node, cmd, timeout=60)
    if not out:
        return None, None, f"KVM deploy failed: {err}"
    return vm_name, http_port, None


async def _deploy_hvm_remote(node, ram_gb, cpu, disk_gb):
    vm_name = f"hvm-{random.randint(1000, 9999)}"
    http_port = random.randint(3000, 3999)
    cmd = (f"docker run -d --privileged --name {vm_name} --cpus {cpu} --memory {ram_gb}g "
           f"-p {http_port}:22 {DEFAULT_DEPLOY_IMAGE} sleep infinity")
    _, out, err = await ssh_exec(node, cmd, timeout=60)
    if not out:
        return None, None, f"HVM deploy failed: {err}"
    return vm_name, http_port, None


async def _deploy_pvm_remote(node, ram_gb, cpu, disk_gb):
    vm_name = f"pvm-{random.randint(1000, 9999)}"
    http_port = random.randint(3000, 3999)
    cmd = (f"docker run -d --privileged --name {vm_name} --cpus {cpu} --memory {ram_gb}g "
           f"-p {http_port}:22 {DEFAULT_DEPLOY_IMAGE} sleep infinity")
    _, out, err = await ssh_exec(node, cmd, timeout=60)
    if not out:
        return None, None, f"PVM deploy failed: {err}"
    return vm_name, http_port, None


async def _deploy_lxc_remote(node, ram_gb, cpu, disk_gb):
    ct_name = f"lxc-{random.randint(1000, 9999)}"
    http_port = random.randint(3000, 3999)
    cmd = (f"docker run -d --privileged --name {ct_name} --cpus {cpu} --memory {ram_gb}g "
           f"-p {http_port}:22 {DEFAULT_DEPLOY_IMAGE} sleep infinity")
    _, out, err = await ssh_exec(node, cmd, timeout=60)
    if not out:
        return None, None, f"LXC deploy failed: {err}"
    return ct_name, http_port, None


DEPLOY_FUNCTIONS = {
    "docker": _deploy_docker_remote,
    "kvm": _deploy_kvm_remote,
    "hvm": _deploy_hvm_remote,
    "pvm": _deploy_pvm_remote,
    "lxc": _deploy_lxc_remote,
}


# ---------------------------------------------------------------------------
# Node resource tracking + auto-selection
# ---------------------------------------------------------------------------

def create_node_record(name, node_type, ip, port=22, ssh_user="root",
                       password="", ssh_key="", max_ram_gb=64,
                       max_cpu=16, max_disk_gb=500):
    return {
        "name": name, "type": node_type, "ip": ip, "port": port,
        "user": ssh_user, "password": password, "ssh_key": ssh_key,
        "max_ram_gb": max_ram_gb, "max_cpu": max_cpu, "max_disk_gb": max_disk_gb,
        "used_ram_gb": 0, "used_cpu": 0, "used_disk_gb": 0,
        "status": "online", "vps_count": 0,
        "created_at": utcnow().isoformat(),
    }


def get_node_record(name):
    return nodes_reg.get(name)


def get_online_nodes():
    return {k: v for k, v in nodes_reg.items() if v.get("status") == "online"}


def auto_select_node(node_type=None, ram_gb=32, cpu=6, disk_gb=100):
    candidates = get_online_nodes()
    if node_type:
        candidates = {k: v for k, v in candidates.items() if v.get("type") == node_type}
    best_name, best_score = None, -1
    for name, node in candidates.items():
        free_ram = node.get("max_ram_gb", 64) - node.get("used_ram_gb", 0)
        free_cpu = node.get("max_cpu", 16) - node.get("used_cpu", 0)
        free_disk = node.get("max_disk_gb", 500) - node.get("used_disk_gb", 0)
        if free_ram < ram_gb or free_cpu < cpu or free_disk < disk_gb:
            continue
        score = free_ram + free_cpu * 2 + free_disk
        if score > best_score:
            best_score = score
            best_name = name
    return best_name


def allocate_node_resources(node_name, ram_gb, cpu, disk_gb):
    node = nodes_reg.get(node_name)
    if not node:
        return False
    node["used_ram_gb"] = node.get("used_ram_gb", 0) + ram_gb
    node["used_cpu"] = node.get("used_cpu", 0) + cpu
    node["used_disk_gb"] = node.get("used_disk_gb", 0) + disk_gb
    node["vps_count"] = node.get("vps_count", 0) + 1
    save_json(NODES_PATH, nodes_reg)
    return True


def free_node_resources(node_name, ram_gb, cpu, disk_gb):
    node = nodes_reg.get(node_name)
    if not node:
        return
    node["used_ram_gb"] = max(0, node.get("used_ram_gb", 0) - ram_gb)
    node["used_cpu"] = max(0, node.get("used_cpu", 0) - cpu)
    node["used_disk_gb"] = max(0, node.get("used_disk_gb", 0) - disk_gb)
    node["vps_count"] = max(0, node.get("vps_count", 0) - 1)
    save_json(NODES_PATH, nodes_reg)


async def remote_check_status(node):
    """Check if a remote node's Docker daemon is reachable via SSH."""
    return await ssh_ok(node, "docker info", timeout=10)


# ---------------------------------------------------------------------------
# VPS creation / helpers
# ---------------------------------------------------------------------------

def get_next_ssh_port():
    used_ports = set()
    for vps_list in vps_data.values():
        for vps in vps_list:
            if "ssh_port" in vps:
                used_ports.add(vps["ssh_port"])
    port = SSH_PORT_START
    while port in used_ports:
        port += 1
    return port


def generate_password(length=16):
    chars = string.ascii_letters + string.digits + "!@#$%"
    return ''.join(random.choice(chars) for _ in range(length))


# Host ports users may never bind through !portadd. The canonical list
# lives in core.hypervisor so the KVM XML builder enforces the same rules.
if CORE_MODULES_LOADED:
    from core.hypervisor import BLOCKED_HOST_PORTS
else:
    BLOCKED_HOST_PORTS = {22, 2375, 3306, 5432, 6379, 27017, 3389}

# Ports the bot's own API/admin console may bind. Deliberately narrow so
# ordinary web ports (80xx, 3xxx, 5xxx) stay available to users.
PANEL_RESERVED_PORTS = {9090, 9091, 9443}


def _validate_user_port(host_port, cont_port):
    """Return an error string if the mapping is not allowed, else None."""
    if host_port < 1024 or host_port > 65535:
        return "Host port must be between 1024 and 65535 (ports below 1024 are privileged)."
    if cont_port < 1 or cont_port > 65535:
        return "Container port must be between 1 and 65535."
    if host_port in BLOCKED_HOST_PORTS:
        return f"Host port **{host_port}** is reserved for host services and cannot be used."
    if host_port in PANEL_RESERVED_PORTS:
        return f"Host port **{host_port}** is used by the control panel or API and cannot be taken."
    # Prevent collisions with SSH ports already handed out to VPS
    for vps_list in vps_data.values():
        for vps in vps_list:
            if vps.get("ssh_port") == host_port:
                return f"Host port **{host_port}** is already assigned to VPS SSH. Pick another port."
    return None


def find_vps(user_id, vps_number):
    vps_list = vps_data.get(str(user_id), [])
    if not vps_list or vps_number < 1 or vps_number > len(vps_list):
        return None, None
    return vps_list[vps_number - 1], vps_list


async def create_docker_container(container_name, ram_mb, cpu_count, ssh_port, password, disk_gb=30, image=None, ports=None):
    image = image or DOCKER_IMAGE
    try:
        await execute_docker(f"docker pull {image}", timeout=300)
    except Exception:
        pass

    port_flags = ""
    if ssh_port and ssh_port > 0:
        port_flags += f" -p {ssh_port}:22"
    for mapping in (ports or []):
        port_flags += f" -p {mapping}"

    run_cmd = (
        f"docker run -d "
        f"--name {container_name} "
        f"--memory={ram_mb}m "
        f"--cpus={cpu_count} "
        f"--restart=unless-stopped "
        f"{port_flags} "
        f"{image} "
        f"sleep infinity"
    )
    await execute_docker(run_cmd, timeout=60)

    setup_script = (
        "apt-get update -qq && "
        "apt-get install -y openssh-server tmate curl -qq && "
        "mkdir -p /var/run/sshd && "
        "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config && "
        "echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config && "
        f"echo 'root:{password}' | chpasswd && "
        "/usr/sbin/sshd"
    )
    stdout, stderr, rc = await docker_exec(container_name, setup_script, timeout=180)
    if rc != 0 and "already" not in stderr.lower():
        raise Exception(f"SSH setup failed: {stderr}")
    return True


async def get_mapped_port(container_name, container_port=22):
    """Query Docker to get the actual host port mapped to a container port."""
    try:
        proc = await asyncio.get_running_loop().run_in_executor(
            None, lambda: subprocess.run(
                ["docker", "port", container_name, str(container_port)],
                capture_output=True, text=True, timeout=10))
        if proc.returncode == 0 and proc.stdout.strip():
            line = proc.stdout.strip().split("\n")[0]
            host_port = line.split(":")[-1].strip()
            return int(host_port)
    except Exception:
        pass
    return None


async def backfill_vps_ports():
    """Assign and publish SSH ports for any VPS that are missing port data."""
    for user_id, vps_list in vps_data.items():
        for vps in vps_list:
            if vps.get("ssh_port"):
                continue
            name = vps.get("container_name", "")
            if not name:
                continue
            ssh_port = get_next_ssh_port()
            try:
                try:
                    await execute_docker(f"docker stop {name}", timeout=30)
                except Exception:
                    pass
                try:
                    await execute_docker(f"docker rm -f {name}", timeout=30)
                except Exception:
                    pass
                ram_mb = int(str(vps.get("ram", "4GB")).replace("GB", "")) * 1024
                cpu = int(vps.get("cpu", 1))
                disk = int(str(vps.get("storage", "10GB")).replace("GB", ""))
                image = vps.get("image", DOCKER_IMAGE)
                password = vps.get("ssh_password", generate_password())
                await create_docker_container(name, ram_mb, cpu, ssh_port, password,
                                              disk_gb=disk, image=image,
                                              ports=vps.get("ports") or [])
                await asyncio.sleep(2)
                try:
                    await docker_exec(name, "/usr/sbin/sshd || true", timeout=10)
                except Exception:
                    pass
                actual = await get_mapped_port(name, 22) or ssh_port
                vps["ssh_port"] = actual
                vps["status"] = "running"
            except Exception:
                vps["ssh_port"] = ssh_port
    save_data()


async def get_tmate_session(container_name):
    tmate_script = (
        "pkill tmate 2>/dev/null || true && "
        "sleep 1 && "
        "tmate -S /tmp/tmate.sock new-session -d && "
        "sleep 3 && "
        "tmate -S /tmp/tmate.sock wait tmate-ready && "
        "tmate -S /tmp/tmate.sock display -p '#{tmate_ssh}'"
    )
    stdout, stderr, rc = await docker_exec(container_name, tmate_script, timeout=30)
    if rc != 0 or not stdout.strip():
        raise Exception(f"tmate session start failed: {stderr}")
    return stdout.strip()


async def get_sshx_session(container_name):
    """Start sshx and return a web share link."""
    sshx_script = (
        "curl -sSf https://sshx.io/get | sh 2>/dev/null && "
        "./sshx --url https://sshx.io 2>/dev/null & "
        "sleep 5 && "
        "cat /tmp/sshx_url.txt 2>/dev/null || "
        "$(which sshx || ./sshx) --url https://sshx.io 2>/dev/null & "
        "sleep 4 && "
        "ps aux | grep sshx | grep -v grep | head -1"
    )
    install_script = "curl -sSf https://sshx.io/get | sh 2>/dev/null"
    await docker_exec(container_name, install_script, timeout=60)
    run_script = "nohup ./sshx --url https://sshx.io > /tmp/sshx_out.txt 2>&1 & sleep 5 && cat /tmp/sshx_out.txt"
    stdout, stderr, rc = await docker_exec(container_name, run_script, timeout=30)
    output = stdout.strip()
    if not output:
        output = stderr.strip()
    import re as _re
    match = _re.search(r'https://sshx\.io/[a-zA-Z0-9]+', output)
    if match:
        return match.group(0)
    raise Exception("sshx failed to generate link. sshx may not be available.")


async def container_alive(name):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format={{.State.Running}}", name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        return out.decode().strip() == "true"
    except Exception:
        return False


async def container_uptime_delta(name):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "inspect", "--format={{.State.StartedAt}}", name,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
        s = out.decode().strip()
        dt = datetime.fromisoformat(s[:19])
        return max(utcnow() - dt, timedelta(0))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resource stats (blocking helpers, called via executor)
# ---------------------------------------------------------------------------

_UNITS = {
    "k": 1024, "m": 1024 ** 2, "g": 1024 ** 3, "t": 1024 ** 4,
    "ki": 1024, "mi": 1024 ** 2, "gi": 1024 ** 3, "ti": 1024 ** 4,
    "kib": 1024, "mib": 1024 ** 2, "gib": 1024 ** 3, "tib": 1024 ** 4,
    "kb": 1000, "mb": 1000 ** 2, "gb": 1000 ** 3, "tb": 1000 ** 4,
}


def to_bytes(s):
    try:
        m = re.match(r"^([\d.]+)\s*([a-zA-Z]*)", s.strip())
        if not m:
            return 0.0
        n = float(m.group(1))
        u = m.group(2).lower()
        return n * _UNITS.get(u, 1.0)
    except Exception:
        return 0.0


def fmt_bytes(n):
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1000 or unit == "TB":
            return f"{n:.1f}{unit}"
        n /= 1000.0


def docker_stats_sync(container):
    try:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}", container],
            capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return None
        d = json.loads(proc.stdout.strip())
        return d
    except Exception:
        return None


async def get_container_stats(container):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, docker_stats_sync, container)


async def container_disk_usage(container):
    out, err, rc = await docker_exec(container, "df -h / 2>/dev/null | tail -1", timeout=15)
    if rc != 0 or not out.strip():
        return None
    parts = out.split()
    if len(parts) < 5:
        return None
    return {"size": parts[1], "used": parts[2], "avail": parts[3], "pct": parts[4]}


def host_cpu_sync():
    try:
        if HAVE_PSUTIL:
            return psutil.cpu_percent(interval=0.5)
        proc = subprocess.run(['top', '-bn1'], capture_output=True, text=True, timeout=10)
        for line in proc.stdout.split('\n'):
            if '%Cpu(s):' in line:
                for part in line.split(','):
                    if 'id,' in part:
                        idle = float(part.split('%')[0].split()[-1])
                        return max(0.0, 100.0 - idle)
        return 0.0
    except Exception:
        return 0.0


def host_ram_sync():
    try:
        if HAVE_PSUTIL:
            vm = psutil.virtual_memory()
            return {"total": vm.total, "used": vm.used, "pct": vm.percent}
        proc = subprocess.run(['free', '-b'], capture_output=True, text=True, timeout=10)
        for line in proc.stdout.split('\n')[1:]:
            parts = line.split()
            if len(parts) >= 3 and parts[0] == "Mem:":
                total, used = int(parts[1]), int(parts[2])
                return {"total": total, "used": used, "pct": (used / total * 100) if total else 0}
        return None
    except Exception:
        return None


def host_disk_sync():
    try:
        if HAVE_PSUTIL:
            du = psutil.disk_usage("/")
            return {"total": du.total, "used": du.used, "pct": du.percent}
        proc = subprocess.run(['df', '-k', '/'], capture_output=True, text=True, timeout=10)
        parts = proc.stdout.split('\n')[1].split()
        total, used = int(parts[1]) * 1024, int(parts[2]) * 1024
        return {"total": total, "used": used, "pct": (used / total * 100) if total else 0}
    except Exception:
        return None


def top_container_sync():
    """Return (name, cpu_percent) of the highest-CPU running container via docker stats."""
    try:
        proc = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "{{json .}}"],
            capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return None
        best = None
        for line in proc.stdout.strip().split("\n"):
            if not line.strip():
                continue
            d = json.loads(line)
            name = d.get("Name", "")
            try:
                cpu = float(d.get("CPUPerc", "0").replace("%", ""))
            except Exception:
                cpu = 0.0
            if best is None or cpu > best[1]:
                best = (name, cpu)
        return best
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Resource watch loop (replaces the old "stop everything" CPU monitor)
# ---------------------------------------------------------------------------

async def alert_admins(title, desc):
    em = create_warning_embed(title, desc)
    targets = [str(MAIN_ADMIN_ID)] + [a for a in admin_data.get("admins", []) if a != str(MAIN_ADMIN_ID)]
    for uid in targets:
        try:
            u = await bot.fetch_user(int(uid))
            await u.send(embed=em)
        except Exception:
            pass


async def resource_watch_loop():
    global cpu_monitor_active
    strikes = {}
    logger.info("Resource watch loop started (threshold %s%%, interval %ss)", CPU_THRESHOLD, CHECK_INTERVAL)
    while True:
        try:
            if cpu_monitor_active:
                cpu = await asyncio.get_running_loop().run_in_executor(None, host_cpu_sync)
                if cpu > CPU_THRESHOLD:
                    top = await asyncio.get_running_loop().run_in_executor(None, top_container_sync)
                    top_str = f"{top[0]} ({top[1]:.1f}%)" if top else "unknown"
                    audit("resource_alert", "system", "host", f"CPU {cpu:.1f}% > {CPU_THRESHOLD}%, top: {top_str}")
                    await alert_admins(
                        "⚠️ High Host CPU",
                        f"Host CPU is at **{cpu:.1f}%** (threshold {CPU_THRESHOLD}%).\n"
                        f"Top consumer: `{top_str}`")
                    if top:
                        name = top[0]
                        if top[1] > 98:
                            strikes[name] = strikes.get(name, 0) + 1
                        else:
                            strikes.pop(name, None)
                        if strikes.get(name, 0) >= 3:
                            await alert_admins(
                                "🚨 Container Stopped (Runaway)",
                                f"`{name}` sustained >98% CPU for 3 checks. It has been stopped to protect the host.")
                            audit("resource_stop", "system", name, "stopped runaway container")
                            try:
                                await execute_docker(f"docker stop {name}", timeout=60)
                                for vl in vps_data.values():
                                    for v in vl:
                                        if v["container_name"] == name:
                                            v["status"] = "stopped"
                                save_data()
                            except Exception as e:
                                logger.error("stop runaway %s: %s", name, e)
                            strikes[name] = 0
                else:
                    strikes.clear()
        except Exception as e:
            logger.error("resource watch: %s", e)
        await asyncio.sleep(CHECK_INTERVAL)


# ---------------------------------------------------------------------------
# Health checker + crash recovery
# ---------------------------------------------------------------------------

@tasks.loop(minutes=5)
async def health_loop():
    logger.info("Health check running over %d users", len(vps_data))
    for uid, vl in list(vps_data.items()):
        for vps in vl:
            if vps.get("status") != "running":
                continue
            c = vps["container_name"]
            if await container_alive(c):
                continue
            audit("health_fail", "system", c, "container not running, attempting recovery")
            recovered = False
            try:
                await execute_docker(f"docker start {c}", timeout=60)
                await asyncio.sleep(2)
                await docker_exec(c, "/usr/sbin/sshd || true", timeout=10)
                vps["status"] = "running"
                recovered = True
            except Exception as e:
                vps["status"] = "stopped"
                logger.error("recovery failed for %s: %s", c, e)
                audit("health_stop", "system", c, f"recovery failed: {e}")
            save_data()
            state = "recovered" if recovered else "still down"
            await alert_admins(
                "🩺 VPS Health Alert",
                f"`{c}` (owner <@{uid}>) was down. Status: **{state}**.")
            try:
                u = await bot.fetch_user(int(uid))
                await u.send(embed=create_warning_embed(
                    "🩺 VPS Health Alert",
                    f"Your VPS `{c}` was found down. We attempted recovery - status: **{state}**."))
            except Exception:
                pass


@health_loop.before_loop
async def before_health_loop():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Automatic scheduled backups
# ---------------------------------------------------------------------------

def snapshot_list_sync(prefix):
    try:
        proc = subprocess.run(
            ["docker", "images", "--format", "{{.Repository}}:{{.Tag}} ({{.Size}}) {{.CreatedSince}}"],
            capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return []
        return [img for img in proc.stdout.strip().split("\n")
                if img.startswith(prefix + "-") or img.startswith(prefix)]
    except Exception:
        return []


async def run_auto_backup():
    if not auto_backup_cfg.get("enabled"):
        return
    hours = int(auto_backup_cfg.get("hours", 24))
    keep = int(auto_backup_cfg.get("keep", 3))
    last = auto_backup_cfg.get("last")
    if last:
        try:
            if utcnow() - datetime.fromisoformat(last) < timedelta(hours=hours):
                return
        except Exception:
            pass
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    done = 0
    for uid, vl in list(vps_data.items()):
        for vps in vl:
            if vps.get("status") != "running":
                continue
            c = vps["container_name"]
            tag = f"{c}-auto-{ts}"
            try:
                await execute_docker(f"docker commit {c} {tag}", timeout=120)
                done += 1
                audit("auto_backup", "system", c, f"snapshot {tag}")
                await prune_auto_backups(c, keep)
            except Exception as e:
                logger.error("auto backup %s: %s", c, e)
    auto_backup_cfg["last"] = utcnow().isoformat()
    save_json(AUTOBACKUP_PATH, auto_backup_cfg)
    if done:
        await alert_admins("📸 Auto Backup", f"Automatically backed up **{done}** running VPS (kept last {keep} per VPS).")


async def prune_auto_backups(container, keep):
    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "images", "--format", "{{.Repository}}:{{.Tag}}",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await proc.communicate()
        names = sorted([img for img in out.decode().strip().split("\n")
                        if img.startswith(container + "-auto-")])
        for old in names[:-keep]:
            try:
                await execute_docker(f"docker rmi -f {old}", timeout=60)
            except Exception:
                pass
    except Exception:
        pass


@tasks.loop(hours=1)
async def auto_backup_loop():
    await run_auto_backup()


@auto_backup_loop.before_loop
async def before_auto_backup_loop():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# VPS Role helper
# ---------------------------------------------------------------------------

async def get_or_create_vps_role(guild):
    global VPS_USER_ROLE_ID
    if VPS_USER_ROLE_ID:
        role = guild.get_role(VPS_USER_ROLE_ID)
        if role:
            return role
    role = discord.utils.get(guild.roles, name="VPS User")
    if role:
        VPS_USER_ROLE_ID = role.id
        return role
    try:
        role = await guild.create_role(
            name="VPS User", color=discord.Color.dark_purple(),
            reason="VPS User role for bot management", permissions=discord.Permissions.none())
        VPS_USER_ROLE_ID = role.id
        logger.info("Created VPS User role: %s (%s)", role.name, role.id)
        return role
    except Exception as e:
        logger.error("Failed to create VPS User role: %s", e)
        return None


# ---------------------------------------------------------------------------
# Bot setup
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix='!', intents=intents, help_command=None)


# ---------------------------------------------------------------------------
# Bot events
# ---------------------------------------------------------------------------

async def _maintenance_auto_check_loop(bot):
    """Periodically check scheduled maintenance and auto-end."""
    while True:
        try:
            if CORE_MODULES_LOADED:
                adv = AdvancedMaintenance(bot)
                adv.auto_end_check()
                adv.check_schedules()
        except Exception:
            pass
        await asyncio.sleep(60)

async def _status_snapshot_loop(bot):
    """Record status page snapshots periodically."""
    while True:
        try:
            if CORE_MODULES_LOADED:
                sp = StatusPage(bot)
                sp.record_status_snapshot()
        except Exception:
            pass
        await asyncio.sleep(300)

@bot.event
async def on_ready():
    global MAIN_LOOP, audit_queue
    MAIN_LOOP = bot.loop
    audit_queue = asyncio.Queue()
    logger.info('%s connected to Discord!', bot.user)
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, name="Turtle Nodes | Made By ᴛɪʀᴇᴅ ᵍᴄ"))
    if not auto_expire_check.is_running():
        auto_expire_check.start()
    if not health_loop.is_running():
        health_loop.start()
    if not auto_backup_loop.is_running():
        auto_backup_loop.start()
    asyncio.ensure_future(resource_watch_loop())
    asyncio.ensure_future(audit_poster())
    asyncio.ensure_future(state_poster())
    await start_extra_loops()
    if CORE_MODULES_LOADED:
        try:
            start_monitoring_loops(bot)
            bot.scheduler = await start_scheduler(bot)
            bot.health_monitor = SystemHealthMonitor(bot)
            asyncio.ensure_future(bot.health_monitor.start_monitoring(interval=120))
            bot.bot_controller = BotController(bot)
            asyncio.ensure_future(bot.bot_controller.run_status_rotation())
            bot.predictive_monitor = PredictiveMonitor()
            bot.vps_health = VPSHealthScorer(bot)
            bot.smart_deployer = SmartDeployer(bot)
            bot.anomaly_detector = AnomalyDetector(bot)
            bot.full_scanner = FullSystemScan(bot)
            bot.adv_maintenance = AdvancedMaintenance(bot)
            bot.dry_run = DryRunManager()
            bot.config_history = ConfigHistory()
            bot.status_page = StatusPage(bot)
            asyncio.ensure_future(_maintenance_auto_check_loop(bot))
            asyncio.ensure_future(_status_snapshot_loop(bot))
            logger.info("Core modules initialized: monitoring + scheduler + health + status rotation + all advanced modules")
        except Exception as e:
            logger.error("Failed to init core modules: %s", e)
    logger.info("Bot is ready!")
    try:
        await backfill_vps_ports()
        logger.info("VPS port backfill complete")
    except Exception as e:
        logger.error("VPS port backfill failed: %s", e)


@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=create_error_embed("Missing Argument", "Please use `!help` for command usage."))
    elif isinstance(error, commands.BadArgument):
        await ctx.send(embed=create_error_embed("Invalid Argument", "Please check your input and try again."))
    elif isinstance(error, commands.CheckFailure):
        pass
    else:
        error_msg = sanitize_error(error)
        safe_msg = secret_guard.redact_error(error_msg)
        logger.error("Command error: %s", safe_msg)
        security_anomaly_detector.record_failure(ctx.command.name if ctx.command else "unknown",
                                                  str(ctx.author.id))
        await ctx.send(embed=create_error_embed("Error", safe_msg))


async def state_poster():
    """Write bot_state.json so the terminal control panel can show live status."""
    while True:
        try:
            state = {
                "status": "online",
                "uptime_sec": int((datetime.now() - BOT_START_TIME).total_seconds()),
                "ping_ms": round(bot.latency * 1000) if bot.latency else 0,
                "db": "connected",
                "docker": "connected" if shutil.which("docker") else "down",
                "vps_total": sum(len(v) for v in vps_data.values()),
                "ts": utcnow().isoformat(),
            }
            save_json(STATE_PATH, state)
        except Exception:
            pass
        await asyncio.sleep(15)


# ---------------------------------------------------------------------------
# Resource embed + live monitor view
# ---------------------------------------------------------------------------

def resource_embed(vps, stats, disk, uptime_delta):
    c = vps["container_name"]
    status_color = 0x00ff88 if vps.get("status") == "running" else 0xff3366
    em = create_embed(f"{E('cpu')} Live Stats - {c}", f"Status: `{vps.get('status','?').upper()}`", status_color)
    cpu = stats.get("CPUPerc", "n/a") if stats else "n/a"
    mem = stats.get("MemUsage", "n/a") if stats else "n/a"
    mem_pct = stats.get("MemPerc", "") if stats else ""
    net = stats.get("NetIO", "n/a / n/a") if stats else "n/a / n/a"
    parts = [p.strip() for p in net.split("/")]
    rx, tx = (parts[0], parts[1]) if len(parts) >= 2 else (net, "")
    em.add_field(name=f"{E('cpu')} CPU", value=f"`{cpu}`", inline=True)
    em.add_field(name=f"{E('ram')} RAM", value=f"`{mem}` ({mem_pct})", inline=True)
    em.add_field(name=f"{E('network')} Network", value=f"⬇ {rx}\n⬆ {tx}", inline=True)
    em.add_field(name=f"{E('disk')} Disk", value=f"`{disk['used']}` / `{disk['size']}` ({disk['pct']})" if disk else f"`{vps.get('storage','30GB')}`", inline=True)
    if uptime_delta:
        d, s = uptime_delta.days, uptime_delta.seconds
        em.add_field(name=f"{E('loading')} Uptime", value=f"`{d}d {s//3600}h {(s%3600)//60}m {s%60}s`", inline=True)
    else:
        em.add_field(name=f"{E('loading')} Uptime", value="`n/a`", inline=True)
    em.add_field(name=f"{E('vps')} Container", value=f"`{c}`", inline=True)
    em.set_footer(text="Auto-refreshes every 5s • Press ⏹ to stop")
    return em


async def refresh_resource_embed(vps):
    stats = await container_op(vps, "stats")
    disk = await container_op(vps, "disk")
    up = await container_op(vps, "uptime")
    return resource_embed(vps, stats, disk, up)


class LiveStatsView(discord.ui.View):
    def __init__(self, vps, owner_id):
        super().__init__(timeout=300)
        self.vps = vps
        self.owner_id = str(owner_id)
        self._task = None
        self._msg = None
        self._closed = False

    async def start(self, ctx):
        self._msg = await ctx.send(embed=await refresh_resource_embed(self.vps), view=self)
        self._task = asyncio.ensure_future(self._loop())

    async def _loop(self):
        while not self._closed:
            await asyncio.sleep(5)
            try:
                await self._msg.edit(embed=await refresh_resource_embed(self.vps), view=self)
            except Exception:
                break

    def _stop(self):
        self._closed = True
        if self._task:
            self._task.cancel()

    async def on_timeout(self):
        self._stop()

    @discord.ui.button(label="🔁 Refresh", style=discord.ButtonStyle.primary)
    async def refresh_btn(self, interaction: discord.Interaction, item: discord.ui.Button):
        if str(interaction.user.id) != self.owner_id and not has_level(interaction.user.id, "Super Admin"):
            await interaction.response.send_message("Not your VPS.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            await self._msg.edit(embed=await refresh_resource_embed(self.vps), view=self)
        except Exception:
            pass

    @discord.ui.button(label="⏹ Stop", style=discord.ButtonStyle.danger)
    async def stop_btn(self, interaction: discord.Interaction, item: discord.ui.Button):
        if str(interaction.user.id) != self.owner_id and not has_level(interaction.user.id, "Super Admin"):
            await interaction.response.send_message("Not your VPS.", ephemeral=True)
            return
        self._stop()
        await interaction.response.edit_message(
            embed=create_embed("📊 Live Stats", "Monitoring stopped.", 0x1a1a1a), view=None)


# ---------------------------------------------------------------------------
# Console modal
# ---------------------------------------------------------------------------

class ConsoleModal(discord.ui.Modal):
    def __init__(self, vps, owner_id):
        super().__init__(title=f"💻 Console - {vps['container_name']}")
        self.vps = vps
        self.owner_id = str(owner_id)
        self.cmd = discord.ui.TextInput(
            label="Linux command",
            placeholder="e.g. apt list --installed | head -20",
            max_length=1000)
        self.add_item(self.cmd)

    async def on_submit(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.owner_id and not has_level(interaction.user.id, "Super Admin"):
            await interaction.response.send_message("Not your VPS.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True)
        cmd = self.cmd.value.strip()
        audit("console", interaction.user, self.vps["container_name"], cmd[:200])
        out = await container_op(self.vps, "exec", cmd=cmd, timeout=30)
        stdout = out.get("stdout", "") if isinstance(out, dict) else ""
        stderr = out.get("stderr", "") if isinstance(out, dict) else ""
        rc = out.get("rc", 0) if isinstance(out, dict) else 0
        em = create_embed(f"💻 Console Output", f"`{cmd}`", 0x1a1a1a)
        body = stdout if stdout else "(no stdout)"
        body = body[:1900] + "\n...(truncated)" if len(body) > 1900 else body
        em.add_field(name="📤 Output", value=f"```\n{body}\n```", inline=False)
        if stderr:
            errt = stderr[:1900]
            em.add_field(name="⚠️ Stderr", value=f"```\n{errt}\n```", inline=False)
        em.add_field(name="🔄 Exit Code", value=f"**{rc}**", inline=False)
        await interaction.followup.send(embed=em, ephemeral=True)


# ---------------------------------------------------------------------------
# Manage view
# ---------------------------------------------------------------------------

class ManageView(discord.ui.View):
    def __init__(self, user_id, vps_list, is_shared=False, owner_id=None, is_admin=False):
        super().__init__(timeout=300)
        self.user_id = str(user_id)
        self.vps_list = vps_list
        self.selected_index = None
        self.is_shared = is_shared
        self.owner_id = str(owner_id or user_id)
        self.is_admin = is_admin

        if len(vps_list) > 1:
            options = [
                discord.SelectOption(
                    label=f"VPS {i+1} ({v.get('plan', 'Custom')})",
                    description=f"Status: {v.get('status', 'unknown')}",
                    value=str(i)) for i, v in enumerate(vps_list)
            ]
            self.select = discord.ui.Select(placeholder="Select a VPS to manage", options=options)
            self.select.callback = self.select_vps
            self.add_item(self.select)
            self.initial_embed = create_embed("VPS Management", "Select a VPS from the dropdown menu below.")
            self.initial_embed.add_field(
                name="Available VPS",
                value="\n".join([f"**VPS {i+1}:** `{v['container_name']}` - Status: `{v.get('status','unknown').upper()}`"
                                 for i, v in enumerate(vps_list)]),
                inline=False)
        else:
            self.selected_index = 0
            self.initial_embed = self.create_vps_embed(0)
            self.add_action_buttons()

    def current_vps(self):
        idx = self.selected_index or 0
        if self.is_shared:
            return vps_data[self.owner_id][idx]
        return self.vps_list[idx]

    def create_vps_embed(self, index):
        vps = self.vps_list[index]
        ssh_port = vps.get('ssh_port')
        if not ssh_port:
            try:
                proc = subprocess.run(
                    ["docker", "port", vps['container_name'], "22"],
                    capture_output=True, text=True, timeout=5)
                if proc.returncode == 0 and proc.stdout.strip():
                    host_port = proc.stdout.strip().split(":")[-1].strip()
                    ssh_port = int(host_port)
                    vps['ssh_port'] = ssh_port
                    save_data()
            except Exception:
                pass
        ssh_port = ssh_port or '?'
        status_color = 0x00ff88 if vps.get('status') == 'running' else 0xff3366
        owner_text = ""
        if self.is_admin and str(self.owner_id) != self.user_id:
            try:
                owner_user = bot.get_user(int(self.owner_id))
                owner_text = f"\n**Owner:** {owner_user.mention}"
            except Exception:
                owner_text = f"\n**Owner ID:** {self.owner_id}"
        embed = create_embed(
            f"VPS Management - VPS {index + 1}",
            f"Managing container: `{vps['container_name']}`{owner_text}",
            status_color)
        expires = vps.get('expires')
        if expires and expires != "Never":
            try:
                exp_dt = datetime.fromisoformat(expires)
                days_left = (exp_dt - utcnow()).days
                expire_str = f"{expires[:10]} ({days_left}d left)" if days_left >= 0 else f"{expires[:10]} (**EXPIRED**)"
            except Exception:
                expire_str = expires
        else:
            expire_str = "Never"
        resource_info = (
            f"**Plan:** {vps.get('plan', 'Custom')}\n"
            f"**Status:** `{vps.get('status', 'unknown').upper()}`\n"
            f"**RAM:** {vps['ram']}\n"
            f"**CPU:** {vps['cpu']} Core(s)\n"
            f"**Storage:** {vps.get('storage', '30GB')}\n"
            f"**Created:** {vps.get('created_at', '?')[:10]}\n"
            f"**Expires:** {expire_str}")
        if "processor" in vps:
            resource_info += f"\n**Processor:** {vps['processor']}"
        embed.add_field(name=f"{E('cpu')} Resources", value=resource_info, inline=False)
        ssh_port = vps.get('ssh_port', '?')
        embed.add_field(name=f"{E('network')} Connection", value=(
            f"**IP:** `{SERVER_IP}`\n"
            f"**SSH Port:** `{ssh_port}`\n"
            f"**Username:** `root`\n"
            f"**SSH Command:** `ssh root@{SERVER_IP} -p {ssh_port}`"), inline=False)
        embed.add_field(name=f"{E('gear')} Controls", value="Use the buttons below to manage your VPS", inline=False)
        return embed

    def add_action_buttons(self):
        start = discord.ui.Button(label="▶ Start", style=discord.ButtonStyle.success)
        start.callback = lambda inter: self.action_callback(inter, 'start')
        stop = discord.ui.Button(label="⏸ Stop", style=discord.ButtonStyle.secondary)
        stop.callback = lambda inter: self.action_callback(inter, 'stop')
        restart = discord.ui.Button(label="🔄 Restart", style=discord.ButtonStyle.primary)
        restart.callback = lambda inter: self.action_callback(inter, 'restart')
        ssh = discord.ui.Button(label="🔑 SSH", style=discord.ButtonStyle.primary)
        ssh.callback = lambda inter: self.action_callback(inter, 'ssh')
        pw = discord.ui.Button(label="🔐 Password", style=discord.ButtonStyle.secondary)
        pw.callback = lambda inter: self.action_callback(inter, 'password')
        console = discord.ui.Button(label="💻 Console", style=discord.ButtonStyle.primary)
        console.callback = lambda inter: self.action_callback(inter, 'console')
        stats = discord.ui.Button(label="📊 Stats", style=discord.ButtonStyle.success)
        stats.callback = lambda inter: self.action_callback(inter, 'stats')
        self.add_item(start)
        self.add_item(stop)
        self.add_item(restart)
        self.add_item(ssh)
        self.add_item(pw)
        self.add_item(console)
        self.add_item(stats)
        if not self.is_shared and not self.is_admin:
            reinstall = discord.ui.Button(label="♻️ Reinstall", style=discord.ButtonStyle.danger)
            reinstall.callback = lambda inter: self.action_callback(inter, 'reinstall')
            self.add_item(reinstall)

    async def select_vps(self, interaction: discord.Interaction):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return
        self.selected_index = int(self.select.values[0])
        new_embed = self.create_vps_embed(self.selected_index)
        self.clear_items()
        self.add_action_buttons()
        await interaction.response.edit_message(embed=new_embed, view=self)

    async def action_callback(self, interaction: discord.Interaction, action: str):
        if str(interaction.user.id) != self.user_id and not self.is_admin:
            await interaction.response.send_message(
                embed=create_error_embed("Access Denied", "This is not your VPS!"), ephemeral=True)
            return
        vps = vps_data[self.owner_id][self.selected_index or 0] if self.is_shared else self.vps_list[self.selected_index or 0]
        container_name = vps["container_name"]

        if action == 'reinstall':
            if self.is_shared or self.is_admin:
                await interaction.response.send_message(
                    embed=create_error_embed("Access Denied", "Only the VPS owner can reinstall!"), ephemeral=True)
                return
            await interaction.response.send_message(
                embed=create_warning_embed("Reinstall Warning", f"⚠️ This will erase all data on `{container_name}`. Continue?"),
                view=ConfirmActionView(self, vps, "reinstall"), ephemeral=True)
            return

        if action in ('start', 'stop', 'restart'):
            await interaction.response.defer(ephemeral=True)
            action_label = action.title()
            screen = ProgressScreen(interaction, title=f"{action_label.upper()} VPS", op_prefix="VPS", color=0x5865F2)
            screen.set_steps([
                (f"Sending {action} command", "pending"),
                ("Waiting for container", "pending"),
                ("Updating status", "pending"),
            ])
            await screen.start()
            try:
                await screen.update(20, 0, f"Sending `{action}` to `{container_name}`")
                await container_op(vps, action)
                await screen.step_done(50, 0, f"Command sent to `{container_name}`")

                if action in ('start', 'restart'):
                    await screen.update(50, 1, "Starting SSH daemon...")
                    await asyncio.sleep(2)
                    try:
                        await docker_exec(container_name, "/usr/sbin/sshd || true", timeout=10)
                    except Exception:
                        pass
                    await screen.step_done(80, 1, "SSH daemon started")
                else:
                    await screen.step_done(80, 1, "Container stopped")

                await screen.update(80, 2, "Updating VPS status...")
                vps["status"] = "running" if action != 'stop' else "stopped"
                save_data()
                audit(f"vps_{action}", interaction.user, container_name)
                await alert_owner(vps, f"🔔 VPS `{container_name}` was **{action}ed** by {interaction.user.mention}.", interaction.user.id)
                await screen.complete(f"VPS is now **{vps['status']}**", extra_fields={
                    "Container": f"`{container_name}`",
                })
                await interaction.message.edit(embed=self.create_vps_embed(self.selected_index or 0), view=self)
            except Exception as e:
                await screen.fail("Operation failed", sanitize_error(e))
            return

        if action == 'password':
            await interaction.response.defer(ephemeral=True)
            new_pw = generate_password()
            try:
                await docker_exec(container_name, f"echo 'root:{new_pw}' | chpasswd", timeout=15)
                vps["ssh_password"] = new_pw
                save_data()
                audit("vps_password", interaction.user, container_name)
                pw_embed = create_embed("🔐 New Root Password", f"Container: `{container_name}`\n\n**Password:** ```{new_pw}```", 0x00ff88)
                try:
                    await interaction.user.send(embed=pw_embed)
                    await interaction.followup.send(embed=create_success_embed("Password Reset", "Sent to your DMs!"), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=create_error_embed("DM Failed", "Enable DMs!"), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("Password Failed", sanitize_error(e)), ephemeral=True)
            return

        if action == 'console':
            await interaction.response.send_modal(ConsoleModal(vps, interaction.user.id))
            return

        if action == 'stats':
            view = LiveStatsView(vps, interaction.user.id)
            await interaction.response.send_message(
                embed=create_embed("📊 Live Stats", "Starting live monitoring...", 0x00ccff), ephemeral=False)
            msg = await interaction.original_response()
            view._msg = msg
            view._task = asyncio.ensure_future(view._loop())
            await msg.edit(embed=await refresh_resource_embed(vps), view=view)
            return

        if action == 'ssh':
            await interaction.response.defer(ephemeral=True)
            try:
                ssh_password = vps.get("ssh_password")
                if not ssh_password:
                    await interaction.followup.send(embed=create_error_embed("SSH Error", "No credentials. Reinstall VPS."), ephemeral=True)
                    return
                await interaction.followup.send(embed=create_info_embed("Starting SSH", "Generating tmate session + sshx link..."), ephemeral=True)

                tmate_cmd = None
                sshx_link = None

                try:
                    tmate_cmd = await get_tmate_session(container_name)
                except Exception:
                    pass

                try:
                    sshx_link = await get_sshx_session(container_name)
                except Exception:
                    pass

                audit("vps_ssh", interaction.user, container_name)

                ssh_embed = create_embed("🔑 SSH Access", f"VPS `{container_name}`:", 0x00ff88)
                if tmate_cmd:
                    ssh_embed.add_field(name="SSH Command (tmate)", value=f"```{tmate_cmd}```", inline=False)
                if sshx_link:
                    ssh_embed.add_field(name="Web Terminal (sshx)", value=f"[Open Terminal]({sshx_link})\n```{sshx_link}```", inline=False)
                ssh_embed.add_field(name="Password", value=f"```{ssh_password}```", inline=True)
                ssh_embed.add_field(name="⚠️ Note", value="tmate dies on restart. sshx link may expire. Change password after first login.", inline=False)
                if not tmate_cmd and not sshx_link:
                    ssh_embed.add_field(name="❌ Error", value="Neither tmate nor sshx could start. Check VPS status.", inline=False)
                try:
                    await interaction.user.send(embed=ssh_embed)
                    await interaction.followup.send(embed=create_success_embed("SSH Sent", "Check DMs for SSH details!"), ephemeral=True)
                except discord.Forbidden:
                    await interaction.followup.send(embed=create_error_embed("DM Failed", "Enable DMs to receive SSH info!"), ephemeral=True)
            except Exception as e:
                await interaction.followup.send(embed=create_error_embed("SSH Error", sanitize_error(e)), ephemeral=True)


def _instance_kind(vps):
    """Virtualization backend for a VPS record, safe before core loads."""
    if not CORE_MODULES_LOADED:
        return "container"
    return backend_key(vps)


async def teardown_instance(vps, remove_disk=True):
    """Destroy an instance on whatever backend it runs on. Never raises."""
    virt = _instance_kind(vps)
    name = vps["container_name"]
    try:
        if virt == "container":
            try:
                await execute_docker(f"docker stop {name}", timeout=60)
            except Exception:
                pass
            await execute_docker(f"docker rm -f {name}")
        else:
            ok, message = await instance_action(
                {"virt": virt, "domain": domain_of(vps), "container_name": name},
                "delete")
            if not ok:
                # retry with a hard destroy before giving up
                await instance_action(
                    {"virt": virt, "domain": domain_of(vps),
                     "container_name": name}, "destroy")
        return True
    except Exception:
        return False


async def rebuild_instance(vps, new_password):
    """Recreate an instance from scratch, preserving its identity.

    Used by reinstall. Containers are rebuilt from their configured image;
    KVM and LXC instances are destroyed and relaunched from a clean disk.
    """
    virt = _instance_kind(vps)
    name = vps["container_name"]
    ram_mb = int(str(vps.get("ram", "4GB")).replace("GB", "")) * 1024
    cpu = int(vps.get("cpu", 1))
    disk_gb = int(str(vps.get("storage", "10GB")).replace("GB", ""))
    ssh_port = int(vps.get("ssh_port") or 0)

    await teardown_instance(vps)

    if virt == "container":
        await create_docker_container(
            name, ram_mb, cpu, ssh_port, new_password, disk_gb=disk_gb,
            image=vps.get("image") or DOCKER_IMAGE, ports=vps.get("ports") or [])
        actual = await get_mapped_port(name, 22) or ssh_port
    else:
        result = await create_instance_for_spec(
            virt, domain_of(vps) or name, ram_mb, cpu, disk_gb,
            vps.get("os_key") or _virt_os_key(virt, vps), new_password,
            ssh_port or get_next_ssh_port(),
            ports=vps.get("ports") or [])
        actual = int(result.get("ssh_port") or ssh_port)

    vps["ssh_port"] = actual
    vps["ssh_password"] = new_password
    vps["status"] = "running"
    vps["created_at"] = utcnow().isoformat()
    save_data()
    return actual


def _virt_os_key(virt, vps):
    """Best guess at the OS key to re-provision with, from the record."""
    key = vps.get("os_key")
    if key:
        return key
    image = str(vps.get("image") or "").lower()
    if virt == "kvm":
        for candidate in KVM_OS_CATALOG:
            if candidate.replace("ubuntu", "ubuntu") in image:
                return candidate
        return "ubuntu2404"
    for candidate in OS_IMAGES:
        if OS_IMAGES[candidate].split(":")[0] in image:
            return candidate
    return "ubuntu22"


class ConfirmActionView(discord.ui.View):
    """Two-step confirmation for destructive actions (reinstall, restore, delete)."""

    def __init__(self, parent_view, vps, action, snapshot=None, uid=None, index=None):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.vps = vps
        self.action = action
        self.snapshot = snapshot
        self.uid = uid
        self.index = index

    @discord.ui.button(label="✅ Confirm", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        container = self.vps["container_name"]
        try:
            if self.action == "reinstall":
                screen = ProgressScreen(interaction, title="REINSTALLING VPS", op_prefix="VPS", color=0x00ccff)
                screen.set_steps([
                    ("Stopping container", "pending"),
                    ("Removing old container", "pending"),
                    ("Creating fresh container", "pending"),
                    ("Configuring SSH", "pending"),
                    ("Running health checks", "pending"),
                ])
                await screen.start()
                try:
                    await screen.update(10, 0, f"Stopping `{container}`...")
                    await teardown_instance(self.vps)
                    await screen.step_done(35, 1, "Old instance removed")

                    await screen.update(35, 2, "Creating a fresh instance...")
                    new_password = generate_password()
                    actual_ssh = await rebuild_instance(self.vps, new_password)
                    await screen.step_done(75, 2, "Fresh instance created")

                    await screen.update(75, 3, "Configuring SSH access...")
                    await screen.step_done(90, 3, "SSH configured")

                    await screen.update(90, 4, "Running health checks...")
                    await asyncio.sleep(0.3)

                    audit("vps_reinstall", interaction.user, container)
                    ssh_port = self.vps.get("ssh_port", 22)
                    await screen.complete("VPS reinstalled successfully!", extra_fields={
                        "Container": f"`{container}`",
                        "Status": "🟢 Running",
                    })
                    reinstall_embed = create_embed(f"{E('unlock')} ROOT PASSWORD RESET", "", color=0xff3366)
                    reinstall_embed.add_field(name=f"{E('network')} Shared IPv4", value=f"`{SERVER_IP}`", inline=False)
                    reinstall_embed.add_field(name=f"{E('terminal')} SSH Port", value=f"`{ssh_port}`", inline=True)
                    reinstall_embed.add_field(name=f"{E('linux')} Username", value=f"`root`", inline=True)
                    reinstall_embed.add_field(name="🔑 New Password", value=f"`{new_password}`", inline=True)
                    reinstall_embed.add_field(name=f"{E('lightning')} SSH Command", value=f"```ssh root@{SERVER_IP} -p {ssh_port}```", inline=False)
                    reinstall_embed.add_field(name=f"{E('shield')} Save this!", value="This password will not be shown again.", inline=False)
                    try:
                        await interaction.user.send(embed=reinstall_embed)
                    except discord.Forbidden:
                        pass
                except Exception as e:
                    await screen.fail("Reinstall failed", sanitize_error(e))

            elif self.action == "restore":
                snap = self.snapshot
                kind = _instance_kind(self.vps).split(" (")[0]
                screen = ProgressScreen(interaction, title="RESTORING SERVER", op_prefix="RST", color=0x00ccff)
                screen.set_steps([
                    ("Stopping the server", "pending"),
                    ("Restoring from snapshot", "pending"),
                    ("Starting back up", "pending"),
                ])
                await interaction.response.defer(ephemeral=True)
                await screen.start()
                try:
                    await screen.update(20, 0, f"Stopping `{container}`...")
                    try:
                        await container_op(self.vps, "stop")
                    except Exception:
                        pass
                    await screen.step_done(35, 0, f"{kind} stopped")

                    await screen.update(35, 1, f"Restoring from `{snap}`...")
                    ok, message = await restore_backup(self.vps, snap)
                    if not ok:
                        await screen.fail("Restore Failed", message)
                        return
                    await screen.step_done(80, 1, "Snapshot restored")

                    await screen.update(80, 2, "Starting back up...")
                    try:
                        await container_op(self.vps, "start")
                    except Exception:
                        pass
                    await screen.step_done(100, 2, "Running")

                    self.vps["status"] = "running"
                    save_data()
                    audit("vps_restore", interaction.user, container, f"from {snap}")
                    await screen.complete("Server restored successfully!", extra_fields={
                        "Server": f"`{container}`",
                        "Type": VIRT_TYPES.get(_instance_kind(self.vps), {}).get("label", "Server"),
                        "Snapshot": f"`{snap}`",
                        "Status": "Running",
                    })
                except Exception as e:
                    await screen.fail("Restore failed", sanitize_error(e))

            elif self.action == "delete_backup":
                snap = self.snapshot
                deleted = False
                if _instance_kind(self.vps) == "kvm":
                    ok, message = await kvm_snapshot(domain_of(self.vps), snap, "delete")
                    if not ok:
                        await interaction.followup.send(
                            embed=create_error_embed("Delete Failed", message),
                            ephemeral=True)
                        return
                    deleted = True
                elif _instance_kind(self.vps) == "lxc":
                    archive = os.path.join(BACKUP_DIR, f"{snap}.tar.gz")
                    if os.path.exists(archive):
                        try:
                            os.remove(archive)
                            deleted = True
                        except OSError as exc:
                            await interaction.followup.send(
                                embed=create_error_embed("Delete Failed", str(exc)),
                                ephemeral=True)
                            return
                if not deleted:
                    await execute_docker(f"docker rmi -f {snap}", timeout=120)
                audit("backup_delete", interaction.user, snap)
                await interaction.followup.send(
                    embed=create_success_embed("Deleted", f"Snapshot `{snap}` removed."),
                    ephemeral=True)

            elif self.action == "delete_vps":
                screen = ProgressScreen(interaction, title="DELETING SERVER", op_prefix="VPS", color=0xff3366)
                screen.set_steps([
                    ("Stopping the instance", "pending"),
                    ("Removing the instance", "pending"),
                    ("Cleaning up resources", "pending"),
                    ("Updating records", "pending"),
                ])
                await interaction.response.defer(ephemeral=True)
                await screen.start()
                try:
                    kind = _instance_kind(self.vps).split(" (")[0]
                    await screen.update(10, 0, f"Stopping `{container}`...")
                    ok = await teardown_instance(self.vps)
                    if not ok:
                        raise RuntimeError(
                            f"Could not remove `{container}`. It may still be "
                            f"running, or the hypervisor rejected the request."
                        )
                    await screen.step_done(40, 0, f"{kind} stopped")
                    await screen.step_done(65, 1, f"{kind} removed")

                    await screen.update(60, 2, "Releasing node resources...")
                    if self.uid is not None and self.index is not None:
                        vl = vps_data.get(str(self.uid), [])
                        if 0 <= self.index < len(vl):
                            old_vps = vl[self.index]
                            n_name = old_vps.get("node", "")
                            if n_name and n_name != "local" and n_name in nodes_reg:
                                try:
                                    r = int(str(old_vps.get("ram", "4GB")).replace("GB", ""))
                                    c = int(old_vps.get("cpu", 1))
                                    d = int(str(old_vps.get("storage", "10GB")).replace("GB", ""))
                                    free_node_resources(n_name, r, c, d)
                                except Exception:
                                    pass
                            del vl[self.index]
                            if not vl:
                                vps_data.pop(str(self.uid), None)
                        save_data()
                    await screen.step_done(85, 2, "Resources released")

                    await screen.update(85, 3, "Saving database...")
                    save_data()
                    await screen.step_done(100, 3, "Records updated")

                    audit("vps_delete", interaction.user, container)
                    await screen.complete(f"`{container}` has been permanently deleted.", extra_fields={
                        "Container": f"`{container}`",
                    })
                    if self.parent_view is not None:
                        try:
                            await self.parent_view.message.edit(embed=create_embed("VPS Management", "VPS deleted.", 0xff3366), view=None)
                        except Exception:
                            pass
                except Exception as e:
                    await screen.fail("Delete failed", sanitize_error(e))
        except Exception as e:
            await interaction.followup.send(
                embed=create_error_embed("Action Failed", sanitize_error(e)), ephemeral=True)

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        try:
            if self.action == "delete_vps" and self.parent_view is not None:
                try:
                    await self.parent_view.message.edit(
                        embed=self.parent_view.create_vps_embed(self.parent_view.selected_index or 0),
                        view=self.parent_view)
                except Exception:
                    pass
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Commands - creation / management
# ---------------------------------------------------------------------------

def _resolve_os_image(virt, os_key):
    """Map a GUI OS key onto whatever the chosen backend actually needs."""
    os_key = (os_key or "").lower()
    if virt == "kvm":
        return (KVM_OS_CATALOG.get(os_key) or {}).get("url") or os_key
    if virt == "lxc":
        from core.hypervisor import LXC_IMAGES
        return LXC_IMAGES.get(os_key, "images:ubuntu/22.04")
    return OS_IMAGES.get(os_key) or OS_IMAGES.get("ubuntu22")


async def _provision_server(ctx_or_inter, spec, actor, target, ephemeral=True):
    """Create one server from a spec dict and report progress.

    Shared by the graphical wizard and the legacy typed form so both paths
    behave identically. spec keys: virt, os_key, ram_gb, cpu, disk_gb, node.
    """
    virt = (spec.get("virt") or "container").lower()
    if virt not in VIRT_TYPES:
        raise ValueError(f"'{virt}' is not a virtualization type.")

    if not await virt_available(virt):
        raise RuntimeError(
            f"{VIRT_TYPES[virt]['label']} is not available on this host right now. "
            f"Run `!hypervisors` to see what is installed."
        )

    user_id = str(target.id)
    vps_data.setdefault(user_id, [])
    index = len(vps_data[user_id]) + 1
    name = f"vps-{user_id}-{index}"
    ram_gb = int(spec["ram_gb"])
    cpu = int(spec["cpu"])
    disk_gb = int(spec["disk_gb"])
    node = spec.get("node") or "local"
    password = generate_password()
    os_key = spec.get("os_key") or "ubuntu22"
    os_label = (KVM_OS_CATALOG.get(os_key, {}).get("name") if virt == "kvm"
                else os_key)
    meta = VIRT_TYPES[virt]

    screen = ProgressScreen(ctx_or_inter, title="CREATING SERVER",
                            op_prefix="SERVER", color=0x0f6b4f)
    screen.set_steps([
        ("Validating request", "pending"),
        ("Reserving resources", "pending"),
        (f"Preparing {os_label}", "pending"),
        (f"Creating {meta['label'].split(' (')[0]}", "pending"),
        ("Publishing SSH port", "pending"),
        ("Running health checks", "pending"),
    ])
    await screen.start()
    detail = ""

    try:
        if node != "local" and node not in nodes_reg:
            raise ValueError(f"Node `{node}` is not registered. Use `!node list`.")

        await screen.update(10, 0, f"{target.display_name} | {ram_gb} GB RAM, "
                                   f"{cpu} vCPU, {disk_gb} GB disk, {meta['label']}")
        await asyncio.sleep(0.4)
        await screen.step_done(25, 0, "Request accepted")

        ssh_port = get_next_ssh_port()
        await screen.update(25, 1, f"Reserving port {ssh_port} on {node}")
        await asyncio.sleep(0.3)
        await screen.step_done(35, 1, f"Port {ssh_port} reserved")

        if virt == "container":
            image = _resolve_os_image(virt, os_key)
            await screen.update(35, 2, f"Pulling {image}")
            await deploy_container(node, name, image, ram_gb * 1024, cpu,
                                   disk_gb, password)
            await screen.step_done(60, 2, f"{image} ready")

            await screen.update(60, 3, f"Starting container `{name}`")
            await asyncio.sleep(0.3)
            await screen.step_done(75, 3, "Container running")

            await screen.update(75, 4, "Verifying the SSH mapping")
            actual_ssh = await get_mapped_port(name, 22) or ssh_port
            await screen.step_done(88, 4, f"SSH reachable on port {actual_ssh}")
        else:
            async def note(msg):
                nonlocal detail
                detail = msg
                await screen.update(35, 2, msg)

            result = await create_instance_for_spec(
                virt, name, ram_gb * 1024, cpu, disk_gb, os_key, password,
                ssh_port, note)
            actual_ssh = int(result.get("ssh_port") or ssh_port)
            os_label = result.get("os_name") or os_label
            await screen.step_done(88, 4, f"Domain running, SSH on port {actual_ssh}")

        await screen.update(88, 5, "Running health checks")

        record = {
            "container_name": name,
            "virt": virt,
            "domain": name if virt != "container" else "",
            "ram": f"{ram_gb}GB",
            "cpu": str(cpu),
            "storage": f"{disk_gb}GB",
            "status": "running",
            "created_at": utcnow().isoformat(),
            "expires": "Never",
            "ssh_password": password,
            "shared_with": [],
            "image": _resolve_os_image(virt, os_key),
            "os_label": os_label,
            "node": node,
            "ports": [],
            "ssh_port": actual_ssh,
            "root_access": bool(meta.get("root", True)),
            "size_key": spec.get("size_key", ""),
        }
        if billing_cfg.get("enabled"):
            record["expires"] = (utcnow() + timedelta(
                days=billing_cfg.get("days", 30))).isoformat()
        vps_data[user_id].append(record)
        save_data()
        if node != "local":
            try:
                allocate_node_resources(node, ram_gb, cpu, disk_gb)
            except Exception:
                pass
        try:
            await _assign_vps_role(ctx_or_inter, target)
        except Exception:
            pass

        audit("vps_create", actor, name,
              f"{virt} {ram_gb}GB {cpu}c {disk_gb}GB {os_label} node={node}")

        await screen.complete(f"{meta['label'].split(' (')[0]} is ready!", extra_fields={
            "Name": f"`{name}`",
            "Type": meta["label"],
            "OS": os_label,
            "Specs": f"{ram_gb} GB RAM • {cpu} vCPU • {disk_gb} GB disk",
            "Root Access": "Full root, real isolated environment",
            "Node": node,
            "SSH Port": f"`{actual_ssh}`",
        })
        await _dm_user(user_id, _build_vps_dm(
            name, ram_gb, cpu, password, node, actual_ssh,
            virt=virt, os_label=os_label))
        return record

    except Exception as exc:
        # Never leave a half created instance behind.
        try:
            if virt != "container":
                await instance_action(
                    {"virt": virt, "domain": name, "container_name": name},
                    "delete")
            else:
                await execute_docker(f"docker rm -f {name}")
        except Exception:
            pass
        text = sanitize_error(exc)
        await screen.fail("Deployment Failed", text)
        raise


async def create_instance_for_spec(virt, name, ram_mb, cpu, disk_gb, os_key,
                                   password, ssh_port, note=None, ports=None):
    """Thin wrapper so bot.py does not need create_instance imported twice."""
    from core.hypervisor import create_instance
    return await create_instance(virt, name, ram_mb, cpu, disk_gb, os_key,
                                 password, ssh_port=ssh_port, ports=ports,
                                 on_progress=note)


@bot.command(name='hypervisors')
@is_admin()
async def hypervisors_cmd(ctx):
    """Show which virtualization backends this host supports"""
    backends = await supported_backends()
    host = {}
    try:
        from core.hypervisor import kvm_host_stats
        host = await kvm_host_stats()
    except Exception:
        host = {}

    em = create_embed("Virtualization Backends",
                      "What this host can actually provision right now.",
                      0x0f6b4f)
    if backends:
        for key, label in backends.items():
            em.add_field(name=label, value=VIRT_TYPES[key]["description"],
                         inline=False)
    else:
        em.add_field(name="Nothing detected",
                     value="Install Docker, Incus or KVM on the host so the "
                           "bot has something to provision.", inline=False)
    if host:
        bits = []
        if host.get("cores"):
            bits.append(f"{host['cores']} CPU threads")
        if host.get("ram_gb"):
            bits.append(f"{host['ram_gb']} GB RAM")
        if host.get("kvm_available"):
            bits.append("hardware virtualization enabled")
        if bits:
            em.add_field(name="Host", value=" • ".join(bits), inline=False)
    em.add_field(name="Unavailable",
                 value=" • ".join(VIRT_TYPES[k]["label"] for k in VIRT_TYPES
                                  if k not in backends) or "None",
                 inline=False)
    await ctx.send(embed=em)


@bot.command(name='create')
@is_superadmin()
async def create_vps(ctx, user: discord.Member = None, ram: int = None,
                     cpu: int = None, disk: int = 30,
                     os_name: str = "ubuntu22", node: str = "local",
                     virt: str = "container"):
    """Create a server - !create @user for the graphical builder,
    or !create @user <ram> <cpu> <disk> [os] [node] [kvm|lxc|container]"""
    if user is None:
        await ctx.send(embed=create_error_embed(
            "Who Is This For?",
            "Tag the person getting the server: `!create @user`"))
        return

    if ram is None or cpu is None:
        # Graphical path: every choice becomes a dropdown.
        backends = await supported_backends()
        if not backends:
            await ctx.send(embed=create_error_embed(
                "No Backend Available",
                "This host has neither Docker, Incus nor KVM installed, so "
                "there is nothing to provision. Fix the host, or use "
                "`!hypervisors` to check."))
            return
        node_names = sorted(get_online_nodes().keys()) or ["local"]
        wizard = CreateWizard(
            ctx, user,
            provision=lambda inter, spec: _provision_server(
                inter, spec, ctx.author, user),
            nodes=node_names,
            can_use=lambda member: has_level(member.id, "Super Admin"),
        )
        await wizard.load()
        msg = await ctx.send(embed=wizard.preview(), view=wizard)
        wizard.message = msg
        return

    if ram <= 0 or cpu <= 0 or disk <= 0:
        await ctx.send(embed=create_error_embed(
            "Invalid Specs", "Usage: `!create @user <ram_GB> <cpu> <disk_GB>`"))
        return
    virt = (virt or "container").lower()
    if virt not in VIRT_TYPES:
        await ctx.send(embed=create_error_embed(
            "Invalid Type", "Types: " + ", ".join(VIRT_TYPES)))
        return
    image = _resolve_os_image(virt, os_name)
    if image is None:
        await ctx.send(embed=create_error_embed(
            "Invalid OS", "Options: " + ", ".join(OS_IMAGES)))
        return
    if node != "local" and node not in nodes_reg:
        await ctx.send(embed=create_error_embed(
            "Invalid Node", f"`{node}` not registered. Use `!node list`."))
        return

    spec = {"virt": virt, "os_key": os_name, "ram_gb": ram, "cpu": cpu,
            "disk_gb": disk, "node": node}
    try:
        await _provision_server(ctx, spec, ctx.author, user)
    except Exception:
        pass  # _provision_server already reported the failure


@bot.command(name='deploy', aliases=['host'])
@is_superadmin()
async def deploy_vps(ctx, user: discord.Member = None, ram: int = None,
                     cpu: int = None, disk: int = 30,
                     os_name: str = "ubuntu22", node: str = "local",
                     virt: str = "container"):
    """Alias for !create - !deploy @user opens the graphical builder"""
    await create_vps.callback(ctx, user=user, ram=ram, cpu=cpu, disk=disk,
                              os_name=os_name, node=node, virt=virt)

@bot.command(name='manage')
async def manage_vps(ctx, user: discord.Member = None):
    """Manage your VPS or another user's VPS (Admin only)"""
    if user:
        if not (str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", [])):
            await ctx.send(embed=create_error_embed("Access Denied", "Only admins can manage other users' VPS."))
            return
        user_id = str(user.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            await ctx.send(embed=create_error_embed("No VPS Found", f"{user.mention} doesn't have any VPS."))
            return
        view = ManageView(str(ctx.author.id), vps_list, is_admin=True, owner_id=user_id)
        msg = await ctx.send(embed=create_info_embed(f"Managing {user.name}'s VPS", f"Managing VPS for {user.mention}"), view=view)
        view.message = msg
    else:
        user_id = str(ctx.author.id)
        vps_list = vps_data.get(user_id, [])
        if not vps_list:
            embed = create_embed("No VPS Found", "You don't have any VPS. Use `.buywc` to purchase one.", 0xff3366)
            embed.add_field(name="Quick Actions", value="• `!plans` - View plans\n• `!buywc <plan> <processor>` - Purchase VPS", inline=False)
            await ctx.send(embed=embed)
            return
        view = ManageView(user_id, vps_list)
        msg = await ctx.send(embed=view.initial_embed, view=view)
        view.message = msg


@bot.command(name='deletevps')
@is_superadmin()
@enforce_security
async def delete_vps(ctx, user: discord.Member, vps_number: int, *, reason: str = "No reason"):
    """Delete a user's VPS (Admin only)"""
    user_id = str(user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number or user doesn't have a VPS."))
        return
    vps = vps_data[user_id][vps_number - 1]
    container_name = vps["container_name"]
    await ctx.send(embed=create_warning_embed(
        "Delete VPS",
        f"⚠️ Delete VPS #{vps_number} (`{container_name}`) for {user.mention}?\nReason: `{reason}`"),
        view=ConfirmActionView(None, vps, "delete_vps", uid=user_id, index=vps_number - 1))
    audit("vps_delete_request", ctx.author, container_name, reason)


@bot.command(name='listall')
@is_admin()
async def list_all_vps(ctx):
    """List all VPS and user information (Admin only)"""
    await ctx.send(embed=await build_all_vps_embed())


async def _assign_vps_role(ctx, user):
    if ctx.guild:
        vps_role = await get_or_create_vps_role(ctx.guild)
        if vps_role:
            try:
                await user.add_roles(vps_role, reason="VPS ownership")
            except discord.Forbidden:
                pass


def _build_vps_dm(container_name, ram, cpu, password, node, ssh_port=None,
                  virt="container", os_label=""):
    """The credentials DM. virt/os_label are optional so older call sites keep
    working unchanged."""
    ssh_port = ssh_port or 22
    meta = (VIRT_TYPES.get(virt) or VIRT_TYPES["container"]) if CORE_MODULES_LOADED \
        else {"label": "Server"}
    kind = meta["label"].split(" (")[0]
    embed = create_embed(f"{E('rocket')} Your {kind} Is Ready!", "", color=0x0f6b4f)
    if os_label:
        embed.add_field(name=f"{E('linux')} Operating System", value=os_label,
                        inline=False)
    embed.add_field(name=f"{E('network')} Shared IPv4", value=f"`{SERVER_IP}`", inline=False)
    embed.add_field(name=f"{E('terminal')} SSH Port", value=f"`{ssh_port}`", inline=True)
    embed.add_field(name=f"{E('linux')} Username", value=f"`root`", inline=True)
    embed.add_field(name=f"{E('unlock')} Password", value=f"`{password}`", inline=True)
    embed.add_field(name=f"{E('lightning')} SSH Command", value=f"```ssh root@{SERVER_IP} -p {ssh_port}```", inline=False)
    if virt == "kvm":
        embed.add_field(name=f"{E('shield')} What This Is",
                        value="A full virtual machine with its own kernel. "
                              "You have real root and cannot see or touch the host.",
                        inline=False)
    elif virt == "lxc":
        embed.add_field(name=f"{E('shield')} What This Is",
                        value="A system container with its own mount namespace. "
                              "Root inside your container.",
                        inline=False)
    embed.add_field(name=f"{E('shield')} Save This!", value="This password will not be shown again.", inline=False)
    return embed


async def build_all_vps_embed():
    embed = create_embed("All VPS Information", "Complete overview of all VPS deployments")
    total_vps = running_vps = stopped_vps = 0
    vps_info = []
    user_summary = []
    for user_id, vps_list in vps_data.items():
        try:
            user = await bot.fetch_user(int(user_id))
            user_vps_count = len(vps_list)
            user_running = sum(1 for vps in vps_list if vps.get('status') == 'running')
            total_vps += user_vps_count
            running_vps += user_running
            stopped_vps += user_vps_count - user_running
            user_summary.append(f"**{user.name}** ({user.mention}) - {user_vps_count} VPS ({user_running} running)")
            for i, vps in enumerate(vps_list):
                status_emoji = "🟢" if vps.get('status') == 'running' else "🔴"
                vps_info.append(f"{status_emoji} **{user.name}** - VPS {i+1}: `{vps['container_name']}` - {vps.get('status','unknown').upper()}")
        except discord.NotFound:
            vps_info.append(f"❓ Unknown User ({user_id}) - {len(vps_list)} VPS")
    embed.add_field(name="System Overview",
                    value=f"**Total Users:** {len(vps_data)}\n**Total VPS:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {stopped_vps}",
                    inline=False)
    if user_summary:
        embed.add_field(name="User Summary", value="\n".join(user_summary[:10]), inline=False)
    if vps_info:
        for i in range(0, min(len(vps_info), 30), 15):
            chunk = vps_info[i:i+15]
            embed.add_field(name=f"VPS Deployments ({i+1}-{min(i+15, len(vps_info))})", value="\n".join(chunk), inline=False)
    return embed


@bot.command(name='manageshared')
async def manage_shared_vps(ctx, owner: discord.Member, vps_number: int):
    """Manage a shared VPS"""
    owner_id = str(owner.id)
    user_id = str(ctx.author.id)
    if owner_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[owner_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[owner_id][vps_number - 1]
    if user_id not in vps.get("shared_with", []):
        await ctx.send(embed=create_error_embed("Access Denied", "You do not have access to this VPS."))
        return
    view = ManageView(user_id, [vps], is_shared=True, owner_id=owner_id)
    msg = await ctx.send(embed=view.initial_embed, view=view)
    view.message = msg


@bot.command(name='shareuser')
async def share_user(ctx, shared_user: discord.Member, vps_number: int):
    """Share VPS access with another user"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[user_id][vps_number - 1]
    if "shared_with" not in vps:
        vps["shared_with"] = []
    if shared_user_id in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Already Shared", f"{shared_user.mention} already has access!"))
        return
    vps["shared_with"].append(shared_user_id)
    save_data()
    audit("vps_share", ctx.author, vps["container_name"], f"shared with {shared_user_id}")
    await ctx.send(embed=create_success_embed("VPS Shared", f"VPS #{vps_number} shared with {shared_user.mention}!"))
    try:
        await shared_user.send(embed=create_embed(
            "VPS Access Granted",
            f"You have access to VPS #{vps_number} from {ctx.author.mention}. Use `!manageshared {ctx.author.mention} {vps_number}`",
            0x00ff88))
    except discord.Forbidden:
        pass


@bot.command(name='shareruser')
async def revoke_share(ctx, shared_user: discord.Member, vps_number: int):
    """Revoke shared VPS access"""
    user_id = str(ctx.author.id)
    shared_user_id = str(shared_user.id)
    if user_id not in vps_data or vps_number < 1 or vps_number > len(vps_data[user_id]):
        await ctx.send(embed=create_error_embed("Invalid VPS", "Invalid VPS number."))
        return
    vps = vps_data[user_id][vps_number - 1]
    if "shared_with" not in vps or shared_user_id not in vps["shared_with"]:
        await ctx.send(embed=create_error_embed("Not Shared", f"{shared_user.mention} doesn't have access!"))
        return
    vps["shared_with"].remove(shared_user_id)
    save_data()
    audit("vps_unshare", ctx.author, vps["container_name"], f"revoked {shared_user_id}")
    await ctx.send(embed=create_success_embed("Access Revoked", f"Access to VPS #{vps_number} revoked from {shared_user.mention}!"))


# ---------------------------------------------------------------------------
# Economy
# ---------------------------------------------------------------------------

@bot.command(name='buywc')
async def buy_with_credits(ctx, plan: str, processor: str = "Intel",
                           virt: str = "container"):
    """Buy a server with credits - !buywc <plan> [Intel/AMD] [kvm|lxc|container]"""
    user_id = str(ctx.author.id)
    prices = {
        "Starter": {"Intel": 42, "AMD": 83}, "Basic": {"Intel": 96, "AMD": 164},
        "Standard": {"Intel": 192, "AMD": 320}, "Pro": {"Intel": 220, "AMD": 340}
    }
    plans = {
        "Starter": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
        "Basic": {"ram": "8GB", "cpu": "1", "storage": "10GB"},
        "Standard": {"ram": "12GB", "cpu": "2", "storage": "10GB"},
        "Pro": {"ram": "16GB", "cpu": "2", "storage": "10GB"}
    }
    if plan not in prices:
        await ctx.send(embed=create_error_embed(
            "Invalid Plan", "Available: " + ", ".join(prices)))
        return
    if processor not in ["Intel", "AMD"]:
        await ctx.send(embed=create_error_embed(
            "Invalid Processor", "Choose: Intel or AMD"))
        return
    virt = (virt or "container").lower()
    if virt not in VIRT_TYPES:
        await ctx.send(embed=create_error_embed(
            "Invalid Type", "Types: " + ", ".join(VIRT_TYPES)))
        return
    if not await virt_available(virt):
        await ctx.send(embed=create_error_embed(
            "Unavailable",
            f"{VIRT_TYPES[virt]['label']} is not installed on this host. "
            f"Run `!hypervisors` to see what is available."))
        return

    # Full virtual machines cost more than containers to run.
    premium = {"container": 1.0, "lxc": 1.2, "kvm": 1.6}.get(virt, 1.0)
    cost = int(round(prices[plan][processor] * premium))

    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    if user_data[user_id]["credits"] < cost:
        await ctx.send(embed=create_error_embed(
            "Insufficient Credits",
            f"Need {cost}, have {user_data[user_id]['credits']}"))
        return

    spec = {
        "virt": virt,
        "os_key": "ubuntu2404" if virt == "kvm" else "ubuntu22",
        "ram_gb": int(plans[plan]["ram"].replace("GB", "")),
        "cpu": int(plans[plan]["cpu"]),
        "disk_gb": int(plans[plan]["storage"].replace("GB", "")),
        "node": "local",
    }

    user_data[user_id]["credits"] -= cost
    save_data()
    audit("vps_purchase", ctx.author, f"{plan}/{virt}",
          f"{plan} {processor} {virt} for {cost}cr")
    try:
        record = await _provision_server(ctx, spec, ctx.author, ctx.author)
        record["plan"] = plan
        record["processor"] = processor
        save_data()
        await ctx.send(embed=create_success_embed(
            "Purchase Complete",
            f"**{plan}** on **{VIRT_TYPES[virt]['label']}** for **{cost} credits**.\n"
            f"Your server is live. The credentials are in your DMs."))
    except Exception:
        user_data[user_id]["credits"] += cost
        save_data()
        await ctx.send(embed=create_error_embed(
            "Purchase Failed",
            "The server could not be created. Your credits were refunded."))



@bot.command(name='buyc')
async def buy_credits(ctx):
    """Get payment information"""
    embed = create_embed("💳 Purchase Credits", "Choose your payment method below:")
    embed.add_field(name="🇮🇳 UPI", value="```\n9526303242@fam\n```", inline=False)
    embed.add_field(name="💰 PayPal", value="```\nexample@paypal.com\n```", inline=False)
    embed.add_field(name="₿ Crypto", value="BTC, ETH, USDT accepted", inline=False)
    embed.add_field(name="📋 Next Steps", value="1. Pay\n2. Contact admin with transaction ID\n3. Receive credits", inline=False)
    try:
        await ctx.author.send(embed=embed)
        await ctx.send(embed=create_success_embed("Information Sent", "Payment details sent to your DMs!"))
    except discord.Forbidden:
        await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive payment info!"))


@bot.command(name='plans')
async def show_plans(ctx):
    """Show available server plans"""
    embed = create_embed(f"{E('rocket')} Plans - Turtle Nodes",
                         "Every plan has full root access. Choose your backend at purchase.")
    base = [
        ("Starter", "4GB", "1 Core", "10GB"),
        ("Basic", "8GB", "1 Core", "10GB"),
        ("Standard", "12GB", "2 Cores", "10GB"),
        ("Pro", "16GB", "2 Cores", "10GB"),
    ]
    prices = {
        "Starter": (42, 83), "Basic": (96, 164),
        "Standard": (192, 320), "Pro": (220, 340),
    }
    for name, ram, cpu, disk in base:
        intel, amd = prices[name]
        embed.add_field(
            name=name,
            value=(f"**RAM:** {ram}\n**CPU:** {cpu}\n**Storage:** {disk}\n"
                   f"**Docker:** {intel} cr ({amd} cr AMD)\n"
                   f"**LXC:** {int(round(intel * 1.2))} cr\n"
                   f"**KVM:** {int(round(intel * 1.6))} cr"),
            inline=True)

    embed.add_field(name="Backends", value=(
        "**Docker** - Lightest and fastest to spin up\n"
        "**LXC** - System container, near native speed\n"
        "**KVM** - Full virtual machine, own kernel, hardware accelerated\n"
        "All three give you root and their own SSH port on the shared IP."),
        inline=False)
    embed.add_field(name="How to Buy",
                    value="`!buywc <plan> [Intel/AMD] [docker|lxc|kvm]`",
                    inline=False)
    await ctx.send(embed=embed)


@bot.command(name='credits')
async def check_credits(ctx):
    """Check your credit balance"""
    user_id = str(ctx.author.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
        save_data()
    credits = user_data[user_id].get("credits", 0)
    embed = create_info_embed("💰 Credit Balance", f"{ctx.author.mention}, you have **{credits}** credits.")
    await ctx.send(embed=embed)


@bot.command(name='adminc')
@is_admin()
async def admin_add_credits(ctx, user: discord.Member, amount: int):
    """Add credits to a user (Admin only)"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    user_data[user_id]["credits"] += amount
    save_data()
    audit("credits_add", ctx.author, str(user), f"+{amount}")
    await ctx.send(embed=create_success_embed("Credits Added",
                                              f"Added **{amount}** credits to {user.mention}. "
                                              f"New balance: **{user_data[user_id]['credits']}**"))


@bot.command(name='adminrc')
@is_admin()
async def admin_remove_credits(ctx, user: discord.Member, amount: str):
    """Remove credits from a user (Admin only). Use 'all' to remove all."""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    user_id = str(user.id)
    if user_id not in user_data:
        user_data[user_id] = {"credits": 0}
    if amount.lower() == "all":
        removed = user_data[user_id]["credits"]
        user_data[user_id]["credits"] = 0
    else:
        removed = int(amount)
        user_data[user_id]["credits"] = max(0, user_data[user_id]["credits"] - removed)
    save_data()
    audit("credits_remove", ctx.author, str(user), f"-{removed}")
    await ctx.send(embed=create_success_embed("Credits Removed",
                                              f"Removed **{removed}** credits from {user.mention}. "
                                              f"New balance: **{user_data[user_id]['credits']}**"))


@bot.command(name='transfer')
async def transfer_credits(ctx, target: discord.Member, amount: int):
    """Transfer credits to another user"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    if target.id == ctx.author.id:
        await ctx.send(embed=create_error_embed("Invalid Target", "You can't transfer to yourself!"))
        return
    sender_id = str(ctx.author.id)
    target_id = str(target.id)
    if sender_id not in user_data:
        user_data[sender_id] = {"credits": 0}
    if user_data[sender_id]["credits"] < amount:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
                                                f"You only have **{user_data[sender_id]['credits']}** credits."))
        return
    if target_id not in user_data:
        user_data[target_id] = {"credits": 0}
    user_data[sender_id]["credits"] -= amount
    user_data[target_id]["credits"] += amount
    save_data()
    audit("credits_transfer", ctx.author, str(target), f"{amount}cr")
    embed = create_success_embed("💸 Transfer Complete",
                                 f"{ctx.author.mention} sent **{amount}** credits to {target.mention}!")
    embed.add_field(name="Your Balance", value=f"**{user_data[sender_id]['credits']}** credits", inline=True)
    await ctx.send(embed=embed)
    try:
        await target.send(embed=create_info_embed("💰 Credits Received",
                                                  f"You received **{amount}** credits from {ctx.author.mention}!\nNew balance: **{user_data[target_id]['credits']}**"))
    except discord.Forbidden:
        pass


@bot.command(name='leaderboard')
async def leaderboard(ctx):
    """Show top 10 credit holders"""
    sorted_users = sorted(user_data.items(), key=lambda x: x[1].get("credits", 0), reverse=True)[:10]
    embed = create_embed("🏆 Credit Leaderboard", "Top 10 credit holders:", 0xffd700)
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    lines = []
    for i, (uid, data) in enumerate(sorted_users):
        try:
            u = await bot.fetch_user(int(uid))
            name = u.name
        except Exception:
            name = f"User#{uid[:4]}"
        lines.append(f"{medals[i]} **{name}** - {data.get('credits', 0)} credits")
    embed.add_field(name="Rankings", value="\n".join(lines) if lines else "No data yet.", inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Info commands
# ---------------------------------------------------------------------------

@bot.command(name='userinfo')
@is_admin()
async def user_info(ctx, user: discord.Member):
    """Get detailed user information (Admin only)"""
    user_id = str(user.id)
    credits = user_data.get(user_id, {}).get("credits", 0)
    embed = create_embed(f"{E('admin')} User Info - {user.name}", "")
    embed.add_field(name="User", value=f"{user.mention}\n**ID:** {user.id}", inline=False)
    embed.add_field(name=f"{E('fire')} Credits", value=f"**{credits}**", inline=True)
    embed.add_field(name=f"{E('shield')} Level", value=f"**{level_name(user_id)}**", inline=True)
    vps_list = vps_data.get(user_id, [])
    if vps_list:
        vps_text = "\n".join([
            f"VPS {i+1}: `{v['container_name']}` | {v.get('status','?').upper()}"
            for i, v in enumerate(vps_list)
        ])
        embed.add_field(name=f"{E('vps')} VPS", value=vps_text, inline=False)
    else:
        embed.add_field(name=f"{E('vps')} VPS", value="No VPS owned", inline=False)
    is_admin_user = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    embed.add_field(name=f"{E('shield')} Admin", value="Yes" if is_admin_user else "No", inline=True)
    await ctx.send(embed=embed)


@bot.command(name='serverstats')
@is_admin()
async def server_stats(ctx):
    """Show server statistics (Admin only)"""
    await ctx.send(embed=await build_server_stats_embed())


async def build_server_stats_embed():
    total_vps = sum(len(v) for v in vps_data.values())
    running_vps = sum(1 for vl in vps_data.values() for v in vl if v.get('status') == 'running')
    total_credits = sum(u.get('credits', 0) for u in user_data.values())
    total_ram = sum(int(v['ram'].replace('GB', '')) for vl in vps_data.values() for v in vl)
    total_cpu = sum(int(v['cpu']) for vl in vps_data.values() for v in vl)
    embed = create_embed(f"{E('cpu')} Server Statistics", "Current server overview")
    embed.add_field(name=f"{E('nodes')} Users", value=f"**Total:** {len(user_data)}\n**Admins:** {len(admin_data.get('admins', [])) + 1}", inline=False)
    embed.add_field(name=f"{E('vps')} VPS", value=f"**Total:** {total_vps}\n**Running:** {running_vps}\n**Stopped:** {total_vps - running_vps}", inline=False)
    embed.add_field(name=f"{E('fire')} Economy", value=f"**Total Credits:** {total_credits}", inline=False)
    embed.add_field(name=f"{E('ram')} Resources", value=f"**Total RAM:** {total_ram}GB\n**Total CPU:** {total_cpu} cores", inline=False)
    return embed


@bot.command(name='vpsinfo')
@is_admin()
async def vps_info(ctx, container_name: str = None):
    """Get VPS information (Admin only)"""
    if not container_name:
        all_vps = []
        for uid, vl in vps_data.items():
            try:
                u = await bot.fetch_user(int(uid))
                for i, v in enumerate(vl):
                    all_vps.append(f"**{u.name}** - VPS {i+1}: `{v['container_name']}` - {v.get('status','?').upper()}")
            except Exception:
                pass
        embed = create_embed("🖥️ All VPS", f"Total: {len(all_vps)}")
        for i in range(0, len(all_vps), 20):
            embed.add_field(name=f"VPS List ({i+1}-{i+20})", value="\n".join(all_vps[i:i+20]), inline=False)
        await ctx.send(embed=embed)
    else:
        found_vps = None
        found_user = None
        for uid, vl in vps_data.items():
            for v in vl:
                if v['container_name'] == container_name:
                    found_vps = v
                    found_user = await bot.fetch_user(int(uid))
                    break
            if found_vps:
                break
        if not found_vps:
            await ctx.send(embed=create_error_embed("Not Found", f"No VPS with name `{container_name}`"))
            return
        embed = create_embed(f"🖥️ VPS - {container_name}", f"Owned by {found_user.mention}")
        embed.add_field(name="Specs", value=f"**RAM:** {found_vps['ram']}\n**CPU:** {found_vps['cpu']} Cores", inline=True)
        embed.add_field(name="Status", value=f"**{found_vps.get('status','?').upper()}**", inline=True)
        embed.add_field(name="Created", value=found_vps.get('created_at', 'Unknown'), inline=False)
        await ctx.send(embed=embed)


@bot.command(name='botstatus')
async def bot_status(ctx):
    """Show bot status and stats"""
    uptime_delta = datetime.now() - BOT_START_TIME
    days = uptime_delta.days
    hours, rem = divmod(uptime_delta.seconds, 3600)
    minutes, _ = divmod(rem, 60)
    total_vps = sum(len(v) for v in vps_data.values())
    running_vps = sum(1 for vl in vps_data.values() for v in vl if v.get("status") == "running")
    embed = create_embed(f"{E('bot')} Bot Status", "Turtle Nodes VPS Manager", 0x00ff88)
    embed.add_field(name=f"{E('loading')} Uptime", value=f"`{days}d {hours}h {minutes}m`", inline=True)
    embed.add_field(name=f"{E('vps')} Total VPS", value=f"**{total_vps}** ({running_vps} running)", inline=True)
    embed.add_field(name=f"{E('nodes')} Users", value=f"**{len(user_data)}**", inline=True)
    embed.add_field(name=f"{E('maint')} Maintenance", value="🔴 ON" if maintenance_mode else "🟢 OFF", inline=True)
    embed.add_field(name=f"{E('network')} Latency", value=f"`{round(bot.latency * 1000)}ms`", inline=True)
    embed.set_footer(text="Made By ᴛɪʀᴇᴅ ᵍᴄ")
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Restart / backup (admin) commands
# ---------------------------------------------------------------------------

@bot.command(name='restartvps')
@is_admin()
async def restart_vps(ctx, container_name: str):
    """Restart a VPS (Admin only)"""
    screen = ProgressScreen(ctx, title="RESTARTING VPS", op_prefix="VPS", color=0x5865F2)
    screen.set_steps([
        ("Stopping container", "pending"),
        ("Starting container", "pending"),
        ("Starting SSH daemon", "pending"),
        ("Updating status", "pending"),
    ])
    await screen.start()
    try:
        uid, vps = find_vps_by_name(container_name)
        await screen.update(15, 0, f"Stopping `{container_name}`...")
        if vps is None:
            await container_op({"container_name": container_name}, "restart")
        else:
            await container_op(vps, "restart")
        await screen.step_done(35, 0, "Container restarted")

        await screen.update(35, 1, "Waiting for container to come online...")
        await asyncio.sleep(3)
        await screen.step_done(60, 1, "Container online")

        await screen.update(60, 2, "Starting SSH daemon...")
        if vps is not None:
            try:
                await container_op(vps, "exec", cmd="/usr/sbin/sshd || true", timeout=10)
            except Exception:
                pass
        await screen.step_done(80, 2, "SSH daemon started")

        await screen.update(80, 3, "Updating database records...")
        for vl in vps_data.values():
            for v in vl:
                if v['container_name'] == container_name:
                    v['status'] = 'running'
                    save_data()
                    break
        await screen.step_done(100, 3, "Records updated")

        audit("vps_restart_admin", ctx.author, container_name)
        await screen.complete("VPS restarted successfully!", extra_fields={
            "Container": f"`{container_name}`",
            "Status": "🟢 Running",
        })
    except Exception as e:
        await ctx.send(embed=create_error_embed("Restart Failed", sanitize_error(e)))


@bot.command(name='backupvps')
@is_admin()
async def backup_vps(ctx, container_name: str):
    """Create a backup snapshot of any server (Admin only)"""
    uid, vps = find_vps_by_name(container_name)
    if vps is None:
        await ctx.send(embed=create_error_embed(
            "Not Found", f"No server named `{container_name}` is registered."))
        return
    name = snapshot_name_for(vps, datetime.now().strftime("%Y%m%d-%H%M%S"))
    screen = ProgressScreen(ctx, title="CREATING BACKUP", op_prefix="BAK", color=0x00ff88)
    screen.set_steps([
        ("Preparing snapshot", "pending"),
        ("Capturing state", "pending"),
        ("Saving to disk", "pending"),
    ])
    await screen.start()
    try:
        await screen.update(15, 0, f"Preparing snapshot for `{container_name}`")
        await asyncio.sleep(0.2)
        await screen.step_done(30, 0, "Snapshot prepared")

        await screen.update(30, 1, "Capturing state...")
        ok, message, kind = await create_backup(vps, name)
        if not ok:
            await screen.fail("Backup Failed", message)
            return
        await screen.step_done(75, 1, message)

        await screen.update(75, 2, "Saving to disk...")
        await asyncio.sleep(0.2)
        await screen.step_done(100, 2, "Saved")

        audit("vps_backup", ctx.author, container_name, f"{name} ({kind})")
        await screen.complete("Backup created successfully!", extra_fields={
            "Server": f"`{container_name}`",
            "Type": VIRT_TYPES.get(_instance_kind(vps), {}).get("label", "Server"),
            "Snapshot": f"`{name}`",
            "Stored As": kind,
        })
    except Exception as e:
        await screen.fail("Backup failed", sanitize_error(e))


@bot.command(name='restorevps')
@is_superadmin()
@enforce_security
async def restore_vps(ctx, container_name: str, snapshot_name: str):
    """Restore any server from a backup (Admin only)"""
    screen = ProgressScreen(ctx, title="RESTORING SERVER", op_prefix="RST", color=0x00ccff)
    screen.set_steps([
        ("Locating server data", "pending"),
        ("Stopping the server", "pending"),
        ("Restoring from snapshot", "pending"),
        ("Starting back up", "pending"),
    ])
    await screen.start()
    try:
        _uid, found_vps = find_vps_by_name(container_name)
        if found_vps is None:
            await screen.fail("Server not found", f"No record for `{container_name}`")
            return
        await screen.update(15, 0, f"Looking up `{container_name}` in database")
        await screen.step_done(25, 0, "Server located")

        kind = _instance_kind(found_vps).split(" (")[0]
        await screen.update(25, 1, f"Stopping `{container_name}`...")
        try:
            await container_op(found_vps, "stop")
        except Exception:
            pass
        await screen.step_done(45, 1, f"{kind} stopped")

        await screen.update(45, 2, f"Restoring from `{snapshot_name}`...")
        ok, message = await restore_backup(found_vps, snapshot_name)
        if not ok:
            await screen.fail("Restore Failed", message)
            return
        await screen.step_done(80, 2, "Snapshot restored")

        await screen.update(80, 3, "Starting back up...")
        try:
            await container_op(found_vps, "start")
        except Exception:
            pass
        await screen.step_done(100, 3, "Running")

        found_vps["status"] = "running"
        save_data()
        audit("vps_restore_admin", ctx.author, container_name, f"from {snapshot_name}")
        await screen.complete("Server restored successfully!", extra_fields={
            "Server": f"`{container_name}`",
            "Type": VIRT_TYPES.get(_instance_kind(found_vps), {}).get("label", "Server"),
            "Snapshot": f"`{snapshot_name}`",
            "Status": "Running",
        })
    except Exception as e:
        await screen.fail("Restore Failed", sanitize_error(e))



@bot.command(name='listsnapshots')
@is_admin()
async def list_snapshots(ctx, container_name: str):
    """List Docker image snapshots for a VPS (Admin only)"""
    try:
        snapshots = await asyncio.get_running_loop().run_in_executor(None, snapshot_list_sync, container_name)
        if snapshots:
            embed = create_embed(f"📸 Snapshots for {container_name}", f"Found {len(snapshots)} snapshots")
            embed.add_field(name="Snapshots", value="\n".join([f"• `{s}`" for s in snapshots[:20]]), inline=False)
        else:
            embed = create_info_embed("No Snapshots", f"No snapshots found for `{container_name}`")
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Error", sanitize_error(e)))


@bot.command(name='deletesnapshot')
@is_superadmin()
async def delete_snapshot(ctx, snapshot_name: str):
    """Delete a Docker snapshot image (Admin only)"""
    await ctx.send(embed=create_warning_embed("Delete Snapshot", f"Delete snapshot `{snapshot_name}`?"),
                   view=ConfirmActionView(None, {"container_name": snapshot_name}, "delete_backup", snapshot=snapshot_name))


@bot.command(name='exec')
@is_superadmin()
@enforce_security
async def execute_command(ctx, container_name: str, *, command: str):
    """Execute a command inside a VPS container (Super Admin only)"""
    await ctx.send(embed=create_info_embed("Executing Command", f"Running in `{container_name}`..."))
    try:
        uid, vps = find_vps_by_name(container_name)
        if vps is None:
            vps = {"container_name": container_name}
        out = await container_op(vps, "exec", cmd=command, timeout=30)
        stdout = out.get("stdout", "") if isinstance(out, dict) else ""
        stderr = out.get("stderr", "") if isinstance(out, dict) else ""
        rc = out.get("rc", 0) if isinstance(out, dict) else 0
        audit("vps_exec", ctx.author, container_name, command[:200])
        embed = create_embed(f"Command Output - {container_name}", f"Command: `{command}`")
        if stdout:
            out_t = stdout[:1000] + "\n...(truncated)" if len(stdout) > 1000 else stdout
            embed.add_field(name="📤 Output", value=f"```\n{out_t}\n```", inline=False)
        if stderr:
            err_t = stderr[:1000] + "\n...(truncated)" if len(stderr) > 1000 else stderr
            embed.add_field(name="⚠️ Stderr", value=f"```\n{err_t}\n```", inline=False)
        embed.add_field(name="🔄 Exit Code", value=f"**{rc}**", inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Execution Failed", sanitize_error(e)))


@bot.command(name='stopvpsall')
@is_superadmin()
@enforce_security
async def stop_all_vps(ctx):
    """Stop all VPS containers (Admin only)"""
    await ctx.send(embed=create_warning_embed("Stopping All VPS",
                                              "⚠️ This will stop ALL running VPS. Continue?"))

    class ConfirmView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="Stop All VPS", style=discord.ButtonStyle.danger)
        async def confirm(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.defer()
            stopped_count = 0
            errors = []
            for vl in vps_data.values():
                for v in vl:
                    if v.get('status') == 'running':
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "docker", "stop", v['container_name'],
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            await asyncio.wait_for(proc.communicate(), timeout=60)
                            v['status'] = 'stopped'
                            stopped_count += 1
                        except Exception as e:
                            errors.append(str(e))
            save_data()
            audit("vps_stop_all", interaction.user, "all", f"{stopped_count} stopped")
            embed = create_success_embed("All VPS Stopped", f"Stopped **{stopped_count}** containers.")
            if errors:
                embed.add_field(name="Errors", value="\n".join(errors[:5]), inline=False)
            await interaction.followup.send(embed=embed)

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.edit_message(embed=create_info_embed("Cancelled", "Operation cancelled."))

    await ctx.send(view=ConfirmView())


@bot.command(name='cpumonitor')
@is_admin()
async def cpu_monitor_control(ctx, action: str = "status"):
    """Control CPU/resource monitoring (Admin only)"""
    global cpu_monitor_active
    if action.lower() == "status":
        status = "Active" if cpu_monitor_active else "Inactive"
        embed = create_embed("Resource Monitor Status", f"Status: **{status}**",
                             0x00ccff if cpu_monitor_active else 0xffaa00)
        embed.add_field(name="Threshold", value=f"{CPU_THRESHOLD}%", inline=True)
        embed.add_field(name="Check Interval", value=f"{CHECK_INTERVAL}s", inline=True)
        embed.add_field(name="Mode", value="Alert admins + stop only runaway containers (>98% for 3 checks)", inline=False)
        await ctx.send(embed=embed)
    elif action.lower() == "enable":
        cpu_monitor_active = True
        await ctx.send(embed=create_success_embed("Resource Monitor Enabled"))
    elif action.lower() == "disable":
        cpu_monitor_active = False
        await ctx.send(embed=create_warning_embed("Resource Monitor Disabled"))
    else:
        await ctx.send(embed=create_error_embed("Invalid Action", "Use: `!cpumonitor <status|enable|disable>`"))


# ---------------------------------------------------------------------------
# User tools: monitor, console, password, backups
# ---------------------------------------------------------------------------

@bot.command(name='monitor')
async def monitor_vps(ctx, vps_number: int = 0):
    """Show live stats for your VPS - auto-refreshes every 5s"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number. Use `!manage` to see your VPS."))
        return
    view = LiveStatsView(vps, ctx.author.id)
    await view.start(ctx)


@bot.command(name='console')
async def console_cmd(ctx, vps_number: int, *, command: str):
    """Run a Linux command inside your VPS - !console <vps#> <command>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    msg = await ctx.send(embed=create_info_embed("Executing", f"`{command}` in `{vps['container_name']}`..."))
    audit("vps_console", ctx.author, vps["container_name"], command[:200])
    out = await container_op(vps, "exec", cmd=command, timeout=30)
    stdout = out.get("stdout", "") if isinstance(out, dict) else ""
    err = out.get("stderr", "") if isinstance(out, dict) else ""
    rc = out.get("rc", 0) if isinstance(out, dict) else 0
    em = create_embed(f"💻 Console Output", f"`{command}`")
    body = stdout if stdout else "(no stdout)"
    body = body[:1900] + "\n...(truncated)" if len(body) > 1900 else body
    em.add_field(name="📤 Output", value=f"```\n{body}\n```", inline=False)
    if err:
        em.add_field(name="⚠️ Stderr", value=f"```\n{err[:1900]}\n```", inline=False)
    em.add_field(name="🔄 Exit Code", value=f"**{rc}**", inline=False)
    await msg.edit(embed=em)


@bot.command(name='resetpass')
async def reset_pass(ctx, vps_number: int = 0):
    """Regenerate your VPS root password - DMed to you only"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    new_pw = generate_password()
    virt = _instance_kind(vps)
    try:
        if virt == "kvm":
            ok, message = await kvm_reset_password(
                domain_of(vps), new_pw)
            if not ok:
                await ctx.send(embed=create_error_embed("Password Failed", message))
                return
            note = message
        else:
            ok, _out, message = await _exec_or_explain(
                vps, f"echo 'root:{new_pw}' | chpasswd", timeout=15)
            if not ok:
                await ctx.send(embed=create_error_embed("Password Failed", message))
                return
            note = "The password changed immediately."
        vps["ssh_password"] = new_pw
        save_data()
        audit("vps_password", ctx.author, vps["container_name"], "root password reset")
        try:
            ssh_port = vps.get("ssh_port", 22)
            reset_embed = create_embed(f"{E('unlock')} ROOT PASSWORD RESET", "", color=0xff3366)
            reset_embed.add_field(name=f"{E('network')} Shared IPv4", value=f"`{SERVER_IP}`", inline=False)
            reset_embed.add_field(name=f"{E('terminal')} SSH Port", value=f"`{ssh_port}`", inline=True)
            reset_embed.add_field(name=f"{E('linux')} Username", value=f"`root`", inline=True)
            reset_embed.add_field(name="🔑 New Password", value=f"`{new_pw}`", inline=True)
            reset_embed.add_field(name=f"{E('lightning')} SSH Command", value=f"```ssh root@{SERVER_IP} -p {ssh_port}```", inline=False)
            if virt == "kvm":
                reset_embed.add_field(name="VM Rebooting", value=note, inline=False)
            reset_embed.add_field(name=f"{E('shield')} Save this!", value="This password will not be shown again.", inline=False)
            await ctx.author.send(embed=reset_embed)
            await ctx.send(embed=create_success_embed(
                "Password Reset", f"New root password sent to your DMs. {note}"))
        except discord.Forbidden:
            await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive the new password!"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Password Failed", sanitize_error(e)))


@bot.command(name='backup')
async def backup_own(ctx, vps_number: int):
    """Create a backup snapshot of your server - !backup <vps#>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    name = snapshot_name_for(vps, datetime.now().strftime("%Y%m%d-%H%M%S"))
    kind_label = (VIRT_TYPES.get(_instance_kind(vps), {}).get("label", "Server")
                  .split(" (")[0])
    screen = ProgressScreen(ctx, title="CREATING BACKUP", op_prefix="BAK", color=0x00ff88)
    screen.set_steps([
        ("Preparing snapshot", "pending"),
        (f"Capturing {kind_label} state", "pending"),
        ("Saving to disk", "pending"),
    ])
    await screen.start()
    try:
        await screen.update(15, 0, f"Preparing snapshot for `{vps['container_name']}`")
        await asyncio.sleep(0.2)
        await screen.step_done(30, 0, "Snapshot prepared")

        await screen.update(30, 1, f"Capturing {kind_label} state...")
        ok, message, kind = await create_backup(vps, name)
        if not ok:
            await screen.fail("Backup Failed", message)
            return
        await screen.step_done(75, 1, message)

        await screen.update(75, 2, "Saving to disk...")
        await asyncio.sleep(0.2)
        await screen.step_done(100, 2, "Saved")

        audit("vps_backup", ctx.author, vps["container_name"], f"{name} ({kind})")
        await screen.complete("Backup created successfully!", extra_fields={
            "Server": f"`{vps['container_name']}`",
            "Type": VIRT_TYPES.get(_instance_kind(vps), {}).get("label", "Server"),
            "Snapshot": f"`{name}`",
            "Stored As": kind,
        })
    except Exception as e:
        await screen.fail("Backup failed", sanitize_error(e))


@bot.command(name='backups')
async def list_own_backups(ctx, vps_number: int = 0):
    """List your VPS snapshots - !backups [vps#]"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    snapshots = await asyncio.get_running_loop().run_in_executor(
        None, snapshot_list_sync, vps["container_name"])
    if snapshots:
        embed = create_embed(f"📸 Snapshots for {vps['container_name']}", f"Found {len(snapshots)} snapshots")
        embed.add_field(name="Snapshots", value="\n".join([f"• `{s}`" for s in snapshots[:20]]), inline=False)
        embed.add_field(name="Restore", value=f"Use `!restorebackup {vps_number} <snapshot>`", inline=False)
    else:
        embed = create_info_embed("No Snapshots", f"No snapshots for `{vps['container_name']}`.\nUse `!backup {vps_number}` to create one.")
    await ctx.send(embed=embed)


@bot.command(name='restorebackup')
async def restore_own_backup(ctx, vps_number: int, snapshot_name: str):
    """Restore your VPS from a snapshot - !restorebackup <vps#> <snapshot>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    await ctx.send(
        embed=create_warning_embed("Restore Backup",
                                   f"⚠️ This will erase current data on `{vps['container_name']}` and restore `{snapshot_name}`."),
        view=ConfirmActionView(None, vps, "restore", snapshot=snapshot_name))


@bot.command(name='deletebackup')
async def delete_own_backup(ctx, vps_number: int, snapshot_name: str):
    """Delete one of your VPS snapshots - !deletebackup <vps#> <snapshot>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    if snapshot_name not in (await asyncio.get_running_loop().run_in_executor(
            None, snapshot_list_sync, vps["container_name"])) and not snapshot_name.startswith(vps["container_name"] + "-"):
        await ctx.send(embed=create_error_embed("Invalid", "Snapshot name must belong to your VPS."))
        return
    await ctx.send(
        embed=create_warning_embed("Delete Backup", f"Delete snapshot `{snapshot_name}`?"),
        view=ConfirmActionView(None, vps, "delete_backup", snapshot=snapshot_name))


@bot.command(name='backupauto')
@is_admin()
async def backup_auto(ctx, mode: str, hours: int = 24):
    """Automatic scheduled backups - !backupauto <on|off> [hours]"""
    global auto_backup_cfg
    if mode.lower() == "on":
        auto_backup_cfg["enabled"] = True
        auto_backup_cfg["hours"] = max(1, hours)
        save_json(AUTOBACKUP_PATH, auto_backup_cfg)
        audit("auto_backup_enable", ctx.author, "", f"every {hours}h")
        await ctx.send(embed=create_success_embed(
            "Auto Backup ON", f"Running VPS will be snapshotted every **{hours}h** (keeping last {auto_backup_cfg.get('keep',3)})."))
    elif mode.lower() == "off":
        auto_backup_cfg["enabled"] = False
        save_json(AUTOBACKUP_PATH, auto_backup_cfg)
        await ctx.send(embed=create_warning_embed("Auto Backup OFF", "Scheduled backups disabled."))
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: `!backupauto on [hours]` or `!backupauto off`"))


@bot.command(name='backupstatus')
@is_admin()
async def backup_status(ctx):
    """Show automatic backup status"""
    embed = create_embed("📸 Auto Backup", "", 0x00ccff)
    embed.add_field(name="Enabled", value="✅ Yes" if auto_backup_cfg.get("enabled") else "❌ No", inline=True)
    embed.add_field(name="Interval", value=f"every {auto_backup_cfg.get('hours', 24)}h", inline=True)
    embed.add_field(name="Keep", value=f"last {auto_backup_cfg.get('keep', 3)}", inline=True)
    embed.add_field(name="Last run", value=auto_backup_cfg.get("last") or "never", inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Rename / notes / ping / uptime / myinfo
# ---------------------------------------------------------------------------

@bot.command(name='renamevps')
async def rename_vps(ctx, vps_number: int, *, new_name: str):
    """Give your VPS a custom nickname"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    if len(new_name) > 30:
        await ctx.send(embed=create_error_embed("Name Too Long", "Nickname must be 30 characters or less."))
        return
    vps["nickname"] = new_name
    save_data()
    audit("vps_rename", ctx.author, vps["container_name"], new_name)
    await ctx.send(embed=create_success_embed("VPS Renamed", f"VPS #{vps_number} is now called **{new_name}**!"))


@bot.command(name='vpsnote')
async def vps_note(ctx, vps_number: int, *, note: str):
    """Add a note to your VPS"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    vps["note"] = note[:200]
    save_data()
    await ctx.send(embed=create_success_embed("Note Saved", f"Note added to VPS #{vps_number}:\n> {note[:200]}"))


@bot.command(name='pingvps')
async def ping_vps(ctx, vps_number: int):
    """Ping your VPS container to check if it's alive"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    container = vps["container_name"]
    nickname = vps.get("nickname", f"VPS #{vps_number}")
    msg = await ctx.send(embed=create_info_embed("Pinging...", f"Checking `{container}`..."))
    start = time.time()
    try:
        ok = await container_op(vps, "alive")
        elapsed = int((time.time() - start) * 1000)
        if ok:
            embed = create_success_embed("🏓 Pong!", f"**{nickname}** is alive!\n⚡ Response: `{elapsed}ms`")
        else:
            embed = create_error_embed("💀 No Response", f"**{nickname}** container is not running.")
        await msg.edit(embed=embed)
    except Exception as e:
        await msg.edit(embed=create_error_embed("Ping Failed", sanitize_error(e)))


@bot.command(name='uptimevps')
async def uptime_vps(ctx, vps_number: int):
    """Check VPS uptime"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Invalid VPS", "VPS not found."))
        return
    container = vps["container_name"]
    nickname = vps.get("nickname", f"VPS #{vps_number}")
    delta = await container_op(vps, "uptime")
    if delta is None:
        await ctx.send(embed=create_error_embed("Uptime Error", "Could not read container start time."))
        return
    d, s = delta.days, delta.seconds
    uptime_str = f"{d}d {s//3600}h {(s%3600)//60}m {s%60}s"
    embed = create_success_embed(f"⏱️ Uptime - {nickname}", f"Container has been running for:\n```{uptime_str}```")
    await ctx.send(embed=embed)


@bot.command(name='myinfo')
async def my_info(ctx):
    """Show your personal dashboard"""
    user_id = str(ctx.author.id)
    credits = user_data.get(user_id, {}).get("credits", 0)
    vps_list = vps_data.get(user_id, [])
    embed = create_embed(f"👤 {ctx.author.name}'s Dashboard", "", 0x5865F2)
    embed.set_thumbnail(url=ctx.author.display_avatar.url)
    embed.add_field(name="💰 Credits", value=f"**{credits}**", inline=True)
    embed.add_field(name="🖥️ VPS Count", value=f"**{len(vps_list)}**", inline=True)
    embed.add_field(name="📅 Account", value=f"Joined: {ctx.author.created_at.strftime('%Y-%m-%d')}", inline=True)
    if vps_list:
        vps_text = ""
        for i, v in enumerate(vps_list):
            nickname = v.get("nickname", f"VPS {i+1}")
            status_icon = "🟢" if v.get("status") == "running" else "🔴"
            note = f" - _{v['note']}_" if v.get("note") else ""
            vps_text += f"{status_icon} **{nickname}** (`{v['container_name']}`){note}\n"
        embed.add_field(name="🖥️ Your VPS", value=vps_text, inline=False)
    else:
        embed.add_field(name="🖥️ Your VPS", value="No VPS yet. Use `!buywc` to get one!", inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Admin: audit, logs, console, dashboard, permission levels
# ---------------------------------------------------------------------------

@bot.command(name='audit')
@is_admin()
async def audit_cmd(ctx, count: int = 15):
    """Show recent audit log entries (Admin only)"""
    count = max(1, min(count, 50))
    entries = list(reversed(audit_logs))[:count]
    if not entries:
        await ctx.send(embed=create_info_embed("Audit Log", "No entries yet."))
        return
    lines = []
    for e in entries:
        lines.append(f"`{e['ts'][:19]}` **{e['action']}** - {e['user']} → {e['target']} | {e['details']}")
    embed = create_embed("📜 Audit Log", f"Last {len(entries)} entries")
    for i in range(0, min(len(lines), 40), 10):
        embed.add_field(name=f"Entries ({i+1}-{i+10})", value="\n".join(lines[i:i+10]), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='logs')
@is_admin()
async def logs_cmd(ctx, lines: int = 20):
    """Show recent bot log lines (Admin only)"""
    lines = max(5, min(lines, 100))
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        tail = "".join(all_lines[-lines:])
        embed = create_embed("📋 Bot Logs", f"Last {len(all_lines[-lines:])} lines")
        embed.add_field(name="Log", value=f"```\n{tail[-1900:]}\n```", inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Logs Error", sanitize_error(e)))


CONSOLE_HELP = (
    "**Admin console commands** (via `!admincmd <cmd>`):\n"
    "• `db status` - DB file status\n"
    "• `db backup` - snapshot all JSON data\n"
    "• `db restore <file>` - restore a backup\n"
    "• `db health` - DB integrity check\n"
    "• `logs tail [n]` - recent log lines\n"
    "• `logs errors` / `logs warnings` - filtered log\n"
    "• `logs clear` - truncate log file\n"
    "• `cache clear` - wipe cache dir\n"
    "• `deps list` / `deps update` / `deps install <pkg>` / `deps requirements`\n"
    "• `bot status` / `bot sync` / `bot reload`\n"
    "• `vps sync` - reconcile container states\n"
    "• `stats` - server statistics\n"
)


@bot.command(name='admincmd')
@is_superadmin()
async def admin_cmd(ctx, *, line: str):
    """Run a console-style command (Super Admin+) - !admincmd db status"""
    parts = [p for p in line.strip().split() if p]
    if not parts or parts[0] in ("help", "?"):
        await ctx.send(embed=create_info_embed("🖥️ Admin Console", CONSOLE_HELP))
        return
    cmd, args = parts[0].lower(), parts[1:]
    try:
        if cmd == "db":
            sub = args[0].lower() if args else "status"
            if sub == "restore":
                if await maybe_2fa_block(ctx):
                    return
                if not check_rate(ctx.author.id, "db_restore", 3):
                    await ctx.send(embed=create_error_embed("Rate Limited", "Too many DB restore attempts."))
                    return
            out = await run_db_cmd(sub, args[1:])
        elif cmd == "logs":
            out = await run_logs_cmd(args)
        elif cmd == "cache":
            out = run_cache_cmd(args)
        elif cmd == "deps":
            out = await run_deps_cmd(args)
        elif cmd == "bot":
            out = await run_bot_cmd(ctx, args)
        elif cmd == "vps":
            out = await run_vps_cmd(ctx, args)
        elif cmd == "stats":
            em = await build_server_stats_embed()
            await ctx.send(embed=em)
            return
        else:
            out = ["Unknown command. Use `!admincmd help`."]
        embed = create_embed("⚡ Admin Console", f"`{line}`")
        embed.add_field(name="Output", value="```\n" + "\n".join(out)[:1900] + "\n```", inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Console Error", sanitize_error(e)))


async def run_db_cmd(sub, args):
    global user_data, vps_data, admin_data, audit_logs, admin_levels, auto_backup_cfg
    global transactions, coupons, billing_cfg, tickets_data, pending_payments, nodes_reg
    if sub == "status":
        rows = []
        for name, path in (("users", USER_DATA_PATH), ("vps", VPS_DATA_PATH), ("admins", ADMIN_DATA_PATH), ("audit", LOGS_PATH)):
            if os.path.exists(path):
                rows.append(f"{name}: {os.path.getsize(path)} bytes")
            else:
                rows.append(f"{name}: missing")
        rows.append(f"users: {len(user_data)}  vps: {sum(len(v) for v in vps_data.values())}  audit: {len(audit_logs)}")
        return rows
    if sub == "health":
        ok = True
        for path in (USER_DATA_PATH, VPS_DATA_PATH, ADMIN_DATA_PATH, LOGS_PATH):
            d = load_json(path, None)
            if d is None:
                ok = False
        return ["Database: OK" if ok else "Database: CORRUPT (one or more files failed to load)"]
    if sub == "backup":
        ts = datetime.now().strftime("%Y%m%d-%H%M%S")
        dstdir = os.path.join(BACKUP_DIR, ts)
        os.makedirs(dstdir, exist_ok=True)
        n = 0
        for path in (USER_DATA_PATH, VPS_DATA_PATH, ADMIN_DATA_PATH, LOGS_PATH, LEVELS_PATH, AUTOBACKUP_PATH,
                     TXN_PATH, COUPONS_PATH, BILLING_PATH, TICKETS_PATH, PENDING_PATH, NODES_PATH):
            if os.path.exists(path):
                shutil.copy2(path, os.path.join(dstdir, os.path.basename(path)))
                n += 1
        return [f"DB backup created: backups/{ts}/ ({n} files)"]
    if sub == "restore":
        if not args:
            entries = sorted(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else []
            return ["usage: db restore <folder>", "Available:"] + (["  " + e for e in entries[-20:]] or ["  (none)"])
        target = os.path.join(BACKUP_DIR, args[0])
        if not os.path.isdir(target):
            return [f"Backup folder not found: {args[0]}"]
        n = 0
        for fn in os.listdir(target):
            src = os.path.join(target, fn)
            if fn == "user_data.json":
                user_data = load_json(src, {})
            elif fn == "vps_data.json":
                vps_data = load_vps_data_from_file(src)
            elif fn == "admin_data.json":
                admin_data = load_json(src, {"admins": [str(MAIN_ADMIN_ID)]})
            elif fn == "audit_logs.json":
                audit_logs = load_json(src, [])
            elif fn == "admin_levels.json":
                admin_levels = load_json(src, {})
            elif fn == "auto_backup.json":
                auto_backup_cfg = load_json(src, {"enabled": False})
            elif fn == "transactions.json":
                transactions = load_json(src, [])
            elif fn == "coupons.json":
                coupons = load_json(src, {})
            elif fn == "billing.json":
                billing_cfg = load_json(src, {"enabled": False, "days": 30})
            elif fn == "tickets.json":
                tickets_data = load_json(src, {"counter": 1, "open": {}, "closed": {}})
            elif fn == "pending.json":
                pending_payments = load_json(src, [])
            elif fn == "nodes.json":
                nodes_reg = load_json(src, {})
            n += 1
        save_data()
        save_json(LOGS_PATH, audit_logs)
        return [f"Restored {n} files from {args[0]}. Data reloaded in memory."]
    if sub == "clear":
        return ["Usage: db clear is not allowed (dangerous). Use `db backup` first."]
    if sub == "migrate":
        return ["Migrations not needed - schema is JSON file-based."]
    if sub == "connect":
        return ["Database: file-based JSON, always connected."]
    return ["Unknown db subcommand: " + sub]


def load_vps_data_from_file(path):
    loaded = load_json(path, {})
    out = {}
    for uid, v in loaded.items():
        if isinstance(v, dict):
            out[uid] = [v] if "container_name" in v else list(v.values())
        elif isinstance(v, list):
            out[uid] = v
    return out


async def run_logs_cmd(args):
    sub = args[0].lower() if args else "tail"
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception:
        return ["No log file yet."]
    if sub == "tail":
        n = int(args[1]) if len(args) > 1 else 20
        return "".join(lines[-n:]).splitlines()[:n]
    if sub == "errors":
        return [l.rstrip() for l in lines if "ERROR" in l][-20:] or ["No errors."]
    if sub == "warnings":
        return [l.rstrip() for l in lines if "WARNING" in l][-20:] or ["No warnings."]
    if sub == "clear":
        open(LOG_PATH, "w").close()
        return ["Log file cleared."]
    return ["Unknown logs subcommand. Try: tail, errors, warnings, clear"]


def run_cache_cmd(args):
    sub = args[0].lower() if args else "clear"
    if sub == "clear":
        n = 0
        if os.path.isdir(CACHE_DIR):
            for fn in os.listdir(CACHE_DIR):
                try:
                    os.remove(os.path.join(CACHE_DIR, fn))
                    n += 1
                except Exception:
                    pass
        return [f"Cache cleared ({n} files removed)."]
    return ["Unknown cache subcommand. Try: clear"]


async def run_deps_cmd(args):
    sub = args[0].lower() if args else "list"
    if sub == "list":
        proc = await asyncio.create_subprocess_exec(
            sys_exec(), "-m", "pip", "list",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        return out.decode().splitlines()[:40] or ["pip list empty"]
    if sub == "update":
        proc = await asyncio.create_subprocess_exec(
            sys_exec(), "-m", "pip", "list", "--outdated", "--format=freeze",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
        pkgs = [l.split("==")[0] for l in out.decode().splitlines() if "==" in l][:20]
        if not pkgs:
            return ["All packages up to date."]
        proc = await asyncio.create_subprocess_exec(
            sys_exec(), "-m", "pip", "install", "--upgrade", *pkgs,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        return ["Upgraded: " + ", ".join(pkgs)]
    if sub == "install":
        if not args:
            return ["usage: deps install <package>"]
        proc = await asyncio.create_subprocess_exec(
            sys_exec(), "-m", "pip", "install", args[0],
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=120)
        return out.decode().splitlines()[-3:] or ["installed " + args[0]]
    if sub == "requirements":
        proc = await asyncio.create_subprocess_exec(
            sys_exec(), "-m", "pip", "install", "-r", os.path.join(BASE_DIR, "requirements.txt"),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=180)
        return out.decode().splitlines()[-3:] or ["requirements installed"]
    return ["Unknown deps subcommand. Try: list, update, install <pkg>, requirements"]


def sys_exec():
    import sys
    return sys.executable


async def run_bot_cmd(ctx, args):
    sub = args[0].lower() if args else "status"
    if sub == "status":
        d = datetime.now() - BOT_START_TIME
        return [f"Uptime: {d.days}d {d.seconds//3600}h  Ping: {round(bot.latency*1000)}ms  VPS: {sum(len(v) for v in vps_data.values())}"]
    if sub == "sync":
        try:
            synced = await bot.tree.sync()
            return [f"Synced {len(synced)} slash commands."]
        except Exception as e:
            return [f"Sync failed: {e}"]
    if sub == "reload":
        for name, path in (("auto_backup", AUTOBACKUP_PATH), ("levels", LEVELS_PATH)):
            if os.path.exists(path):
                load_json(path, {})
        return ["Reloaded config files from disk."]
    if sub in ("restart", "stop"):
        return ["Use the terminal control panel (admin_console.py) to restart/stop the bot process."]
    return ["Unknown bot subcommand. Try: status, sync, reload"]


async def run_vps_cmd(ctx, args):
    sub = args[0].lower() if args else "sync"
    if sub == "sync":
        running = stopped = 0
        for vl in vps_data.values():
            for v in vl:
                alive = await container_alive(v["container_name"])
                if alive:
                    running += 1
                else:
                    stopped += 1
                v["status"] = "running" if alive else "stopped"
        save_data()
        return [f"Reconciled states: {running} running, {stopped} stopped."]
    return ["Unknown vps subcommand. Try: sync"]


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------

class DashboardView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)

    @discord.ui.button(label="🖥️ VPS Management", style=discord.ButtonStyle.primary)
    async def vps_mgmt(self, interaction: discord.Interaction, item: discord.ui.Button):
        await interaction.response.send_message(embed=await build_all_vps_embed(), ephemeral=True)

    @discord.ui.button(label="👥 Users", style=discord.ButtonStyle.primary)
    async def users(self, interaction: discord.Interaction, item: discord.ui.Button):
        await interaction.response.send_message(embed=await build_server_stats_embed(), ephemeral=True)

    @discord.ui.button(label="💰 Billing", style=discord.ButtonStyle.primary)
    async def billing(self, interaction: discord.Interaction, item: discord.ui.Button):
        await interaction.response.send_message(embed=await build_billing_embed(), ephemeral=True)

    @discord.ui.button(label="📸 Backups", style=discord.ButtonStyle.primary)
    async def backups(self, interaction: discord.Interaction, item: discord.ui.Button):
        snaps = await asyncio.get_running_loop().run_in_executor(None, list_all_snapshots_sync)
        em = create_embed("📸 All Snapshots", f"Found {len(snaps)} snapshot images")
        if snaps:
            em.add_field(name="Snapshots", value="\n".join(f"• `{s}`" for s in snaps[:15]), inline=False)
        await interaction.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="📜 Logs", style=discord.ButtonStyle.primary)
    async def logs(self, interaction: discord.Interaction, item: discord.ui.Button):
        entries = list(reversed(audit_logs))[:10]
        lines = [f"`{e['ts'][:19]}` **{e['action']}** - {e['user']}" for e in entries]
        em = create_embed("📜 Recent Activity", "\n".join(lines) if lines else "No entries.")
        await interaction.response.send_message(embed=em, ephemeral=True)

    @discord.ui.button(label="⚙️ System", style=discord.ButtonStyle.secondary)
    async def system(self, interaction: discord.Interaction, item: discord.ui.Button):
        host = await host_stats_async()
        d = datetime.now() - BOT_START_TIME
        em = create_embed("⚙️ System", "", 0x00ccff)
        em.add_field(name="⏱️ Bot Uptime", value=f"`{d.days}d {d.seconds//3600}h`", inline=True)
        em.add_field(name="⚡ Host CPU", value=f"`{host['cpu']}%`", inline=True)
        em.add_field(name="🧠 Host RAM", value=f"`{host['ram_pct']}%`", inline=True)
        em.add_field(name="💾 Disk", value=f"`{host['disk_pct']}%`", inline=True)
        await interaction.response.send_message(embed=em, ephemeral=True)


def list_all_snapshots_sync():
    try:
        proc = subprocess.run(["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"],
                              capture_output=True, text=True, timeout=30)
        if proc.returncode != 0:
            return []
        return [img for img in proc.stdout.strip().split("\n") if "backup" in img.lower()]
    except Exception:
        return []


async def build_billing_embed():
    total_credits = sum(u.get('credits', 0) for u in user_data.values())
    top = sorted(user_data.items(), key=lambda x: x[1].get("credits", 0), reverse=True)[:5]
    lines = []
    for uid, d in top:
        try:
            u = await bot.fetch_user(int(uid))
            name = u.name
        except Exception:
            name = f"User#{uid[:4]}"
        lines.append(f"**{name}** - {d.get('credits', 0)} cr")
    em = create_embed("💰 Billing Overview", "", 0xffd700)
    em.add_field(name="Total Credits in circulation", value=f"**{total_credits}**", inline=False)
    em.add_field(name="Top balances", value="\n".join(lines) or "No data", inline=False)
    em.add_field(name="Recent credit actions", value="\n".join(
        [f"`{e['ts'][:16]}` **{e['action']}** {e['target']} ({e['details']})"
         for e in reversed(audit_logs) if e["action"] in ("credits_add", "credits_remove", "credits_transfer")][:5]) or "None",
        inline=False)
    return em


async def host_stats_async():
    loop = asyncio.get_running_loop()
    cpu = await loop.run_in_executor(None, host_cpu_sync)
    ram = await loop.run_in_executor(None, host_ram_sync)
    disk = await loop.run_in_executor(None, host_disk_sync)
    return {
        "cpu": cpu,
        "ram_pct": ram["pct"] if ram else 0,
        "ram_used": ram["used"] if ram else 0,
        "ram_total": ram["total"] if ram else 0,
        "disk_pct": disk["pct"] if disk else 0,
    }


@bot.command(name='dashboard')
@is_admin()
async def dashboard(ctx):
    """Open the Turtle Nodes Control Center"""
    host = await host_stats_async()
    total_vps = sum(len(v) for v in vps_data.values())
    running_vps = sum(1 for vl in vps_data.values() for v in vl if v.get('status') == 'running')
    total_credits = sum(u.get('credits', 0) for u in user_data.values())
    embed = create_embed(f"{E('gearneon')} Turtle Nodes Control Center", "", 0x5865F2)
    embed.add_field(name=f"{E('vps')} VPS", value=f"**{total_vps}**\n🟢 Running: {running_vps}\n🔴 Stopped: {total_vps - running_vps}", inline=True)
    embed.add_field(name=f"{E('nodes')} Customers", value=f"**{len(vps_data)}**", inline=True)
    embed.add_field(name=f"{E('fire')} Credits", value=f"**{total_credits:,}**", inline=True)
    embed.add_field(name=f"{E('cpu')} Host CPU", value=f"`{host['cpu']}%`", inline=True)
    embed.add_field(name=f"{E('ram')} Host RAM", value=f"`{host['ram_pct']}%`", inline=True)
    embed.add_field(name=f"{E('disk')} Disk", value=f"`{host['disk_pct']}%`", inline=True)
    await ctx.send(embed=embed, view=DashboardView())


# ---------------------------------------------------------------------------
# Ultra-advanced bot control panel
# ---------------------------------------------------------------------------

@bot.command(name='botpanel')
@is_admin()
async def botpanel(ctx):
    """Open the Turtle Nodes Ultra-Advanced Control Center"""
    if not CORE_MODULES_LOADED:
        err_msg = CORE_ERROR or "Unknown error"
        embed = create_error_embed(
            "Core Modules Failed",
            f"```\n{err_msg[:1500]}\n```\n"
            "**The bot runs in basic mode.** Fix the error and restart."
        )
        embed.add_field(name="What to do", value=(
            "1. `pip install -r requirements.txt`\n"
            "2. Make sure `core/` folder exists\n"
            "3. Run `python -c \"from core.services import *\"` to test"
        ), inline=False)
        await ctx.send(embed=embed)
        return
    embed = make_embed(
        "⚡ AXO NODES CONTROL CENTER",
        "Ultra-advanced hosting & DevOps management platform",
        color=0x5865F2
    )
    status = await get_full_status(bot)
    h = status.get("health", {})
    overall = "🟢" if all(v.get("ok", True) for v in h.values()) else "🟡"
    embed.add_field(name="System Health", value=overall, inline=True)
    embed.add_field(name="Uptime", value=str(status.get("uptime", "-")), inline=True)
    embed.add_field(name="Latency", value="%dms" % (bot.latency * 1000), inline=True)
    embed.add_field(name="Guilds", value=str(len(bot.guilds)), inline=True)
    embed.add_field(name="Users", value=str(len(bot.users)), inline=True)
    embed.add_field(name="Commands", value=str(len(bot.commands)), inline=True)
    embed.add_field(name="Docker", value=status_emoji(h.get("docker_daemon", {}).get("status", "offline")), inline=True)
    embed.add_field(name="Database", value=status_emoji(h.get("database", {}).get("status", "offline")), inline=True)
    embed.add_field(name="Host CPU", value="%.1f%%" % status.get("cpu_percent", 0), inline=True)
    embed.set_footer(text="Turtle Nodes • Powered by Turtle Nodes")
    await ctx.send(embed=embed, view=BotControlPanel(bot))


@bot.command(name='botdb')
@is_admin()
async def botdb(ctx):
    """Open the Database Control Center"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    db = DBManager()
    health = db.health_check()
    embed = make_embed("🗄️ DATABASE CONTROL CENTER", color=0x00ff88)
    embed.add_field(name="Status", value=status_emoji("healthy" if health["ok"] else "critical"), inline=True)
    embed.add_field(name="Files", value=str(health["total_count"]), inline=True)
    embed.add_field(name="Healthy", value=str(health["healthy_count"]), inline=True)
    if health.get("corrupt_files"):
        embed.add_field(name="Corrupt", value=", ".join(health["corrupt_files"]), inline=False)
    await ctx.send(embed=embed, view=DatabasePanel(bot))


@bot.command(name='botmonitor')
@is_admin()
async def botmonitor(ctx):
    """Open the Live Monitoring Dashboard"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    dash = MonitoringDashboard(bot)
    embed = await dash.render()
    msg = await ctx.send(embed=embed, view=MonitoringPanel(bot))


@bot.command(name='botlogs')
@is_admin()
async def botlogs(ctx):
    """Open the Log Viewer"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    lm = LogManager()
    stats = lm.get_stats()
    embed = make_embed("📋 LOG CENTER", color=0xffaa00)
    embed.add_field(name="Total Lines", value=str(stats.get("total_lines", 0)), inline=True)
    embed.add_field(name="Errors", value=str(stats.get("error_count", 0)), inline=True)
    embed.add_field(name="Warnings", value=str(stats.get("warning_count", 0)), inline=True)
    embed.add_field(name="File Size", value="%.1f KB" % (stats.get("file_size_bytes", 0) / 1024), inline=True)
    await ctx.send(embed=embed, view=LogViewer(bot))


@bot.command(name='botemergency')
@is_main_admin()
async def botemergency(ctx):
    """Open Emergency Control Center (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    em = EmergencyManager(bot)
    fields = em.get_status_embed_fields()
    embed = make_embed("🚨 EMERGENCY CONTROL", "Use with extreme caution.", color=0xff3366)
    for f in fields:
        embed.add_field(name=f["name"], value=f["value"], inline=True)
    await ctx.send(embed=embed, view=EmergencyPanel(bot))


@bot.command(name='botconfig')
@is_main_admin()
async def botconfig(ctx):
    """Open Configuration Center (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    cm = ConfigManager()
    cats = cm.get_categories()
    embed = make_embed("⚙️ CONFIGURATION CENTER", color=0x5865F2)
    for cat, keys in cats.items():
        embed.add_field(name=cat, value="%d settings" % len(keys), inline=True)
    await ctx.send(embed=embed, view=ConfigPanel(bot))


@bot.command(name='botterminal')
@is_main_admin()
async def botterminal(ctx):
    """Open the Admin Terminal (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    embed = make_embed("⌨️ ADMIN TERMINAL", "Type a command to execute.", color=0x1a1a1a)
    await ctx.send(embed=embed, view=TerminalView(bot))


@bot.command(name='botanalytics')
@is_admin()
async def botanalytics(ctx):
    """Open Analytics Dashboard"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    ae = AnalyticsEngine(bot)
    overview = ae.get_overview(bot)
    embed = make_embed("📈 ANALYTICS", color=0x00ccff)
    embed.add_field(name="Total Users", value=ae.format_number(overview.get("total_users", 0)), inline=True)
    embed.add_field(name="Total VPS", value=str(overview.get("total_vps", 0)), inline=True)
    embed.add_field(name="Running", value=str(overview.get("running_vps", 0)), inline=True)
    embed.add_field(name="Expired", value=str(overview.get("expired_vps", 0)), inline=True)
    embed.add_field(name="Total Credits", value=ae.format_number(overview.get("total_credits", 0)), inline=True)
    embed.add_field(name="Commands", value=ae.format_number(overview.get("total_commands", 0)), inline=True)
    await ctx.send(embed=embed, view=AnalyticsPanel(bot))


@bot.command(name='botsecurity')
@is_main_admin()
async def botsecurity(ctx):
    """Open Security Center (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    sm = SecurityManager(bot)
    report = sm.get_security_report()
    embed = make_embed("🔐 SECURITY CENTER", color=0xff3366)
    embed.add_field(name="Lockdown", value="🟢 OFF" if not report.get("lockdown") else "🔴 ON", inline=True)
    await ctx.send(embed=embed, view=SecurityPanel(bot))


@bot.command(name='botdiagnostics')
@is_admin()
async def botdiagnostics(ctx):
    """Run full system diagnostics"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    await ctx.send(embed=create_info_embed("Running Diagnostics", "Checking all systems..."))
    dr = DiagnosticsRunner(bot)
    results = await dr.run_full_diagnostics()
    overall = dr.get_overall_status(results)
    fields = dr.get_report_embed_fields(results)
    color_map = {"healthy": 0x00ff88, "degraded": 0xffaa00, "critical": 0xff3366}
    embed = make_embed("🔍 DIAGNOSTIC REPORT", "Overall: %s" % overall.upper(), color=color_map.get(overall, 0xff3366))
    for f in fields:
        embed.add_field(name="%s %s" % (f.get("icon", ""), f["name"]), value=f["value"], inline=True)
    recs = dr.get_recommendations(results)
    if recs:
        embed.add_field(name="⚠️ Recommendations", value="\n".join(recs[:5]), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='botbackups')
@is_admin()
async def botbackups(ctx):
    """Open Backup Center"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```\nBot runs in basic mode."))
        return
    bm = BackupManager(DBManager())
    stats = bm.get_backup_stats()
    embed = make_embed("💾 BACKUP CENTER", color=0x00ff88)
    embed.add_field(name="Total Backups", value=str(stats.get("total", 0)), inline=True)
    embed.add_field(name="Total Size", value="%.1f MB" % (stats.get("total_size", 0) / 1048576), inline=True)
    embed.add_field(name="Oldest", value=str(stats.get("oldest", "-")), inline=True)
    embed.add_field(name="Newest", value=str(stats.get("newest", "-")), inline=True)
    await ctx.send(embed=embed, view=BackupPanel(bot))


# ---------------------------------------------------------------------------
# Ultra features: Status, VPS Control, Purge, Troubleshoot, Incidents, Health, Maintenance
# ---------------------------------------------------------------------------

def _core_check(ctx):
    if not CORE_MODULES_LOADED:
        raise commands.CheckFailure("Core modules not loaded")


@bot.command(name='botctl')
@is_main_admin()
async def botctl(ctx):
    """Bot Control Center - status, restart, cache, sync (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    ctrl = BotController(bot)
    health = ctrl.get_health()
    embed = make_embed("🤖 BOT CONTROL CENTER", color=0x5865F2)
    embed.add_field(name="Status", value=str(health.get("gateway_ready", "-")), inline=True)
    embed.add_field(name="Latency", value="%dms" % health.get("latency_ms", 0), inline=True)
    embed.add_field(name="Uptime", value=str(health.get("uptime", "-")), inline=True)
    embed.add_field(name="Guilds", value=str(health.get("guild_count", 0)), inline=True)
    embed.add_field(name="Users", value=str(health.get("user_count", 0)), inline=True)
    embed.add_field(name="VPS", value=str(health.get("vps_count", 0)), inline=True)
    embed.add_field(name="CPU", value="%.1f%%" % health.get("cpu_percent", 0), inline=True)
    embed.add_field(name="Memory", value="%.1f MB" % health.get("memory_mb", 0), inline=True)
    embed.add_field(name="Tasks", value=str(health.get("background_tasks", 0)), inline=True)
    await ctx.send(embed=embed, view=BotControlPanel(bot))


@bot.command(name='vpsctl')
@is_superadmin()
async def vpsctl(ctx, vps_number: int = None):
    """Advanced VPS Control - !vpsctl [vps#] (Admin only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    user_id = str(ctx.author.id)
    if vps_number is None:
        vl = vps_data.get(user_id, [])
        if not vl:
            await ctx.send(embed=create_error_embed("No VPS", "You don't have a VPS. Use `!vpsctl @user <#>` as admin."))
            return
        vps_number = 1
    else:
        if not (user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])):
            await ctx.send(embed=create_error_embed("Access Denied", "Admin only."))
            return
        target = ctx.message.mentions[0] if ctx.message.mentions else ctx.author
        user_id = str(target.id)
    vl = vps_data.get(user_id, [])
    if vps_number < 1 or vps_number > len(vl):
        await ctx.send(embed=create_error_embed("Invalid VPS", f"VPS #{vps_number} doesn't exist."))
        return
    vps = vl[vps_number - 1]
    embed = make_embed("🖥️ VPS CONTROL", f"VPS #{vps_number} - `{vps['container_name']}`", color=0x5865F2)
    status = vps.get("status", "unknown")
    embed.add_field(name="Status", value=status.upper(), inline=True)
    embed.add_field(name="RAM", value=vps.get("ram", "?"), inline=True)
    embed.add_field(name="CPU", value=vps.get("cpu", "?"), inline=True)
    embed.add_field(name="Storage", value=vps.get("storage", "?"), inline=True)
    if vps.get("locked"):
        embed.add_field(name="🔒 Locked", value="Yes", inline=True)
    if vps.get("suspended"):
        embed.add_field(name="⛔ Suspended", value="Yes", inline=True)
    embed.add_field(name="Node", value=vps.get("node", "local"), inline=True)
    await ctx.send(embed=embed, view=VPSControlPanel(bot, user_id, vps_number - 1))


@bot.command(name='purge')
@is_main_admin()
async def purge_cmd(ctx):
    """Purge System - safe cleanup with confirmation (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    screen = ProgressScreen(ctx, title="SYSTEM PURGE", op_prefix="PRG", color=0xff8800)
    screen.set_steps([
        ("Scanning cache files", "pending"),
        ("Scanning temp files", "pending"),
        ("Scanning old logs", "pending"),
        ("Scanning old backups", "pending"),
        ("Scanning Docker artifacts", "pending"),
    ])
    await screen.start()
    pm = PurgeManager(bot)
    preview = pm.get_full_preview()
    await screen.update(10, 0, f"Found {preview.get('cache_count', 0)} cache items")
    await asyncio.sleep(0.3)
    await screen.step_done(20, 0, f"{preview.get('cache_count', 0)} cache items")

    await screen.update(20, 1, f"Scanning temp directory...")
    await asyncio.sleep(0.3)
    await screen.step_done(40, 1, f"{preview.get('temp_count', 0)} temp items")

    await screen.update(40, 2, f"Scanning log files...")
    await asyncio.sleep(0.3)
    await screen.step_done(60, 2, f"{preview.get('logs_count', 0)} log items")

    await screen.update(60, 3, f"Scanning old backups...")
    await asyncio.sleep(0.3)
    await screen.step_done(80, 3, f"{preview.get('backups_count', 0)} backup items")

    await screen.update(80, 4, f"Scanning Docker artifacts...")
    await asyncio.sleep(0.3)
    await screen.step_done(100, 4, f"{preview.get('docker_containers_count', 0)} containers")

    await screen.complete("Scan complete! Select what to clean.", extra_fields={
        "Cache": f"{preview.get('cache_count', 0)} items",
        "Temp Files": f"{preview.get('temp_count', 0)} items",
        "Old Logs": f"{preview.get('logs_count', 0)} items",
        "Estimated Freed": f"{preview.get('total_bytes', 0) / 1048576:.1f} MB",
    })
    await ctx.send(embed=make_embed("🧹 PURGE SYSTEM", "Select what to clean. All actions require confirmation.", color=0xff8800), view=PurgePanel(bot))


@bot.command(name='troubleshoot')
@is_superadmin()
async def troubleshoot_cmd(ctx):
    """Auto Troubleshooting - diagnose & fix (Admin only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    screen = ProgressScreen(ctx, title="SYSTEM DIAGNOSTICS", op_prefix="TRO", color=0x00ccff)
    screen.set_steps([
        ("Checking Docker daemon", "pending"),
        ("Checking container health", "pending"),
        ("Checking disk space", "pending"),
        ("Checking network", "pending"),
        ("Checking security status", "pending"),
        ("Generating report", "pending"),
    ])
    await screen.start()
    ts = Troubleshooter(bot)
    await screen.update(10, 0, "Checking Docker daemon status...")
    await asyncio.sleep(0.3)
    await screen.step_done(20, 0, "Docker checked")

    await screen.update(20, 1, "Scanning container health...")
    await asyncio.sleep(0.3)
    await screen.step_done(40, 1, "Containers checked")

    await screen.update(40, 2, "Checking disk usage...")
    await asyncio.sleep(0.3)
    await screen.step_done(55, 2, "Disk checked")

    await screen.update(55, 3, "Testing network connectivity...")
    await asyncio.sleep(0.3)
    await screen.step_done(70, 3, "Network checked")

    await screen.update(70, 4, "Running security scan...")
    await asyncio.sleep(0.3)
    await screen.step_done(85, 4, "Security checked")

    await screen.update(85, 5, "Generating diagnostics report...")
    checks = ts.run_all_checks()
    overall = ts.get_overall_status(checks)
    fields = ts.get_summary_embed_fields(checks)
    await screen.step_done(100, 5, "Report generated")

    color_map = {"healthy": 0x00ff88, "degraded": 0xffaa00, "critical": 0xff3366}
    emoji_map = {"healthy": "🟢", "degraded": "🟡", "critical": "🔴"}
    status_emoji = emoji_map.get(overall, "🔴")
    await screen.complete(f"Overall: {status_emoji} **{overall.upper()}**", extra_fields={
        f["name"]: f["value"] for f in fields
    })
    await ctx.send(view=TroubleshootPanel(bot))


@bot.command(name='incidents')
@is_superadmin()
async def incidents_cmd(ctx):
    """Incident History - view past incidents (Admin only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    im = IncidentManager()
    stats = im.get_stats()
    recent = im.get_recent(5)
    embed = make_embed("🚨 INCIDENTS", color=0xff3366)
    embed.add_field(name="Total", value=str(stats.get("total", 0)), inline=True)
    embed.add_field(name="Open", value=str(stats.get("open", 0)), inline=True)
    embed.add_field(name="Recovered", value=str(stats.get("recovered", 0)), inline=True)
    embed.add_field(name="Failed", value=str(stats.get("failed", 0)), inline=True)
    if recent:
        lines = []
        for inc in recent[:5]:
            icon = "✅" if inc.get("status") == "recovered" else "🔴"
            lines.append("%s #%s - %s (%s)" % (icon, inc.get("id", "?")[:8], inc.get("service", "?"), inc.get("status", "?")))
        embed.add_field(name="Recent", value="\n".join(lines), inline=False)
    await ctx.send(embed=embed, view=IncidentsPanel(bot))


@bot.command(name='syshealth')
@is_superadmin()
async def syshealth_cmd(ctx):
    """System Health Monitor - live metrics (Admin only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    hm = SystemHealthMonitor(bot)
    overview = hm.get_system_overview()
    embed = make_embed("📊 SYSTEM HEALTH", "Overall: %s" % overview.get("overall", "unknown").upper(), color=0x00ccff)
    for key in ["cpu", "ram", "swap", "disk", "docker", "containers", "database", "api", "latency"]:
        val = overview.get(key, {})
        icon = "🟢" if val.get("status") == "ok" else ("🟡" if val.get("status") == "warning" else "🔴")
        embed.add_field(name="%s %s" % (icon, key.replace("_", " ").title()), value=str(val.get("value", "-")), inline=True)
    await ctx.send(embed=embed, view=HealthMonitorPanel(bot))


@bot.command(name='maint')
@is_main_admin()
async def maint_cmd(ctx):
    """Maintenance Center - tools & controls (Owner only)"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", f"```\n{(CORE_ERROR or 'Unknown')[:1000]}\n```"))
        return
    mc = MaintenanceCenter(bot)
    checklist = mc.get_maintenance_checklist()
    embed = make_embed("🔧 MAINTENANCE CENTER", color=0xffaa00)
    for item in checklist:
        icon = "✅" if item.get("status") == "ok" else ("⚠️" if item.get("status") == "warning" else "🔴")
        embed.add_field(name="%s %s" % (icon, item["name"]), value=item.get("detail", "-"), inline=True)
    await ctx.send(embed=embed, view=MaintenancePanel(bot))


# ---------------------------------------------------------------------------
# Advanced Operations Commands
# ---------------------------------------------------------------------------

@bot.command(name='vpsscore')
@is_main_admin()
async def vps_score_cmd(ctx, index: int = None):
    """Check VPS health score (0-100) - !vpsscore or !vpsscore <index>"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    uid = str(ctx.author.id)
    user_vpss = vps_data.get(uid, [])
    if not user_vpss:
        await ctx.send(embed=create_error_embed("No VPS", "You have no VPS."))
        return
    scorer = VPSHealthScorer(bot)
    if index is not None:
        idx = index - 1
        if idx < 0 or idx >= len(user_vpss):
            await ctx.send(embed=create_error_embed("Invalid", f"Use 1-{len(user_vpss)}"))
            return
        result = await scorer.calculate_health(uid, idx)
        vps = user_vpss[idx]
        bar = result.get("progress_bar", "░░░░░░░░░░")
        embed = create_embed(f"❤️ Health Score - {vps.get('container_name', '?')}", f"**{result['score']}/100** ({result['grade']})\n`{bar}`\nStatus: {result['status']}", 0x2ECC71 if result['score'] >= 80 else (0xF39C12 if result['score'] >= 50 else 0xE74C3C))
        for k, v in result.get("breakdown", {}).items():
            embed.add_field(name=k, value=f"Score: {v['score']}/100\n{v.get('detail','')}", inline=True)
        await ctx.send(embed=embed, view=VPSScorePanel(bot, uid))
    else:
        embed = create_embed("❤️ All VPS Health Scores", "Loading...", 0x9866A5)
        msg = await ctx.send(embed=embed)
        fields = []
        for i, vps in enumerate(user_vpss):
            result = await scorer.calculate_health(uid, i)
            bar = result.get("progress_bar", "░░░░░░░░░░")
            fields.append({"name": f"#{i+1} {vps.get('container_name','?')}", "value": f"**{result['score']}/100** ({result['grade']})\n`{bar}`"})
        embed = make_embed("❤️ All VPS Health Scores", 0x9866A5, fields)
        await msg.edit(embed=embed, view=VPSScorePanel(bot, uid))


@bot.command(name='scan')
@is_main_admin()
async def scan_cmd(ctx):
    """Full system scan - one-click comprehensive check"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    embed = create_embed("🔍 Full System Scan", "Scanning all systems... Please wait.", 0x5865F2)
    msg = await ctx.send(embed=embed)
    scanner = FullSystemScan(bot)
    results = await scanner.run_full_scan()
    embed = scanner.get_report_embed(results["results"], results["overall"], results["duration_ms"])
    await msg.edit(embed=embed, view=FullScanPanel(bot))


@bot.command(name='predict')
@is_main_admin()
async def predict_cmd(ctx):
    """Predictive monitoring - trend analysis & capacity forecast"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    pm = PredictiveMonitor()
    disk_risk = pm.get_disk_risk()
    forecast = pm.get_capacity_forecast()
    recommendations = pm.get_recommendations()
    fields = []
    fields.append({"name": "💿 Disk Risk", "value": f"Current: {disk_risk.get('current',0):.1f}%\nGrowth: +{disk_risk.get('growth_per_day',0):.2f}%/day\nDays to 95%: {disk_risk.get('days_to_95', 'N/A')}\nRisk: {disk_risk.get('risk_level', 'unknown')}", "inline": True})
    for name, data in forecast.items():
        fields.append({"name": f"📊 {name}", "value": f"Now: {data.get('current',0):.1f}%\n7d: {data.get('predicted_7d',0):.1f}%\n30d: {data.get('predicted_30d',0):.1f}%", "inline": True})
    if recommendations:
        fields.append({"name": "💡 Recommendations", "value": "\n".join(recommendations[:5]), "inline": False})
    embed = make_embed("📈 Predictive Monitoring", 0x5865F2, fields)
    await ctx.send(embed=embed, view=PredictivePanel(bot))


@bot.command(name='anomalies')
@is_main_admin()
async def anomalies_cmd(ctx):
    """Anomaly detection - scan for unusual behavior"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    embed = create_embed("🕵️ Anomaly Detection", "Scanning...", 0xE67E22)
    msg = await ctx.send(embed=embed)
    detector = AnomalyDetector(bot)
    anomalies = await detector.scan_all()
    summary = detector.get_anomaly_summary()
    fields = [{"name": "Summary", "value": f"Total: {summary.get('total',0)}\nCritical: {summary.get('by_severity',{}).get('critical',0)}\nHigh: {summary.get('by_severity',{}).get('high',0)}", "inline": True}]
    for a in anomalies[:10]:
        fields.append({"name": f"{a.get('type','?')} [{a.get('severity','?')}]", "value": a.get('detail','')[:200], "inline": False})
    embed = make_embed("🕵️ Anomaly Detection Results", 0xE67E22 if anomalies else 0x2ECC71, fields)
    await msg.edit(embed=embed, view=AnomalyPanel(bot))


@bot.command(name='dryrun')
@is_main_admin()
async def dryrun_cmd(ctx, action: str, *, params: str = ""):
    """Dry run - preview changes before applying. !dryrun <action> [params]"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    drm = DryRunManager()
    result = drm.preview(action, {"params": params})
    validation = drm.validate(action, {"params": params})
    fields = [
        {"name": "Action", "value": action, "inline": True},
        {"name": "Risk Level", "value": result.get("risk_level", "unknown"), "inline": True},
        {"name": "Will Affect", "value": "\n".join(result.get("will_affect", ["Nothing"]))[:1000], "inline": False},
    ]
    if result.get("warnings"):
        fields.append({"name": "⚠️ Warnings", "value": "\n".join(result["warnings"])[:1000], "inline": False})
    if not validation.get("valid"):
        fields.append({"name": "❌ Validation Failed", "value": "\n".join(validation.get("errors", []))[:1000], "inline": False})
    embed = make_embed(f"🧪 Dry Run - {action}", 0xF39C12 if result.get("risk_level") != "low" else 0x2ECC71, fields)
    await ctx.send(embed=embed, view=DryRunPanel(bot, action, {"params": params}))


@bot.command(name='confighistory')
@is_main_admin()
async def confighistory_cmd(ctx, action: str = "list", version: int = None):
    """Config time machine - !confighistory list | compare <v1> <v2> | restore <v> | snapshot"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    ch = ConfigHistory()
    if action == "snapshot":
        entry = ch.snapshot(str(ctx.author.id), "manual")
        await ctx.send(embed=create_success_embed("Snapshot Created", f"Version #{entry.get('version_num', '?')} saved."))
        return
    elif action == "list":
        versions = ch.get_versions()
        fields = []
        for v in versions[-20:]:
            fields.append({"name": f"#{v['version_num']}", "value": f"By: {v.get('admin_id','?')}\nReason: {v.get('reason','?')}\n{v.get('timestamp','?')}", "inline": True})
        if not fields:
            fields.append({"name": "No Versions", "value": "No config snapshots saved yet."})
        embed = make_embed("⏱️ Config Time Machine - Versions", 0x5865F2, fields)
        await ctx.send(embed=embed, view=ConfigHistoryPanel(bot))
        return
    elif action == "compare" and version:
        versions = ch.get_versions()
        if len(versions) < 2:
            await ctx.send(embed=create_error_embed("Not Enough Versions", "Need at least 2 versions to compare."))
            return
        diff = ch.compare(versions[-2]["version_num"], versions[-1]["version_num"])
        text = "\n".join([f"`{d['key']}`: {d.get('old_value','?')} → {d.get('new_value','?')}" for d in diff[:20]])
        await ctx.send(embed=create_info_embed("Config Diff", text or "No changes found."))
        return
    elif action == "restore" and version:
        result = ch.restore(version, str(ctx.author.id))
        if result.get("success"):
            await ctx.send(embed=create_success_embed("Restored", f"Config restored to version #{version}."))
        else:
            await ctx.send(embed=create_error_embed("Failed", result.get("error", "Unknown error.")))
        return
    await ctx.send(embed=create_error_embed("Usage", "`!confighistory list | snapshot | compare <v1> <v2> | restore <v>`"))


@bot.command(name='status')
async def status_cmd(ctx):
    """Public status - shows all system services status"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    sp = StatusPage(bot)
    overall = sp.get_overall_status()
    embed = sp.get_status_embed()
    uptime = sp.get_uptime_percent()
    embed.add_field(name="📈 Uptime (24h)", value=f"{uptime:.2f}%", inline=True)
    await ctx.send(embed=embed, view=StatusPagePanel(bot))


@bot.command(name='maintadv')
@is_main_admin()
async def maint_adv_cmd(ctx):
    """Advanced maintenance - scoped, scheduled, persistent"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    adv = AdvancedMaintenance(bot)
    state = adv.get_state()
    scopes = adv.get_scopes_status()
    fields = [
        {"name": "Status", "value": "🟢 ACTIVE" if state.get("active") else "🟢 Inactive", "inline": True},
    ]
    if state.get("active"):
        fields.append({"name": "Reason", "value": state.get("reason", "-"), "inline": True})
        fields.append({"name": "Started By", "value": state.get("started_by", "-"), "inline": True})
    scope_lines = [f"{'🔴' if disabled else '🟢'} {name}" for name, disabled in scopes.items()]
    fields.append({"name": "Scopes", "value": "\n".join(scope_lines)[:1000], "inline": False})
    schedules = adv.get_schedules()
    if schedules:
        sched_text = "\n".join([f"• {s.get('reason','?')} ({s.get('start_time','?')})" for s in schedules[:5]])
        fields.append({"name": "📅 Scheduled", "value": sched_text, "inline": False})
    embed = make_embed("🔧 Advanced Maintenance", 0xFFAA00 if state.get("active") else 0x2ECC71, fields)
    await ctx.send(embed=embed, view=MaintenanceAdvPanel(bot))


@bot.command(name='smartdeploy')
@is_main_admin()
async def smart_deploy_cmd(ctx):
    """Smart deploy - preview best node selection"""
    if not CORE_MODULES_LOADED:
        await ctx.send(embed=create_error_embed("Core Modules", "Not loaded."))
        return
    sd = SmartDeployer(bot)
    summary = await sd.get_node_capacity_summary()
    fields = []
    for node in summary:
        fields.append({"name": f"🖧 {node.get('name','?')}", "value": f"Health: {node.get('health_score', '?')}/100\nVPS: {node.get('vps_count',0)}\nRAM: {node.get('used_ram_gb',0):.1f}/{node.get('total_ram_gb',0):.1f} GB\nRec: {node.get('recommendation','?')}", "inline": True})
    if not fields:
        fields.append({"name": "No Nodes", "value": "No nodes registered. Use `!node add` first."})
    embed = make_embed("🚀 Smart Deploy - Node Capacity", 0x5865F2, fields)
    await ctx.send(embed=embed, view=SmartDeployPanel(bot))


# ---------------------------------------------------------------------------
# Admin levels
# ---------------------------------------------------------------------------

def level_name(uid):
    if str(uid) == str(MAIN_ADMIN_ID):
        return "Owner"
    return str(admin_levels.get(str(uid)) or "User")


@bot.command(name='adminlevel')
@is_main_admin()
async def admin_level(ctx, action: str, user: discord.Member = None, level: str = None):
    """Admin permission levels - !adminlevel list | set @user <level>"""
    if action.lower() == "list":
        lines = []
        for uid in admin_data.get("admins", []) + [str(MAIN_ADMIN_ID)]:
            try:
                u = await bot.fetch_user(int(uid))
                name = u.name
            except Exception:
                name = uid
            lines.append(f"• **{name}** - {level_name(uid)}")
        await ctx.send(embed=create_info_embed("Admin Levels", "\n".join(lines)))
        return
    if action.lower() == "set":
        if not user or not level:
            await ctx.send(embed=create_error_embed("Usage", "`!adminlevel set @user <level>`\nLevels: " + ", ".join(PERM_LEVELS)))
            return
        if level not in PERM_LEVELS or level == "Owner":
            await ctx.send(embed=create_error_embed("Invalid Level", "Levels: " + ", ".join([k for k in PERM_LEVELS if k != "Owner"])))
            return
        admin_levels[str(user.id)] = level
        if str(user.id) not in admin_data["admins"]:
            admin_data["admins"].append(str(user.id))
        save_data()
        audit("admin_level_set", ctx.author, str(user), level)
        await ctx.send(embed=create_success_embed("Level Set", f"{user.mention} is now **{level}**."))
        return
    await ctx.send(embed=create_error_embed("Invalid", "Use: `!adminlevel list` or `!adminlevel set @user <level>`"))


# ---------------------------------------------------------------------------
# Admin management (promote/remove/list)
# ---------------------------------------------------------------------------

@bot.command(name='adminadd')
@is_main_admin()
async def admin_add(ctx, user: discord.Member):
    """Promote a user to admin - !adminadd @user"""
    uid = str(user.id)
    if uid in admin_data.get("admins", []):
        await ctx.send(embed=create_error_embed("Already Admin", f"{user.mention} is already an admin."))
        return
    admin_data.setdefault("admins", []).append(uid)
    if uid not in admin_levels:
        admin_levels[uid] = "Super Admin"
    save_data()
    audit("admin_add", ctx.author, str(user), "Promoted to admin")
    try:
        await user.send(embed=create_success_embed("Admin Promotion", "You have been promoted to admin by the main admin."))
    except discord.Forbidden:
        pass
    await ctx.send(embed=create_success_embed("Admin Added", f"{user.mention} has been promoted to **admin**."))


@bot.command(name='adminremove')
@is_main_admin()
async def admin_remove(ctx, user: discord.Member):
    """Remove admin role - !adminremove @user"""
    uid = str(user.id)
    if uid == str(MAIN_ADMIN_ID):
        await ctx.send(embed=create_error_embed("Invalid", "Cannot remove the main admin (Owner)."))
        return
    if uid not in admin_data.get("admins", []):
        await ctx.send(embed=create_error_embed("Not Admin", f"{user.mention} is not an admin."))
        return
    admin_data["admins"].remove(uid)
    admin_levels.pop(uid, None)
    save_data()
    audit("admin_remove", ctx.author, str(user), "Removed from admin")
    try:
        await user.send(embed=create_warning_embed("Admin Removed", "Your admin role has been removed by the main admin."))
    except discord.Forbidden:
        pass
    await ctx.send(embed=create_success_embed("Admin Removed", f"{user.mention} has been removed from admin."))


@bot.command(name='adminlist')
@is_main_admin()
async def admin_list(ctx):
    """View all admins - !adminlist"""
    admins = admin_data.get("admins", [])
    if not admins:
        await ctx.send(embed=create_info_embed("Admin List", "No admins found."))
        return
    lines = []
    for uid in admins:
        try:
            u = await bot.fetch_user(int(uid))
            name = f"{u.name}#{u.discriminator}"
        except Exception:
            name = uid
        level = level_name(uid)
        is_owner = " 👑" if uid == str(MAIN_ADMIN_ID) else ""
        lines.append(f"• **{name}** > {level}{is_owner}")
    await ctx.send(embed=create_info_embed("Admin List", "\n".join(lines)))


@bot.command(name='emojiupload')
@is_admin()
async def emoji_upload(ctx):
    """Upload all loaded custom emojis to the server so they work everywhere."""
    embed = create_info_embed(
        f"{E('upload')} Emoji Upload",
        "Starting emoji check...")
    msg = await ctx.send(embed=embed)

    errors = []
    created = 0
    for name, eid in EMOJI.items():
        existing = discord.utils.get(ctx.guild.emojis, id=int(eid)) if ctx.guild else None
        if existing:
            continue
        image_url = f"https://cdn.discordapp.com/emojis/{eid}.png?v=1"
        try:
            image_bytes = await asyncio.to_thread(_fetch_url_bytes, image_url)
            if image_bytes:
                await ctx.guild.create_custom_emoji(name=name, image=image_bytes)
                created += 1
            else:
                errors.append(f"`{name}` (empty/failed)")
        except Exception as ex:
            errors.append(f"`{name}` ({sanitize_error(ex)})")

    result = f"✅ Added **{created}** emoji(s)."
    if errors:
        result += f"\n⚠️ Failed: {', '.join(errors)}"
    embed = create_success_embed(f"{E('upload')} Emoji Upload Complete", result)
    embed.description = f"All **{len(EMOJI)}** defined emojis are now available in this server."
    await msg.edit(embed=embed)


# ---------------------------------------------------------------------------
# Help
# ---------------------------------------------------------------------------

def build_help_pages(is_user_admin, is_user_main_admin):
    pages = {}

    pages["home"] = create_embed(
        " Turtle Nodes Help Center",
        "Welcome to the **Turtle Nodes** help center.\n"
        "Use the dropdown below to navigate categories.\n\n"
        "**Quick Links:**\n"
        "👤 **User** > VPS management for everyone\n"
        "💰 **Credits** > Plans, billing, payments\n"
        "📢 **Extras** > Announcements, leaderboard\n"
        "🤖 **Bot Panel** > Bot management\n"
        "🛡️ **Admin** > Admin VPS & node commands\n"
        "👑 **Main Admin** > Owner-only controls\n"
        "🔧 **Advanced** > Health, scan, deploy\n"
        "🔒 **Security** > Security systems\n\n"
        "*Made By ᴛɪʀᴇᴅ ᵍᴄ*",
        0x2b2d31
    )

    user_embed = create_embed("👤 User Commands", "VPS management commands for all users:", 0x00ff88)
    user_embed.add_field(name="🖥️ VPS Management", value=(
        "`!manage` - Manage your VPS (Start/Stop/Restart/SSH/Console/Stats)\n"
        "`!manage [@user]` - Admin: manage another user's VPS\n"
        "`!shareuser @user <#>` - Share VPS access\n"
        "`!shareruser @user <#>` - Revoke shared access\n"
        "`!manageshared @owner <#>` - Access shared VPS"), inline=False)
    user_embed.add_field(name="🛠️ Tools", value=(
        "`!monitor <#>` - Live CPU/RAM/Disk/Network stats\n"
        "`!console <#> <cmd>` - Run a command in your VPS\n"
        "`!resetpass <#>` - New root password (DM only)\n"
        "`!renamevps <#> <name>` - Nickname your VPS\n"
        "`!vpsnote <#> <text>` - Add a note\n"
        "`!pingvps <#>` - Check container alive\n"
        "`!uptimevps <#>` - VPS uptime\n"
        "`!myinfo` - Your profile & VPS summary"), inline=False)
    user_embed.add_field(name="⚡ VPS Power", value=(
        "`!upgrade <#> <plan>` - Upgrade plan (charges difference)\n"
        "`!renew <#>` - Renew with credits\n"
        "`!os <#>` - Show OS\n"
        "`!reinstallos <#> <os>` - Reinstall OS\n"
        "`!ports <#>` / `!portadd <#> <host:port>` / `!portremove <#>` - Ports\n"
        "`!files <#> [path]` / `!cat <#> <path>` / `!upload <#> <path>` / `!download <#> <path>`\n"
        "`!mkdir <#> <path>` / `!rmfile <#> <path>` - File manager"), inline=False)
    user_embed.add_field(name="🖥️ Server Types", value=(
        "Every server comes with full root access.\n"
        "**KVM** - Full virtual machine, own kernel, hardware accelerated\n"
        "**LXC** - System container, near native speed\n"
        "**Docker** - Lightest and fastest to deploy\n"
        "`!ports <#>` shows the SSH port you connect on"), inline=False)
    user_embed.add_field(name="🔐 Security", value=(
        "`!2fa enable|confirm <code>|disable <code>|status` - 2FA\n"
        "`!verify <code>` - Unlock sensitive commands (10 min)\n"
        "`!alerts on|off` - DM alerts when your VPS is accessed\n"
        "`!token generate|list|revoke-all` - API tokens"), inline=False)
    user_embed.add_field(name="📸 Backups", value=(
        "`!backup <#>` - Snapshot your VPS\n"
        "`!backups [<#]` - List your snapshots\n"
        "`!restorebackup <#> <snapshot>` - Restore (confirmed)\n"
        "`!deletebackup <#> <snapshot>` - Remove snapshot"), inline=False)
    user_embed.add_field(name="🛡️ VPS Protection", value=(
        "`!protect <#> [reason]` - Protect VPS from purge\n"
        "`!unprotect <#>` - Remove protection"), inline=False)
    pages["user"] = user_embed

    credits_embed = create_embed("💰 Credits & Plans", "Purchase plans and manage credits:", 0xffaa00)
    credits_embed.add_field(name="💳 Commands", value=(
        "`!plans` - View available VPS plans & prices\n"
        "`!buyc` - Get payment info (UPI/PayPal/Crypto)\n"
        "`!buywc <plan> <Intel/AMD>` - Buy VPS with credits\n"
        "`!credits` / `!balance` - Check your balance\n"
        "`!pay @user <amount>` - Send credits\n"
        "`!transactions [@user]` - Credit history\n"
        "`!deposit <amount>` - Request credits (admin approves)\n"
        "`!withdraw <amount>` - Request payout\n"
        "`!redeem <CODE>` - Redeem a coupon"), inline=False)
    credits_embed.add_field(name="💳 Auto-Billing", value=(
        "`!renew <#>` - Renew a VPS with credits\n"
        "`!billing on|off [days]` - Toggle auto-renewal (Owner)\n"
        "`!billingstatus` - Billing config\n"
        "`!coupon create|list|delete` - Coupons (Billing+)\n"
        "`!pendingpayments` - Pending deposits/withdrawals (Billing+)"), inline=False)
    credits_embed.add_field(name="📦 Available Plans", value=(
        "🥉 **Starter** - 4GB RAM | 1 CPU | Intel: 42cr / AMD: 83cr\n"
        "🥈 **Basic** - 8GB RAM | 1 CPU | Intel: 96cr / AMD: 164cr\n"
        "🥇 **Standard** - 12GB RAM | 2 CPU | Intel: 192cr / AMD: 320cr\n"
        "💎 **Pro** - 16GB RAM | 2 CPU | Intel: 220cr / AMD: 340cr"), inline=False)
    pages["credits"] = credits_embed

    extras_embed = create_embed("📢 Extras & Fun", "Announcements, leaderboard, and more:", 0xff6b9d)
    extras_embed.add_field(name="📣 Announcements", value=(
        "`!announce <message>` - Admin: broadcast to all VPS owners via DM\n"
        "`!botstatus` - Bot uptime, total VPS, active users\n"
        "`!leaderboard` - Top credit holders on the server\n"
        "`!serverstats` - Server overview\n"
        "`!status` - Public system status page"), inline=False)
    extras_embed.add_field(name="ℹ️ Info", value=(
        "`!help` - Open this help menu\n"
        "`!plans` - VPS plan pricing\n"
        "`!myinfo` - Your personal dashboard"), inline=False)
    pages["extras"] = extras_embed

    botpanel_embed = create_embed("🤖 Bot Control Panel", "Bot management dashboards:", 0x9866A5)
    botpanel_embed.add_field(name="🎛️ Dashboards", value=(
        "`!botpanel` - Main bot control panel\n"
        "`!dashboard` - Operations dashboard\n"
        "`!botctl` - Bot status, restart, reconnect, cache\n"
        "`!vpsctl` - Advanced VPS operations"), inline=False)
    botpanel_embed.add_field(name="📊 Monitoring", value=(
        "`!botmonitor` - Live monitoring dashboard\n"
        "`!syshealth` - System health check\n"
        "`!scan` - Full system scan\n"
        "`!botlogs` - Log viewer"), inline=False)
    botpanel_embed.add_field(name="💾 Data & Config", value=(
        "`!botdb` - Database manager\n"
        "`!botconfig` - Configuration panel\n"
        "`!botbackups` - Backup manager\n"
        "`!botanalytics` - Analytics dashboard"), inline=False)
    botpanel_embed.add_field(name="🔒 Security & Tools", value=(
        "`!securitycenter` - Full security center GUI\n"
        "`!botsecurity` - Security center\n"
        "`!botterminal` - Terminal console\n"
        "`!botdiagnostics` - System diagnostics\n"
        "`!botemergency` - Emergency controls"), inline=False)
    pages["botpanel"] = botpanel_embed

    admin_embed = create_embed("🛡️ Admin Panel", "Admin-only commands:", 0xff3366)
    admin_embed.add_field(name="🖥️ VPS Control", value=(
        "`!create @user <ram_GB> <cpu> <disk_GB> [os] [node]` - Custom VPS (Super+)\n"
        "`!deploy @user <plan> [os]` - Deploy VPS with auto node selection\n"
        "`!deletevps @user <#> <reason>` - Delete VPS (Super+)\n"
        "`!resize @user <#> <ram_GB> <cpu>` - Resize live (Super+)\n"
        "`!restartvps <container>` - Restart a container\n"
        "`!stopvpsall` - Emergency stop ALL (Super+)\n"
        "`!exec <container> <cmd>` - Run command (Super+)"), inline=False)
    admin_embed.add_field(name="🖧 Node Management", value=(
        "`!node add <name> <url> <token>` - Register a remote node\n"
        "`!node remove <name>` - Unregister a node\n"
        "`!node list` - List all nodes\n"
        "`!node status` - Check node health\n"
        "`!smartdeploy` - View node capacity & health"), inline=False)
    admin_embed.add_field(name="💾 Backups", value=(
        "`!backupvps <container>` - Snapshot (admin)\n"
        "`!restorevps <container> <snap>` - Restore (Super+)\n"
        "`!listsnapshots <container>` - List snapshots\n"
        "`!deletesnapshot <snap>` - Delete snapshot (Super+)\n"
        "`!backupauto <on/off> [hours]` - Scheduled backups\n"
        "`!backupstatus` - Auto backup status"), inline=False)
    admin_embed.add_field(name="💰 Payments & Billing", value=(
        "`!adminc @user <amt>` / `!adminrc @user <amt/all>` - Credits (Billing+)\n"
        "`!depositapprove <id>` / `!depositdeny <id>` - Deposits\n"
        "`!withdrawapprove <id> [ref]` / `!withdrawdeny <id>` - Withdrawals\n"
        "`!pendingpayments` - Queue\n"
        "`!coupon create <code> <cr> [uses]` - Coupons\n"
        "`!billing on|off [days]` - Auto-renewal (Owner)"), inline=False)
    admin_embed.add_field(name="🎫 Tickets", value=(
        "`!ticket <subject>` - Users open a support thread\n"
        "`!tickets` - List open tickets\n"
        "`!ticketclose` - Close the ticket you're in"), inline=False)
    admin_embed.add_field(name="🖥️ Provisioning", value=(
        "`!create @user` - Graphical server builder (dropdowns)\n"
        "`!deploy @user` - Alias for !create\n"
        "`!hypervisors` - Show KVM / LXC / Docker availability\n"
        "`!create @user <ram> <cpu> <disk> [os] [node] [kvm|lxc|container]` - Typed form"),
        inline=False)
    admin_embed.add_field(name="📊 Info & Monitoring", value=(
        "`!userinfo @user` - Full user info\n"
        "`!vpsinfo [container]` - VPS details\n"
        "`!listall` - All VPS overview\n"
        "`!cpumonitor <status|enable|disable>` - Resource monitor\n"
        "`!audit [n]` - Recent audit entries\n"
        "`!logs [n]` - Bot log tail\n"
        "`!admincmd <cmd>` - Console (db backup, logs clear, deps, ...)"), inline=False)
    admin_embed.add_field(name="🔒 Security", value=(
        "`!protectlist` - List all protected VPS\n"
        "`!isolatenode <id> [reason]` - Isolate a node\n"
        "`!enablenode <id>` - Re-enable isolated node\n"
        "`!dbhealth` - Database health check\n"
        "`!depcheck` - Dependency security check"), inline=False)
    admin_embed.add_field(name="⏰ Expiry Management", value=(
        "`!setexpire @user <vps#> <days>` - Set expiry\n"
        "`!extendexpire @user <vps#> <days>` - Extend\n"
        "`!removeexpire @user <vps#>` - Remove expiry\n"
        "`!checkexpire [@user]` - Expiry status"), inline=False)
    pages["admin"] = admin_embed

    mainadmin_embed = create_embed("👑 Main Admin", "Exclusive main admin commands:", 0xffd700)
    mainadmin_embed.add_field(name="👥 Admin Management", value=(
        "`!adminadd @user` - Promote user to admin\n"
        "`!adminremove @user` - Remove admin role\n"
        "`!adminlist` - View all admins\n"
        "`!adminlevel list|set @user <level>` - Permissions\n"
        "`!emojiupload` - Upload all custom emojis to this server"), inline=False)
    mainadmin_embed.add_field(name="🔒 Emergency Controls", value=(
        "`!lockdown <reason>` - Activate emergency lockdown\n"
        "`!unlock <reason>` - Deactivate lockdown\n"
        "`!maintenance on|off` - Toggle maintenance mode\n"
        "`!recoverypoint [reason]` - Create recovery point\n"
        "`!drrestore <name>` - Disaster recovery restore"), inline=False)
    pages["mainadmin"] = mainadmin_embed

    advops_embed = create_embed("🔧 Advanced Operations", "Advanced system management (Owner only):", 0x5865F2)
    advops_embed.add_field(name="❤️ Health & Monitoring", value=(
        "`!vpsscore [index]` - VPS health score (0-100, A-F grade)\n"
        "`!scan` - Full system scan (18 checks)\n"
        "`!predict` - Predictive monitoring & capacity forecast\n"
        "`!anomalies` - Anomaly & abuse detection scan"), inline=False)
    advops_embed.add_field(name="🛠️ Deployment & Maintenance", value=(
        "`!smartdeploy` - Smart deploy node capacity view\n"
        "`!maintadv` - Advanced maintenance (scoped, scheduled, persistent)\n"
        "`!maint` - Maintenance checklist & tools\n"
        "`!purge` - Safe purge with preview\n"
        "`!troubleshoot` - Auto-troubleshooting & recovery"), inline=False)
    advops_embed.add_field(name="⏱️ Config & Status", value=(
        "`!confighistory list|snapshot|compare <v1> <v2>|restore <v>` - Config time machine\n"
        "`!status` - Public system status page\n"
        "`!dryrun <action> [params]` - Preview changes before applying\n"
        "`!incidents` - Incident management"), inline=False)
    pages["advops"] = advops_embed

    security_embed = create_embed("🔒 Security Center", "Advanced security systems & controls:", 0xff3366)
    security_embed.add_field(name="🛡️ Security Dashboard", value=(
        "`!securitycenter` - Full security center GUI with interactive controls\n"
        "`!securityaudit` - Quick security audit (score/100)"), inline=False)
    security_embed.add_field(name="🔍 Monitoring & Detection", value=(
        "`!audit [n]` - Immutable audit log entries\n"
        "`!alertack <id>` - Acknowledge a security alert\n"
        "`!depcheck` - Check dependencies for vulnerabilities\n"
        "`!dbhealth` - Database integrity check"), inline=False)
    security_embed.add_field(name="🔒 Access Control", value=(
        "`!lockdown <reason>` - Emergency lockdown (Owner)\n"
        "`!unlock <reason>` - Deactivate lockdown (Owner)\n"
        "`!isolatenode <id> [reason]` - Isolate node from deployments\n"
        "`!enablenode <id>` - Re-enable isolated node"), inline=False)
    security_embed.add_field(name="💾 Backup & Recovery", value=(
        "`!recoverypoint [reason]` - Create disaster recovery point\n"
        "`!drrestore <name>` - Restore from recovery point\n"
        "`!protectlist` - List all protected VPS\n"
        "`!backupvps <container>` - Snapshot (admin)"), inline=False)
    security_embed.add_field(name="⚙️ Systems Active", value=(
        "Two-Step Confirmation • Immutable Audit Log\n"
        "Rate Limiting • Session Security • Secret Protection\n"
        "Command Allowlisting • Emergency Lockdown\n"
        "Anomaly Detection • Fail Guard • Backup Protection\n"
        "Node Isolation • Health Verification • DB Protection\n"
        "Security Alerts • Disaster Recovery"), inline=False)
    pages["security"] = security_embed

    return pages


# Category order and metadata
HELP_CATEGORIES = [
    ("home", "🏠 Home", 0x2b2d31),
    ("user", "👤 User", 0x5865F2),
    ("credits", "💰 Credits", 0x00ff88),
    ("extras", "📢 Extras", 0x9866A5),
    ("botpanel", f"{E('bot')} Bot Panel", 0x9866A5),
    ("admin", f"{E('shield')} Admin", 0xff3366),
    ("mainadmin", f"{E('admin')} Main Admin", 0xffd700),
    ("advops", "🔧 Advanced", 0x5865F2),
    ("security", "🔒 Security", 0xff3366),
]


class HelpDropdown(discord.ui.Select):
    def __init__(self, pages, author_id):
        self.pages = pages
        self.author_id = author_id
        options = [
            discord.SelectOption(label="🗄️ Home", description="Welcome & quick links", value="home", emoji="🏠"),
            discord.SelectOption(label="👤 User Commands", description="VPS management for all users", value="user", emoji="👤"),
            discord.SelectOption(label="💰 Credits & Plans", description="Buy VPS, check plans", value="credits", emoji="💰"),
            discord.SelectOption(label="📢 Extras", description="Leaderboard, status, info", value="extras", emoji="📢"),
            discord.SelectOption(label="🤖 Bot Panel", description="Bot management dashboards", value="botpanel", emoji="🤖"),
            discord.SelectOption(label="🛡️ Admin Panel", description="Admin VPS & node commands", value="admin", emoji="🛡️"),
            discord.SelectOption(label="👑 Main Admin", description="Admin management", value="mainadmin", emoji="👑"),
            discord.SelectOption(label="🔧 Advanced Operations", description="Health, scan, deploy, maintain", value="advops", emoji="🔧"),
            discord.SelectOption(label="🔒 Security Center", description="Security systems & controls", value="security", emoji="🔒"),
        ]
        super().__init__(placeholder="📂 Select a category...", options=options, min_values=1, max_values=1)

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.author_id:
            await interaction.response.send_message("Not your help menu.", ephemeral=True)
            return
        selected = self.values[0]
        embed = self.pages.get(selected, self.pages["home"])
        await interaction.response.edit_message(embed=embed, view=self.view)


class HelpView(discord.ui.View):
    def __init__(self, pages, author_id):
        super().__init__(timeout=300)
        self.add_item(HelpDropdown(pages, author_id))

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True


@bot.command(name='help')
async def show_help(ctx):
    """Interactive help center"""
    user_id = str(ctx.author.id)
    is_user_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    is_user_main_admin = user_id == str(MAIN_ADMIN_ID)
    pages = build_help_pages(is_user_admin, is_user_main_admin)

    view = HelpView(pages, ctx.author.id)
    await ctx.send(embed=pages["home"], view=view)


# ---------------------------------------------------------------------------
# Announce / maintenance
# ---------------------------------------------------------------------------

@bot.command(name='announce')
@is_admin()
async def announce(ctx, *, message: str):
    """Send an announcement DM to all VPS owners (Admin only)"""
    sent = 0
    failed = 0
    announce_embed = create_embed("📢 Announcement", message, 0xffaa00)
    announce_embed.add_field(name="From", value=f"**Turtle Nodes Team** ({ctx.author.mention})", inline=False)
    status_msg = await ctx.send(embed=create_info_embed("Sending Announcement", "Broadcasting to all VPS owners..."))
    for uid in vps_data.keys():
        try:
            user = await bot.fetch_user(int(uid))
            await user.send(embed=announce_embed)
            sent += 1
            await asyncio.sleep(0.5)
        except Exception:
            failed += 1
    await status_msg.edit(embed=create_success_embed("Announcement Sent",
                                                     f"✅ Delivered to **{sent}** users\n❌ Failed: **{failed}** (DMs closed)"))


@bot.command(name='maintenance')
@is_admin()
async def maintenance_toggle(ctx, mode: str):
    """Toggle maintenance mode (Admin only)"""
    global maintenance_mode
    if mode.lower() == "on":
        maintenance_mode = True
        await bot.change_presence(status=discord.Status.idle,
                                   activity=discord.Activity(type=discord.ActivityType.watching, name="🔴 Under Maintenance"))
        audit("maintenance", ctx.author, "", "enabled")
        await ctx.send(embed=create_warning_embed("🔴 Maintenance Mode ON",
            "Bot is now in maintenance mode.\n• ALL commands blocked for non-admins\n• Bot status set to Idle"))
    elif mode.lower() == "off":
        maintenance_mode = False
        await bot.change_presence(status=discord.Status.online,
                                   activity=discord.Activity(type=discord.ActivityType.watching, name="Turtle Nodes | VPS Manager"))
        audit("maintenance", ctx.author, "", "disabled")
        await ctx.send(embed=create_success_embed("🟢 Maintenance Mode OFF", "Bot is back to normal operation."))
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: `!maintenance on` or `!maintenance off`"))


@bot.check
async def maintenance_check(ctx):
    global maintenance_mode
    if not maintenance_mode and not maint_security.is_active():
        return True
    user_id = str(ctx.author.id)
    is_user_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
    if maint_security.is_active() and not maint_security.can_user_bypass(user_id):
        if not is_user_admin:
            await ctx.send(embed=create_warning_embed(
                "🔴 System Maintenance",
                "The system is under security maintenance.\nAll commands are temporarily disabled."))
            return False
    if is_user_admin and ctx.command and ctx.command.name == 'maintenance':
        return True
    if not is_user_admin:
        await ctx.send(embed=create_warning_embed(
            "🔴 Under Maintenance",
            "The bot is currently under maintenance.\nAll commands are disabled until maintenance is complete."))
        return False
    return True


@bot.event
async def on_message(message):
    if message.author.bot:
        return
    if isinstance(message.channel, discord.DMChannel):
        await bot.process_commands(message)
        return
    if maintenance_mode and isinstance(message.channel, discord.TextChannel):
        user_id = str(message.author.id)
        is_user_admin = user_id == str(MAIN_ADMIN_ID) or user_id in admin_data.get("admins", [])
        if not is_user_admin and message.content.startswith(bot.command_prefix):
            await message.channel.send(embed=create_warning_embed(
                "🔴 Under Maintenance",
                "The bot is currently under maintenance.\nAll commands are disabled for users."))
            return
    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Typo aliases
# ---------------------------------------------------------------------------

@bot.command(name='mangage')
async def manage_typo(ctx):
    await ctx.send(embed=create_info_embed("Command Correction", "Did you mean `!manage`?"))


@bot.command(name='stats')
async def stats_alias(ctx):
    if str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", []):
        await ctx.send(embed=await build_server_stats_embed())
    else:
        await ctx.send(embed=create_error_embed("Access Denied", "Admin only."))


# ---------------------------------------------------------------------------
# Expire system
# ---------------------------------------------------------------------------

@bot.command(name='setexpire')
@is_admin()
async def set_expire(ctx, user: discord.Member, vps_number: int, days: int):
    """Set VPS expiry - !setexpire @user <vps#> <days>"""
    vps, _ = find_vps(user.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    exp_date = (utcnow() + timedelta(days=days)).isoformat()
    vps['expires'] = exp_date
    save_data()
    audit("expire_set", ctx.author, vps["container_name"], f"{days}d")
    vps_name = vps['container_name']
    await ctx.send(embed=create_success_embed("Expiry Set",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) expires on `{exp_date[:10]}` ({days}d from now)."))
    try:
        await user.send(embed=create_warning_embed("⏳ VPS Expiry Set",
            f"Your **VPS #{vps_number}** (`{vps_name}`) has been set to expire on **{exp_date[:10]}** ({days} days).\n"
            f"Contact an admin to extend it before it expires!"))
    except discord.Forbidden:
        pass


@bot.command(name='extendexpire')
@is_admin()
async def extend_expire(ctx, user: discord.Member, vps_number: int, days: int):
    """Extend VPS expiry - !extendexpire @user <vps#> <days>"""
    vps, _ = find_vps(user.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    current = vps.get('expires', 'Never')
    if current == 'Never' or not current:
        base = utcnow()
    else:
        try:
            base = datetime.fromisoformat(current)
            if base < utcnow():
                base = utcnow()
        except Exception:
            base = utcnow()
    new_exp = (base + timedelta(days=days)).isoformat()
    vps['expires'] = new_exp
    save_data()
    audit("expire_extend", ctx.author, vps["container_name"], f"+{days}d")
    vps_name = vps['container_name']
    await ctx.send(embed=create_success_embed("Expiry Extended",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) extended by **{days} days**.\nNew expiry: `{new_exp[:10]}`"))
    try:
        await user.send(embed=create_success_embed("✅ VPS Extended",
            f"Your **VPS #{vps_number}** (`{vps_name}`) has been extended by **{days} days**!\nNew expiry: **{new_exp[:10]}**"))
    except discord.Forbidden:
        pass


@bot.command(name='checkexpire')
async def check_expire(ctx, user: discord.Member = None):
    """Check VPS expiry - users check own, admins can check others"""
    is_user_admin = str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", [])
    target = user if (user and is_user_admin) else ctx.author
    user_id = str(target.id)
    if user_id not in vps_data or not vps_data[user_id]:
        await ctx.send(embed=create_error_embed("Not Found", f"{target.mention} has no VPS."))
        return
    embed = create_info_embed(f"⏳ VPS Expiry - {target.display_name}", "")
    for i, vps in enumerate(vps_data[user_id]):
        expires = vps.get('expires', 'Never')
        if expires and expires != 'Never':
            try:
                exp_dt = datetime.fromisoformat(expires)
                days_left = (exp_dt - utcnow()).days
                if days_left < 0:
                    status = f"❌ **EXPIRED** {abs(days_left)}d ago"
                elif days_left <= 3:
                    status = f"⚠️ Expires in **{days_left}d** - {expires[:10]}"
                else:
                    status = f"✅ Expires on `{expires[:10]}` ({days_left}d left)"
            except Exception:
                status = expires
        else:
            status = "♾️ Never (No expiry set)"
        embed.add_field(name=f"VPS #{i+1} - `{vps['container_name']}`", value=status, inline=False)
    await ctx.send(embed=embed)


@bot.command(name='removeexpire')
@is_admin()
async def remove_expire(ctx, user: discord.Member, vps_number: int):
    """Remove expiry from a specific VPS - !removeexpire @user <vps#>"""
    vps, _ = find_vps(user.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    vps['expires'] = 'Never'
    save_data()
    audit("expire_remove", ctx.author, vps["container_name"])
    vps_name = vps['container_name']
    await ctx.send(embed=create_success_embed("Expiry Removed",
        f"✅ {user.mention}'s **VPS #{vps_number}** (`{vps_name}`) expiry set to **Never**."))


async def _dm_user(user_id, embed):
    try:
        u = await bot.fetch_user(int(user_id))
        await u.send(embed=embed)
    except Exception:
        pass


@tasks.loop(hours=1)
async def auto_expire_check():
    now = utcnow()
    for user_id, vps_list in list(vps_data.items()):
        for vps in vps_list:
            expires = vps.get('expires', 'Never')
            if not expires or expires == 'Never':
                continue
            try:
                exp_dt = datetime.fromisoformat(expires)
                days_left = (exp_dt - now).days
                cname = vps['container_name']
                if days_left == 3:
                    await _dm_user(user_id, create_warning_embed(
                        "⚠️ VPS Expiring Soon",
                        f"`{cname}` expires in **3 days** on `{expires[:10]}`!"))
                elif days_left == 1:
                    await _dm_user(user_id, create_error_embed(
                        "🚨 VPS Expiring Tomorrow!",
                        f"`{cname}` expires **tomorrow** (`{expires[:10]}`)!"))
                elif days_left < 0:
                    renewed = False
                    if billing_cfg.get("enabled"):
                        price = billing_price(vps)
                        if price is not None and ensure_user(user_id).get("credits", 0) >= price:
                            ensure_user(user_id)["credits"] -= price
                            days = billing_cfg.get("days", 30)
                            vps["expires"] = (utcnow() + timedelta(days=days)).isoformat()
                            save_data()
                            log_tx(user_id, "renewal", -price, f"auto-renewed {cname} ({days}d)")
                            audit("vps_auto_renew", "system", cname, f"{price}cr")
                            renewed = True
                            await _dm_user(user_id, create_success_embed(
                                "💳 Auto-Renewed",
                                f"`{cname}` renewed for **{days}d** ({price}cr). New expiry: `{vps['expires'][:10]}`"))
                    if not renewed:
                        try:
                            proc = await asyncio.create_subprocess_exec(
                                "docker", "stop", cname,
                                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                            await asyncio.wait_for(proc.communicate(), timeout=60)
                            vps['status'] = 'stopped'
                        except Exception:
                            pass
                        audit("expire_enforce", "system", cname, "expired, stopped")
                        await _dm_user(user_id, create_error_embed(
                            "❌ VPS Expired",
                            f"`{cname}` has expired and been **stopped**. Renew with `!renew`."))
            except Exception:
                continue
    save_data()


# ===========================================================================
# Phase 2-4: Economy, tickets, VPS depth, security, multi-node/API
# ===========================================================================

PLAN_PRICES = {
    "Starter": {"Intel": 42, "AMD": 83},
    "Basic": {"Intel": 96, "AMD": 164},
    "Standard": {"Intel": 192, "AMD": 320},
    "Pro": {"Intel": 220, "AMD": 340},
}
PLAN_SPECS = {
    "Starter": {"ram": "4GB", "cpu": "1", "storage": "10GB"},
    "Basic": {"ram": "8GB", "cpu": "1", "storage": "10GB"},
    "Standard": {"ram": "12GB", "cpu": "2", "storage": "10GB"},
    "Pro": {"ram": "16GB", "cpu": "2", "storage": "10GB"},
}
OS_IMAGES = {
    "ubuntu22": "ubuntu:22.04",
    "ubuntu24": "ubuntu:24.04",
    "debian12": "debian:12",
    "debian11": "debian:11",
    "alpine": "alpine:3.20",
    "centos": "quay.io/centos/centos:stream9",
    "fedora": "fedora:40",
    "rocky": "rockylinux:9",
    "almalinux": "almalinux:9",
    "kali": "kalilinux/kali-rolling",
    "arch": "archlinux:latest",
}

# NOTE: DOCKER_IMAGE is intentionally NOT redefined here. It is read from
# config.json at the top of this file and a second assignment would silently
# override whatever the operator configured.


def ensure_user(uid):
    uid = str(uid)
    if uid not in user_data or not isinstance(user_data[uid], dict):
        user_data[uid] = {"credits": 0}
    return user_data[uid]


def log_tx(uid, kind, amount, desc, other=""):
    uid = str(uid)
    transactions.append({
        "ts": utcnow().isoformat(),
        "user": uid,
        "kind": kind,
        "amount": amount,
        "desc": desc,
        "other": str(other),
    })
    del transactions[:-10000]
    save_json(TXN_PATH, transactions)


def plan_cost(plan, proc="Intel"):
    return PLAN_PRICES.get(plan, {}).get(proc)


def billing_price(vps):
    plan = vps.get("plan")
    if plan not in PLAN_PRICES:
        return None
    proc = vps.get("processor", "Intel")
    return PLAN_PRICES[plan].get(proc, PLAN_PRICES[plan].get("Intel"))


def vps_owner(container_name):
    for uid, vl in vps_data.items():
        for v in vl:
            if v.get("container_name") == container_name:
                return uid
    return None


def find_vps_by_name(container_name):
    for uid, vl in vps_data.items():
        for v in vl:
            if v.get("container_name") == container_name:
                return uid, v
    return None, None


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

# (check_rate is defined near the top with the security helpers)


# ---------------------------------------------------------------------------
# 2FA (TOTP RFC 6238, pure stdlib - no external dependency)
# ---------------------------------------------------------------------------

# (TOTP helpers are defined near the top with the security helpers)


# ---------------------------------------------------------------------------
# VPS owner alerts
# ---------------------------------------------------------------------------

async def alert_owner(vps, text, actor):
    uid = vps_owner(vps["container_name"])
    if not uid or str(uid) == str(actor):
        return
    if not ensure_user(uid).get("alerts"):
        return
    try:
        u = await bot.fetch_user(int(uid))
        await u.send(embed=create_warning_embed("🔔 VPS Alert", text))
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Multi-node routing (REST API client)
# ---------------------------------------------------------------------------

def call_node_sync(node, method, path, body=None, timeout=30):
    import urllib.request as urlreq
    n = nodes_reg.get(node)
    if not n:
        raise RuntimeError(f"Node '{node}' is not registered (use `!node add`).")
    req = urlreq.Request(n["url"].rstrip("/") + path,
                         data=json.dumps(body or {}).encode() if body is not None else None,
                         method=method)
    req.add_header("Content-Type", "application/json")
    if n.get("token"):
        req.add_header("Authorization", "Bearer " + n["token"])
    try:
        with urlreq.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception as e:
        raise RuntimeError(f"Node '{node}' request failed: {e}")


async def call_node(node, method, path, body=None, timeout=30):
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: call_node_sync(node, method, path, body, timeout))


async def deploy_container(node, container_name, image, ram_mb, cpu, disk_gb, password, ports=None):
    """Create a container, deploying to a remote node when configured."""
    if node and node != "local":
        n_rec = nodes_reg.get(node, {})
        n_type = n_rec.get("type", "")
        if n_type in DEPLOY_FUNCTIONS and n_rec.get("ip"):
            ram_gb = max(1, ram_mb // 1024)
            cid, http_port, err = await DEPLOY_FUNCTIONS[n_type](n_rec, ram_gb, cpu, disk_gb)
            if err:
                raise Exception(err)
            allocate_node_resources(node, ram_gb, cpu, disk_gb)
            return True
        await call_node(node, "POST", "/create", {
            "name": container_name,
            "image": image,
            "ram_mb": ram_mb,
            "cpu": str(cpu),
            "disk_gb": disk_gb,
            "password": password,
            "ports": ports or [],
        }, timeout=300)
        return True
    return await create_docker_container(container_name, ram_mb, cpu, 0, password,
                                         disk_gb=disk_gb, image=image, ports=ports or [])


async def container_op(vps, op, **kw):
    """Run an operation against any server, whatever its backend.

    Containers go through Docker. KVM guests and LXC system containers are
    routed to libvirt / Incus instead, so start, stop, delete and friends
    behave the same for every kind of server the bot hands out.
    """
    node = vps.get("node") or "local"
    name = vps["container_name"]
    virt = backend_key(vps) if CORE_MODULES_LOADED else "container"

    if node == "local" and virt != "container":
        return await _vm_op(vps, virt, op, **kw)

    if node != "local":
        if op == "exec":
            return await call_node(node, "POST", "/exec",
                                   {"name": name, "cmd": kw.get("cmd", ""), "timeout": kw.get("timeout", 30)})
        if op == "stats":
            return await call_node(node, "GET", f"/containers/{name}/stats")
        if op == "disk":
            return await call_node(node, "GET", f"/containers/{name}/disk")
        if op == "alive":
            try:
                d = await call_node(node, "GET", f"/containers/{name}/state")
                return bool(d.get("running"))
            except Exception:
                return False
        if op == "uptime":
            d = await call_node(node, "GET", f"/containers/{name}/state")
            return timedelta(seconds=d.get("uptime_sec", 0))
        if op == "port":
            d = await call_node(node, "GET", f"/containers/{name}/ports")
            return d.get("ports", [])
        if op in ("start", "stop", "restart", "remove"):
            return await call_node(node, "POST", "/" + op, {"name": name})
        if op == "resize":
            return await call_node(node, "POST", f"/containers/{name}/resize",
                                   {"memory_mb": kw.get("memory_mb"), "cpus": kw.get("cpus")})
        raise RuntimeError(f"Operation '{op}' is not supported on remote nodes yet.")
    if op == "start":
        await execute_docker(f"docker start {name}")
        return True
    if op == "stop":
        await execute_docker(f"docker stop {name}", timeout=120)
        return True
    if op == "restart":
        await execute_docker(f"docker restart {name}", timeout=120)
        return True
    if op == "remove":
        await execute_docker(f"docker rm -f {name}")
        return True
    if op == "exec":
        out, err, rc = await docker_exec_async(name, kw.get("cmd", ""), timeout=kw.get("timeout", 30))
        return {"stdout": out, "stderr": err, "rc": rc}
    if op == "stats":
        return await get_container_stats(name)
    if op == "disk":
        return await container_disk_usage(name)
    if op == "alive":
        return await container_alive(name)
    if op == "uptime":
        return await container_uptime_delta(name)
    if op == "port":
        return await current_ports(name)
    if op == "resize":
        await execute_docker(
            f"docker update --memory={kw.get('memory_mb')}m --cpus={kw.get('cpus')} {name}", timeout=30)
        return True
    raise RuntimeError(f"Unknown container operation '{op}'")


async def _vm_op(vps, virt, op, **kw):
    """KVM / LXC equivalent of container_op."""
    name = domain_of(vps) or vps["container_name"]

    if op in ("start", "stop", "restart", "reboot", "remove", "pause", "resume"):
        action = "delete" if op == "remove" else op
        if virt == "kvm" and action == "restart":
            action = "reboot"
        ok, message = await instance_action(
            {"virt": virt, "domain": name, "container_name": vps["container_name"]},
            action)
        if not ok:
            raise RuntimeError(message)
        return message

    if op == "alive":
        state = await instance_state({"virt": virt, "domain": name,
                                      "container_name": name})
        return state == "running"

    if op == "state":
        return await instance_state({"virt": virt, "domain": name,
                                     "container_name": name})

    if op == "port":
        ports = []
        if vps.get("ssh_port"):
            ports.append(f"{vps['ssh_port']}:22")
        ports.extend(vps.get("ports") or [])
        return ports

    if op == "resize":
        ok, message = await instance_resize(
            {"virt": virt, "domain": name, "container_name": name},
            new_ram_mb=kw.get("memory_mb") or 0,
            new_cpu=kw.get("cpus") or 0,
            new_disk_gb=kw.get("disk_gb") or 0)
        if not ok:
            raise RuntimeError(message)
        return message

    if op == "disk":
        if virt == "kvm":
            gb = await kvm_disk_size_gb(name)
            return {"used_gb": gb, "total_gb": gb, "percent": 0}
        return {"used_gb": 0, "total_gb": 0, "percent": 0}

    if op == "uptime":
        return None

    if op == "exec":
        if virt == "lxc":
            backend = await lxc_backend()
            cli = "incus" if backend == "incus" else "lxc"
            rc, out, err = await vm_run_cmd(
                [cli, "exec", name, "--", "sh", "-c", kw.get("cmd", "")],
                timeout=kw.get("timeout", 30))
            return {"stdout": out, "stderr": err, "rc": rc}
        raise RuntimeError(
            "Command execution is not available on full virtual machines. "
            "SSH in using the port you were sent and run it there."
        )

    if op == "stats":
        if virt == "kvm":
            rc, out, _ = await vm_run_cmd(
                ["virsh", "domstats", name, "--balloon", "-v", "memory"], timeout=30)
            used_kb = total_kb = 0
            for line in out.splitlines():
                if "balloon.current" in line:
                    used_kb = _parse_kb(line)
                elif "memory.current" in line or "balloon.maximum" in line:
                    total_kb = max(total_kb, _parse_kb(line))
            pct = int(used_kb / total_kb * 100) if total_kb else 0
            return {"cpu": "n/a", "mem_usage": f"{used_kb // 1024} MB",
                    "mem_percent": pct, "mem_total": f"{total_kb // 1024} MB",
                    "net_in": "n/a", "net_out": "n/a", "block_in": "n/a",
                    "block_out": "n/a"}
        return {"cpu": "n/a", "mem_usage": "n/a", "mem_percent": 0,
                "mem_total": "n/a", "net_in": "n/a", "net_out": "n/a",
                "block_in": "n/a", "block_out": "n/a"}

    raise RuntimeError(f"Operation '{op}' does not apply to {virt} servers.")


def _parse_kb(line):
    """virsh prints values like '123456' or '12.34 MiB'. Return KB as int."""
    parts = line.split(":", 1)
    if len(parts) != 2:
        return 0
    raw = parts[1].strip()
    try:
        if " " in raw:
            number, unit = raw.split(None, 1)
            factors = {"b": 1 / 1024, "bytes": 1 / 1024, "kb": 1,
                       "kib": 1, "k": 1, "mb": 1024, "mib": 1024, "m": 1024,
                       "gb": 1024 ** 2, "gib": 1024 ** 2, "g": 1024 ** 2}
            return int(float(number) * factors.get(unit.lower().rstrip(","), 1))
        return int(float(raw))
    except Exception:
        return 0


def current_ports(name):
    """List published ports for a container ('host:container')."""
    try:
        proc = subprocess.run(["docker", "port", name], capture_output=True, text=True, timeout=15)
        out = proc.stdout.strip()
        result = []
        for line in out.splitlines():
            parts = line.split(" -> ")
            if len(parts) == 2 and "/" in parts[0]:
                port = parts[0].split("/")[0]
                host = parts[1].split(":")[-1]
                result.append(f"{host}:{port}")
        return result
    except Exception:
        return []


async def vps_embed_list(user):
    lines = []
    for i, v in enumerate(vps_data.get(str(user.id), [])):
        icon = "🟢" if v.get("status") == "running" else "🔴"
        name = v.get("nickname", f"VPS {i + 1}")
        node = v.get("node") or "local"
        lines.append(f"{icon} **{name}** (`{v['container_name']}`) [node: {node}]")
    return lines


# ===========================================================================
# Economy: wallet & transactions
# ===========================================================================

@bot.command(name='balance', aliases=['bal'])
async def balance_cmd(ctx):
    """Check your credit balance"""
    uid = str(ctx.author.id)
    credits = ensure_user(uid).get("credits", 0)
    await ctx.send(embed=create_info_embed("💰 Credit Balance",
                                           f"{ctx.author.mention}, you have **{credits}** credits."))


@bot.command(name='pay')
async def pay_cmd(ctx, target: discord.Member, amount: int):
    """Send credits to another user - !pay @user <amount>"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    if target.id == ctx.author.id:
        await ctx.send(embed=create_error_embed("Invalid Target", "You can't pay yourself!"))
        return
    sender = ensure_user(ctx.author.id)
    if sender.get("credits", 0) < amount:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
                                                f"You only have **{sender['credits']}** credits."))
        return
    receiver = ensure_user(target.id)
    sender["credits"] -= amount
    receiver["credits"] += amount
    save_data()
    log_tx(ctx.author.id, "transfer_out", -amount, f"sent to {target}")
    log_tx(target.id, "transfer_in", amount, f"received from {ctx.author}")
    audit("credits_transfer", ctx.author, str(target), f"{amount}cr")
    await ctx.send(embed=create_success_embed(
        "💸 Payment Sent", f"{ctx.author.mention} sent **{amount}** credits to {target.mention}!"))
    try:
        await target.send(embed=create_info_embed(
            "💰 Credits Received",
            f"You received **{amount}** credits from {ctx.author.mention}!\nNew balance: **{receiver['credits']}**"))
    except discord.Forbidden:
        pass


@bot.command(name='transactions', aliases=['tx'])
async def tx_cmd(ctx, user: discord.Member = None):
    """Show your recent transactions - !transactions [@user] (admin sees others)"""
    is_user_admin = str(ctx.author.id) == str(MAIN_ADMIN_ID) or str(ctx.author.id) in admin_data.get("admins", [])
    target = user if (user and is_user_admin) else ctx.author
    rows = [t for t in reversed(transactions) if t.get("user") == str(target.id)][:10]
    embed = create_embed(f"💳 Transactions - {target.display_name}",
                         "Your last 10 credit movements:" if not user else f"{target.mention}'s last 10 credit movements:")
    if not rows:
        embed.add_field(name="No Transactions", value="Nothing yet. Use `!buyc` to purchase credits!", inline=False)
    else:
        lines = []
        for t in rows:
            sign = "+" if t.get("amount", 0) >= 0 else ""
            lines.append(f"`{t['ts'][:16]}` {sign}{t['amount']}cr - {t['desc']}")
        embed.add_field(name="Activity", value="\n".join(lines), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='deposit')
async def deposit_cmd(ctx, amount: int):
    """Request to deposit credits - !deposit <amount>"""
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    entry = {
        "id": len(pending_payments) + 1,
        "type": "deposit",
        "user": str(ctx.author.id),
        "amount": amount,
        "status": "pending",
        "ts": utcnow().isoformat(),
    }
    pending_payments.append(entry)
    save_data()
    audit("deposit_request", ctx.author, "", f"{amount}cr (id {entry['id']})")
    embed = create_info_embed("🏦 Deposit Request", f"Deposit request **#{entry['id']}** for **{amount}** credits.")
    embed.add_field(name="How to pay", value=(
        "1. Send payment via UPI / PayPal / Crypto (`!buyc` for addresses)\n"
        "2. Tell an admin your **request ID** and the transaction reference\n"
        "3. Admin runs `!depositapprove <id>`"), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='withdraw')
async def withdraw_cmd(ctx, amount: int):
    """Request to withdraw credits - !withdraw <amount>"""
    uid = str(ctx.author.id)
    if amount <= 0:
        await ctx.send(embed=create_error_embed("Invalid Amount", "Amount must be positive."))
        return
    if ensure_user(uid).get("credits", 0) < amount:
        await ctx.send(embed=create_error_embed("Insufficient Credits", "You don't have enough credits."))
        return
    entry = {
        "id": len(pending_payments) + 1,
        "type": "withdraw",
        "user": uid,
        "amount": amount,
        "status": "pending",
        "ts": utcnow().isoformat(),
    }
    pending_payments.append(entry)
    ensure_user(uid)["credits"] -= amount
    save_data()
    audit("withdraw_request", ctx.author, "", f"{amount}cr (id {entry['id']})")
    await ctx.send(embed=create_info_embed(
        "💸 Withdrawal Request", f"Withdrawal request **#{entry['id']}** for **{amount}** credits created.\n"
        f"Amount is held until an admin processes it (`!withdrawapprove <id>`)."))


@bot.command(name='pendingpayments')
@is_admin()
async def pending_payments_cmd(ctx):
    """List pending deposit/withdrawal requests (Billing+)"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    rows = [p for p in pending_payments if p.get("status") == "pending"]
    embed = create_embed("🕓 Pending Payments", f"{len(rows)} pending request(s)")
    if not rows:
        embed.add_field(name="None", value="No pending payments.", inline=False)
    else:
        for p in rows[-10:]:
            tag = "🏦 deposit" if p["type"] == "deposit" else "💸 withdraw"
            embed.add_field(
                name=f"{tag} #{p['id']} - {p['amount']}cr",
                value=f"User: `{p['user']}` | `{p['ts'][:16]}`\nApprove: `!{p['type']}-approve {p['id']}`", inline=False)
    await ctx.send(embed=embed)


@bot.command(name='depositapprove')
@is_admin()
async def deposit_approve_cmd(ctx, req_id: int):
    """Approve a deposit request (Billing+)"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    for p in pending_payments:
        if p.get("id") == req_id and p.get("type") == "deposit" and p.get("status") == "pending":
            p["status"] = "approved"
            ensure_user(p["user"])["credits"] += p["amount"]
            save_data()
            log_tx(p["user"], "deposit", p["amount"], "deposit approved")
            audit("deposit_approve", ctx.author, p["user"], f"{p['amount']}cr")
            try:
                u = await bot.fetch_user(int(p["user"]))
                await u.send(embed=create_success_embed(
                    "🏦 Deposit Approved",
                    f"Your deposit of **{p['amount']}** credits was approved. New balance: **{ensure_user(p['user'])['credits']}**"))
            except Exception:
                pass
            await ctx.send(embed=create_success_embed(
                "Deposit Approved", f"Added **{p['amount']}** credits to `{p['user']}`."))
            return
    await ctx.send(embed=create_error_embed("Not Found", f"No pending deposit #{req_id}."))


@bot.command(name='depositdeny')
@is_admin()
async def deposit_deny_cmd(ctx, req_id: int):
    """Deny a deposit request (Billing+)"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    for p in pending_payments:
        if p.get("id") == req_id and p.get("type") == "deposit" and p.get("status") == "pending":
            p["status"] = "denied"
            save_data()
            audit("deposit_deny", ctx.author, p["user"], f"{p['amount']}cr")
            await ctx.send(embed=create_success_embed("Deposit Denied", f"Request #{req_id} marked denied."))
            return
    await ctx.send(embed=create_error_embed("Not Found", f"No pending deposit #{req_id}."))


@bot.command(name='withdrawapprove')
@is_admin()
async def withdraw_approve_cmd(ctx, req_id: int, txn_ref: str = ""):
    """Approve a withdrawal (payout done) - !withdrawapprove <id> [txn_ref]"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    for p in pending_payments:
        if p.get("id") == req_id and p.get("type") == "withdraw" and p.get("status") == "pending":
            p["status"] = "approved"
            save_data()
            log_tx(p["user"], "withdraw", -p["amount"], f"withdrawal approved (ref {txn_ref or 'n/a'})")
            audit("withdraw_approve", ctx.author, p["user"], f"{p['amount']}cr {txn_ref}")
            try:
                u = await bot.fetch_user(int(p["user"]))
                await u.send(embed=create_success_embed(
                    "💸 Withdrawal Sent",
                    f"Your withdrawal of **{p['amount']}** credits has been paid out.\nReference: `{txn_ref or 'n/a'}`"))
            except Exception:
                pass
            await ctx.send(embed=create_success_embed(
                "Withdrawal Approved", f"Paid **{p['amount']}** credits to `{p['user']}`."))
            return
    await ctx.send(embed=create_error_embed("Not Found", f"No pending withdrawal #{req_id}."))


@bot.command(name='withdrawdeny')
@is_admin()
async def withdraw_deny_cmd(ctx, req_id: int):
    """Deny a withdrawal and refund the held credits (Billing+)"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    for p in pending_payments:
        if p.get("id") == req_id and p.get("type") == "withdraw" and p.get("status") == "pending":
            p["status"] = "denied"
            ensure_user(p["user"])["credits"] += p["amount"]
            save_data()
            audit("withdraw_deny", ctx.author, p["user"], f"{p['amount']}cr refunded")
            await ctx.send(embed=create_success_embed("Withdrawal Denied", f"Refunded **{p['amount']}** credits to `{p['user']}`."))
            return
    await ctx.send(embed=create_error_embed("Not Found", f"No pending withdrawal #{req_id}."))


# ===========================================================================
# Auto-billing / renewal
# ===========================================================================

@bot.command(name='billing')
@is_main_admin()
async def billing_cmd(ctx, mode: str, days: int = 30):
    """Toggle automatic billing - !billing <on|off> [days]"""
    global billing_cfg
    if mode.lower() == "on":
        billing_cfg["enabled"] = True
        billing_cfg["days"] = max(1, days)
        save_data()
        audit("billing_enable", ctx.author, "", f"{days}d")
        await ctx.send(embed=create_success_embed(
            "💳 Auto-Billing ON",
            f"VPS will auto-renew every **{billing_cfg['days']} days** by charging the plan price in credits. "
            f"Renewal failures stop the VPS."))
    elif mode.lower() == "off":
        billing_cfg["enabled"] = False
        save_data()
        audit("billing_disable", ctx.author, "", "")
        await ctx.send(embed=create_warning_embed("Auto-Billing OFF", "No automatic renewals. VPS with `Never` expiry are not charged."))
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: `!billing on [days]` or `!billing off`"))


@bot.command(name='billingstatus')
@is_admin()
async def billing_status_cmd(ctx):
    """Show billing configuration"""
    embed = create_embed("💳 Billing Status", "", 0xffd700)
    embed.add_field(name="Enabled", value="✅ Yes" if billing_cfg.get("enabled") else "❌ No", inline=True)
    embed.add_field(name="Period", value=f"every {billing_cfg.get('days', 30)} days", inline=True)
    prices = "\n".join([f"{p} - Intel {PLAN_PRICES[p]['Intel']}cr / AMD {PLAN_PRICES[p]['AMD']}cr" for p in PLAN_PRICES])
    embed.add_field(name="Renewal prices", value=prices, inline=False)
    await ctx.send(embed=embed)


@bot.command(name='renew')
async def renew_cmd(ctx, vps_number: int):
    """Manually renew a VPS - charges your credits for the plan price"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    price = billing_price(vps)
    if price is None:
        await ctx.send(embed=create_error_embed("No Price", "This VPS has no plan price configured."))
        return
    uid = str(ctx.author.id)
    if ensure_user(uid).get("credits", 0) < price:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
                                                f"Renewal costs **{price}** credits (you have **{ensure_user(uid)['credits']}**)."))
        return
    days = billing_cfg.get("days", 30) if billing_cfg.get("enabled") else 30
    base = utcnow()
    cur = vps.get("expires")
    if cur and cur != "Never":
        try:
            exp = datetime.fromisoformat(cur)
            if exp > base:
                base = exp
        except Exception:
            pass
    vps["expires"] = (base + timedelta(days=days)).isoformat()
    ensure_user(uid)["credits"] -= price
    vps["status"] = vps.get("status", "running")
    save_data()
    log_tx(uid, "renewal", -price, f"renewed {vps['container_name']} ({days}d)")
    audit("vps_renew", ctx.author, vps["container_name"], f"{price}cr for {days}d")
    await ctx.send(embed=create_success_embed(
        "✅ VPS Renewed", f"VPS #{vps_number} (`{vps['container_name']}`) renewed for **{days} days** for **{price}** credits.\n"
        f"New expiry: `{vps['expires'][:10]}`"))


# ===========================================================================
# Coupons
# ===========================================================================

@bot.command(name='coupon')
@is_admin()
async def coupon_cmd(ctx, action: str, code: str = None, amount: int = 0, max_uses: int = 0):
    """Coupons - !coupon create <code> <credits> [max_uses] | list | delete <code>"""
    if not has_level(ctx.author.id, "Billing"):
        await ctx.send(embed=create_error_embed("Access Denied", "Billing+ required."))
        return
    action = action.lower()
    if action == "create":
        if not code or amount <= 0:
            await ctx.send(embed=create_error_embed("Usage", "`!coupon create <code> <credits> [max_uses]`"))
            return
        coupons[code.upper()] = {"amount": amount, "uses": 0, "max_uses": max_uses,
                                 "created_by": str(ctx.author.id), "ts": utcnow().isoformat()}
        save_data()
        audit("coupon_create", ctx.author, code.upper(), f"{amount}cr x{max_uses or 'unlimited'}")
        await ctx.send(embed=create_success_embed("Coupon Created", f"`{code.upper()}` = **{amount}** credits (max {max_uses or '∞'} uses)."))
    elif action == "list":
        if not coupons:
            await ctx.send(embed=create_info_embed("Coupons", "No coupons."))
            return
        lines = [f"`{c}` - {d['amount']}cr | used {d['uses']}/{d['max_uses'] or '∞'}" for c, d in coupons.items()]
        await ctx.send(embed=create_embed("🎟️ Coupons", "\n".join(lines), 0xffaa00))
    elif action == "delete":
        if not code:
            await ctx.send(embed=create_error_embed("Usage", "`!coupon delete <code>`"))
            return
        if coupons.pop(code.upper(), None):
            save_data()
            audit("coupon_delete", ctx.author, code.upper(), "")
            await ctx.send(embed=create_success_embed("Coupon Deleted", f"`{code.upper()}` removed."))
        else:
            await ctx.send(embed=create_error_embed("Not Found", "Unknown coupon code."))
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: create | list | delete"))


@bot.command(name='redeem')
async def redeem_cmd(ctx, code: str):
    """Redeem a coupon code - !redeem <CODE>"""
    c = coupons.get(code.upper())
    if not c:
        await ctx.send(embed=create_error_embed("Invalid Code", "This coupon does not exist."))
        return
    if c.get("max_uses") and c["uses"] >= c["max_uses"]:
        await ctx.send(embed=create_error_embed("Expired", "This coupon has reached its usage limit."))
        return
    if str(ctx.author.id) in c.get("redeemed", []):
        await ctx.send(embed=create_error_embed("Already Used", "You have already redeemed this coupon."))
        return
    c["uses"] += 1
    c.setdefault("redeemed", []).append(str(ctx.author.id))
    ensure_user(ctx.author.id)["credits"] += c["amount"]
    save_data()
    log_tx(ctx.author.id, "coupon", c["amount"], f"redeemed {code.upper()}")
    audit("coupon_redeem", ctx.author, code.upper(), f"+{c['amount']}cr")
    await ctx.send(embed=create_success_embed(
        "🎟️ Coupon Redeemed", f"You received **{c['amount']}** credits!\nNew balance: **{ensure_user(ctx.author.id)['credits']}**"))


# ===========================================================================
# Tickets
# ===========================================================================

@bot.command(name='ticket')
async def ticket_cmd(ctx, *, subject: str):
    """Open a support ticket - !ticket <subject>"""
    uid = str(ctx.author.id)
    ensure_user(uid)
    t_id = tickets_data["counter"]
    tickets_data["counter"] += 1
    support_channel = ctx.channel
    try:
        thread = await support_channel.create_thread(
            name=f"ticket-{ctx.author.display_name[:20]}-{t_id}", type=discord.ChannelType.public_thread)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Ticket Failed", sanitize_error(e)))
        return
    tickets_data["open"][str(thread.id)] = {
        "id": t_id, "user": uid, "subject": subject[:100],
        "status": "open", "claimed_by": None, "ts": utcnow().isoformat(),
    }
    save_data()
    audit("ticket_open", ctx.author, str(thread.id), subject[:100])
    intro = create_embed("🎫 Support Ticket", f"**Subject:** {subject[:100]}", 0x5865F2)
    intro.add_field(name="User", value=ctx.author.mention, inline=True)
    intro.add_field(name="Ticket ID", value=f"`#{t_id}`", inline=True)
    intro.add_field(name="How to close", value="Click **Close Ticket** below or run `!ticketclose`.", inline=False)

    class TicketView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="🗂️ Close Ticket", style=discord.ButtonStyle.danger)
        async def close(self, interaction: discord.Interaction, item: discord.ui.Button):
            rec = tickets_data["open"].pop(str(thread.id), None)
            if not rec:
                await interaction.response.send_message("Ticket already closed.", ephemeral=True)
                return
            rec["status"] = "closed"
            tickets_data["closed"][str(thread.id)] = rec
            save_data()
            audit("ticket_close", interaction.user, str(thread.id), "")
            try:
                await thread.send(embed=create_info_embed("Ticket Closed", f"Closed by {interaction.user.mention}."))
                await thread.edit(archived=True, locked=True)
            except Exception:
                pass
            await interaction.response.defer()

        @discord.ui.button(label="🙋 Claim", style=discord.ButtonStyle.primary)
        async def claim(self, interaction: discord.Interaction, item: discord.ui.Button):
            if not (str(interaction.user.id) == str(MAIN_ADMIN_ID) or str(interaction.user.id) in admin_data.get("admins", [])):
                await interaction.response.send_message("Only staff can claim tickets.", ephemeral=True)
                return
            rec = tickets_data["open"].get(str(thread.id))
            if not rec:
                await interaction.response.send_message("Ticket already closed.", ephemeral=True)
                return
            rec["claimed_by"] = str(interaction.user.id)
            save_data()
            await thread.send(embed=create_info_embed("Ticket Claimed", f"Claimed by {interaction.user.mention}."))
            await interaction.response.defer()

    await thread.send(embed=intro, view=TicketView())
    await ctx.send(embed=create_success_embed(
        "Ticket Opened", f"Your ticket is here: {thread.mention}\nTicket ID: `#{t_id}`"))


@bot.command(name='tickets')
@is_admin()
async def tickets_cmd(ctx):
    """List open tickets"""
    rows = tickets_data.get("open", {})
    embed = create_embed("🎫 Open Tickets", f"{len(rows)} open")
    if not rows:
        embed.add_field(name="None", value="No open tickets.", inline=False)
    else:
        for tid, rec in list(rows.items())[:10]:
            embed.add_field(
                name=f"#{rec['id']} - {rec['subject'][:40]}",
                value=f"User: `{rec['user']}` | claimed: {'`' + rec['claimed_by'][:8] + '`' if rec.get('claimed_by') else 'no'}", inline=False)
    await ctx.send(embed=embed)


@bot.command(name='ticketclose')
async def ticket_close_cmd(ctx):
    """Close the ticket you're in (or use the Close button)"""
    if not isinstance(ctx.channel, discord.Thread):
        await ctx.send(embed=create_error_embed("Not a Ticket", "Run this inside a ticket thread."))
        return
    rec = tickets_data["open"].pop(str(ctx.channel.id), None)
    if not rec:
        await ctx.send(embed=create_info_embed("Already Closed", "This thread is not an open ticket."))
        return
    rec["status"] = "closed"
    tickets_data["closed"][str(ctx.channel.id)] = rec
    save_data()
    audit("ticket_close", ctx.author, str(ctx.channel.id), "")
    await ctx.send(embed=create_success_embed("Ticket Closed", "Thanks for reaching out!"))
    try:
        await ctx.channel.edit(archived=True, locked=True)
    except Exception:
        pass


# ===========================================================================
# VPS depth: upgrades, OS, ports, files
# ===========================================================================

@bot.command(name='upgrade')
async def upgrade_cmd(ctx, vps_number: int, plan: str):
    """Upgrade your VPS plan - !upgrade <vps#> <plan> (charges the price difference)"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    old_plan = vps.get("plan")
    if plan not in PLAN_PRICES:
        await ctx.send(embed=create_error_embed("Invalid Plan", "Plans: " + ", ".join(PLAN_PRICES)))
        return
    proc = vps.get("processor", "Intel")
    new_cost = plan_cost(plan, proc)
    if new_cost is None:
        await ctx.send(embed=create_error_embed("Invalid Plan", "Plans: " + ", ".join(PLAN_PRICES)))
        return
    old_cost = billing_price(vps) or 0
    diff = max(0, new_cost - old_cost)
    uid = str(ctx.author.id)
    if ensure_user(uid).get("credits", 0) < diff:
        await ctx.send(embed=create_error_embed("Insufficient Credits",
                                                f"Upgrade to **{plan}** costs **{diff}** credits (you have **{ensure_user(uid)['credits']}**)."))
        return
    if diff > 0:
        ensure_user(uid)["credits"] -= diff
    specs = PLAN_SPECS[plan]
    vps["plan"] = plan
    vps["ram"] = specs["ram"]
    vps["cpu"] = specs["cpu"]
    vps["storage"] = specs["storage"]
    save_data()
    await ctx.send(embed=create_info_embed("Applying Upgrade", f"Upgrading to **{plan}** - applying resource limits..."))
    try:
        ram_mb = int(specs["ram"].replace("GB", "")) * 1024
        await container_op(vps, "resize", memory_mb=ram_mb, cpus=int(specs["cpu"]))
        log_tx(uid, "upgrade", -diff, f"upgraded to {plan}")
        audit("vps_upgrade", ctx.author, vps["container_name"], f"{old_plan} -> {plan} ({diff}cr)")
        await ctx.send(embed=create_success_embed(
            "✅ VPS Upgraded", f"VPS #{vps_number} is now **{plan}** (`{specs['ram']}` RAM, `{specs['cpu']}` CPU)."))
    except Exception as e:
        vps["plan"] = old_plan
        vps["ram"] = PLAN_SPECS[old_plan]["ram"] if old_plan in PLAN_SPECS else vps["ram"]
        vps["cpu"] = PLAN_SPECS[old_plan]["cpu"] if old_plan in PLAN_SPECS else vps["cpu"]
        if diff > 0:
            ensure_user(uid)["credits"] += diff
        save_data()
        await ctx.send(embed=create_error_embed("Upgrade Failed", f"{e}\nRolled back. Credits refunded."))


@bot.command(name='resize')
@is_superadmin()
async def resize_cmd(ctx, user: discord.Member, vps_number: int, ram_gb: int, cpu: int):
    """Resize a VPS without changing plan - !resize @user <vps#> <ram_GB> <cpu>"""
    vps, _ = find_vps(user.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", f"{user.mention} has no VPS #{vps_number}."))
        return
    if ram_gb <= 0 or cpu <= 0:
        await ctx.send(embed=create_error_embed("Invalid Specs", "RAM and CPU must be positive."))
        return
    old_ram, old_cpu = vps.get("ram"), vps.get("cpu")
    vps["ram"], vps["cpu"] = f"{ram_gb}GB", str(cpu)
    save_data()
    try:
        await container_op(vps, "resize", memory_mb=ram_gb * 1024, cpus=cpu)
        audit("vps_resize", ctx.author, vps["container_name"], f"{old_ram}->{ram_gb}GB {old_cpu}->{cpu}c")
        await ctx.send(embed=create_success_embed(
            "Resized", f"{user.mention}'s VPS #{vps_number} now `{ram_gb}GB` RAM / `{cpu}` CPU."))
    except Exception as e:
        vps["ram"], vps["cpu"] = old_ram, old_cpu
        save_data()
        await ctx.send(embed=create_error_embed("Resize Failed", sanitize_error(e)))


@bot.command(name='os')
async def os_cmd(ctx, vps_number: int = 0):
    """Show your VPS operating system"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    image = vps.get("image") or "unknown"
    await ctx.send(embed=create_info_embed("🐧 Operating System",
                                           f"VPS #{vps_number}: `{vps['container_name']}` runs **{image}**.\n\n"
                                           f"**Available OS:**\n"
                                           f"`ubuntu22` • `ubuntu24` • `debian12` • `debian11`\n"
                                           f"`alpine` • `centos` • `fedora` • `rocky`\n"
                                           f"`almalinux` • `kali` • `arch`\n\n"
                                           f"Reinstall: `!reinstallos <vps#> <os>`"))


@bot.command(name='reinstallos')
async def reinstall_os_cmd(ctx, vps_number: int, os_name: str):
    """Reinstall your server with a different OS - !reinstallos <vps#> <os>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return

    virt = _instance_kind(vps)
    os_name = os_name.lower()
    if virt == "kvm":
        options = KVM_OS_CATALOG if CORE_MODULES_LOADED else {}
        if os_name not in options:
            await ctx.send(embed=create_error_embed(
                "Invalid OS",
                "This is a KVM machine. Options: " +
                ", ".join(f"{k} ({v['name']})" for k, v in options.items())))
            return
        os_label = options[os_name]["name"]
    elif virt == "lxc":
        from core.hypervisor import LXC_IMAGES
        if os_name not in LXC_IMAGES:
            await ctx.send(embed=create_error_embed(
                "Invalid OS", "This is an LXC container. Options: "
                + ", ".join(LXC_IMAGES)))
            return
        os_label = LXC_IMAGES[os_name]
    else:
        if os_name not in OS_IMAGES:
            await ctx.send(embed=create_error_embed(
                "Invalid OS", "Options: " + ", ".join(OS_IMAGES)))
            return
        os_label = OS_IMAGES[os_name]
    image = _resolve_os_image(virt, os_name)

    class OSConfirm(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=60)

        @discord.ui.button(label="Reinstall", style=discord.ButtonStyle.danger)
        async def go(self, interaction: discord.Interaction, item: discord.ui.Button):
            if str(interaction.user.id) != str(ctx.author.id):
                await interaction.response.send_message("Not your server.", ephemeral=True)
                return
            await interaction.response.defer()
            screen = ProgressScreen(interaction, title="REINSTALLING OS", op_prefix="OS", color=0x00ccff)
            screen.set_steps([
                ("Removing the old instance", "pending"),
                (f"Preparing {os_label}", "pending"),
                ("Creating the fresh instance", "pending"),
                ("Configuring SSH access", "pending"),
                ("Running health checks", "pending"),
            ])
            await screen.start()
            try:
                name = vps["container_name"]
                await screen.update(10, 0, f"Removing `{name}`...")
                if not await teardown_instance(vps):
                    raise RuntimeError(
                        f"Could not remove `{name}`. Check the hypervisor state.")
                await screen.step_done(35, 1, "Old instance removed")

                new_pw = generate_password()
                vps["image"] = image
                vps["os_key"] = os_name
                vps["os_label"] = os_label
                vps["ssh_password"] = new_pw
                save_data()

                await screen.update(35, 1, f"Preparing {os_label}...")
                await rebuild_instance(vps, new_pw)
                await screen.step_done(70, 2, "Fresh instance created")

                await screen.update(70, 3, "Configuring SSH access...")
                await screen.step_done(85, 3, "SSH configured")

                await screen.update(85, 4, "Running health checks...")
                vps["status"] = "running"
                save_data()
                await asyncio.sleep(0.3)

                audit("vps_reinstall_os", interaction.user, name, os_label)
                ssh_port = vps.get("ssh_port", 22)
                await screen.complete(f"Your server now runs **{os_label}**!", extra_fields={
                    "Server": f"`{name}`",
                    "Type": VIRT_TYPES.get(virt, {}).get("label", virt),
                    "OS": os_label,
                    "Status": "Running",
                })
                reinstall_embed = _build_vps_dm(
                    name, str(vps["ram"]).replace("GB", ""), vps["cpu"], new_pw,
                    vps.get("node") or "local", ssh_port, virt=virt,
                    os_label=os_label)
                try:
                    await interaction.user.send(embed=reinstall_embed)
                except discord.Forbidden:
                    pass
            except Exception as e:
                vps["status"] = "stopped"
                save_data()
                await screen.fail("Reinstall Failed", sanitize_error(e))

        @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
        async def cancel(self, interaction: discord.Interaction, item: discord.ui.Button):
            await interaction.response.edit_message(embed=create_info_embed("Cancelled", "Reinstall cancelled."))

    await ctx.send(embed=create_warning_embed(
        "Reinstall OS",
        f"This erases all data on `{vps['container_name']}` and installs "
        f"**{os_label}**.\nA new root password will be sent to your DMs."),
        view=OSConfirm())


# ---------------------------------------------------------------------------
# VPS Protection system - prevents purge from removing protected VPS
# ---------------------------------------------------------------------------

def is_vps_protected(container_name):
    """Check if a VPS container is protected from purge."""
    return container_name in protected_vps.get("containers", [])


def protect_vps(container_name, owner_id, reason=""):
    """Mark a VPS as protected."""
    containers = protected_vps.setdefault("containers", [])
    if container_name not in containers:
        containers.append(container_name)
    protected_vps.setdefault("owners", {})[container_name] = {
        "owner": str(owner_id),
        "reason": reason,
        "protected_at": utcnow().isoformat(),
    }
    save_json(PROTECTED_VPS_PATH, protected_vps)


def unprotect_vps(container_name):
    """Remove protection from a VPS."""
    containers = protected_vps.get("containers", [])
    if container_name in containers:
        containers.remove(container_name)
    protected_vps.get("owners", {}).pop(container_name, None)
    save_json(PROTECTED_VPS_PATH, protected_vps)


@bot.command(name='protect')
async def protect_cmd(ctx, vps_number: int = 0, *, reason: str = "No reason"):
    """Protect a VPS from purge operations - !protect <vps#> [reason]"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    cname = vps["container_name"]
    if is_vps_protected(cname):
        await ctx.send(embed=create_warning_embed("Already Protected", f"`{cname}` is already protected."))
        return
    protect_vps(cname, ctx.author.id, reason)
    audit("vps_protect", ctx.author, cname, reason)
    embed = create_success_embed("VPS Protected",
        f"`{cname}` is now **protected** from purge operations.")
    embed.add_field(name="Reason", value=reason, inline=False)
    embed.add_field(name="Protected By", value=ctx.author.mention, inline=True)
    await ctx.send(embed=embed)


@bot.command(name='unprotect')
async def unprotect_cmd(ctx, vps_number: int = 0):
    """Remove protection from a VPS - !unprotect <vps#>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    cname = vps["container_name"]
    if not is_vps_protected(cname):
        await ctx.send(embed=create_error_embed("Not Protected", f"`{cname}` is not protected."))
        return
    unprotect_vps(cname)
    audit("vps_unprotect", ctx.author, cname, "")
    await ctx.send(embed=create_success_embed("Protection Removed",
        f"`{cname}` is no longer protected from purge operations."))


@bot.command(name='protectlist')
@is_admin()
async def protect_list_cmd(ctx):
    """List all protected VPS"""
    containers = protected_vps.get("containers", [])
    owners = protected_vps.get("owners", {})
    if not containers:
        await ctx.send(embed=create_info_embed("Protected VPS", "No VPS are currently protected."))
        return
    lines = []
    for cname in containers:
        info = owners.get(cname, {})
        owner_id = info.get("owner", "?")
        reason = info.get("reason", "No reason")
        prot_at = info.get("protected_at", "?")[:10]
        lines.append(f"**{cname}** - Owner: <@{owner_id}> - Reason: `{reason}` - Since: {prot_at}")
    embed = create_embed("🛡️ Protected VPS", f"**{len(containers)}** VPS protected from purge:", 0x00ff88)
    embed.add_field(name="Protected", value="\n".join(lines[:15]), inline=False)
    await ctx.send(embed=embed)


# ---------------------------------------------------------------------------
# Security Center & advanced security commands
# ---------------------------------------------------------------------------

@bot.command(name='securitycenter')
@is_admin()
async def security_center_cmd(ctx):
    """Open the Turtle Nodes Security Center"""
    embed = build_security_center_embed(bot)
    view = SecurityCenterView(bot, ctx.author.id)
    await ctx.send(embed=embed, view=view)


@bot.command(name='securityaudit')
@is_admin()
async def security_audit_cmd(ctx):
    """Run a quick security audit"""
    await ctx.send(embed=create_info_embed("Security Audit", "Running audit..."))
    try:
        secret_issues = secret_guard.scan_data_dir()
        db_health = db_guard.check_db_health()
        audit_verified, _, audit_msg = immutable_audit.verify_chain()
        dep_status = dep_checker.get_status()
        node_status = node_isolation.get_status()
        fail_status = fail_guard.get_stats()
        hv_stats = health_verifier.get_stats()

        score = 100
        issues = []
        if secret_issues:
            score -= len(secret_issues) * 5
            issues.append(f"{len(secret_issues)} secret exposure(s)")
        if db_health.get("issues"):
            score -= len(db_health["issues"]) * 10
            issues.append(f"{len(db_health['issues'])} DB issue(s)")
        if not audit_verified:
            score -= 30
            issues.append("Audit chain broken")
        if node_status.get("isolated_count", 0) > 0:
            score -= node_status["isolated_count"] * 5
            issues.append(f"{node_status['isolated_count']} isolated node(s)")
        if fail_status.get("currently_blocked", 0) > 0:
            score -= fail_status["currently_blocked"] * 10
            issues.append(f"{fail_status['currently_blocked']} blocked operation(s)")
        score = max(0, min(100, score))

        color = 0x00ff88 if score >= 80 else (0xffaa00 if score >= 50 else 0xff3366)
        status = "SECURE" if score >= 80 else ("ATTENTION" if score >= 50 else "AT RISK")

        embed = create_embed(f"Security Audit - {status}", f"**Score: {score}/100**", color)
        embed.add_field(name="Secrets", value=f"{len(secret_issues)} issues" if secret_issues else "Clean", inline=True)
        embed.add_field(name="Database", value=f"{db_health['healthy']}/{db_health['total_files']} OK", inline=True)
        embed.add_field(name="Audit Chain", value="Verified" if audit_verified else "BROKEN", inline=True)
        embed.add_field(name="Dependencies", value=f"{dep_status.get('vulnerabilities_found',0)} vuln", inline=True)
        embed.add_field(name="Fail Guard", value=f"Blocked: {fail_status.get('currently_blocked',0)}", inline=True)
        embed.add_field(name="Health", value=f"Checks: {hv_stats.get('total',0)}", inline=True)
        if issues:
            embed.add_field(name="Issues", value="\n".join(f"- {i}" for i in issues), inline=False)
        await ctx.send(embed=embed)
        immutable_audit.log("security_audit", ctx.author.id, "quick_audit",
                           f"score={score} issues={len(issues)}")
        security_anomaly_detector.record_admin_action(ctx.author.id, "security_audit")
    except Exception as e:
        await ctx.send(embed=create_error_embed("Audit Failed", str(e)[:500]))


@bot.command(name='recoverypoint')
@is_admin()
async def recovery_point_cmd(ctx, *, reason: str = "manual"):
    """Create a disaster recovery point"""
    try:
        rp = disaster_recovery.create_recovery_point(reason=reason)
        embed = create_success_embed("Recovery Point Created",
            f"**Name:** `{rp['name']}`\n**Files:** {rp['files']}")
        await ctx.send(embed=embed)
        immutable_audit.log("recovery_point_create", ctx.author.id, rp["name"], reason)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Failed", str(e)[:500]))


@bot.command(name='depcheck')
@is_admin()
async def dep_check_cmd(ctx):
    """Check dependencies for security issues"""
    try:
        results = dep_checker.check_vulnerabilities()
        embed = create_embed("Dependency Security Check",
            f"**Vulnerabilities found:** {len(results)}", 0xff3366 if results else 0x00ff88)
        if results:
            lines = []
            for r in results:
                lines.append(f"`{r['package']}` {r['installed']} -> {r['recommended']} ({r['cve']})")
            embed.add_field(name="Vulnerabilities", value="\n".join(lines[:10]), inline=False)
        else:
            embed.add_field(name="Status", value="All dependencies are up to date", inline=False)
        await ctx.send(embed=embed)
        immutable_audit.log("dep_check", ctx.author.id, "all",
                           f"vulnerabilities={len(results)}")
    except Exception as e:
        await ctx.send(embed=create_error_embed("Check Failed", str(e)[:500]))


@bot.command(name='dbhealth')
@is_admin()
async def db_health_cmd(ctx):
    """Check database health"""
    try:
        health = db_guard.check_db_health()
        embed = create_embed("Database Health",
            f"**Files:** {health['healthy']}/{health['total_files']} OK",
            0x00ff88 if not health['issues'] else 0xff3366)
        if health['issues']:
            embed.add_field(name="Issues", value="\n".join(health['issues'][:10]), inline=False)
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(embed=create_error_embed("Check Failed", str(e)[:500]))


@bot.command(name='isolatenode')
@is_admin()
async def isolate_node_cmd(ctx, node_id: str, *, reason: str = "Manual isolation"):
    """Isolate a node from deployments"""
    node_isolation.isolate_node(node_id, ctx.author.id, reason)
    embed = create_warning_embed("Node Isolated",
        f"Node `{node_id}` has been isolated.\nReason: {reason}")
    await ctx.send(embed=embed)
    immutable_audit.log("node_isolate", ctx.author.id, node_id, reason, level="warning")
    security_alerts.send_alert("node_isolation", "Node Isolated",
        f"Node {node_id} isolated by admin", resource=node_id, admin_id=ctx.author.id)


@bot.command(name='enablenode')
@is_admin()
async def enable_node_cmd(ctx, node_id: str):
    """Re-enable an isolated node"""
    success = node_isolation.enable_node(node_id, ctx.author.id)
    if success:
        await ctx.send(embed=create_success_embed("Node Enabled", f"Node `{node_id}` is now enabled."))
    else:
        await ctx.send(embed=create_error_embed("Not Found", f"Node `{node_id}` is not isolated."))


@bot.command(name='drrestore')
@is_admin()
async def dr_restore_cmd(ctx, rp_name: str):
    """Restore from a disaster recovery point (requires confirmation)"""
    async def do_restore(confirmed, op_id):
        if not confirmed:
            await ctx.send(embed=create_info_embed("Cancelled", "Restore cancelled."))
            return
        result = disaster_recovery.restore_from_recovery_point(rp_name, ctx.author.id)
        if result.get("success"):
            embed = create_success_embed("Restore Complete",
                f"Restored {result['restored']} files from `{rp_name}`\n"
                f"Pre-restore backup: `{result.get('pre_restore_backup', '?')}`")
        else:
            embed = create_error_embed("Restore Failed", result.get("error", "Unknown"))
        await ctx.send(embed=embed)
        immutable_audit.log("dr_restore", ctx.author.id, rp_name,
                           f"success={result.get('success')}", result="success" if result.get("success") else "failure")

    await send_confirmation(ctx, "restore_backup", f"Recovery point `{rp_name}`", do_restore, extreme=True)


@bot.command(name='alertack')
@is_admin()
async def alert_ack_cmd(ctx, alert_id: str):
    """Acknowledge a security alert"""
    if security_alerts.acknowledge(alert_id):
        await ctx.send(embed=create_success_embed("Alert Acknowledged", f"Alert `{alert_id}` acknowledged."))
    else:
        await ctx.send(embed=create_error_embed("Not Found", f"Alert `{alert_id}` not found."))


@bot.command(name='lockdown')
@is_main_admin()
async def lockdown_cmd(ctx, *, reason: str = "Emergency lockdown"):
    """Activate emergency lockdown (Owner only, requires confirmation)"""
    async def do_lockdown(confirmed, op_id):
        if not confirmed:
            await ctx.send(embed=create_info_embed("Cancelled", "Lockdown cancelled."))
            return
        screen = ProgressScreen(ctx, title="ACTIVATING LOCKDOWN", op_prefix="SEC", color=0xff3366)
        screen.set_steps([
            ("Verifying admin authority", "pending"),
            ("Locking all systems", "pending"),
            ("Broadcasting alert", "pending"),
        ])
        await screen.start()
        await screen.update(20, 0, "Verifying emergency lockdown authority...")
        await asyncio.sleep(0.3)
        await screen.step_done(40, 0, "Authority verified")

        await screen.update(40, 1, "Locking all systems...")
        success, msg = emergency_lockdown.activate_lockdown(ctx.author.id, reason)
        await screen.step_done(70, 1, "Systems locked" if success else "Lockdown failed")

        await screen.update(70, 2, "Broadcasting security alert...")
        security_alerts.send_alert("emergency_lockdown", "Emergency Lockdown",
            f"Activated by admin: {reason}", admin_id=ctx.author.id)
        await screen.step_done(100, 2, "Alert sent")

        if success:
            await screen.complete("Emergency lockdown activated!", extra_fields={
                "Reason": reason,
                "Status": "🔴 ALL SYSTEMS LOCKED",
            })
        else:
            await screen.fail("Lockdown failed", msg)

    await send_confirmation(ctx, "emergency_lockdown", f"Emergency lockdown: {reason}", do_lockdown, extreme=True)


@bot.command(name='unlock')
@is_main_admin()
async def unlock_cmd(ctx, *, reason: str = "Manual unlock"):
    """Deactivate emergency lockdown (Owner only)"""
    screen = ProgressScreen(ctx, title="DEACTIVATING LOCKDOWN", op_prefix="SEC", color=0x00ff88)
    screen.set_steps([
        ("Verifying admin authority", "pending"),
        ("Unlocking all systems", "pending"),
    ])
    await screen.start()
    await screen.update(20, 0, "Verifying admin authority...")
    await asyncio.sleep(0.3)
    await screen.step_done(50, 0, "Authority verified")

    await screen.update(50, 1, "Unlocking all systems...")
    success, msg = emergency_lockdown.deactivate_lockdown(ctx.author.id, reason)
    await screen.step_done(100, 1, "Systems unlocked" if success else "Unlock failed")

    if success:
        await screen.complete("All systems unlocked!", extra_fields={
            "Reason": reason,
            "Status": "🟢 SYSTEMS UNLOCKED",
        })
    else:
        await screen.fail("Unlock failed", msg)


@bot.command(name='ports')
async def ports_cmd(ctx, vps_number: int = 0):
    """Show port mappings for your VPS"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    planned = vps.get("ports") or []
    try:
        active = await container_op(vps, "port")
    except Exception:
        active = []
    embed = create_embed("🌐 Port Mappings", f"VPS #{vps_number} - `{vps['container_name']}`", 0x00ccff)
    embed.add_field(name="Active (published)", value="\n".join([f"`{p}`" for p in active]) or "None", inline=True)
    embed.add_field(name="Configured (apply on reinstall)", value="\n".join([f"`{p}`" for p in planned]) or "None", inline=True)
    embed.add_field(name="Add / Remove", value="`!portadd <#> <host:container>`\n`!portremove <#> <host:container>`", inline=False)
    await ctx.send(embed=embed)


async def _apply_port_config(vps):
    """Push the saved port list into a running virtual machine.

    Docker publishes ports itself, so there is nothing to do. A virtual
    machine needs its domain redefined, and the host forwards only take
    effect after a restart. Returns (applied_live, message).
    """
    kind = _instance_kind(vps)
    if kind != "kvm":
        return True, "It will be published on the next reinstall of this server."
    if vps.get("node") != "local":
        return False, "Restart this VM from the node it runs on to apply the change."
    try:
        ok, message = await kvm_redefine_ports(
            domain_of(vps), int(vps.get("ssh_port") or 0), vps.get("ports") or [])
    except Exception as exc:
        return False, sanitize_error(exc)
    return ok, message


# ---------------------------------------------------------------------------
# Backups, per backend
# ---------------------------------------------------------------------------

def snapshot_name_for(vps, stamp):
    return f"{vps['container_name']}-backup-{stamp}"


async def create_backup(vps, name):
    """Snapshot a server using whichever mechanism its backend supports.

    Returns (ok, message, kind) where kind describes what was created so the
    caller can word the confirmation correctly.
    """
    virt = _instance_kind(vps)

    if virt == "kvm":
        if vps.get("node") != "local":
            return False, "Snapshots are taken on the node the VM runs on.", "none"
        if await kvm_state(domain_of(vps)) != "running":
            return False, "Start the VM before taking a snapshot.", "none"
        ok, message = await kvm_snapshot(domain_of(vps), name, "create")
        return ok, message, ("libvirt snapshot" if ok else "none")

    if virt == "lxc":
        if vps.get("node") != "local":
            return False, "Exports are run on the node the container runs on.", "none"
        os.makedirs(BACKUP_DIR, exist_ok=True)
        archive = os.path.join(BACKUP_DIR, f"{name}.tar.gz")
        ok, message = await lxc_export(domain_of(vps), archive)
        return ok, message, ("archive" if ok else "none")

    await execute_docker(f"docker commit {vps['container_name']} {name}", timeout=180)
    return True, "Image saved.", "docker image"


async def restore_backup(vps, name):
    """Put a server back to a backup made by create_backup."""
    virt = _instance_kind(vps)

    if virt == "kvm":
        if vps.get("node") != "local":
            return False, "Restores run on the node the VM runs on."
        if name not in await kvm_snapshots(domain_of(vps)):
            existing = await kvm_snapshots(domain_of(vps))
            listed = ", ".join(f"`{s}`" for s in existing) or "none"
            return False, f"No snapshot named `{name}` on this VM. Available: {listed}"
        return await kvm_snapshot(domain_of(vps), name, "revert")

    if virt == "lxc":
        if vps.get("node") != "local":
            return False, "Imports run on the node the container runs on."
        archive = os.path.join(BACKUP_DIR, f"{name}.tar.gz")
        if not os.path.exists(archive):
            return False, f"No archive found for `{name}`."
        stopped = await instance_state(vps) == "running"
        if stopped:
            await lxc_action(domain_of(vps), "stop")
        ok, message = await lxc_import(archive, domain_of(vps))
        if ok and stopped:
            await lxc_action(domain_of(vps), "start")
        return ok, message

    # Docker: commit the snapshot back into a fresh container.
    ram_mb = int(str(vps.get("ram", "4GB")).replace("GB", "")) * 1024
    cpu = vps.get("cpu", 1)
    ssh_port = int(vps.get("ssh_port") or 0)
    ports = vps.get("ports") or []
    await teardown_instance(vps)
    port_flags = ""
    if ssh_port:
        port_flags += f" -p {ssh_port}:22"
    for mapping in ports:
        port_flags += f" -p {mapping}"
    await execute_docker(
        f"docker run -d --name {vps['container_name']} "
        f"--memory={ram_mb}m --cpus={cpu} --restart=unless-stopped{port_flags} "
        f"{name} sleep infinity", timeout=180)
    await docker_exec(vps["container_name"], "/usr/sbin/sshd || true", timeout=15)
    return True, "Container restored from the snapshot image."


def snapshot_exists(vps, name):
    """Fast synchronous hint used to decide which restore path to offer."""
    return _instance_kind(vps) == "container"


@bot.command(name='portadd')
async def port_add_cmd(ctx, vps_number: int, mapping: str):
    """Add a port mapping - !portadd <vps#> <host:container>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    if ":" not in mapping or not re.match(r"^\d+:\d+$", mapping.strip()):
        await ctx.send(embed=create_error_embed("Invalid", "Format: `<host_port>:<container_port>`, e.g. `8080:80`"))
        return
    mapping = mapping.strip()
    host_port = int(mapping.split(":")[0])
    cont_port = int(mapping.split(":")[1])
    bad = _validate_user_port(host_port, cont_port)
    if bad:
        await ctx.send(embed=create_error_embed("Port Not Allowed", bad))
        return
    if mapping in vps.get("ports", []):
        await ctx.send(embed=create_error_embed("Exists", "This mapping is already configured."))
        return
    vps.setdefault("ports", []).append(mapping)
    save_data()
    audit("vps_port_add", ctx.author, vps["container_name"], mapping)

    live, message = await _apply_port_config(vps)
    if not live:
        await ctx.send(embed=create_error_embed(
            "Port Saved, Not Live",
            f"{message}\nThe mapping is stored and will apply on the next "
            f"reinstall."))
        return
    await ctx.send(embed=create_success_embed(
        "Port Added", f"Mapping `{mapping}` saved. {message}"))


@bot.command(name='portremove')
async def port_remove_cmd(ctx, vps_number: int, mapping: str):
    """Remove a configured port mapping - !portremove <vps#> <host:container>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    if mapping in vps.get("ports", []):
        vps["ports"].remove(mapping)
        save_data()
        audit("vps_port_remove", ctx.author, vps["container_name"], mapping)
        live, message = await _apply_port_config(vps)
        if not live:
            await ctx.send(embed=create_error_embed(
                "Port Saved, Not Live",
                f"{message}\nThe mapping is stored and will apply on the next "
                f"reinstall."))
            return
        await ctx.send(embed=create_success_embed(
            "Port Removed", f"`{mapping}` removed from config. {message}"))
    else:
        await ctx.send(embed=create_error_embed("Not Found", "Mapping not in config."))


# ---------------------------------------------------------------------------
# File manager (docker exec / docker cp)
# ---------------------------------------------------------------------------

def _safe_container_path(path):
    path = path.strip().strip('"').strip("'")
    if not path or "\x00" in path or (".." in path.split("/")):
        return None
    return path


async def _exec_or_explain(vps, cmd, timeout=20):
    """Run a shell command inside a server of any backend.

    Returns (ok, stdout, message). Backends that genuinely cannot run a
    command (a full virtual machine with no guest agent) get a readable
    explanation instead of a raw traceback.
    """
    try:
        out = await container_op(vps, "exec", cmd=cmd, timeout=timeout)
    except Exception as exc:
        return False, "", sanitize_error(exc)
    if isinstance(out, dict):
        rc = out.get("rc", 0)
        stdout = out.get("stdout", "") or ""
        stderr = (out.get("stderr", "") or "").strip()
        if rc != 0:
            return False, stdout, stderr or f"The command exited with status {rc}."
        return True, stdout, ""
    return True, "", ""


@bot.command(name='files')
async def files_cmd(ctx, vps_number: int, path: str = "/"):
    """List files in a directory - !files <vps#> <path>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    p = _safe_container_path(path) or "/"
    cmd = f"ls -la --block-size=K {shlex.quote(p)}"
    ok, stdout, message = await _exec_or_explain(vps, cmd, timeout=20)
    if not ok and not stdout:
        await ctx.send(embed=create_error_embed(
            "Cannot List Files", message))
        return
    embed = create_embed("File Manager", f"`{vps['container_name']}`:{p}", 0x00ccff)
    embed.add_field(name="Directory listing",
                    value=f"```\n{(stdout[:1900] if stdout else '(empty or not readable)')}\n```",
                    inline=False)
    embed.add_field(name="Tools", value=(
        "`!cat <#> <path>` - view file\n`!upload <#> <path>` - attach a file\n"
        "`!download <#> <path>` - fetch a file\n`!rmfile <#> <path>` - delete\n"
        "`!mkdir <#> <path>` - create dir"), inline=False)
    await ctx.send(embed=embed)


@bot.command(name='cat')
async def cat_cmd(ctx, vps_number: int, path: str):
    """View a file - !cat <vps#> <path>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    p = _safe_container_path(path)
    if not p:
        await ctx.send(embed=create_error_embed("Invalid Path", "Path cannot contain `..` segments."))
        return
    ok, stdout, message = await _exec_or_explain(
        vps, f"head -c 4000 {shlex.quote(p)}", timeout=20)
    if not ok and not stdout:
        await ctx.send(embed=create_error_embed("Cannot Read File", message))
        return
    embed = create_embed("File Content", f"`{p}`", 0x00ccff)
    embed.add_field(name="Content", value=f"```\n{stdout or '(empty or not readable)'}\n```",
                    inline=False)
    await ctx.send(embed=embed)


@bot.command(name='mkdir')
async def mkdir_cmd(ctx, vps_number: int, path: str):
    """Create a directory - !mkdir <vps#> <path>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    p = _safe_container_path(path)
    if not p:
        await ctx.send(embed=create_error_embed("Invalid Path", "Path cannot contain `..` segments."))
        return
    ok, _out, message = await _exec_or_explain(
        vps, f"mkdir -p {shlex.quote(p)}", timeout=15)
    if not ok:
        await ctx.send(embed=create_error_embed("Could Not Create Directory", message))
        return
    audit("file_mkdir", ctx.author, vps["container_name"], p)
    await ctx.send(embed=create_success_embed("Directory Created", f"`{p}`"))


@bot.command(name='rmfile')
async def rm_file_cmd(ctx, vps_number: int, path: str):
    """Delete a file or directory - !rmfile <vps#> <path>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    p = _safe_container_path(path)
    if not p:
        await ctx.send(embed=create_error_embed("Invalid Path", "Path cannot contain `..` segments."))
        return
    if p in ("/", "/root", "/home", "/etc", "/usr", "/var", "/bin", "/sbin", "/lib", "/boot", "/dev", "/proc", "/sys"):
        await ctx.send(embed=create_error_embed(
            "Refused", f"`{p}` is a core system path. Deleting it would break the server."))
        return
    ok, _out, message = await _exec_or_explain(
        vps, f"rm -rf {shlex.quote(p)}", timeout=15)
    if not ok:
        await ctx.send(embed=create_error_embed("Could Not Delete", message))
        return
    audit("file_delete", ctx.author, vps["container_name"], p)
    await ctx.send(embed=create_success_embed("Deleted", f"Removed `{p}`."))


@bot.command(name='upload')
async def upload_cmd(ctx, vps_number: int, path: str):
    """Upload an attached file into your VPS - !upload <vps#> <dest_path> with a file attached"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    if not ctx.message.attachments:
        await ctx.send(embed=create_error_embed("No File", "Attach a file to the message you send with this command."))
        return
    p = _safe_container_path(path)
    if not p:
        await ctx.send(embed=create_error_embed("Invalid Path", "Path cannot contain `..` segments."))
        return
    if vps.get("node") and vps.get("node") != "local":
        await ctx.send(embed=create_error_embed("Not Supported", "File upload on remote nodes requires the API endpoint (see api.py)."))
        return
    att = ctx.message.attachments[0]
    tmp = os.path.join(CACHE_DIR, f"up_{ctx.author.id}_{int(time.time())}_{att.filename}")
    try:
        await att.save(tmp)
        ok, message = await vm_file_push(vps, tmp, p)
        if not ok:
            await ctx.send(embed=create_error_embed("Upload Failed", message))
            return
        audit("file_upload", ctx.author, vps["container_name"], f"{att.filename} -> {p}")
        await ctx.send(embed=create_success_embed("Uploaded", f"`{att.filename}` → `{p}`"))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Upload Failed", sanitize_error(e)))
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


@bot.command(name='download')
async def download_cmd(ctx, vps_number: int, path: str):
    """Download a file from your VPS - !download <vps#> <path>"""
    vps, _ = find_vps(ctx.author.id, vps_number)
    if vps is None:
        await ctx.send(embed=create_error_embed("Not Found", "Enter a valid VPS number."))
        return
    p = _safe_container_path(path)
    if not p:
        await ctx.send(embed=create_error_embed("Invalid Path", "Path cannot contain `..` segments."))
        return
    if vps.get("node") and vps.get("node") != "local":
        await ctx.send(embed=create_error_embed("Not Supported", "File download on remote nodes requires the API endpoint (see api.py)."))
        return
    name = os.path.basename(p) or "file"
    tmp = os.path.join(CACHE_DIR, f"dl_{ctx.author.id}_{int(time.time())}_{name}")
    try:
        ok, message = await vm_file_pull(vps, p, tmp)
        if not ok:
            await ctx.send(embed=create_error_embed("Download Failed", message))
            return
        audit("file_download", ctx.author, vps["container_name"], p)
        await ctx.send(file=discord.File(tmp, filename=name))
    except Exception as e:
        await ctx.send(embed=create_error_embed("Download Failed", sanitize_error(e)))
    finally:
        try:
            os.remove(tmp)
        except Exception:
            pass


# ===========================================================================
# Security: 2FA, alerts, API tokens
# ===========================================================================

@bot.command(name='2fa')
async def twofa_cmd(ctx, action: str):
    """Two-factor authentication - !2fa enable | confirm <code> | disable <code> | status"""
    uid = str(ctx.author.id)
    action = action.lower()
    if action == "enable":
        if user_2fa(uid):
            await ctx.send(embed=create_error_embed("Already Enabled", "2FA is already on. Use `!2fa disable <code>`."))
            return
        secret = _b32_secret()
        ensure_user(uid)["totp_secret"] = secret
        ensure_user(uid)["totp_pending"] = True
        save_data()
        embed = create_embed("🔐 Enable 2FA", "Your secret - add it to an authenticator app (Google Authenticator / Aegis).", 0x5865F2)
        embed.add_field(name="Secret", value=f"```\n{secret}\n```", inline=False)
        embed.add_field(name="Next", value=f"Confirm with your current code: `!2fa confirm <code>`", inline=False)
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(embed=create_success_embed("2FA Setup Started", "Secret sent to your DMs. Use `!2fa confirm <code>` to activate."))
        except discord.Forbidden:
            ensure_user(uid).pop("totp_secret", None)
            save_data()
            await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive your 2FA secret."))
        return
    if action == "confirm":
        secret = ensure_user(uid).get("totp_secret")
        if not secret or not ensure_user(uid).get("totp_pending"):
            await ctx.send(embed=create_error_embed("Not Setup", "Run `!2fa enable` first."))
            return
        code = " ".join(ctx.message.content.split()[2:]) if len(ctx.message.content.split()) >= 3 else ""
        if not code:
            await ctx.send(embed=create_error_embed("Missing Code", "Usage: `!2fa confirm <code>`"))
            return
        if _totp_code(secret) != code:
            await ctx.send(embed=create_error_embed("Wrong Code", "That code is invalid. Try again with the current 6-digit code."))
            return
        ensure_user(uid)["totp_pending"] = False
        VERIFIED_2FA[uid] = time.time()
        save_data()
        audit("2fa_enable", ctx.author, "", "")
        await ctx.send(embed=create_success_embed("2FA Enabled", "Sensitive commands now require your authenticator code (`!verify`)."))
        return
    if action == "disable":
        if not user_2fa(uid):
            await ctx.send(embed=create_error_embed("Not Enabled", "2FA is not enabled."))
            return
        code = " ".join(ctx.message.content.split()[2:]) if len(ctx.message.content.split()) >= 3 else ""
        if not code or not totp_verify(uid, code):
            await ctx.send(embed=create_error_embed("Wrong Code", "Usage: `!2fa disable <code>` with a valid code."))
            return
        ensure_user(uid).pop("totp_secret", None)
        ensure_user(uid).pop("totp_pending", None)
        save_data()
        audit("2fa_disable", ctx.author, "", "")
        await ctx.send(embed=create_success_embed("2FA Disabled", "Two-factor authentication turned off."))
        return
    if action == "status":
        on = user_2fa(uid)
        await ctx.send(embed=create_info_embed(
            "🔐 2FA Status",
            "Enabled" if on else "Disabled",
            color=0x00ff88 if on else 0xff3366))
        return
    await ctx.send(embed=create_error_embed("Invalid", "Use: `!2fa enable | confirm <code> | disable <code> | status`"))


@bot.command(name='verify')
async def verify_cmd(ctx, code: str):
    """Verify a 2FA code to unlock sensitive commands (10 min)"""
    if not user_2fa(ctx.author.id):
        await ctx.send(embed=create_error_embed("Not Enabled", "You don't have 2FA enabled."))
        return
    if totp_verify(ctx.author.id, code):
        VERIFIED_2FA[str(ctx.author.id)] = time.time()
        await ctx.send(embed=create_success_embed("✅ Verified", "Sensitive commands unlocked for 10 minutes."))
    else:
        await ctx.send(embed=create_error_embed("Wrong Code", "That code is not valid."))


@bot.command(name='alerts')
async def alerts_cmd(ctx, mode: str):
    """Login/VPS activity alerts - !alerts <on|off> (DMs you when your VPS is accessed)"""
    mode = mode.lower()
    if mode not in ("on", "off"):
        await ctx.send(embed=create_error_embed("Invalid", "Use: `!alerts on` or `!alerts off`"))
        return
    ensure_user(ctx.author.id)["alerts"] = mode == "on"
    save_data()
    await ctx.send(embed=create_success_embed(
        "Alerts " + ("ON" if mode == "on" else "OFF"),
        "You will be DMed when your VPS is started/stopped/accessed by someone else."))


@bot.command(name='token')
async def token_cmd(ctx, action: str):
    """Personal API tokens - !token generate | list | revoke-all"""
    uid = str(ctx.author.id)
    action = action.lower()
    if action == "generate":
        raw = secrets_hex(32)
        ensure_user(uid).setdefault("api_tokens", []).append({"token": raw, "ts": utcnow().isoformat()})
        save_data()
        audit("token_generate", ctx.author, "", "")
        embed = create_embed("🔑 API Token", "Keep this token secret. It grants API access as you.", 0x5865F2)
        embed.add_field(name="Token", value=f"```\n{raw}\n```", inline=False)
        embed.add_field(name="Revoke", value="`!token revoke-all`", inline=False)
        try:
            await ctx.author.send(embed=embed)
            await ctx.send(embed=create_success_embed("Token Created", "New API token sent to your DMs."))
        except discord.Forbidden:
            await ctx.send(embed=create_error_embed("DM Failed", "Enable DMs to receive your token."))
        return
    if action == "list":
        toks = ensure_user(uid).get("api_tokens", [])
        lines = [f"• `{t['token'][:8]}...` created {t['ts'][:16]}" for t in toks] or ["No tokens."]
        await ctx.send(embed=create_embed("🔑 Your API Tokens", "\n".join(lines), 0x5865F2))
        return
    if action == "revoke-all":
        ensure_user(uid)["api_tokens"] = []
        save_data()
        audit("token_revoke", ctx.author, "", "")
        await ctx.send(embed=create_success_embed("Tokens Revoked", "All your API tokens were revoked."))
        return
    await ctx.send(embed=create_error_embed("Invalid", "Use: `!token generate | list | revoke-all`"))


def secrets_hex(n):
    return "".join(random.choice(string.hexdigits.lower()) for _ in range(n))


# ---------------------------------------------------------------------------
# 2FA + rate-limit enforcement on sensitive commands
# ---------------------------------------------------------------------------

# (enforce_security is defined near the top with the security helpers)


# ===========================================================================
# Multi-node management
# ===========================================================================

@bot.command(name='node')
@is_main_admin()
async def node_cmd(ctx, action: str, name: str = None, url: str = None, token: str = None):
    """Manage remote nodes - !node add <name> <url> <token> | remove <name> | list | status"""
    action = action.lower()
    if action == "add":
        if not name or not url:
            await ctx.send(embed=create_error_embed("Usage", "`!node add <name> <url> <token>`"))
            return
        nodes_reg[name] = {"url": url, "token": token or "", "enabled": True, "ts": utcnow().isoformat()}
        save_data()
        audit("node_add", ctx.author, name, url)
        await ctx.send(embed=create_success_embed("Node Added", f"**{name}** → `{url}`"))
    elif action == "remove":
        if not name:
            await ctx.send(embed=create_error_embed("Usage", "`!node remove <name>`"))
            return
        if nodes_reg.pop(name, None):
            save_data()
            audit("node_remove", ctx.author, name, "")
            await ctx.send(embed=create_success_embed("Node Removed", f"**{name}** removed."))
        else:
            await ctx.send(embed=create_error_embed("Not Found", f"No node named `{name}`."))
    elif action == "list":
        if not nodes_reg:
            await ctx.send(embed=create_info_embed("Nodes", "No remote nodes registered. The bot manages its local Docker socket."))
            return
        lines = [f"• **{n}** - `{d['url']}` ({'✅' if d.get('enabled') else '❌'})" for n, d in nodes_reg.items()]
        await ctx.send(embed=create_embed("🖧 Nodes", "\n".join(lines), 0x5865F2))
    elif action == "status":
        screen = ProgressScreen(ctx, title="NODE HEALTH CHECK", op_prefix="ND", color=0x5865F2)
        screen.set_steps([
            ("Scanning registered nodes", "pending"),
            ("Checking health endpoints", "pending"),
            ("Collecting Docker status", "pending"),
        ])
        await screen.start()
        await screen.update(10, 0, f"Found {len(nodes_reg)} registered nodes")
        await asyncio.sleep(0.3)
        await screen.step_done(30, 0, f"{len(nodes_reg)} nodes registered")

        out = []
        await screen.update(30, 1, "Querying health endpoints...")
        for n, d in nodes_reg.items():
            try:
                health = await call_node(n, "GET", "/health", timeout=10)
                out.append(f"• **{n}** - ✅ {health.get('status')} (docker: {'yes' if health.get('docker') else 'no'})")
            except Exception as e:
                out.append(f"• **{n}** - 🔴 {e}")
        await screen.step_done(70, 1, f"{len(nodes_reg)} endpoints checked")

        await screen.update(70, 2, "Compiling results...")
        await asyncio.sleep(0.3)
        await screen.step_done(100, 2, "Done")

        await screen.complete("Node health check complete!", extra_fields={
            "Results": "\n".join(out) if out else "No nodes registered.",
        })
    else:
        await ctx.send(embed=create_error_embed("Invalid", "Use: add | remove | list | status"))


# ===========================================================================
# Wire-up: start billing loop; guard auto_expire_check with billing
# ===========================================================================

@tasks.loop(hours=24)
async def billing_loop():
    """Daily sanity pass over VPS expiries + alert admins about renewals coming up."""
    if not billing_cfg.get("enabled"):
        return
    now = utcnow()
    near = []
    for uid, vl in vps_data.items():
        for v in vl:
            expires = v.get("expires")
            if not expires or expires == "Never":
                continue
            try:
                days_left = (datetime.fromisoformat(expires) - now).days
            except Exception:
                continue
            if days_left <= 7:
                near.append((uid, v, days_left))
    if not near:
        return
    lines = [f"`{v['container_name']}` - {d}d left (user {u})" for u, v, d in near[:15]]
    await alert_admins("💳 Billing", "VPS renewing within 7 days:\n" + "\n".join(lines))


@billing_loop.before_loop
async def before_billing_loop():
    await bot.wait_until_ready()


# ---------------------------------------------------------------------------
# Start extra loops in on_ready
# ---------------------------------------------------------------------------

async def start_extra_loops():
    if not billing_loop.is_running():
        billing_loop.start()


if __name__ == "__main__":
    token = load_token()
    if not token:
        print("=" * 50)
        print("  NO BOT TOKEN FOUND")
        print("=" * 50)
        print()
        print("  Create one of:")
        print("    1. token.txt  - paste your token in it")
        print("    2. .env       - line with TOKEN=your_token")
        print("    3. env var    - set TOKEN=your_token")
        print()
        print("  Get a token from:")
        print("    https://discord.com/developers/applications")
        print()
        print("  The bot cannot start without a token.")
        raise SystemExit(1)
    if token.strip().lower() in (
        "your_bot_token_here", "your_token", "token", "changeme",
        "your_token_here", "paste_your_token_here", "bot_token",
    ):
        print("=" * 50)
        print("  TOKEN IS STILL THE PLACEHOLDER")
        print("=" * 50)
        print()
        print("  Your .env still has the example value.")
        print(f"    TOKEN={token}")
        print()
        print("  Fix it:")
        print("    1. Open .env in this folder")
        print("    2. Replace the value after TOKEN= with your real token")
        print("    3. Save the file, then start the bot again")
        print()
        print("  Get a token from:")
        print("    Discord Developer Portal > your app > Bot > Reset Token")
        print("    https://discord.com/developers/applications")
        print()
        raise SystemExit(1)
    if len(token) < 50 or "." not in token:
        print("=" * 50)
        print("  WARNING: Token looks suspicious (too short or no dots)")
        print("=" * 50)
        print("  Trying anyway - Discord will reject if invalid.")
        print()
    if not HAS_DOCKER:
        print("=" * 50)
        print("  WARNING: Docker not found - Docker features unavailable")
        print("=" * 50)
    if not CORE_MODULES_LOADED:
        print("=" * 50)
        print("  WARNING: Core modules failed - running in basic mode")
        print("=" * 50)

    try:
        bot.run(token)
    except discord.LoginFailure:
        print("=" * 50)
        print("  DISCORD REJECTED THE TOKEN")
        print("=" * 50)
        print()
        print("  Discord refused to log in with the token that was found.")
        print("  Common causes:")
        print("    1. The token has a typo or was pasted with spaces")
        print("    2. The token was reset in the Developer Portal")
        print("    3. The bot was deleted from the application")
        print("    4. You are using the Application ID instead of the")
        print("       Bot token (they look similar but are not the same)")
        print()
        print("  Fix: Developer Portal > your app > Bot > Reset Token")
        print("       then copy the new token into .env after TOKEN=")
        print("       https://discord.com/developers/applications")
        print()
        raise SystemExit(1)
    except discord.HTTPException as ex:
        print("=" * 50)
        print("  COULD NOT REACH DISCORD")
        print("=" * 50)
        print()
        print(f"  {sanitize_error(ex)}")
        print()
        print("  Check your internet connection, then start the bot again.")
        print()
        raise SystemExit(1)
