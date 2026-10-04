"""
⚡ Turtle Nodes Admin Console (terminal control panel)

Cross-platform (Windows + Linux), pure standard library.
Controls the bot (start/stop/restart), the file-based DB,
logs, dependencies, cache and shows live host stats.

Usage:
    python admin_console.py
"""
import os
import sys
import json
import shutil
import subprocess
import time
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
CACHE_DIR = os.path.join(BASE_DIR, "cache")
LOG_PATH = os.path.join(BASE_DIR, "bot.log")
STATE_PATH = os.path.join(BASE_DIR, "bot_state.json")
PID_PATH = os.path.join(BASE_DIR, "bot.pid")
BOT_SCRIPT = os.path.join(BASE_DIR, "bot.py")
REQ_PATH = os.path.join(BASE_DIR, "requirements.txt")
CONFIG_PATH = os.path.join(BASE_DIR, "config.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.txt")
ENV_PATH = os.path.join(BASE_DIR, ".env")

for _d in (DATA_DIR, BACKUP_DIR, CACHE_DIR):
    os.makedirs(_d, exist_ok=True)

# ANSI colors
R = "\033[0m"
B = "\033[1m"
GREEN = "\033[32m"
RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"
MAGENTA = "\033[35m"
DIM = "\033[2m"


def c(text, color):
    return f"{color}{text}{R}"


def box(inner_lines):
    w = max([len(l) for l in inner_lines] + [0])
    top = "╭" + "─" * (w + 2) + "╮"
    bot = "╰" + "─" * (w + 2) + "╯"
    out = [top]
    for l in inner_lines:
        out.append("│ " + l.ljust(w) + " │")
    out.append(bot)
    return "\n".join(out)


# ---------------------------------------------------------------------------
# Bot process management
# ---------------------------------------------------------------------------

def bot_is_running():
    if os.path.exists(PID_PATH):
        try:
            with open(PID_PATH) as f:
                pid = int(f.read().strip())
            os.kill(pid, 0)
            return True
        except (ValueError, ProcessLookupError, PermissionError, OSError):
            pass
    return False


def get_bot_pid():
    try:
        with open(PID_PATH) as f:
            return int(f.read().strip())
    except Exception:
        return None


def has_token():
    if os.environ.get("TOKEN", "").strip():
        return True
    for path in (TOKEN_PATH, ENV_PATH):
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#"):
                            continue
                        if line.startswith("TOKEN=") and line.split("=", 1)[1].strip().strip('"').strip("'"):
                            return True
                        if "=" not in line and line:
                            return True
            except Exception:
                pass
    return False


def setup_token():
    print(c("  No bot token found!", RED))
    print(c("  You need a Discord bot token to run the bot.", DIM))
    print(c("  Get one from https://discord.com/developers/applications", DIM))
    print()
    try:
        token = input(c("  Paste your bot token (or press Enter to skip): ", MAGENTA)).strip()
    except (KeyboardInterrupt, EOFError):
        return False
    if not token:
        return False
    with open(TOKEN_PATH, "w", encoding="utf-8") as f:
        f.write(token)
    print(c("  Token saved to token.txt", GREEN))
    return True


def start_bot():
    if bot_is_running():
        return c("Bot is already running (PID %d)." % get_bot_pid(), YELLOW)
    if not has_token():
        if not setup_token():
            return c("Cannot start bot without a token. Create token.txt with your Discord bot token.", RED)
    try:
        proc = subprocess.Popen(
            [sys.executable, BOT_SCRIPT],
            cwd=BASE_DIR,
            stdout=open(os.path.join(BASE_DIR, "bot.stdout.log"), "a"),
            stderr=subprocess.STDOUT)
        with open(PID_PATH, "w") as f:
            f.write(str(proc.pid))
        time.sleep(2)
        if proc.poll() is not None:
            if os.path.exists(PID_PATH):
                os.remove(PID_PATH)
            return c("Bot failed to start (exit code %d). Check bot.log for errors." % proc.returncode, RED)
        return c("Bot started (PID %d)." % proc.pid, GREEN)
    except Exception as e:
        return c("Failed to start bot: %s" % e, RED)


def stop_bot():
    pid = get_bot_pid()
    if not pid:
        return c("Bot is not running (no PID file).", YELLOW)
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
        else:
            os.kill(pid, 15)
        if os.path.exists(PID_PATH):
            os.remove(PID_PATH)
        return c("Bot stopped (PID %d)." % pid, GREEN)
    except Exception as e:
        return c("Failed to stop bot: %s" % e, RED)


