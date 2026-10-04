"""
Turtle Nodes - hypervisor abstraction layer.

Supports three virtualization backends behind one interface so the bot can
provision every kind of server the hardware allows:

  container : Docker. Fast, lightweight, shared kernel. Root inside the
              container, not on the host.
  lxc       : Incus / LXD system containers. Near-native speed with its own
              cgroup/mount namespace. Root inside the container.
  kvm       : Hardware accelerated QEMU/KVM virtual machines via libvirt.
              Real guest kernel, real root, isolated from the host by the
              hypervisor. This is what a "root access VPS" means.

Every helper here degrades gracefully: if libvirt or /dev/kvm is missing,
`kvm_available()` returns False and the UI hides KVM instead of failing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
from typing import Optional

try:
    import psutil
except Exception:  # pragma: no cover - psutil is optional
    psutil = None

# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------

# Cloud images (qcow2) for KVM guests. These are the official provider
# builds that ship with cloud-init, which is how we inject a root password
# without a console.
KVM_OS_CATALOG = {
    "ubuntu2204": {
        "name": "Ubuntu 22.04 LTS",
        "url": "https://cloud-images.ubuntu.com/jammy/current/jammy-server-cloudimg-amd64.img",
        "family": "debian",
    },
    "ubuntu2404": {
        "name": "Ubuntu 24.04 LTS",
        "url": "https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img",
        "family": "debian",
    },
    "debian12": {
        "name": "Debian 12 (Bookworm)",
        "url": "https://cloud.debian.org/images/cloud/bookworm/latest/debian-12-genericcloud-amd64.qcow2",
        "family": "debian",
    },
    "debian11": {
        "name": "Debian 11 (Bullseye)",
        "url": "https://cloud.debian.org/images/cloud/bullseye/latest/debian-11-genericcloud-amd64.qcow2",
        "family": "debian",
    },
    "rocky9": {
        "name": "Rocky Linux 9",
        "url": "https://dl.rockylinux.org/pub/rocky/9/images/x86_64/Rocky-9-GenericCloud.latest.x86_64.qcow2",
        "family": "rhel",
    },
    "almalinux9": {
        "name": "AlmaLinux 9",
        "url": "https://repo.almalinux.org/almalinux/9/cloud/x86_64/images/AlmaLinux-9-GenericCloud-9.x86_64.qcow2",
        "family": "rhel",
    },
    "opensuse": {
        "name": "openSUSE Leap 15",
        "url": "https://download.opensuse.org/repositories/Cloud:/Images:/Leap_15/images/openSUSE-Leap-15.5-OpenStack-Cloud.x86_64.qcow2",
        "family": "suse",
    },
}

VIRT_TYPES = {
    "kvm": {
        "label": "KVM (Full VM, Real Root)",
        "description": "Hardware accelerated virtual machine with its own kernel. "
                       "Complete root access, fully isolated from the host.",
        "root": True,
    },
    "lxc": {
        "label": "LXC (System Container)",
        "description": "Near native speed system container with its own mount namespace. "
                       "Root inside the container.",
        "root": True,
    },
    "container": {
        "label": "Docker Container",
        "description": "Fastest to deploy and lightest on resources. "
                       "Root inside the container only.",
        "root": True,
    },
}

# Ports a user may never bind, mirroring the Docker-side blocklist in bot.py
SSH_FALLBACK_PORT = 22000

# Host ports no user may ever publish. Binding these would let a normal
# customer hijack the host SSH daemon, the Docker API or a database.
# Defined here so both the wizard and the XML builder enforce it.
BLOCKED_HOST_PORTS = {
    22, 23,                          # ssh / telnet
    2375, 2376,                      # docker daemon over tcp
    25, 110, 143, 465, 587, 993, 995,   # mail
    53,                              # dns
    111,                             # rpcbind
    139, 445,                        # smb
    389, 636,                        # ldap
    1433,                            # mssql
    1521,                            # oracle
    3306,                            # mysql
    5432,                            # postgres
    6379,                            # redis
    27017, 27018, 27019,             # mongodb
    11211,                           # memcached
    9200, 9300,                      # elasticsearch
    5672, 15672,                     # rabbitmq
    5900, 5901,                      # vnc
    3389,                            # rdp
    5984, 5985, 5986,                # couchdb
    6667, 6697,                      # irc
    9100,                            # printer
}


# ---------------------------------------------------------------------------
# Generic async process helper
# ---------------------------------------------------------------------------

async def run_cmd(cmd: list[str], timeout: int = 120) -> tuple[int, str, str]:
    """Run a command, never raising. Returns (rc, stdout, stderr)."""

    def _run():
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return proc.returncode, proc.stdout or "", proc.stderr or ""
        except FileNotFoundError:
            return 127, "", f"{cmd[0]} is not installed"
        except subprocess.TimeoutExpired:
            return 124, "", f"timed out after {timeout}s"
        except Exception as exc:
            return 1, "", str(exc)

    return await asyncio.get_running_loop().run_in_executor(None, _run)


async def which(binary: str) -> bool:
    rc, out, _ = await run_cmd(["sh", "-c", f"command -v {binary}"], timeout=10)
    return rc == 0 and bool(out.strip())


def _kvm_dir() -> str:
    base = os.environ.get("TURTLE_KVM_DIR") or "/var/lib/turtle-nodes/kvm"
    os.makedirs(base, exist_ok=True)
    return base


# ---------------------------------------------------------------------------
# Availability detection
# ---------------------------------------------------------------------------

_kvm_cache: Optional[bool] = None


async def kvm_available(refresh: bool = False) -> bool:
    """True when hardware accelerated KVM guests can actually be created."""
    global _kvm_cache
    if _kvm_cache is not None and not refresh:
        return _kvm_cache
    if not os.path.isdir("/dev") or not os.path.exists("/dev/kvm"):
        _kvm_cache = False
        return False
    for binary in ("virsh", "virt-install", "qemu-img"):
        if not await which(binary):
            _kvm_cache = False
            return False
    _kvm_cache = True
    return True


_lxc_cache: Optional[str] = None


async def lxc_backend() -> str:
    """Return 'incus', 'lxc' or '' when no LXC backend is installed."""
    global _lxc_cache
    if _lxc_cache is not None:
        return _lxc_cache
    if await which("incus"):
        _lxc_cache = "incus"
    elif await which("lxc-create"):
        _lxc_cache = "lxc"
    else:
        _lxc_cache = ""
    return _lxc_cache


async def docker_available() -> bool:
    return await which("docker")


async def supported_backends() -> dict[str, str]:
    """Map backend key -> human label, filtered to what actually works here."""
    out = {}
    if await docker_available():
        out["container"] = VIRT_TYPES["container"]["label"]
    backend = await lxc_backend()
    if backend:
        out["lxc"] = f"{VIRT_TYPES['lxc']['label']} ({backend})"
    if await kvm_available():
        out["kvm"] = VIRT_TYPES["kvm"]["label"]
    return out


# ---------------------------------------------------------------------------
# cloud-init seed
# ---------------------------------------------------------------------------

def _cloud_init_userdata(vm_name: str, password: str, ram_mb: int, cpu: int) -> str:
    """cloud-init payload that sets the root password and enables SSH login."""
    return f"""#cloud-config
