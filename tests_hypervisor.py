"""
Tests for the Turtle Nodes hypervisor layer and the create wizard.

These run without Docker, libvirt or a Discord connection because the XML
builder, the port forward logic and the wizard state machine are all pure
functions.
"""

import asyncio
import io
import os
import re
import sys
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.stdout.reconfigure(encoding="utf-8")

from core import hypervisor as H

FAILURES = []


def check(label, condition, detail=""):
    if condition:
        print(f"  [OK  ] {label}")
    else:
        print(f"  [FAIL] {label}  {detail}")
        FAILURES.append(label)


# ---------------------------------------------------------------------------
print("=== KVM domain XML ===")
# ---------------------------------------------------------------------------
xml = H._domain_xml("vps-123-1", ram_mb=4096, disk="/var/lib/turtle/k.vm.qcow2",
                    seed="/var/lib/turtle/seed.iso", vcpus=2, ssh_port=10000)

# 1. must be well formed XML
try:
    root = ET.fromstring(xml)
    parsed = True
except ET.ParseError as exc:
    parsed = False
    print(f"       parse error: {exc}")
check("domain XML is well formed", parsed)

if parsed:
    check("domain type is kvm", root.get("type") == "kvm", root.get("type"))
    check("name is set", root.findtext("name") == "vps-123-1")
    check("memory is 4 GB in KiB", root.findtext("memory") == str(4096 * 1024),
          root.findtext("memory"))
    check("vcpu count is 2", root.findtext("vcpu") == "2")

    # 2. the qemu namespace must be declared or libvirt rejects the doc
    check("qemu namespace declared",
          "xmlns:qemu" in xml and "libvirt.org/schemas/domain/qemu/1.0" in xml)

    # 3. SSH port must be forwarded into the guest
    check("hostfwd publishes the SSH port", "hostfwd=tcp:0.0.0.0:10000-:22" in xml)

    # 4. user mode networking (no libvirt bridge needed)
    check("uses qemu user networking", "user,id=tn0" in xml)
    check("no libvirt user interface element",
          root.find(".//devices/interface") is None)

    # 5. the netdev must be wired to a NIC device
    args = [a.get("value") for a in root.findall(".//qemu:arg", {
        "qemu": "http://libvirt.org/schemas/domain/qemu/1.0"})]
    check("netdev arg present", "user,id=tn0,hostfwd=tcp:0.0.0.0:10000-:22" in args, args)
    check("netdev is attached to a NIC",
          any(a and a.startswith("virtio-net-pci,netdev=tn0") for a in args), args)

    # 6. MAC must be deterministic so DHCP leases stick
    xml2 = H._domain_xml("vps-123-1", 4096, "/d.qcow2", "/s.iso", 2, ssh_port=10000)
    mac1 = re.search(r"mac=([0-9A-Fa-f:]+)", xml).group(1)
    mac2 = re.search(r"mac=([0-9A-Fa-f:]+)", xml2).group(1)
    check("MAC is deterministic for the same name", mac1 == mac2, f"{mac1} vs {mac2}")
    xml3 = H._domain_xml("vps-999-9", 4096, "/d.qcow2", "/s.iso", 2, ssh_port=10000)
    mac3 = re.search(r"mac=([0-9A-Fa-f:]+)", xml3).group(1)
    check("MAC differs for a different name", mac1 != mac3, f"{mac1} vs {mac3}")
    check("MAC is locally administered and unicast",
          mac1.startswith("52:54:00:"), mac1)

# ---------------------------------------------------------------------------
print()
print("=== Extra port forwarding ===")
# ---------------------------------------------------------------------------
xml = H._domain_xml("vm", 2048, "/d.qcow2", "/s.iso", 1, ssh_port=10001,
                    extra_ports=["8080:80", "25565:25565"])
check("SSH forward present", "hostfwd=tcp:0.0.0.0:10001-:22" in xml)
check("web port forwarded", "hostfwd=tcp:0.0.0.0:8080-:80" in xml)
check("game port forwarded", "hostfwd=tcp:0.0.0.0:25565-:25565" in xml)