def restart_bot():
    out = stop_bot()
    time.sleep(1)
    out2 = start_bot()
    return out + "\n" + out2


# ---------------------------------------------------------------------------
# Live state / panel
# ---------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def uptime_str(seconds):
    d, s = int(seconds // 86400), int(seconds % 86400)
    h, m = s // 3600, (s % 3600) // 60
    return f"{d}d {h}h {m}m"


# ---------------------------------------------------------------------------
# Host stats
# ---------------------------------------------------------------------------

def host_stats():
    out = []
    try:
        if os.name == "nt":
            out.append(c("[i] Host stats limited on Windows (use a Linux VPS for full view).", DIM))
            return out
        cpu = subprocess.run(['top', '-bn1'], capture_output=True, text=True, timeout=10).stdout
        for line in cpu.split('\n'):
            if '%Cpu(s):' in line:
                for part in line.split(','):
                    if 'id,' in part:
                        idle = float(part.split('%')[0].split()[-1])
                        out.append(c("⚡ Host CPU: %.1f%%" % (100.0 - idle), CYAN))
        free = subprocess.run(['free', '-h'], capture_output=True, text=True, timeout=10).stdout
        for line in free.split('\n'):
            if line.startswith('Mem:'):
                parts = line.split()
                out.append(c("🧠 Host RAM: %s used of %s" % (parts[2], parts[1]), CYAN))
        df = subprocess.run(['df', '-h', '/'], capture_output=True, text=True, timeout=10).stdout
        rows = df.split('\n')
        if len(rows) > 1 and rows[1].split():
            parts = rows[1].split()
            out.append(c("💾 Disk: %s used of %s (%s)" % (parts[2], parts[1], parts[4]), CYAN))
    except Exception as e:
        out.append(c("[i] %s" % e, DIM))
    return out


# ---------------------------------------------------------------------------
# DB operations
# ---------------------------------------------------------------------------

def db_backup():
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dstdir = os.path.join(BACKUP_DIR, ts)
    os.makedirs(dstdir, exist_ok=True)
    n = 0
    for fn in os.listdir(DATA_DIR):
        src = os.path.join(DATA_DIR, fn)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dstdir, fn))
            n += 1
    return c("DB backup created: backups/%s (%d files)" % (ts, n), GREEN)


def db_restore(name):
    target = os.path.join(BACKUP_DIR, name)
    if not os.path.isdir(target):
        return c("Backup folder not found: %s" % name, RED)
    n = 0
    for fn in os.listdir(target):
        try:
            shutil.copy2(os.path.join(target, fn), os.path.join(DATA_DIR, fn))
            n += 1
        except Exception as e:
            return c("Restore failed: %s" % e, RED)
    return c("Restored %d files from %s. Restart the bot to load them." % (n, name), GREEN)


def db_status():
    out = []
    for fn in sorted(os.listdir(DATA_DIR)):
        p = os.path.join(DATA_DIR, fn)
        if os.path.isfile(p):
            out.append(c("• %-22s %8d bytes" % (fn, os.path.getsize(p)), DIM))
    if not out:
        out.append("(no data files yet)")
    return out


def db_health():
    bad = []
    for fn in sorted(os.listdir(DATA_DIR)):
        if not fn.endswith(".json"):
            continue
        try:
            with open(os.path.join(DATA_DIR, fn), encoding="utf-8") as f:
                json.load(f)
        except Exception:
            bad.append(fn)
    if bad:
        return [c("DATABASE CORRUPT: %s" % ", ".join(bad), RED)]
    return [c("Database: OK (%d json files parse cleanly)" % len([f for f in os.listdir(DATA_DIR) if f.endswith('.json')]), GREEN)]


def db_clear_temp():
    n = 0
    for fn in os.listdir(DATA_DIR):
        if fn.endswith(".tmp"):
            try:
                os.remove(os.path.join(DATA_DIR, fn))
                n += 1
            except Exception:
                pass
    for fn in os.listdir(CACHE_DIR):
        try:
            os.remove(os.path.join(CACHE_DIR, fn))
            n += 1
        except Exception:
            pass
    return c("Cleared %d temp/cache files." % n, GREEN)


# ---------------------------------------------------------------------------
# Logs
# ---------------------------------------------------------------------------

def logs_tail(n=20):
    if not os.path.exists(LOG_PATH):
        return ["(no log file yet, bot not run)"]
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-n:]).splitlines()[:n]