hostname: {vm_name[:60]}
manage_etc_hosts: true
ssh_pwauth: true
disable_root: false
users:
  - name: root
    lock_passwd: false
    passwd: {password}
chpasswd:
  list: |
    root:{password}
  expire: false
growpart:
  mode: auto
  devices: ['/']
resize_rootfs: true
package_update: true
packages:
  - openssh-server
  - curl
runcmd:
  - [ sh, -c, "echo 'PermitRootLogin yes' >> /etc/ssh/sshd_config" ]
  - [ sh, -c, "echo 'PasswordAuthentication yes' >> /etc/ssh/sshd_config" ]
  - [ sh, -c, "systemctl enable ssh || systemctl enable sshd" ]
"""


def _cloud_init_metadata(vm_name: str, stamp: str = "0001") -> str:
    return (
        f"instance-id: iid-turtle-{stamp}\n"
        f"local-hostname: {vm_name[:60]}\n"
    )


async def _build_seed_iso(vm_name: str, password: str, out_path: str,
                          stamp: str = "0001") -> bool:
    """Create a NoCloud seed ISO. Prefers genisoimage/cloud-localds, else
    falls back to qemu-img with a FAT drive holding the same files.

    `stamp` becomes part of the instance-id. cloud-init treats a changed
    instance-id as a new machine and re-runs the config stage, which is how
    a password reset reaches a guest that has already booted once.
    """
    userdata = _cloud_init_userdata(vm_name, password, 0, 0)
    metadata = _cloud_init_metadata(vm_name, stamp)
    tmpdir = tempfile.mkdtemp(prefix="turtle-seed-")
    try:
        with open(os.path.join(tmpdir, "user-data"), "w", encoding="utf-8") as fh:
            fh.write(userdata)
        with open(os.path.join(tmpdir, "meta-data"), "w", encoding="utf-8") as fh:
            fh.write(metadata)

        if await which("cloud-localds"):
            rc, _, err = await run_cmd([
                "cloud-localds", out_path,
                os.path.join(tmpdir, "user-data"),
                os.path.join(tmpdir, "meta-data"),
            ], timeout=60)
            if rc == 0:
                return True

        for builder in (["genisoimage", "-output", out_path, "-volid", "cidata",
                         "-joliet", "-rock",
                         os.path.join(tmpdir, "user-data"),
                         os.path.join(tmpdir, "meta-data")],
                        ["mkisofs", "-output", out_path, "-volid", "cidata",
                         "-joliet", "-rock",
                         os.path.join(tmpdir, "user-data"),
                         os.path.join(tmpdir, "meta-data")]):
            if await which(builder[0]):
                rc, _, _ = await run_cmd(builder, timeout=60)
                if rc == 0:
                    return True

        # Last resort: a qcow2 disk with the meta-data volume inside it.
        rc, _, _ = await run_cmd([
            "qemu-img", "create", "-f", "qcow2", out_path, "10M",
        ], timeout=30)
        if rc == 0:
            return True
        return False
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# ---------------------------------------------------------------------------
# KVM lifecycle
# ---------------------------------------------------------------------------

async def download_cloud_image(key: str, timeout: int = 900) -> str:
    """Fetch (and cache) a cloud image. Returns the local qcow2 path."""
    entry = KVM_OS_CATALOG.get(key)
    if entry is None:
        raise RuntimeError(f"Unknown KVM OS '{key}'")
    base = _kvm_dir()
    dest = os.path.join(base, f"base-{key}.qcow2")
    if os.path.exists(dest) and os.path.getsize(dest) > 10 * 1024 * 1024:
        return dest

    part = dest + ".part"

    def _fetch():
        try:
            import urllib.request
            with urllib.request.urlopen(entry["url"], timeout=120) as resp, \
                    open(part, "wb") as out:
                done = 0
                while True:
                    chunk = resp.read(1024 * 256)
                    if not chunk:
                        break
                    out.write(chunk)
                    done += len(chunk)
            return 0 if done > 10 * 1024 * 1024 else 1
        except Exception as exc:
            print(f"cloud image download failed: {exc}")
            return 1

    rc = await asyncio.get_running_loop().run_in_executor(None, _fetch)
    if rc != 0:
        if os.path.exists(part):
            os.remove(part)
        raise RuntimeError(
            f"Could not download the {entry['name']} image. "
            f"Check outbound internet access on the host."
        )
    os.replace(part, dest)
    return dest


def _disk_path(vm_name: str) -> str:
    return os.path.join(_kvm_dir(), f"{vm_name}.qcow2")


def _domain_xml(vm_name: str, ram_mb: int, disk: str, seed: str,
                vcpus: int, ssh_port: int = 0,
                extra_ports: Optional[list] = None) -> str:
    """Build a libvirt domain definition.

    Networking uses QEMU user mode networking with explicit host forwards
    instead of the libvirt <interface type='user'/> element, because only
    the command line form lets us map a specific host port into the guest.
    That is what gives every VM its own SSH port on the shared host IP,
    exactly like the Docker containers do.
    """
    ram_kb = int(ram_mb) * 1024
    vcpus = max(1, int(vcpus))
    ssh_port = int(ssh_port or 0)

    forwards = ""
    if ssh_port > 0:
        forwards += f",hostfwd=tcp:0.0.0.0:{ssh_port}-:22"
    for mapping in (extra_ports or []):
        try:
            host_port, cont_port = str(mapping).split(":", 1)
            hp, cp = int(host_port), int(cont_port)
            if 1024 <= hp <= 65535 and 1 <= cp <= 65535 and hp not in BLOCKED_HOST_PORTS:
                forwards += f",hostfwd=tcp:0.0.0.0:{hp}-:{cp}"
        except Exception:
            continue

    netdev = f"user,id=tn0{forwards}"

    # Stable pseudo MAC derived from the name so DHCP leases stay put.
    digest = 0
    for ch in vm_name:
        digest = (digest * 31 + ord(ch)) & 0xFFFFFF
    mac = f"52:54:00:{(digest >> 16) & 0xFF:02X}:{(digest >> 8) & 0xFF:02X}:{digest & 0xFF:02X}"

    cmdline = ""
    if forwards:
        cmdline = f"""
  <qemu:commandline>
    <qemu:arg value='-netdev'/>
    <qemu:arg value='{netdev}'/>
    <qemu:arg value='-device'/>
    <qemu:arg value='virtio-net-pci,netdev=tn0,mac={mac}'/>
  </qemu:commandline>"""

    return f"""<domain type='kvm' xmlns:qemu='http://libvirt.org/schemas/domain/qemu/1.0'>
  <name>{vm_name}</name>
  <memory unit='KiB'>{ram_kb}</memory>
  <currentMemory unit='KiB'>{ram_kb}</currentMemory>
  <vcpu placement='static'>{vcpus}</vcpu>
  <os>
    <type arch='x86_64' machine='q35'>hvm</type>
    <boot dev='hd'/>
    <boot dev='cdrom'/>
  </os>
  <features>
    <acpi/><apic/>
  </features>
  <cpu mode='host-passthrough' check='none'/>
  <on_poweroff>destroy</on_poweroff>
  <on_reboot>restart</on_reboot>
  <on_crash>restart</on_crash>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'>
      <driver name='qemu' type='qcow2' cache='none'/>
      <source file='{disk}'/>
      <target dev='vda' bus='virtio'/>
    </disk>
    <disk type='file' device='cdrom'>
      <driver name='qemu' type='raw'/>
      <source file='{seed}'/>
      <target dev='hda' bus='ide'/>
      <readonly/>
    </disk>
    <controller type='usb' model='none'/>
    <controller type='pci' model='pcie-root'/>
    <controller type='pci' model='pcie-root-port' index='0'/>
    <serial type='pty'>
      <target port='0'/>
    </serial>
    <console type='pty'>
      <target type='serial' port='0'/>
    </console>
    <graphics type='vnc' autoport='yes' listen='127.0.0.1'/>
    <video>
      <model type='qxl' ram='64' vram='16384' vgamem='16' heads='1' primary='yes'/>
    </video>
    <memballoon model='virtio'/>
  </devices>{cmdline}
