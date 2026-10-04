"""
Turtle Nodes - graphical server creation wizard.

`!create @user` with no arguments opens this panel instead of demanding a
string of numbers. Every choice is a dropdown, the embed previews the
resulting spec live, and one button kicks off the deploy.

The panel is deliberately unaware of bot.py internals: the caller injects
a `provision` coroutine, so the wizard stays testable and importable
without Discord state.
"""

from __future__ import annotations

from datetime import datetime

import discord

from core.hypervisor import (KVM_OS_CATALOG, VIRT_TYPES,
                             supported_backends, kvm_host_stats)

BRAND = "Turtle Nodes"

# Turtle themed sizing tiers. ram/cpu/disk are the real numbers handed to
# the hypervisor.
SIZE_TIERS = [
    {"key": "hatchling", "label": "Hatchling", "cpu": 1, "ram": 2, "disk": 20},
    {"key": "turtle", "label": "Turtle", "cpu": 2, "ram": 4, "disk": 40},
    {"key": "reef", "label": "Reef", "cpu": 4, "ram": 8, "disk": 80},
    {"key": "ocean", "label": "Ocean", "cpu": 6, "ram": 16, "disk": 160},
    {"key": "deep", "label": "Deep", "cpu": 8, "ram": 32, "disk": 320},
]

# Docker side operating systems, keyed the same way OS_IMAGES is.
CONTAINER_OS = {
    "ubuntu22": "Ubuntu 22.04 LTS",
    "ubuntu24": "Ubuntu 24.04 LTS",
    "debian12": "Debian 12",
    "alpine": "Alpine 3.20",
    "rocky": "Rocky Linux 9",
    "almalinux": "AlmaLinux 9",
    "fedora": "Fedora 40",
    "kali": "Kali Rolling",
    "arch": "Arch Linux",
}

LXC_OS = {
    "ubuntu22": "images:ubuntu/22.04",
    "ubuntu24": "images:ubuntu/24.04",
    "debian12": "images:debian/12",
    "alpine": "images:alpine/3.20",
    "rocky": "images:rocky/9",
}


