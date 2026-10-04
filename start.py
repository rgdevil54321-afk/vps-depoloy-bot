#!/usr/bin/env python3
"""
Turtle Nodes - Cross-platform launcher
Works on Windows, Linux, and macOS.

Usage:
    python start.py

On first run it will create the required folders and generate .env
from .env.example if it does not exist yet.
"""

import os
import shutil
import subprocess
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def log(msg):
    print(msg, flush=True)


def ensure_dirs():
    for name in ("data", "backups", "cache"):
        path = os.path.join(BASE_DIR, name)
        try:
            os.makedirs(path, exist_ok=True)
        except Exception as ex:
            log(f"[X] Could not create {name}/: {ex}")
            return False
    return True


def ensure_env():
    env_path = os.path.join(BASE_DIR, ".env")
    example_path = os.path.join(BASE_DIR, ".env.example")

    if os.path.isfile(env_path):
        return True

    if os.path.isfile(example_path):
        try:
            shutil.copyfile(example_path, env_path)
        except Exception as ex:
            log(f"[X] Could not create .env: {ex}")
            return False
        log("[!] Created .env from .env.example")
        log("[!] Open .env and put your bot token in TOKEN=")
        log("[!] Then run this script again.")
        return False

    log("[X] No .env and no .env.example found.")
    log("[!] Create a .env file with this line:")
    log("    TOKEN=your_bot_token_here")
    return False


def check_deps():
    missing = []
    for mod, pkg in (("discord", "discord.py"), ("psutil", "psutil"), ("dotenv", "python-dotenv")):
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)

    if missing:
        log(f"[X] Missing package(s): {', '.join(missing)}")
        log("[!] Install them with:")
        log("    pip install -r requirements.txt")
        return False
    return True


def check_docker():
    exe = shutil.which("docker")
    if not exe:
        log("[!] Docker was not found on PATH.")
        log("[!] Docker is one of three ways Turtle Nodes can provision servers.")
        log("[!] Install it, or install Incus (LXC) or libvirt+QEMU (KVM) instead.")
        return False

    try:
        out = subprocess.run(
            [exe, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0 and out.stdout.strip():
            log(f"[*] Docker daemon reachable (server {out.stdout.strip()})")
        else:
            err = (out.stderr or "").strip().splitlines()
            detail = err[-1] if err else "unknown error"
            log(f"[!] Docker CLI found but the daemon is not responding.")
            log(f"[!] {detail}")
            log("[!] Start Docker (or dockerd) and try again.")
            return False
    except Exception as ex:
        log(f"[!] Could not query Docker: {ex}")
        return False
    return True


def report_hypervisors():
    """Tell the operator which provisioning backends this host supports."""
    log("")
    log("[*] Checking virtualization backends...")

    if shutil.which("docker"):
        log("[*]   Docker    - available (containers)")
    else:
        log("[ ]   Docker    - not installed")

    backend = ""
    if shutil.which("incus"):
        backend = "Incus"
    elif shutil.which("lxc-create"):
        backend = "LXD"
    if backend:
        log(f"[*]   LXC       - available ({backend})")
    else:
        log("[ ]   LXC       - not installed (apt install incus)")

    missing_kvm = []
    for tool in ("virsh", "virt-install", "qemu-img"):
        if not shutil.which(tool):
            missing_kvm.append(tool)
    has_kvm_dev = os.path.exists("/dev/kvm")
    if not missing_kvm and has_kvm_dev:
        log("[*]   KVM       - available (full virtual machines)")
    else:
        log("[ ]   KVM       - not available")
        if not has_kvm_dev:
            log("[ ]     /dev/kvm missing. Enable virtualization in BIOS, or")
            log("[ ]     the host is a VM without nested virt.")
        if missing_kvm:
            log(f"[ ]     missing: {', '.join(missing_kvm)}")
            log("[ ]     apt install libvirt-clients qemu-kvm cloud-image-utils")

    if not any(shutil.which(t) for t in ("docker", "incus", "lxc-create", "virsh")):
        log("")
        log("[!] No provisioning backend found. The bot cannot create servers.")
    log("")


def pick_python():
    """Pick the interpreter used to launch the bot."""
    if os.name == "nt":
        exe = shutil.which("py") and [shutil.which("py"), "-3"] or None
        if exe:
            return exe
        return [sys.executable]
    exe = shutil.which("python3") or shutil.which("python")
    return [exe] if exe else [sys.executable]


def main():
    os.chdir(BASE_DIR)
    log("=" * 52)
    log("  Turtle Nodes  |  Made By Tired MC")
    log("=" * 52)

    if not ensure_dirs():
        return 1

    if not ensure_env():
        return 1

    if not check_deps():
        return 1

    check_docker()
    report_hypervisors()

    cmd = pick_python() + ["-u", "bot.py"]
    log(f"[*] Starting Turtle Nodes Bot...")
    log(f"[*] {' '.join(cmd)}")
    log("=" * 52)
    log("")

    try:
        return subprocess.call(cmd, cwd=BASE_DIR)
    except KeyboardInterrupt:
        log("")
        log("[*] Shutting down.")
        return 0


if __name__ == "__main__":
    sys.exit(main())