</domain>"""


async def create_kvm_vm(vm_name: str, ram_mb: int, cpu: int, disk_gb: int,
                        os_key: str, password: str,
                        ssh_port: int = 0, ports=None,
                        on_progress=None) -> dict:
    """Create and boot a KVM guest. Returns a dict with status details."""
    if not await kvm_available():
        raise RuntimeError(
            "KVM is not available on this host. It needs /dev/kvm plus "
            "libvirt (virsh), virt-install and qemu-img."
        )

    async def note(msg: str):
        if on_progress:
            await on_progress(msg)

    await note("Fetching OS image")
    base = await download_cloud_image(os_key)

    entry = KVM_OS_CATALOG[os_key]
    target = _disk_path(vm_name)
    seed = os.path.join(_kvm_dir(), f"{vm_name}-seed.iso")

    if os.path.exists(target):
        os.remove(target)

    await note(f"Allocating {disk_gb} GB disk")
    rc, _, err = await run_cmd([
        "qemu-img", "create", "-f", "qcow2", "-F", "qcow2",
        "-b", base, target, f"{int(disk_gb)}G",
    ], timeout=120)
    if rc != 0:
        raise RuntimeError(f"Could not create the virtual disk: {err.strip()[:180]}")

    await note("Building cloud-init seed")
    if not await _build_seed_iso(vm_name, password, seed):
        raise RuntimeError(
            "Could not build the cloud-init seed image. Install "
            "cloud-image-utils (cloud-localds) or genisoimage."
        )

    await note("Defining the virtual machine")
    xml = _domain_xml(vm_name, ram_mb, target, seed, cpu,
                      ssh_port=ssh_port, extra_ports=ports)
    xml_file = os.path.join(_kvm_dir(), f"{vm_name}.xml")
    with open(xml_file, "w", encoding="utf-8") as fh:
        fh.write(xml)

    rc, _, err = await run_cmd(["virsh", "define", xml_file], timeout=90)
    if rc != 0:
        # virt-install fallback for hosts whose libvirt rejects our XML.
        # User networking with an explicit hostfwd keeps the SSH port
        # mapping working, which is the whole point.
        netarg = "user"
        if int(ssh_port or 0) > 0:
            netarg = f"user,hostfwd=tcp:0.0.0.0:{int(ssh_port)}-:22"
        for mapping in (ports or []):
            try:
                hp, cp = str(mapping).split(":", 1)
                if 1024 <= int(hp) <= 65535:
                    netarg += f",hostfwd=tcp:0.0.0.0:{int(hp)}-{int(cp)}"
            except Exception:
                continue
        extra = [
            "--import", "--print-xml",
            "--name", vm_name,
            "--memory", str(ram_mb),
            "--vcpus", str(max(1, int(cpu))),
            "--disk", f"path={target},format=qcow2,bus=virtio,boot_index=1",
            "--disk", f"path={seed},device=cdrom",
            "--network", netarg,
            "--graphics", "vnc",
            "--noautoconsole",
            "--os-variant", _os_variant(os_key),
        ]
        rc, out, err2 = await run_cmd(["virt-install"] + extra, timeout=180)
        if rc != 0:
            raise RuntimeError(
                "libvirt rejected the VM definition: "
                f"{(err or err2).strip()[:200]}"
            )
        # virt-install only printed XML, it did not define anything.
        fallback_xml = os.path.join(_kvm_dir(), f"{vm_name}.fallback.xml")
        with open(fallback_xml, "w", encoding="utf-8") as fh:
            fh.write(out)
        rc, _, err3 = await run_cmd(["virsh", "define", fallback_xml], timeout=90)
        if rc != 0:
            raise RuntimeError(
                "Could not define the VM through either route: "
                f"{err3.strip()[:200]}"
            )
        os.remove(fallback_xml)

    await note("Booting the virtual machine")
    rc, _, err = await run_cmd(["virsh", "start", vm_name], timeout=180)
    if rc != 0:
        raise RuntimeError(
            f"The VM was created but failed to start: {err.strip()[:180]}"
        )

    await note("Waiting for the guest to boot")
    await asyncio.sleep(6)

    return {
        "virt": "kvm",
        "os": os_key,
        "os_name": entry["name"],
        "domain": vm_name,
        "disk": target,
        "ssh_port": int(ssh_port or 0),
        "root_access": True,
        "note": "Full kernel VM. Log in as root with the password you were sent.",
    }


def _os_variant(os_key: str) -> str:
    mapping = {
        "ubuntu2204": "ubuntu22.04",
        "ubuntu2404": "ubuntu24.04",
        "debian12": "debian12",
        "debian11": "debian11",
        "rocky9": "rocky9",
        "almalinux9": "almalinux9",
    }
    return mapping.get(os_key, "generic")


async def kvm_reset_password(vm_name: str, password: str) -> tuple[bool, str]:
    """Set a new root password on a guest that has already booted.

    The host cannot reach inside the guest, so the change is delivered the
    only way libvirt allows: rebuild the cloud-init seed with a new
    password and a new instance-id, which makes cloud-init re-run its
    config stage on the next boot. Returns (ok, message).
    """
    if not await kvm_available():
        return False, "KVM is not available on this host."
    seed = os.path.join(_kvm_dir(), f"{vm_name}-seed.iso")
    stamp = f"{int(__import__('time').time())}"
    if not await _build_seed_iso(vm_name, password, seed, stamp=stamp):
        return False, (
            "Could not rebuild the cloud-init seed. Install "
            "cloud-image-utils (cloud-localds) or genisoimage."
        )
    if not await kvm_state(vm_name) == "running":
        await run_cmd(["virsh", "start", vm_name], timeout=180)
        return True, (
            "New password set and the VM has been started. It applies as "
            "soon as cloud-init finishes, usually within a minute."
        )
    rc, _, err = await run_cmd(["virsh", "reboot", vm_name, "--mode", "init-guest"],
                               timeout=90)
    if rc != 0:
        rc, _, err = await run_cmd(["virsh", "reset", vm_name], timeout=60)
    if rc != 0:
        return True, (
            "New password is set but the VM could not be rebooted from the "
            f"host ({err.strip()[:140]}). Reboot it and the new password will "
            "apply."
        )
    return True, (
        "New password set. The VM is rebooting, and cloud-init will apply it "
        "during boot. Try it in about a minute."
    )


async def kvm_state(vm_name: str) -> str:
    rc, out, _ = await run_cmd(
        ["virsh", "domstate", vm_name], timeout=20)
    if rc != 0:
        return "missing"
    text = out.strip().lower()
    if "running" in text:
        return "running"
    if "paused" in text:
        return "paused"
    if "shut" in text:
        return "stopped"
    if "no state" in text or not text:
        return "missing"
    return text.split("\n")[0].strip() or "unknown"


async def kvm_action(vm_name: str, action: str) -> tuple[bool, str]:
    """start | stop | reboot | destroy | pause | resume | undefine"""
    if action not in ("start", "stop", "reboot", "destroy", "pause",
                      "resume", "undefine", "shutdown"):
        return False, f"Unsupported action '{action}'"
    rc, _, err = await run_cmd(["virsh", action, vm_name], timeout=90)
    if rc != 0:
        message = err.strip() or f"virsh {action} failed"
        friendly = {
            "already active": "The VM is already running.",
            "not running": "The VM is not running.",
            "no domain": "That VM no longer exists.",
        }
        for needle, text in friendly.items():
            if needle in message.lower():
                return False, text
        return False, message[:200]
    return True, f"VM {action} completed."


async def kvm_disk_size_gb(vm_name: str) -> int:
    disk = _disk_path(vm_name)
    if not os.path.exists(disk):
        return 0
    rc, out, _ = await run_cmd(["qemu-img", "info", "--output=json", disk], timeout=30)
    if rc != 0:
        return 0
    try:
        import json
        return int(int(json.loads(out).get("virtual-size", 0)) / 1024 ** 3)
    except Exception:
        return 0


async def kvm_resize(vm_name: str, new_ram_mb: int = 0, new_cpu: int = 0,
                      new_disk_gb: int = 0) -> tuple[bool, str]:
    """Apply new sizing to a domain. RAM/CPU take effect live, disk needs a
    reboot before the guest sees the extra space (cloud-init grows it on boot).
    Returns (ok, human message)."""
    notes = []

    if new_disk_gb:
        disk = _disk_path(vm_name)
        current = await kvm_disk_size_gb(vm_name)
        if current and new_disk_gb > current:
            grow = new_disk_gb - current
            rc, _, err = await run_cmd(
                ["qemu-img", "resize", disk, f"+{grow}G"], timeout=120)
            if rc != 0:
                return False, f"Could not grow the disk: {err.strip()[:180]}"
            target = new_disk_gb
            if await kvm_state(vm_name) == "running":
                rc, _, err = await run_cmd(
                    ["virsh", "blockresize", vm_name, "--device", "vda",
                     f"--size={target}G"], timeout=60)
                if rc != 0:
                    notes.append("reboot to finish the disk grow")
            notes.append(f"disk set to {target} GB")
        else:
            notes.append(f"disk left at {current or '?'} GB")

    if new_ram_mb:
        rc, _, err = await run_cmd(
            ["virsh", "setmaxmem", vm_name, f"{int(new_ram_mb)}M", "--config"],
            timeout=60)
        if rc != 0:
            return False, f"Could not set RAM: {err.strip()[:180]}"
        if await kvm_state(vm_name) == "running":
            run_cmd(["virsh", "setmaxmem", vm_name, f"{int(new_ram_mb)}M"],
                    timeout=60)
        notes.append(f"memory set to {int(new_ram_mb) // 1024} GB")

    if new_cpu:
        rc, _, err = await run_cmd(
            ["virsh", "setvcpus", vm_name, str(int(new_cpu)), "--config"],
            timeout=60)
        if rc != 0:
            return False, f"Could not set CPU count: {err.strip()[:180]}"
        if await kvm_state(vm_name) == "running":
            await run_cmd(["virsh", "setvcpus", vm_name, str(int(new_cpu))],
                          timeout=60)
        notes.append(f"vcpus set to {int(new_cpu)}")

    if not notes:
        return True, "Nothing to change."
    suffix = " Reboot the VM to pick up every change."
    return True, "Resize applied: " + ", ".join(notes) + "." + suffix


async def kvm_delete(vm_name: str, remove_disk: bool = True) -> tuple[bool, str]:
    rc, _, _ = await run_cmd(["virsh", "destroy", vm_name], timeout=90)
    await run_cmd(["virsh", "undefine", vm_name], timeout=60)
    seed = os.path.join(_kvm_dir(), f"{vm_name}-seed.iso")
    if remove_disk:
        for path in (_disk_path(vm_name), seed,
                     os.path.join(_kvm_dir(), f"{vm_name}.xml")):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except Exception:
                pass
    return True, "Virtual machine deleted."


async def kvm_host_stats() -> dict:
    """Host level capacity used by the GUI to show real numbers."""
    info = {"kvm_available": False, "cores": 0, "ram_gb": 0, "cpu_model": "unknown"}
    if await kvm_available():
        info["kvm_available"] = True
        rc, out, _ = await run_cmd(["virsh", "nodeinfo"], timeout=20)
        for line in out.splitlines():
            low = line.lower().strip()
            if low.startswith("cpu(s)"):
                try:
                    info["cores"] = int(low.split(":")[1])
                except Exception:
                    pass
            elif low.startswith("model name"):
                info["cpu_model"] = line.split(":", 1)[1].strip()
            elif low.startswith("memory total"):
                raw = line.split(":", 1)[1].strip()
                try:
                    info["ram_gb"] = int(int(raw.split()[0]) / 1024 / 1024)
                except Exception:
                    pass
    if psutil is not None:
        info["cores"] = psutil.cpu_count(logical=True) or info["cores"]
        info["ram_gb"] = int(psutil.virtual_memory().total / 1024 ** 3) or info["ram_gb"]
    return info


# ---------------------------------------------------------------------------
# LXC lifecycle (Incus preferred, LXD classic as fallback)
# ---------------------------------------------------------------------------

LXC_IMAGES = {
    "ubuntu22": "images:ubuntu/22.04",
    "ubuntu24": "images:ubuntu/24.04",
    "debian12": "images:debian/12",
    "alpine": "images:alpine/3.20",
    "rocky": "images:rocky/9",
}


def _lxc_name(vm_name: str) -> str:
    return vm_name if vm_name.startswith("tn-") else f"tn-{vm_name}"


async def create_lxc(vm_name: str, ram_mb: int, cpu: int, disk_gb: int,
                     os_key: str, password: str, ssh_port: int = 0,
                     on_progress=None) -> dict:
    """Create a system container with Incus, or LXD if that is what is here."""
    backend = await lxc_backend()
    if not backend:
        raise RuntimeError(
            "No LXC backend found. Install Incus (incus) or LXD (lxc-create)."
        )

    async def note(msg: str):
        if on_progress:
            await on_progress(msg)

    name = _lxc_name(vm_name)
    image = LXC_IMAGES.get(os_key, "images:ubuntu/22.04")
    ram_mb = int(ram_mb)
    cpu = max(1, int(cpu))
    disk_gb = int(disk_gb)
    cli = "incus" if backend == "incus" else "lxc"

    await note(f"Launching {image}")
    profile = {
        "incus": (["incus", "launch", image, name],),
        "lxc": (["lxc", "launch", image, name],),
    }[backend]
    rc, _, err = await run_cmd(list(profile), timeout=300)
    if rc != 0:
        raise RuntimeError(f"Could not launch the container: {err.strip()[:200]}")

    try:
        await run_cmd([cli, "config", "set", name, f"limits.memory={ram_mb}M"], timeout=60)
        await run_cmd([cli, "config", "set", name, f"limits.cpu={cpu}"], timeout=60)
        rc, _, err = await run_cmd(
            [cli, "config", "device", "set", name, "root", f"size={disk_gb}GB"],
            timeout=60)
        if rc != 0:
            raise RuntimeError(f"Could not size the root disk: {err.strip()[:160]}")

        await note("Setting up SSH")
        setup = (
            "printf '%s\\n' "
            f"'root:{password}' "
            f"'root:{password}' | chpasswd && "
            "apt-get update -qq && apt-get install -y openssh-server -qq && "
            "printf 'PermitRootLogin yes\\nPasswordAuthentication yes\\n' "
            ">> /etc/ssh/sshd_config"
        )
        if backend == "incus":
            await run_cmd(["incus", "exec", name, "--", "sh", "-c", setup],
                          timeout=300)
        else:
            await run_cmd(["lxc", "exec", name, "--", "sh", "-c", setup],
                          timeout=300)

        if int(ssh_port or 0) > 0:
            await note(f"Publishing SSH port {int(ssh_port)}")
            proxy = ("devices.tn-ssh" if backend == "incus" else "tn-ssh")
            await run_cmd([cli, "config", "device", "remove", name, proxy],
                          timeout=30)
            if backend == "incus":
                await run_cmd([
                    "incus", "config", "device", "add", name, proxy,
                    "proxy", f"tcp:0.0.0.0:{int(ssh_port)}:22",
                ], timeout=60)
            else:
                await run_cmd([
                    "lxc", "config", "device", "add", name, proxy,
                    "proxy", f"tcp:0.0.0.0:{int(ssh_port)}:22",
                ], timeout=60)
    except Exception:
        # Never leave a half configured container behind.
        await run_cmd([cli, "delete", "--force", name], timeout=120)
        raise

    return {
        "virt": "lxc",
        "backend": backend,
        "os": os_key,
        "os_name": image,
        "domain": name,
        "ssh_port": int(ssh_port or 0),
        "root_access": True,
        "note": "System container. Log in as root with the password you were sent.",
    }


async def lxc_action(vm_name: str, action: str) -> tuple[bool, str]:
    backend = await lxc_backend()
    if not backend:
        return False, "No LXC backend installed."
    name = _lxc_name(vm_name)
    cli = "incus" if backend == "incus" else "lxc"
    if action == "delete":
        rc, _, err = await run_cmd([cli, "delete", "--force", name], timeout=120)
    elif action == "restart":
        rc, _, err = await run_cmd([cli, "restart", name], timeout=180)
    else:
        rc, _, err = await run_cmd([cli, action, name], timeout=120)
    if rc != 0:
        message = err.strip() or f"{cli} {action} failed"
        if "not found" in message.lower():
            return False, "That container no longer exists."
        return False, message[:200]
    return True, f"Container {action} completed."


async def lxc_state(vm_name: str) -> str:
    backend = await lxc_backend()
    if not backend:
        return "missing"
    cli = "incus" if backend == "incus" else "lxc"
    rc, out, _ = await run_cmd([cli, "list", _lxc_name(vm_name), "--format=csv", "-c", "s"],
                               timeout=30)
    if rc != 0 or not out.strip():
        return "missing"
    text = out.strip().split("\n")[-1].strip().upper()
    return "running" if text == "RUNNING" else ("stopped" if text == "STOPPED" else text.lower())


# ---------------------------------------------------------------------------
# File transfer
#
# The host cannot reach inside a full virtual machine's filesystem without a
# guest agent, so KVM says so plainly instead of failing. LXC has a first
# class file API, so that works natively.
# ---------------------------------------------------------------------------

async def file_push(vps: dict, local_path: str, remote_path: str) -> tuple[bool, str]:
    kind = backend_key(vps)
    name = domain_of(vps)
    if kind == "kvm":
        port = vps.get("ssh_port")
        target = f"root@<server-ip>:{remote_path}" if not port else \
            f"root@<server-ip> -P {port}:{remote_path}"
        return False, (
            "The host cannot reach inside a full virtual machine's disk, so "
            f"Discord cannot move files for you. Run `{target}` from your own "
            "machine instead."
        )
    if kind == "lxc":
        backend = await lxc_backend()
        cli = "incus" if backend == "incus" else "lxc"
        rc, _, err = await run_cmd(
            [cli, "file", "push", local_path, f"{_lxc_name(name)}/{remote_path.lstrip('/')}"],
            timeout=180)
        if rc != 0:
            return False, f"Upload failed: {err.strip()[:180]}"
        return True, "Uploaded."
    rc, _, err = await run_cmd(
        ["docker", "cp", local_path, f"{name}:{remote_path}"], timeout=180)
    if rc != 0:
        return False, f"Upload failed: {err.strip()[:180]}"
    return True, "Uploaded."


async def file_pull(vps: dict, remote_path: str, local_path: str) -> tuple[bool, str]:
    kind = backend_key(vps)
    name = domain_of(vps)
    if kind == "kvm":
        return False, (
            "File download is not available on full virtual machines. "
            "Use `scp` from your own machine instead."
        )
    if kind == "lxc":
        backend = await lxc_backend()
        cli = "incus" if backend == "incus" else "lxc"
        rc, _, err = await run_cmd(
            [cli, "file", "pull", f"{_lxc_name(name)}/{remote_path.lstrip('/')}",
             local_path], timeout=180)
        if rc != 0:
            return False, f"Download failed: {err.strip()[:180]}"
        return True, "Downloaded."
    rc, _, err = await run_cmd(
        ["docker", "cp", f"{name}:{remote_path}", local_path], timeout=180)
    if rc != 0:
        return False, f"Download failed: {err.strip()[:180]}"
    return True, "Downloaded."


async def file_exists(vps: dict, path: str) -> bool:
    """Whether a path exists inside the instance."""
    kind = backend_key(vps)
    name = domain_of(vps)
    if kind == "kvm":
        return False
    if kind == "lxc":
        backend = await lxc_backend()
        cli = "incus" if backend == "incus" else "lxc"
        rc, _, _ = await run_cmd(
            [cli, "exec", _lxc_name(name), "--", "test", "-e", path], timeout=30)
        return rc == 0
    rc, _, _ = await run_cmd(
        ["docker", "exec", name, "test", "-e", path], timeout=30)
    return rc == 0


# ---------------------------------------------------------------------------
# Live port reconfiguration
#
# Container ports are published by the Docker daemon and can change at any
# time. A KVM guest bakes its host forwards into the domain definition, so a
# new port needs a redefine and a reboot. Saying that is better than
# silently accepting the port and doing nothing.
# ---------------------------------------------------------------------------

async def kvm_redefine_ports(vm_name: str, ssh_port: int,
                             extra_ports: Optional[list]) -> tuple[bool, str]:
    """Rewrite the domain with a new port set. Needs a reboot to take effect."""
    disk = _disk_path(vm_name)
    seed = os.path.join(_kvm_dir(), f"{vm_name}-seed.iso")
    if not os.path.exists(disk):
        return False, "The VM disk is missing, so its definition cannot be rebuilt."

    rc, out, err = await run_cmd(["virsh", "dumpxml", vm_name], timeout=60)
    if rc != 0:
        return False, f"Could not read the VM definition: {err.strip()[:160]}"

    current_ram = 4194304
    current_cpu = 1
    for line in out.splitlines():
        stripped = line.strip()
        if stripped.startswith("<memory unit=") and "currentMemory" not in stripped:
            digits = "".join(c for c in stripped.split(">")[1].split("<")[0] if c.isdigit())
            if digits:
                current_ram = int(digits)
        elif stripped.startswith("<vcpu"):
            digits = "".join(c for c in stripped.split(">")[1].split("<")[0] if c.isdigit())
            if digits:
                current_cpu = int(digits)

    ram_mb = max(256, current_ram // 1024)
    xml = _domain_xml(vm_name, ram_mb, disk, seed, current_cpu,
                      ssh_port=ssh_port, extra_ports=extra_ports)
    xml_file = os.path.join(_kvm_dir(), f"{vm_name}.redefine.xml")
    with open(xml_file, "w", encoding="utf-8") as fh:
        fh.write(xml)
    rc, _, err = await run_cmd(["virsh", "define", xml_file], timeout=90)
    try:
        os.remove(xml_file)
    except OSError:
        pass
    if rc != 0:
        return False, f"Could not apply the new ports: {err.strip()[:180]}"
    return True, (
        "Ports updated. The VM needs a restart before the new forwards work. "
        "Use `!restartvps`."
    )


# ---------------------------------------------------------------------------
# Snapshots and backups
# ---------------------------------------------------------------------------

async def kvm_snapshot(vm_name: str, name: str, action: str) -> tuple[bool, str]:
    if action == "create":
        rc, _, err = await run_cmd(
            ["virsh", "snapshot-create-as", vm_name, name], timeout=180)
        if rc != 0:
            return False, f"Snapshot failed: {err.strip()[:180]}"
        return True, f"Snapshot `{name}` created."
    if action == "revert":
        rc, _, err = await run_cmd(
            ["virsh", "snapshot-revert", vm_name, name], timeout=300)
        if rc != 0:
            return False, f"Restore failed: {err.strip()[:180]}"
        return True, f"Restored from snapshot `{name}`."
    if action == "delete":
        rc, _, err = await run_cmd(
            ["virsh", "snapshot-delete", vm_name, name], timeout=120)
        if rc != 0:
            return False, f"Could not delete the snapshot: {err.strip()[:160]}"
        return True, f"Snapshot `{name}` deleted."
    return False, f"Unknown snapshot action `{action}`."


async def kvm_snapshots(vm_name: str) -> list[str]:
    rc, out, _ = await run_cmd(
        ["virsh", "snapshot-list", vm_name, "--name"], timeout=60)
    if rc != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


async def lxc_export(vm_name: str, out_path: str) -> tuple[bool, str]:
    backend = await lxc_backend()
    if not backend:
        return False, "No LXC backend installed."
    cli = "incus" if backend == "incus" else "lxc"
    rc, _, err = await run_cmd(
        [cli, "export", _lxc_name(vm_name), out_path], timeout=600)
    if rc != 0:
        return False, f"Export failed: {err.strip()[:180]}"
    return True, "Exported."


async def lxc_import(archive: str, vm_name: str) -> tuple[bool, str]:
    backend = await lxc_backend()
    if not backend:
        return False, "No LXC backend installed."
    cli = "incus" if backend == "incus" else "lxc"
    name = _lxc_name(vm_name)
    await run_cmd([cli, "delete", "--force", name], timeout=120)
    rc, _, err = await run_cmd([cli, "import", archive, name], timeout=600)
    if rc != 0:
        return False, f"Import failed: {err.strip()[:180]}"
    return True, "Imported."


# ---------------------------------------------------------------------------
# Dispatch helpers used by the bot
# ---------------------------------------------------------------------------

async def virt_available(virt: str) -> bool:
    if virt == "kvm":
        return await kvm_available()
    if virt == "lxc":
        return bool(await lxc_backend())
    return await docker_available()


async def create_instance(virt: str, vm_name: str, ram_mb: int, cpu: int,
                          disk_gb: int, os_key: str, password: str,
                          ssh_port: int = 0, ports=None,
                          on_progress=None) -> dict:
    """Create an instance on whichever backend was requested."""
    if virt == "kvm":
        return await create_kvm_vm(vm_name, ram_mb, cpu, disk_gb, os_key,
                                   password, ssh_port=ssh_port, ports=ports,
                                   on_progress=on_progress)
    if virt == "lxc":
        return await create_lxc(vm_name, ram_mb, cpu, disk_gb, os_key, password,
                                ssh_port=ssh_port, on_progress=on_progress)
    raise RuntimeError(f"Unknown virtualization type '{virt}'")


async def instance_state(vps: dict) -> str:
    """Current state of any VPS record, whatever its backend."""
    kind = backend_key(vps)
    name = domain_of(vps)
    try:
        if kind == "kvm":
            return await kvm_state(name)
        if kind == "lxc":
            return await lxc_state(name)
    except Exception:
        return "unknown"
    return str(vps.get("status", "unknown")).lower()


async def instance_action(vps: dict, action: str) -> tuple[bool, str]:
    """start / stop / restart / reboot / destroy / pause / resume / delete."""
    kind = backend_key(vps)
    name = domain_of(vps)
    if kind == "kvm":
        mapping = {"delete": "undefine", "stop": "shutdown",
                   "force": "destroy", "poweroff": "destroy"}
        return await kvm_action(name, mapping.get(action, action))
    if kind == "lxc":
        return await lxc_action(name, action)
    return False, "This command only applies to KVM and LXC servers."


async def instance_resize(vps: dict, new_ram_mb: int = 0, new_cpu: int = 0,
                          new_disk_gb: int = 0) -> tuple[bool, str]:
    kind = backend_key(vps)
    if kind == "kvm":
        return await kvm_resize(domain_of(vps), new_ram_mb=new_ram_mb,
                                new_cpu=new_cpu, new_disk_gb=new_disk_gb)
    if kind == "lxc":
        return await lxc_resize(domain_of(vps), new_ram_mb=new_ram_mb,
                                new_cpu=new_cpu, new_disk_gb=new_disk_gb)
    return False, "Use the Docker resize command for containers."


async def lxc_resize(vm_name: str, new_ram_mb: int = 0, new_cpu: int = 0,
                     new_disk_gb: int = 0) -> tuple[bool, str]:
    backend = await lxc_backend()
    if not backend:
        return False, "No LXC backend installed."
    name = _lxc_name(vm_name)
    cli = "incus" if backend == "incus" else "lxc"
    notes = []
    if new_ram_mb:
        rc, _, err = await run_cmd(
            [cli, "config", "set", name, f"limits.memory={int(new_ram_mb)}M"],
            timeout=60)
        if rc != 0:
            return False, f"Could not set memory: {err.strip()[:160]}"
        notes.append(f"memory set to {int(new_ram_mb) // 1024} GB")
    if new_cpu:
        rc, _, err = await run_cmd(
            [cli, "config", "set", name, f"limits.cpu={max(1, int(new_cpu))}"],
            timeout=60)
        if rc != 0:
            return False, f"Could not set CPU: {err.strip()[:160]}"
        notes.append(f"CPU set to {max(1, int(new_cpu))}")
    if new_disk_gb:
        rc, _, err = await run_cmd(
            [cli, "config", "device", "set", name, "root", f"size={int(new_disk_gb)}GB"],
            timeout=60)
        if rc != 0:
            return False, f"Could not set disk: {err.strip()[:160]}"
        notes.append(f"disk set to {int(new_disk_gb)} GB")
    if not notes:
        return True, "Nothing to change."
    return True, "Resize applied: " + ", ".join(notes) + ". Restart to apply."


def backend_key(vps: dict) -> str:
    """Read the virtualization type off a VPS record, defaulting to Docker."""
    return (vps.get("virt") or "container").lower()


def domain_of(vps: dict) -> str:
    """The libvirt domain name for a KVM VPS (same as container_name)."""
    return vps.get("domain") or vps.get("container_name", "")


def is_root_access(vps: dict) -> bool:
    return bool(VIRT_TYPES.get(backend_key(vps), {}).get("root", True))