def _embed(title: str, description: str = "", color: int = 0x0f6b4f,
           fields=None) -> discord.Embed:
    em = discord.Embed(title=f"▌ {title}", description=description, color=color)
    for field in (fields or []):
        em.add_field(name=f"▸ {field['name']}", value=str(field["value"]),
                     inline=field.get("inline", False))
    em.set_footer(
        text=f"{BRAND} | Made By \u1d1b\u026a\u0280\u1d07\u1d05 \u1d4d\u1d04 "
             f"• {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    return em


def tier(key: str) -> dict:
    for item in SIZE_TIERS:
        if item["key"] == key:
            return item
    return SIZE_TIERS[1]


class CreateWizard(discord.ui.View):
    """Dropdown driven create panel.

    Parameters
    ----------
    interaction : the original !create invocation
    target       : the discord.Member the server is for
    provision    : async (interaction, spec: dict) -> None
    nodes        : iterable of registered node names
    can_use      : optional async (member) -> bool used to gate the buttons
    """

    def __init__(self, interaction, target, provision, nodes=None,
                 can_use=None, timeout: float = 300.0):
        super().__init__(timeout=timeout)
        self.interaction = interaction
        self.target = target
        self.provision = provision
        self.nodes = list(nodes or [])
        self.can_use = can_use
        self.message = None
        self.busy = False
        self.finished = False

        self.backends = {}
        self.virt = "container"
        self.os_key = "ubuntu22"
        self.tier_key = "turtle"
        self.node = self.nodes[0] if self.nodes else "local"
        self.host = {}

    # -- state -------------------------------------------------------------

    async def load(self):
        """Fill in what this host can actually run. Call before sending."""
        self.backends = await supported_backends()
        if "container" in self.backends:
            self.virt = "container"
        elif "kvm" in self.backends:
            self.virt = "kvm"
        else:
            self.virt = next(iter(self.backends), "container")
        self.host = await kvm_host_stats()
        self._sync_os()
        self._rebuild()
        return self

    def _os_options(self) -> dict:
        if self.virt == "kvm":
            return {k: v["name"] for k, v in KVM_OS_CATALOG.items()}
        if self.virt == "lxc":
            return dict(LXC_OS)
        return dict(CONTAINER_OS)

    def _sync_os(self):
        options = self._os_options()
        if self.os_key not in options:
            self.os_key = next(iter(options), "ubuntu22")

    def spec(self) -> dict:
        size = tier(self.tier_key)
        options = self._os_options()
        return {
            "virt": self.virt,
            "os_key": self.os_key,
            "os_name": options.get(self.os_key, self.os_key),
            "size_key": self.tier_key,
            "ram_gb": size["ram"],
            "cpu": size["cpu"],
            "disk_gb": size["disk"],
            "node": self.node,
            "user_id": str(self.target.id),
        }

    # -- rendering ---------------------------------------------------------

    def preview(self, extra: str = "") -> discord.Embed:
        spec = self.spec()
        meta = VIRT_TYPES.get(self.virt, VIRT_TYPES["container"])
        colour = {"kvm": 0x7b5cff, "lxc": 0x0f9d58, "container": 0x1a73e8}.get(
            self.virt, 0x1a73e8)

        host_line = ""
        if self.host:
            bits = []
            if self.host.get("kvm_available"):
                bits.append("hardware virtualization available")
            if self.host.get("cores"):
                bits.append(f"{self.host['cores']} threads")
            if self.host.get("ram_gb"):
                bits.append(f"{self.host['ram_gb']} GB RAM")
            if bits:
                host_line = "This host: " + " • ".join(bits)

        fields = [
            {"name": "Virtualization", "value": meta["label"], "inline": True},
            {"name": "Operating System", "value": spec["os_name"], "inline": True},
            {"name": "Size", "value": tier(self.tier_key)["label"], "inline": True},
            {"name": "Memory", "value": f"{spec['ram_gb']} GB", "inline": True},
            {"name": "vCPU", "value": str(spec["cpu"]), "inline": True},
            {"name": "Disk", "value": f"{spec['disk_gb']} GB", "inline": True},
            {"name": "Node", "value": self.node, "inline": True},
            {"name": "Assigned To", "value": self.target.mention, "inline": True},
            {"name": "Root Access",
             "value": ("Full root, real kernel, isolated by the hypervisor"
                       if self.virt == "kvm"
                       else "Root inside your isolated environment"),
             "inline": False},
        ]
        em = _embed("New Server", meta["description"], colour, fields)
        if host_line:
            em.add_field(name="Host Capacity", value=host_line, inline=False)
        if extra:
            em.add_field(name="Status", value=extra, inline=False)
        return em

    def _rebuild(self):
        """Rebuild every select so the options always match the state."""
        for item in list(self.children):
            self.remove_item(item)

        backends = self.backends or {"container": VIRT_TYPES["container"]["label"]}
        self.add_item(discord.ui.Select(
            placeholder="1. Pick the virtualization type",
            custom_id="wiz_type",
            options=[discord.SelectOption(
                label=label[:100],
                value=key,
                description=VIRT_TYPES.get(key, VIRT_TYPES["container"])
                ["description"][:100],
                default=(key == self.virt))
                for key, label in backends.items()],
        ))

        options = self._os_options()
        keys = list(options)
        self.add_item(discord.ui.Select(
            placeholder="2. Pick the operating system",
            custom_id="wiz_os",
            options=[discord.SelectOption(
                label=str(options[k])[:100], value=k,
                default=(k == self.os_key)) for k in keys],
        ))

        self.add_item(discord.ui.Select(
            placeholder="3. Pick the size",
            custom_id="wiz_size",
            options=[discord.SelectOption(
                label=f"{t['label']}  •  {t['ram']} GB / {t['cpu']} vCPU / {t['disk']} GB",
                value=t["key"],
                description=f"{t['cpu']} vCPU, {t['ram']} GB RAM, {t['disk']} GB disk",
                default=(t["key"] == self.tier_key))
                for t in SIZE_TIERS],
        ))

        if self.nodes:
            self.add_item(discord.ui.Select(
                placeholder="4. Pick the node",
                custom_id="wiz_node",
                options=[discord.SelectOption(label=n[:100], value=n,
                                              default=(n == self.node))
                         for n in self.nodes[:25]],
            ))

    async def _refresh(self, note: str = ""):
        self._rebuild()
        if self.message is not None:
            try:
                await self.message.edit(embed=self.preview(note), view=self)
            except discord.HTTPException:
                pass

    # -- interactions ------------------------------------------------------

    @discord.ui.select(custom_id="wiz_type")
    async def _sel_type(self, interaction, select):
        await interaction.response.defer(ephemeral=True)
        self.virt = select.values[0]
        self._sync_os()
        await self._refresh(f"Switched to {VIRT_TYPES.get(self.virt, {}).get('label', self.virt)}.")

    @discord.ui.select(custom_id="wiz_os")
    async def _sel_os(self, interaction, select):
        await interaction.response.defer(ephemeral=True)
        self.os_key = select.values[0]
        await self._refresh("Operating system updated.")

    @discord.ui.select(custom_id="wiz_size")
    async def _sel_size(self, interaction, select):
        await interaction.response.defer(ephemeral=True)
        self.tier_key = select.values[0]
        await self._refresh(f"Size set to {tier(self.tier_key)['label']}.")

    @discord.ui.select(custom_id="wiz_node")
    async def _sel_node(self, interaction, select):
        await interaction.response.defer(ephemeral=True)
        self.node = select.values[0]
        await self._refresh(f"Node set to {self.node}.")

    async def interaction_check(self, interaction) -> bool:
        if self.busy or self.finished:
            await interaction.response.send_message(
                "This panel is no longer active.", ephemeral=True)
            return False
        if self.can_use is None:
            return True
        if not await self.can_use(interaction.user):
            await interaction.response.send_message(
                "You are not allowed to use this panel.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Create Server", style=discord.ButtonStyle.success,
                       custom_id="wiz_go")
    async def _go(self, interaction, button):
        await interaction.response.defer(ephemeral=True)
        self.busy = True
        self.finished = True
        spec = self.spec()
        try:
            await self.provision(interaction, spec)
        except Exception as exc:
            text = getattr(exc, "detail", None) or str(exc)
            try:
                await interaction.followup.send(
                    embed=_embed("Deployment Failed", str(text)[:1900], 0xff3366),
                    ephemeral=True)
            except discord.HTTPException:
                pass
        finally:
            self.busy = False
            try:
                if self.message is not None:
                    await self.message.edit(embed=_embed(
                        "New Server", "This request has been processed.", 0x0f6b4f),
                        view=None)
            except discord.HTTPException:
                pass

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary,
                       custom_id="wiz_cancel")
    async def _cancel(self, interaction, button):
        self.finished = True
        await interaction.response.edit_message(
            embed=_embed("Cancelled", "No server was created.", 0x808080), view=None)

    async def on_timeout(self):
        if not self.finished:
            self.finished = True
            if self.message is not None:
                try:
                    await self.message.edit(
                        embed=_embed("Expired",
                                     "This panel timed out. Run the command again.",
                                     0x808080), view=None)
                except discord.HTTPException:
                    pass