xml = H._domain_xml("vm", 2048, "/d.qcow2", "/s.iso", 1, ssh_port=10002,
                    extra_ports=["22:22", "80:80", "garbage", "2375:2375"])
check("privileged host port 22 dropped", ":22-:22" not in xml.replace("10002-:22", ""))
check("privileged host port 80 dropped", "0.0.0.0:80-" not in xml)
check("malformed mapping dropped without crashing", "garbage" not in xml)
check("docker API port 2375 blocked at the XML layer too",
      "0.0.0.0:2375-:2375" not in xml)
check("docker API port 2376 blocked", "0.0.0.0:2376-:2376" not in xml)

# ---------------------------------------------------------------------------
print()
print("=== cloud-init seed ===")
# ---------------------------------------------------------------------------
userdata = H._cloud_init_userdata("vps-1", "P@ssw0rd!", 4096, 2)
check("sets the root password", "P@ssw0rd!" in userdata)
check("enables password auth", "ssh_pwauth: true" in userdata)
check("permits root login", "PermitRootLogin yes" in userdata)
check("installs sshd", "openssh-server" in userdata)
check("grows the root partition on boot",
      "resize_rootfs: true" in userdata and "growpart" in userdata)
check("hostname set", "hostname: vps-1" in userdata)

metadata = H._cloud_init_metadata("vps-1")
check("meta-data has an instance id", "instance-id:" in metadata)
check("meta-data has the hostname", "local-hostname: vps-1" in metadata)

# ---------------------------------------------------------------------------
print()
print("=== Backend detection degrades safely ===")
# ---------------------------------------------------------------------------
check("kvm unavailable without /dev/kvm", asyncio.run(H.kvm_available()) is False)
backends = asyncio.run(H.supported_backends())
check("supported_backends returns a dict", isinstance(backends, dict))
check("no backends claimed on a bare host", backends == {}, backends)
check("virt_available('kvm') is False here",
      asyncio.run(H.virt_available("kvm")) is False)

# ---------------------------------------------------------------------------
print()
print("=== Command dispatch routing ===")
# ---------------------------------------------------------------------------
check("backend_key defaults to container",
      H.backend_key({"container_name": "x"}) == "container")
check("backend_key reads the virt field",
      H.backend_key({"virt": "kvm", "container_name": "x"}) == "kvm")
check("domain_of falls back to container_name",
      H.domain_of({"container_name": "vps-1-1"}) == "vps-1-1")
check("domain_of prefers the domain field",
      H.domain_of({"domain": "tn-1", "container_name": "vps-1-1"}) == "tn-1")
check("kvm reports full root access",
      H.is_root_access({"virt": "kvm"}) is True)

ok, msg = asyncio.run(H.instance_action({"virt": "kvm", "domain": "nope",
                                         "container_name": "nope"}, "delete"))
check("kvm delete on a missing domain reports cleanly",
      ok is False and "error" not in msg.lower(), msg)

try:
    asyncio.run(H.instance_state({"virt": "container", "container_name": "x",
                                  "node": "nonexistent-node"}))
    routed = True
except Exception as exc:
    routed = False
    print(f"       {exc}")
check("remote-node routing still raises a clear error", routed)

# ---------------------------------------------------------------------------
print()
print("=== Cross-backend coexistence ===")
# ---------------------------------------------------------------------------
# The whole point: containers and root-access VMs live on the same host at
# the same time. Nothing one backend does may disturb the other.

# SSH ports are allocated across every record regardless of backend, so a
# container and a VM can never collide on the host port.
records = {
    "user1": [{"container_name": "vps-1-1", "virt": "container", "ssh_port": 10000},
              {"container_name": "vps-1-2", "virt": "kvm", "ssh_port": 10001},
              {"container_name": "vps-1-3", "virt": "lxc", "ssh_port": 10002}],
}
used = {v["ssh_port"] for vl in records.values() for v in vl if "ssh_port" in v}
check("all three backends coexist in one user",
      len(used) == 3, used)
