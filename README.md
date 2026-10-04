# Turtle Nodes

Discord hosting bot for selling and managing virtual servers on your own
hardware. Runs containers, system containers and full hardware accelerated
virtual machines, all behind one interface.

Made By ᴛɪʀᴇᴅ ᴍᴄ

---

## What it can provision

| Backend | What you get | Needs |
|---|---|---|
| **Docker** | Fastest, lightest. Root inside the container. | Docker |
| **LXC** | System container, near native speed, own mount namespace. | Incus or LXD |
| **KVM** | Full virtual machine with its own kernel. Real root, isolated by the hypervisor. | `/dev/kvm`, libvirt, qemu |

All three hand the customer an SSH port on the shared host IP. Run
`!hypervisors` in Discord to see what the current host actually supports.
Backends that are not installed are simply hidden from the builder instead
of failing.

## Requirements

- Linux (the bot shells out to Docker, `virsh` and `incus`)
- Python 3.10 or newer
- Discord bot token in `.env` as `TOKEN=...`

### Docker

```bash
apt install docker.io
systemctl enable --now docker
```

### LXC

```bash
apt install incus            # preferred
# or
apt install lxd
```

### KVM

```bash
# enable virtualization in BIOS first, then:
apt install libvirt-clients libvirt-daemon-system \
            qemu-kvm cloud-image-utils genisoimage
systemctl enable --now libvirtd
ls -l /dev/kvm                # must exist
```

`cloud-image-utils` provides `cloud-localds`, which is how the bot injects
the root password into a fresh VM without a console. `genisoimage` is used
as a fallback seed builder.

## Install

```bash
git clone <this repo>
cd <this repo>
pip install -r requirements.txt
python start.py
```

`start.py` (and `start.bat` / `start.sh`) checks dependencies, prints which
hypervisors are available, then launches the bot.

## Configuration

`config.json`:

```json
{
  "MAIN_ADMIN_ID": 0,
  "VPS_USER_ROLE_ID": 0,
  "DOCKER_IMAGE": "ubuntu:22.04",
  "LOG_CHANNEL_ID": null,
  "CPU_THRESHOLD": 90,
  "CHECK_INTERVAL": 60,
  "SSH_PORT_START": 10000,
  "SERVER_IP": "203.0.113.10"
}
```

**`SERVER_IP` must be an address customers can actually reach.** Leaving it
as `127.0.0.1` means every SSH command the bot hands out points at the
customer's own machine. Use the host's public or private LAN IP.

`SSH_PORT_START` is the first port handed out to customer servers. Each
server gets the next free port.

## Creating servers

### Graphical builder

```
!create @user
```

Opens a panel with a dropdown for the virtualization type, the operating
system, the size and the node. The embed previews the finished spec, and one
button deploys it. This is the recommended path.

Sizes are fixed tiers so the menu stays short:

| Tier | vCPU | RAM | Disk |
|---|---|---|---|
| Hatchling | 1 | 2 GB | 20 GB |
| Turtle | 2 | 4 GB | 40 GB |
| Reef | 4 | 8 GB | 80 GB |
| Ocean | 6 | 16 GB | 160 GB |
| Deep | 8 | 32 GB | 320 GB |

### Typed form

```
!create @user 4 2 40 ubuntu24 local kvm
```

### Self service

```
!buywc Pro AMD kvm
```

KVM and LXC carry a price multiplier over Docker (1.6x and 1.2x) because
they cost more to run.

## Permissions

Levels are stored per user and unknown users start at **0**.

| Level | Value | Grants |
|---|---|---|
| Owner | 100 | Everything |
| Super Admin | 90 | Create and delete servers, run admin commands |
| VPS Manager | 70 | Manage any server |
| Support | 50 | Support tooling |
| Billing | 40 | Credits and billing |
| Moderator | 30 | Moderation tooling |
| User | 0 | Only their own servers |

```
!adminlevel @user "VPS Manager"
!adminadd @user
```

## Customer commands

Everything below works the same on all three backends.

```
!manage                panel for your servers
!start / !stop / !restart <#>
!ports <#>             show published ports
!portadd <#> 8080:80   publish a port
!resetpass <#>         rotate the root password
!reinstallos <#> <os>  wipe and reinstall
!backup / !backups / !restorebackup
!protect <#>           protect from automated purges
!console <#> <cmd>     run a command (Docker and LXC only)
```

`!console` deliberately refuses on KVM. A full virtual machine has no shell
the host can reach without a guest agent, so the bot tells the customer to
SSH in instead of pretending it worked.

## Port safety

Customers cannot publish host ports that would let them take over the host:

- anything below 1024
- 22, 2375, 2376 (Docker API), 3306, 5432, 6379, 27017 and other common
  service ports
- ports already assigned as a server's SSH port

This is enforced twice: in `!portadd` for a friendly error message, and again
in the KVM XML builder so no code path can bypass it.

## Permissions on the host

The bot needs these to actually create things:

- Docker: membership of the `docker` group, or run as root
- KVM: access to `/dev/kvm`, and permission to talk to the libvirt socket
- VM disks land in `/var/lib/turtle-nodes/kvm`, override with
  `TURTLE_KVM_DIR`

## Tests

```bash
python tests_permissions.py    # permission levels, gates, port rules
python tests_hypervisor.py     # KVM XML, cloud-init, dispatch routing
```

Neither needs Docker, libvirt or a Discord connection.