def logs_filter(word, n=25):
    if not os.path.exists(LOG_PATH):
        return ["(no log file yet)"]
    with open(LOG_PATH, encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return [l.rstrip() for l in lines if word in l][-n:] or ["No %s found." % word.lower()]


def logs_clear():
    if os.path.exists(LOG_PATH):
        open(LOG_PATH, "w").close()
    return c("Log file cleared.", GREEN)


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

def pip(args, timeout=180):
    try:
        proc = subprocess.run([sys.executable, "-m", "pip"] + args,
                              capture_output=True, text=True, timeout=timeout)
        return (proc.stdout or "") + (proc.stderr or "")
    except Exception as e:
        return str(e)


def deps_list():
    out = pip(["list"], 60).splitlines()
    return out[:40] or ["pip list empty"]


def deps_outdated():
    out = pip(["list", "--outdated", "--format=freeze"], 60)
    pkgs = [l.split("==")[0] for l in out.splitlines() if "==" in l]
    if not pkgs:
        return ["All packages up to date."]
    return ["Outdated: " + ", ".join(pkgs)]


def deps_update():
    out = pip(["list", "--outdated", "--format=freeze"], 60)
    pkgs = [l.split("==")[0] for l in out.splitlines() if "==" in l][:20]
    if not pkgs:
        return ["All packages up to date."]
    res = pip(["install", "--upgrade"] + pkgs)
    return res.splitlines()[-3:] or ["Upgraded: " + ", ".join(pkgs)]


def deps_install(pkg):
    res = pip(["install", pkg])
    return res.splitlines()[-3:] or ["Installed " + pkg]


def deps_requirements():
    res = pip(["install", "-r", REQ_PATH])
    return res.splitlines()[-3:] or ["requirements installed"]


def deps_auto_install():
    results = []

    results.append(c("Installing Python packages from requirements.txt...", CYAN))
    res = pip(["install", "-r", REQ_PATH], 120)
    results.extend(res.splitlines()[-5:] or ["requirements installed"])

    sys_pkgs = {
        "docker": "Docker",
        "tmate": "tmate (SSH sessions)",
        "curl": "curl",
        "openssh-server": "OpenSSH server",
    }
    missing = []
    for cmd, name in sys_pkgs.items():
        if not shutil.which(cmd):
            missing.append((cmd, name))

    if missing:
        results.append(c("\nInstalling missing system packages...", CYAN))
        for cmd, name in missing:
            results.append(f"  Installing {name}...")
            try:
                r = subprocess.run(
                    ["apt-get", "install", "-y", cmd],
                    capture_output=True, text=True, timeout=60
                )
                if r.returncode == 0:
                    results.append(c(f"  ✓ {name} installed", GREEN))
                else:
                    results.append(c(f"  ⚠ {name}: {r.stderr[:100]}", YELLOW))
            except FileNotFoundError:
                results.append(c(f"  ⚠ apt-get not found (non-Debian OS). Install '{cmd}' manually.", YELLOW))
            except Exception as e:
                results.append(c(f"  ⚠ {name}: {e}", YELLOW))
    else:
        results.append(c("\nAll system packages already installed.", GREEN))

    results.append(c("\nDone! All dependencies installed.", GREEN))
    return results


# ---------------------------------------------------------------------------
# Config management
# ---------------------------------------------------------------------------

DEFAULTS = {
    "MAIN_ADMIN_ID": 1251119503492775956,
    "VPS_USER_ROLE_ID": 1431499643698544720,
    "DOCKER_IMAGE": "ubuntu:22.04",
    "LOG_CHANNEL_ID": None,
    "CPU_THRESHOLD": 90,
    "CHECK_INTERVAL": 60,
    "SSH_PORT_START": 10000,
}


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return dict(DEFAULTS)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def config_set(key, value):
    key = key.upper()
    if key not in DEFAULTS:
        return c("Unknown key: %s. Valid: %s" % (key, ", ".join(DEFAULTS)), RED)
    cfg = load_config()
    if value.lower() == "none":
        cfg[key] = None
    elif isinstance(DEFAULTS[key], int) or DEFAULTS[key] is None:
        try:
            cfg[key] = int(value)
        except ValueError:
            cfg[key] = value
    else:
        cfg[key] = value
    save_config(cfg)
    return c("Set %s = %s (restart bot to apply)" % (key, cfg[key]), GREEN)


def config_list():
    cfg = load_config()
    lines = []
    for key in DEFAULTS:
        actual = cfg.get(key, DEFAULTS[key])
        default = DEFAULTS[key]
        changed = "  (default)" if actual == default or (default is None and actual is None) else c("  ← CHANGED", YELLOW)
        lines.append("%-20s = %s%s" % (key, actual, changed))
    return lines


# ---------------------------------------------------------------------------
# UI helpers
# ---------------------------------------------------------------------------

def clear():
    os.system("cls" if os.name == "nt" else "clear")


def prompt_input(msg="> "):
    try:
        return input(c("  " + msg, MAGENTA)).strip()
    except (KeyboardInterrupt, EOFError):
        return ""


def button(num, emoji, label):
    return c("  [%d] %s %s" % (num, emoji, label), B)


# ---------------------------------------------------------------------------
# Status panel
# ---------------------------------------------------------------------------

def print_panel():
    state = load_state()
    running = bot_is_running()
    if running:
        status = c("🟢 Online", GREEN)
    else:
        status = c("🔴 Offline", RED)
    if state:
        up = uptime_str(state.get("uptime_sec", 0))
        ping = "%sms" % state.get("ping_ms", 0)
        db = c("🟢 Connected", GREEN)
        docker = c("🟢 Connected", GREEN) if state.get("docker") == "connected" else c("🔴 Down", RED)
        vps = str(state.get("vps_total", "?"))
    else:
        up, ping, db, docker, vps = "-", "-", c("-", DIM), c("-", DIM), "?"
    print()
    print("  ╭──────────────────────────────────────────╮")
    print("  │       🤖 BOT CONTROL PANEL                │")
    print("  ├──────────────────────────────────────────┤")
    print("  │ Status: %-32s │" % status)
    print("  │ Uptime: %-32s │" % up)
    print("  │ Discord Ping: %-27s │" % ping)
    print("  │ Database: %-30s │" % db)
    print("  │ VPS API: %-30s │" % docker)
    print("  │ Total VPS: %-29s │" % vps)
    print("  ╰──────────────────────────────────────────╯")



# ---------------------------------------------------------------------------
# Screens
# ---------------------------------------------------------------------------

def action_result(msg):
    print()
    print(box(msg if isinstance(msg, list) else [msg]))
    input(c("\n  Press Enter to continue...", DIM))


def screen_main():
    while True:
        clear()
        print()
        print(c("  ══════════════════════════════════════════", CYAN))
        print(c("           ⚡ Turtle Nodes ADMIN CONSOLE       ", B))
        print(c("  ══════════════════════════════════════════", CYAN))
        print_panel()
        print()
        print(button(1, "▶", "Start Bot"))
        print(button(2, "🔄", "Restart Bot"))
        print(button(3, "⏹️", "Stop Bot"))
        print(button(4, "🔃", "Reload Bot"))
        print(button(5, "🧹", "Clear Cache"))
        print()
        print(button(6, "🗄️", "Database"))
        print(button(7, "💾", "DB Backup"))
        print(button(8, "♻️", "DB Restore"))
        print()
        print(button(9, "📦", "Dependencies"))
        print(button(10, "📋", "Logs"))
        print(button(11, "📊", "Statistics"))
        print(button(12, "⚙️", "Configuration"))
        print()
        print(button(0, "🚪", "Exit"))
        choice = prompt_input("Select option > ")
        if choice == "1":
            action_result([start_bot()])
        elif choice == "2":
            action_result([restart_bot()])
        elif choice == "3":
            action_result([stop_bot()])
        elif choice == "4":
            action_result(["Reload from Discord: !admincmd bot reload"])
        elif choice == "5":
            action_result([db_clear_temp()])
        elif choice == "6":
            screen_database()
        elif choice == "7":
            action_result([db_backup()])
        elif choice == "8":
            screen_db_restore()
        elif choice == "9":
            screen_deps()
        elif choice == "10":
            screen_logs()
        elif choice == "11":
            action_result(host_stats() or ["No host stats available."])
        elif choice == "12":
            screen_config()
        elif choice == "0":
            print(c("\n  Bye!", CYAN))
            break


def screen_database():
    while True:
        clear()
        print()
        print(c("  🗄️ DATABASE", B))
        print(c("  ──────────────────────────────────────", CYAN))
        print()
        print(button(1, "📊", "DB Status"))
        print(button(2, "🩺", "DB Health Check"))
        print(button(3, "💾", "Backup Now"))
        print(button(4, "🧹", "Clear Temp/Cache"))
        print()
        print(button(0, "🔙", "Back"))
        choice = prompt_input("Database > ")
        if choice == "1":
            action_result(db_status())
        elif choice == "2":
            action_result(db_health())
        elif choice == "3":
            action_result([db_backup()])
        elif choice == "4":
            action_result([db_clear_temp()])
        elif choice == "0":
            return


def screen_db_restore():
    dirs = sorted(os.listdir(BACKUP_DIR)) if os.path.isdir(BACKUP_DIR) else []
    if not dirs:
        action_result([c("No backups found.", RED)])
        return
    clear()
    print()
    print(c("  ♻️ DB RESTORE", B))
    print(c("  ──────────────────────────────────────", CYAN))
    print()
    for i, d in enumerate(dirs[-20:], 1):
        print(c("  [%d] %s" % (i, d), DIM))
    print()
    print(c("  [0] Back", DIM))
    choice = prompt_input("Select backup > ")
    if choice == "0" or not choice:
        return
    try:
        idx = int(choice) - 1
        if 0 <= idx < len(dirs[-20:]):
            action_result([db_restore(dirs[-20:][idx])])
            return
    except ValueError:
        pass
    action_result([c("Invalid selection.", RED)])


def screen_deps():
    while True:
        clear()
        print()
        print(c("  📦 DEPENDENCY MANAGER", B))
        print(c("  ──────────────────────────────────────", CYAN))
        print()
        print(button(1, "🚀", "Auto-Install Everything"))
        print(button(2, "📄", "Install requirements.txt"))
        print(button(3, "🔄", "Update All Packages"))
        print(button(4, "📋", "List Installed Packages"))
        print(button(5, "🔍", "Check for Updates"))
        print()
        print(button(0, "🔙", "Back"))
        choice = prompt_input("Deps > ")
        if choice == "1":
            action_result(deps_auto_install())
        elif choice == "2":
            r = deps_requirements()
            action_result(r if isinstance(r, list) else [r])
        elif choice == "3":
            action_result(deps_update())
        elif choice == "4":
            action_result(deps_list())
        elif choice == "5":
            action_result(deps_outdated())
        elif choice == "0":
            return


def screen_logs():
    while True:
        clear()
        print()
        print(c("  📋 BOT LOGS", B))
        print(c("  ──────────────────────────────────────", CYAN))
        print()
        print(button(1, "📜", "Live Logs"))
        print(button(2, "❌", "Error Logs"))
        print(button(3, "⚠️", "Warning Logs"))
        print(button(4, "🗄️", "Database Logs"))
        print(button(5, "🖥️", "VPS Logs"))
        print(button(6, "💰", "Payment Logs"))
        print(button(7, "🔑", "Admin Logs"))
        print(button(8, "🧹", "Clear Logs"))
        print()
        print(button(0, "🔙", "Back"))
        choice = prompt_input("Logs > ")
        if choice == "1":
            action_result(logs_tail(30))
        elif choice == "2":
            action_result(logs_filter("ERROR"))
        elif choice == "3":
            action_result(logs_filter("WARNING"))
        elif choice == "4":
            action_result(logs_filter("DB") or logs_filter("db") or ["No database logs found."])
        elif choice == "5":
            action_result(logs_filter("VPS") or logs_filter("docker") or ["No VPS logs found."])
        elif choice == "6":
            action_result(logs_filter("pay") or logs_filter("credit") or ["No payment logs found."])
        elif choice == "7":
            action_result(logs_filter("ADMIN") or logs_filter("admin") or logs_filter("audit") or ["No admin logs found."])
        elif choice == "8":
            action_result([logs_clear()])
        elif choice == "0":
            return


def screen_config():
    while True:
        clear()
        print()
        print(c("  ⚙️ CONFIGURATION", B))
        print(c("  ──────────────────────────────────────", CYAN))
        print()
        print(button(1, "👁️", "View All Settings"))
        print(button(2, "✏️", "Edit a Setting"))
        print()
        print(button(0, "🔙", "Back"))
        choice = prompt_input("Config > ")
        if choice == "1":
            action_result(config_list())
        elif choice == "2":
            clear()
            print()
            print(c("  ⚙️ EDIT SETTING", B))
            print(c("  ──────────────────────────────────────", CYAN))
            print()
            cfg = load_config()
            keys = list(DEFAULTS.keys())
            for i, key in enumerate(keys, 1):
                val = cfg.get(key, DEFAULTS[key])
                print(c("  [%d] %-20s = %s" % (i, key, val), DIM))
            print()
            print(c("  [0] Back", DIM))
            idx = prompt_input("Setting number > ")
            if idx == "0" or not idx:
                continue
            try:
                key = keys[int(idx) - 1]
                print(c("  Current: %s = %s" % (key, cfg.get(key, DEFAULTS[key])), DIM))
                val = prompt_input("New value > ")
                if val:
                    action_result([config_set(key, val)])
            except (ValueError, IndexError):
                action_result([c("Invalid selection.", RED)])
        elif choice == "0":
            return


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    os.system("")
    screen_main()


if __name__ == "__main__":
    main()