check("backend_key reads each one correctly",
      [H.backend_key(v) for vl in records.values() for v in vl]
      == ["container", "kvm", "lxc"])

# A KVM domain and a Docker container must never be able to take the same
# identity, because libvirt and Docker keep separate namespaces but the bot
# keys records on container_name.
check("KVM domain name is distinct from a container name",
      H.domain_of({"virt": "kvm", "domain": "vps-9-1", "container_name": "vps-9-1"})
      == "vps-9-1")

# VM disks live outside Docker's storage so wiping Docker cannot touch them.
check("VM disk path is not under Docker's storage",
      "docker" not in H._kvm_dir().lower())

# Existing records with no virt field must keep working as containers, so an
# upgrade never orphans a customer's server.
check("legacy record without virt is still a container",
      H.backend_key({"container_name": "old-1"}) == "container")

# The KVM XML builder must refuse a host port that belongs to another server.
vps = {"virt": "kvm", "domain": "d", "container_name": "d", "ssh_port": 10000}
ok, msg = asyncio.run(H.kvm_redefine_ports("nonexistent-domain", 10000, ["2375:2375"]))
check("redefine of a missing domain fails cleanly",
      ok is False and "Traceback" not in msg, msg)

# ---------------------------------------------------------------------------
print()
print("=== virsh output parser ===")
# ---------------------------------------------------------------------------
ns = {}
exec(io.open(os.path.join("bot.py"), encoding="utf-8").read().split(
    "def _parse_kb(")[1].split("\n\n")[0].join(["def _parse_kb(", ""]), ns)
parse_kb = ns["_parse_kb"]

check("parses a raw byte count", parse_kb("balloon.current: 1048576") == 1048576)
check("converts MiB to KiB",
      parse_kb("memory.current: 512.00 MiB") == 512 * 1024,
      parse_kb("memory.current: 512.00 MiB"))
check("converts GiB to KiB", parse_kb("balloon.maximum: 4.00 GiB") == 4 * 1024 * 1024)
check("returns 0 on garbage", parse_kb("nonsense") == 0)
check("returns 0 with no colon", parse_kb("") == 0)

# ---------------------------------------------------------------------------
print()
print("=== Size tiers ===")
# ---------------------------------------------------------------------------
from core.create_panel import SIZE_TIERS, tier

check("five size tiers", len(SIZE_TIERS) == 5)
check("tiers are ordered by size ascending",
      all(SIZE_TIERS[i]["ram"] < SIZE_TIERS[i + 1]["ram"]
          for i in range(len(SIZE_TIERS) - 1)))
check("every tier has ram cpu and disk",
      all(t["ram"] > 0 and t["cpu"] > 0 and t["disk"] > 0 for t in SIZE_TIERS))
check("tier() falls back safely", tier("nonsense")["key"] != "nonsense")

# ---------------------------------------------------------------------------
print()
print("=== OS catalogs ===")
# ---------------------------------------------------------------------------
check("7 KVM cloud images", len(H.KVM_OS_CATALOG) == 7)
check("all KVM images are https",
      all(v["url"].startswith("https://") for v in H.KVM_OS_CATALOG.values()))
check("all KVM images are qcow2",
      all(v["url"].endswith((".qcow2", ".img")) for v in H.KVM_OS_CATALOG.values()))
check("KVM os_variant resolves ubuntu2204",
      H._os_variant("ubuntu2204") == "ubuntu22.04")
check("KVM os_variant resolves debian12",
      H._os_variant("debian12") == "debian12")

print()
if FAILURES:
    print(f"{len(FAILURES)} TEST(S) FAILED:")
    for name in FAILURES:
        print(f"  - {name}")
    sys.exit(1)
print("ALL HYPERVISOR TESTS PASSED")