import discord
from discord.ext import commands
import asyncio
import json
import os
import time
from datetime import datetime, timezone
from typing import Optional, Callable, Any, List, Dict

# Lazy imports handled inside methods to avoid circular imports


def status_color(status: str) -> int:
    """Return color based on status string."""
    colors = {
        "healthy": 0x2ECC71,
        "online": 0x2ECC71,
        "warning": 0xF39C12,
        "yellow": 0xF39C12,
        "critical": 0xE74C3C,
        "red": 0xE74C3C,
        "degraded": 0xE67E22,
        "orange": 0xE67E22,
        "offline": 0x95A5A6,
        "gray": 0x95A5A6,
    }
    return colors.get(status.lower(), 0x9866A5)


def make_panel_embed(
    title: str,
    description: str = "",
    color: int = 0x9866A5,
    fields: list = None,
    footer: str = "Turtle Nodes • Hosting Panel",
    thumbnail: str = None,
) -> discord.Embed:
    """Standardized embed builder for all panels."""
    embed = discord.Embed(title=title, description=description, color=color)
    if fields:
        for f in fields:
            embed.add_field(name=f["name"], value=f["value"], inline=f.get("inline", False))
    embed.set_footer(text=footer)
    if thumbnail:
        embed.set_thumbnail(url=thumbnail)
    return embed


def metric_field(name: str, value: str, status: str = "healthy") -> dict:
    """Return embed field dict with status icon prefix."""
    icons = {
        "healthy": "🟢",
        "online": "🟢",
        "warning": "🟡",
        "critical": "🔴",
        "degraded": "🟠",
        "offline": "⚪",
        "ok": "✅",
        "error": "❌",
        "info": "ℹ️",
    }
    icon = icons.get(status.lower(), "•")
    return {"name": f"{icon} {name}", "value": str(value), "inline": True}


def _check_permission(user: discord.User) -> bool:
    """Check if user has admin level >= 90 in admin_levels.json."""
    admin_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), "admin_levels.json")
    try:
        with open(admin_file, "r") as f:
            data = json.load(f)
        level = data.get(str(user.id), {}).get("level", 0)
        return level >= 90
    except (FileNotFoundError, json.JSONDecodeError):
        return False


class ConfirmView(discord.ui.View):
    def __init__(
        self,
        on_confirm_callback: Callable = None,
        on_cancel_callback: Callable = None,
        message: str = "Are you sure?",
        timeout: int = 60,
    ):
        super().__init__(timeout=timeout)
        self.on_confirm_callback = on_confirm_callback
        self.on_cancel_callback = on_cancel_callback
        self.confirmation_message = message
        self.result: Optional[bool] = None
        self.interaction: Optional[discord.Interaction] = None

    @discord.ui.button(label="Yes, Confirm", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = True
        self.interaction = interaction
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        if self.on_confirm_callback:
            try:
                await self.on_confirm_callback(interaction)
            except Exception as e:
                embed = make_panel_embed("Error", f"Callback failed: {e}", 0xE74C3C)
                await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="No, Cancel", style=discord.ButtonStyle.danger, emoji="❌", row=0)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.result = False
        self.interaction = interaction
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        if self.on_cancel_callback:
            try:
                await self.on_cancel_callback(interaction)
            except Exception:
                pass
        embed = make_panel_embed("Cancelled", "Action was cancelled.", 0x95A5A6)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class StatusConfigModal(discord.ui.Modal, title="Set Custom Activity Text"):
    activity_text = discord.ui.TextInput(
        label="Activity Text",
        placeholder="e.g. serving 100 servers | turtle.cloud",
        required=True,
        max_length=128,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        text = self.activity_text.value
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            current = self.bot.activity
            activity_type = current.type if current else discord.ActivityType.playing
            await ctrl.set_activity(activity_type, text)
            embed = make_panel_embed(
                "Activity Updated",
                f"Bot activity set to: **{text}**",
                0x2ECC71,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        embed = make_panel_embed("Error", str(error), 0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class RenameModal(discord.ui.Modal, title="Rename VPS"):
    new_name = discord.ui.TextInput(
        label="New VPS Name",
        placeholder="Enter new name for this VPS",
        required=True,
        max_length=64,
    )

    def __init__(self, bot: commands.Bot, user_id: int, vps_index: int):
        super().__init__()
        self.bot = bot
        self.user_id = user_id
        self.vps_index = vps_index

    async def on_submit(self, interaction: discord.Interaction):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.rename_vps(self.user_id, self.vps_index, self.new_name.value)
            embed = make_panel_embed(
                "VPS Renamed",
                f"VPS renamed to **{self.new_name.value}**",
                0x2ECC71,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.response.send_message(embed=embed, ephemeral=True)

    async def on_error(self, interaction: discord.Interaction, error: Exception):
        embed = make_panel_embed("Error", str(error), 0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)


class BotControlPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self) -> discord.Embed:
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            info = ctrl.get_status()
        except Exception:
            info = {"status": "unknown", "activity": "N/A", "latency": 0, "uptime": "N/A"}

        latency_ms = int(info.get("latency", self.bot.latency) * 1000)
        activity = self.bot.activity
        activity_text = activity.name if activity else "None"
        activity_type = str(activity.type).split(".")[-1].title() if activity else "None"

        status_map = {
            discord.Status.online: "online",
            discord.Status.idle: "idle",
            discord.Status.dnd: "dnd",
            discord.Status.invisible: "invisible",
        }
        current_status = status_map.get(self.bot.status, "unknown")

        fields = [
            metric_field("Status", current_status, current_status),
            metric_field("Activity Type", activity_type, "info"),
            metric_field("Activity Text", activity_text, "info"),
            metric_field("Latency", f"{latency_ms}ms", "healthy" if latency_ms < 200 else "warning"),
            metric_field("Uptime", info.get("uptime", "N/A"), "info"),
            metric_field("Guilds", str(len(self.bot.guilds)), "info"),
            metric_field("Users", str(sum(g.member_count or 0 for g in self.bot.guilds)), "info"),
            metric_field("Synced", info.get("synced", "N/A"), "ok"),
        ]
        return make_panel_embed(
            "🤖 Bot Control Panel",
            "Manage the bot's status, activity, and core controls.",
            0x9866A5,
            fields,
        )

    @discord.ui.select(
        placeholder="Set Bot Status",
        options=[
            discord.SelectOption(label="Online", value="online", emoji="🟢"),
            discord.SelectOption(label="Idle", value="idle", emoji="🟡"),
            discord.SelectOption(label="Do Not Disturb", value="dnd", emoji="🔴"),
            discord.SelectOption(label="Invisible", value="invisible", emoji="⚪"),
        ],
        row=0,
    )
    async def status_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            await ctrl.set_status(select.values[0])
            embed = self._build_embed()
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.select(
        placeholder="Set Activity Type",
        options=[
            discord.SelectOption(label="Playing", value="playing", emoji="🎮"),
            discord.SelectOption(label="Watching", value="watching", emoji="👁️"),
            discord.SelectOption(label="Listening", value="listening", emoji="🎧"),
            discord.SelectOption(label="Streaming", value="streaming", emoji="📺"),
        ],
        row=0,
    )
    async def activity_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            await ctrl.set_activity_type(select.values[0])
            embed = self._build_embed()
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Set Activity Text", style=discord.ButtonStyle.primary, emoji="✏️", row=1)
    async def set_activity_text(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        modal = StatusConfigModal(self.bot)
        await interaction.response.send_modal(modal)

    @discord.ui.select(
        placeholder="Dynamic Activity Templates",
        options=[
            discord.SelectOption(label="Playing a game", value="playing", emoji="🎮"),
            discord.SelectOption(label="Watching over servers", value="watching_servers", emoji="👁️"),
            discord.SelectOption(label="Listening to commands", value="listening_commands", emoji="🎧"),
            discord.SelectOption(label="Streaming on Twitch", value="streaming", emoji="📺"),
            discord.SelectOption(label="Hosting Turtle Nodes", value="hosting", emoji="🖥️"),
            discord.SelectOption(label="Managing VPS instances", value="managing", emoji="⚙️"),
        ],
        row=1,
    )
    async def dynamic_activity(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            templates = {
                "playing": ("playing", "a game"),
                "watching_servers": ("watching", f"{len(self.bot.guilds)} servers"),
                "listening_commands": ("listening", "to /commands"),
                "streaming": ("streaming", "Turtle Nodes"),
                "hosting": ("playing", "Turtle Nodes • Hosting Panel"),
                "managing": ("watching", f"{len(self.bot.guilds)} VPS instances"),
            }
            act_type, text = templates.get(select.values[0], ("playing", "Turtle Nodes"))
            await ctrl.set_activity(act_type, text)
            embed = self._build_embed()
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.danger, emoji="🔄", row=2)
    async def restart_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_restart)
        embed = make_panel_embed("⚠️ Restart Bot", "Are you sure you want to restart the bot?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_restart(self, interaction: discord.Interaction):
        await interaction.followup.send("🔄 Restarting...", ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            await ctrl.restart()
        except Exception as e:
            await interaction.followup.send(f"❌ Restart failed: {e}", ephemeral=True)

    @discord.ui.button(label="Reconnect", style=discord.ButtonStyle.secondary, emoji="🔌", row=2)
    async def reconnect_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            await ctrl.reconnect()
            embed = make_panel_embed("Reconnected", "Bot WebSocket reconnected.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Shutdown", style=discord.ButtonStyle.danger, emoji="🛑", row=2)
    async def shutdown_bot(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_shutdown)
        embed = make_panel_embed("⚠️ Shutdown Bot", "Are you sure? This will stop the bot completely.", 0xE74C3C)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_shutdown(self, interaction: discord.Interaction):
        await interaction.followup.send("🛑 Shutting down...", ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            await ctrl.shutdown()
        except Exception as e:
            await interaction.followup.send(f"❌ Shutdown failed: {e}", ephemeral=True)

    @discord.ui.button(label="Sync Commands", style=discord.ButtonStyle.primary, emoji="📡", row=2)
    async def sync_commands(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            result = await ctrl.sync_commands()
            embed = make_panel_embed("Commands Synced", f"Synced {result} commands.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Clear Cache", style=discord.ButtonStyle.secondary, emoji="🗑️", row=3)
    async def clear_cache(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            result = await ctrl.clear_cache()
            embed = make_panel_embed("Cache Cleared", result, 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reload Config", style=discord.ButtonStyle.secondary, emoji="🔄", row=3)
    async def reload_config(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.control import BotController
            ctrl = BotController(self.bot)
            result = await ctrl.reload_config()
            embed = make_panel_embed("Config Reloaded", result, 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Health", style=discord.ButtonStyle.success, emoji="💓", row=3)
    async def health_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.health_monitor import HealthMonitor
            monitor = HealthMonitor(self.bot)
            report = await monitor.run_full_check()
            fields = [metric_field(k, v["value"], v["status"]) for k, v in report.items()]
            embed = make_panel_embed("Bot Health Report", "Full diagnostic results:", 0x2ECC71, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=4)
    async def refresh_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = self._build_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class VPSControlPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int, vps_index: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id
        self.vps_index = vps_index

    def _build_embed(self) -> discord.Embed:
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            info = ctrl.get_vps_info(self.user_id, self.vps_index)
        except Exception:
            info = {
                "name": "Unknown",
                "container": "N/A",
                "status": "unknown",
                "cpu": "N/A",
                "ram": "N/A",
                "disk": "N/A",
                "locked": False,
                "suspended": False,
            }

        status = info.get("status", "unknown")
        lock_icon = "🔒" if info.get("locked") else "🔓"
        suspend_icon = "⚠️ Suspended" if info.get("suspended") else "✅ Active"

        fields = [
            metric_field("Name", info.get("name", "Unknown"), "info"),
            metric_field("Container", info.get("container", "N/A"), "info"),
            metric_field("Status", status, status),
            metric_field("CPU", info.get("cpu", "N/A"), "info"),
            metric_field("RAM", info.get("ram", "N/A"), "info"),
            metric_field("Disk", info.get("disk", "N/A"), "info"),
            metric_field("Lock", lock_icon, "info"),
            metric_field("State", suspend_icon, "info"),
        ]
        return make_panel_embed(
            f"🖥️ VPS Control - {info.get('name', 'Unknown')}",
            f"Managing VPS instance for user `{self.user_id}` (index `{self.vps_index}`)",
            0x9866A5,
            fields,
        )

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, emoji="▶️", row=0)
    async def start_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.start_vps(self.user_id, self.vps_index)
            embed = make_panel_embed("VPS Started", f"VPS index `{self.vps_index}` started.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, emoji="⏹️", row=0)
    async def stop_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_stop)
        embed = make_panel_embed("⚠️ Stop VPS", "Stop this VPS instance?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_stop(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.stop_vps(self.user_id, self.vps_index)
            await interaction.followup.send("⏹️ VPS stopped.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, emoji="🔄", row=0)
    async def restart_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_restart)
        embed = make_panel_embed("⚠️ Restart VPS", "Restart this VPS instance?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_restart(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.restart_vps(self.user_id, self.vps_index)
            await interaction.followup.send("🔄 VPS restarted.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Reboot", style=discord.ButtonStyle.secondary, emoji="🔃", row=0)
    async def reboot_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_reboot)
        embed = make_panel_embed("⚠️ Reboot VPS", "Reboot the underlying container?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_reboot(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.reboot_vps(self.user_id, self.vps_index)
            await interaction.followup.send("🔃 VPS container rebooted.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Force Stop", style=discord.ButtonStyle.danger, emoji="⛔", row=0)
    async def force_stop_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_force_stop)
        embed = make_panel_embed(
            "⚠️ Force Stop",
            "This will forcefully terminate the VPS. Are you sure?",
            0xE74C3C,
        )
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_force_stop(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.force_stop_vps(self.user_id, self.vps_index)
            await interaction.followup.send("⛔ VPS force stopped.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Suspend", style=discord.ButtonStyle.secondary, emoji="⏸️", row=1)
    async def suspend_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.suspend_vps(self.user_id, self.vps_index)
            embed = make_panel_embed("VPS Suspended", "VPS has been suspended.", 0xF39C12)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Unsuspend", style=discord.ButtonStyle.success, emoji="▶️", row=1)
    async def unsuspend_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.unsuspend_vps(self.user_id, self.vps_index)
            embed = make_panel_embed("VPS Unsuspended", "VPS has been unsuspended.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Lock", style=discord.ButtonStyle.secondary, emoji="🔒", row=1)
    async def lock_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_lock)
        embed = make_panel_embed("⚠️ Lock VPS", "Lock this VPS to prevent modifications?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_lock(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.lock_vps(self.user_id, self.vps_index)
            await interaction.followup.send("🔒 VPS locked.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Unlock", style=discord.ButtonStyle.success, emoji="🔓", row=1)
    async def unlock_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.unlock_vps(self.user_id, self.vps_index)
            embed = make_panel_embed("VPS Unlocked", "VPS has been unlocked.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Reset SSH", style=discord.ButtonStyle.primary, emoji="🔑", row=2)
    async def reset_ssh(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_reset_ssh)
        embed = make_panel_embed("⚠️ Reset SSH", "Regenerate SSH credentials for this VPS?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_reset_ssh(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            creds = await ctrl.reset_ssh(self.user_id, self.vps_index)
            embed = make_panel_embed(
                "SSH Reset",
                f"**Host:** `{creds.get('host', 'N/A')}`\n**User:** `{creds.get('user', 'N/A')}`\n**Pass:** ||`{creds.get('pass', 'N/A')}`||\n**Port:** `{creds.get('port', 22)}`",
                0x2ECC71,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Recreate", style=discord.ButtonStyle.danger, emoji="♻️", row=2)
    async def recreate_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_recreate)
        embed = make_panel_embed(
            "⚠️ Recreate VPS",
            "This will DESTROY the current VPS and create a new one. All data will be lost. Continue?",
            0xE74C3C,
        )
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_recreate(self, interaction: discord.Interaction):
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            await ctrl.recreate_vps(self.user_id, self.vps_index)
            await interaction.followup.send("♻️ VPS recreated successfully.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Rename", style=discord.ButtonStyle.secondary, emoji="✏️", row=2)
    async def rename_vps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        modal = RenameModal(self.bot, self.user_id, self.vps_index)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Health", style=discord.ButtonStyle.success, emoji="💓", row=2)
    async def vps_health(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.vps_advanced import VPSController
            ctrl = VPSController(self.bot)
            health = await ctrl.get_vps_health(self.user_id, self.vps_index)
            fields = [metric_field(k, v["value"], v["status"]) for k, v in health.items()]
            embed = make_panel_embed("VPS Health Report", "Resource and health check:", 0x2ECC71, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Back", style=discord.ButtonStyle.secondary, emoji="◀️", row=3)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await interaction.edit_original_response(content=None, embed=make_panel_embed("Returning...", "Loading previous panel...", 0x9866A5), view=None)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=3)
    async def refresh_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = self._build_embed()
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class PurgePanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self) -> discord.Embed:
        fields = [
            metric_field("Cache", "System cache files", "info"),
            metric_field("Temp", "Temporary files", "info"),
            metric_field("Old Logs", "Logs older than 30 days", "info"),
            metric_field("Old Backups", "Backups older than 30 days", "info"),
            metric_field("Docker Containers", "Stopped containers", "info"),
            metric_field("Docker Images", "Unused images", "info"),
            metric_field("Docker Volumes", "Dangling volumes", "info"),
            metric_field("Build Cache", "Docker build cache", "info"),
        ]
        return make_panel_embed(
            "🧹 Purge System",
            "Clean up disk space by removing unnecessary files.\nEvery action shows a preview before executing.",
            0x9866A5,
            fields,
        )

    async def _preview_and_execute(self, interaction: discord.Interaction, purge_type: str, destructive: bool = True):
        await interaction.response.defer(ephemeral=True)
        try:
            from core.purge import PurgeManager
            pm = PurgeManager(self.bot)
            preview = await pm.preview(purge_type)
            if not preview or preview.get("total_bytes", 0) == 0:
                embed = make_panel_embed("Nothing to Purge", f"No files found for **{purge_type}** cleanup.", 0x2ECC71)
                return await interaction.followup.send(embed=embed, ephemeral=True)

            size_mb = round(preview.get("total_bytes", 0) / (1024 * 1024), 2)
            file_list = "\n".join([f"• `{f}`" for f in preview.get("files", [])[:15]])
            if len(preview.get("files", [])) > 15:
                file_list += f"\n• ...and {len(preview['files']) - 15} more"

            desc = f"**Type:** {purge_type}\n**Size:** {size_mb} MB\n**Files:** {len(preview.get('files', []))}\n\n{file_list}"

            async def do_purge(inter: discord.Interaction):
                try:
                    result = await pm.execute(purge_type)
                    embed = make_panel_embed("Purge Complete", f"Removed **{size_mb} MB** from {purge_type}.", 0x2ECC71)
                    await inter.followup.send(embed=embed, ephemeral=True)
                except Exception as e:
                    await inter.followup.send(f"❌ Purge failed: {e}", ephemeral=True)

            confirm = ConfirmView(on_confirm_callback=do_purge)
            embed = make_panel_embed(f"Preview - {purge_type}", desc, 0xF39C12)
            await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Cache", style=discord.ButtonStyle.secondary, emoji="💾", row=0)
    async def purge_cache(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "cache")

    @discord.ui.button(label="Temp", style=discord.ButtonStyle.secondary, emoji="📁", row=0)
    async def purge_temp(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "temp")

    @discord.ui.button(label="Old Logs", style=discord.ButtonStyle.secondary, emoji="📜", row=0)
    async def purge_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "logs")

    @discord.ui.button(label="Old Backups", style=discord.ButtonStyle.secondary, emoji="📦", row=0)
    async def purge_backups(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "backups")

    @discord.ui.button(label="Docker Containers", style=discord.ButtonStyle.secondary, emoji="🐳", row=1)
    async def purge_containers(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "docker_containers")

    @discord.ui.button(label="Docker Images", style=discord.ButtonStyle.secondary, emoji="🖼️", row=1)
    async def purge_images(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "docker_images")

    @discord.ui.button(label="Docker Volumes", style=discord.ButtonStyle.secondary, emoji="📀", row=1)
    async def purge_volumes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "docker_volumes")

    @discord.ui.button(label="Build Cache", style=discord.ButtonStyle.secondary, emoji="🔨", row=1)
    async def purge_build_cache(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "build_cache")

    @discord.ui.button(label="Safe Cleanup", style=discord.ButtonStyle.success, emoji="✅", row=2)
    async def safe_cleanup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "safe_cleanup")

    @discord.ui.button(label="Deep Cleanup", style=discord.ButtonStyle.danger, emoji="🗑️", row=2)
    async def deep_cleanup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await self._preview_and_execute(interaction, "deep_cleanup")

    @discord.ui.button(label="Full Preview", style=discord.ButtonStyle.primary, emoji="👁️", row=2)
    async def full_preview(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.purge import PurgeManager
            pm = PurgeManager(self.bot)
            report = await pm.full_preview()
            fields = []
            total_mb = 0
            for name, data in report.items():
                mb = round(data.get("total_bytes", 0) / (1024 * 1024), 2)
                total_mb += mb
                fields.append(metric_field(name, f"{mb} MB • {len(data.get('files', []))} files", "info" if mb > 0 else "ok"))
            fields.append(metric_field("Total", f"{round(total_mb, 2)} MB", "warning"))
            embed = make_panel_embed("Full Disk Preview", "Estimated space that can be reclaimed:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class TroubleshootPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot
        self.last_results: Dict[str, Any] = {}

    def _build_embed(self, results: Dict = None) -> discord.Embed:
        if not results:
            return make_panel_embed(
                "🔍 Troubleshoot Panel",
                "Run diagnostics to identify and fix issues automatically.",
                0x9866A5,
            )
        fields = []
        problems_found = 0
        for check_name, result in results.items():
            status = result.get("status", "unknown")
            message = result.get("message", "No details")
            icon = "✅" if status == "ok" else "⚠️" if status == "warning" else "❌" if status == "error" else "ℹ️"
            if status in ("warning", "error"):
                problems_found += 1
            fields.append({"name": f"{icon} {check_name}", "value": message, "inline": True})
        color = 0x2ECC71 if problems_found == 0 else 0xF39C12 if problems_found < 3 else 0xE74C3C
        desc = f"**{problems_found}** problem(s) found." if problems_found else "All checks passed!"
        return make_panel_embed("🔍 Diagnostic Results", desc, color, fields)

    @discord.ui.button(label="Run Full Diagnostics", style=discord.ButtonStyle.primary, emoji="🔍", row=0)
    async def run_diagnostics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.troubleshoot import Troubleshooter
            ts = Troubleshooter(self.bot)
            results = await ts.run_all_checks()
            self.last_results = results
            embed = self._build_embed(results)
            problems = sum(1 for r in results.values() if r.get("status") in ("warning", "error"))
            view = TroubleshootPanel(self.bot)
            view.last_results = results
            if problems > 0:
                view.add_item(discord.ui.Button(label="Auto-Fix", style=discord.ButtonStyle.success, emoji="🔧", row=1))
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class IncidentsPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self) -> discord.Embed:
        return make_panel_embed(
            "📋 Incidents Panel",
            "View incident history, statistics, and manage old records.",
            0x9866A5,
        )

    @discord.ui.button(label="Recent Incidents", style=discord.ButtonStyle.primary, emoji="📋", row=0)
    async def recent_incidents(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.incidents import IncidentManager
            im = IncidentManager(self.bot)
            incidents = await im.get_recent(limit=10)
            if not incidents:
                embed = make_panel_embed("No Incidents", "No recent incidents found.", 0x2ECC71)
                return await interaction.followup.send(embed=embed, ephemeral=True)
            fields = []
            for inc in incidents[:10]:
                ts = inc.get("timestamp", "N/A")
                severity = inc.get("severity", "info")
                desc_text = inc.get("description", "No description")[:80]
                fields.append(metric_field(f"{inc.get('title', 'Incident')}", f"`{ts}` - {desc_text}", severity))
            embed = make_panel_embed("Recent Incidents", f"Last {len(incidents)} incidents:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Stats", style=discord.ButtonStyle.secondary, emoji="📊", row=0)
    async def incident_stats(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.incidents import IncidentManager
            im = IncidentManager(self.bot)
            stats = await im.get_stats()
            fields = [
                metric_field("Total Incidents", str(stats.get("total", 0)), "info"),
                metric_field("Critical", str(stats.get("critical", 0)), "critical"),
                metric_field("Warning", str(stats.get("warning", 0)), "warning"),
                metric_field("Info", str(stats.get("info", 0)), "ok"),
                metric_field("Last 24h", str(stats.get("last_24h", 0)), "info"),
                metric_field("Last 7d", str(stats.get("last_7d", 0)), "info"),
                metric_field("Avg Resolution", stats.get("avg_resolution", "N/A"), "info"),
                metric_field("Most Common", stats.get("most_common", "N/A"), "info"),
            ]
            embed = make_panel_embed("📊 Incident Statistics", "Overview of all incidents:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Clear Old", style=discord.ButtonStyle.danger, emoji="🗑️", row=0)
    async def clear_old(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.incidents import IncidentManager
            im = IncidentManager(self.bot)
            old_count = await im.count_old(days=30)
            if old_count == 0:
                embed = make_panel_embed("Nothing to Clear", "No incidents older than 30 days found.", 0x2ECC71)
                return await interaction.followup.send(embed=embed, ephemeral=True)

            async def do_clear(inter: discord.Interaction):
                try:
                    from core.incidents import IncidentManager as IM2
                    mgr = IM2(self.bot)
                    cleared = await mgr.clear_old(days=30)
                    await inter.followup.send(f"🗑️ Cleared **{cleared}** old incidents.", ephemeral=True)
                except Exception as e:
                    await inter.followup.send(f"❌ {e}", ephemeral=True)

            confirm = ConfirmView(on_confirm_callback=do_clear)
            embed = make_panel_embed(
                "⚠️ Clear Old Incidents",
                f"Found **{old_count}** incidents older than 30 days. Delete them?",
                0xF39C12,
            )
            await interaction.followup.send(embed=embed, view=confirm, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class HealthMonitorPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, report: Dict = None) -> discord.Embed:
        if not report:
            return make_panel_embed(
                "💓 Health Monitor",
                "System health dashboard with real-time metrics.",
                0x9866A5,
            )
        fields = [metric_field(k, v.get("value", "N/A"), v.get("status", "info")) for k, v in report.items()]
        overall = "healthy"
        for v in report.values():
            if v.get("status") == "critical":
                overall = "critical"
                break
            elif v.get("status") == "warning":
                overall = "warning"
        return make_panel_embed("💓 Health Monitor", f"Overall: **{overall.title()}**", status_color(overall), fields)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=0)
    async def refresh_health(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.health_monitor import HealthMonitor
            hm = HealthMonitor(self.bot)
            report = await hm.run_full_check()
            embed = self._build_embed(report)
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Alerts", style=discord.ButtonStyle.secondary, emoji="🔔", row=0)
    async def show_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.health_monitor import HealthMonitor
            hm = HealthMonitor(self.bot)
            alerts = await hm.get_recent_alerts(limit=10)
            if not alerts:
                embed = make_panel_embed("No Alerts", "No recent health alerts.", 0x2ECC71)
                return await interaction.followup.send(embed=embed, ephemeral=True)
            fields = []
            for alert in alerts[:10]:
                ts = alert.get("timestamp", "N/A")
                msg = alert.get("message", "Unknown alert")[:80]
                sev = alert.get("severity", "info")
                fields.append(metric_field(f"Alert at {ts}", msg, sev))
            embed = make_panel_embed("🔔 Recent Alerts", f"Last {len(alerts)} alerts:", 0xF39C12, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Thresholds", style=discord.ButtonStyle.secondary, emoji="⚙️", row=0)
    async def show_thresholds(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.health_monitor import HealthMonitor
            hm = HealthMonitor(self.bot)
            thresholds = await hm.get_thresholds()
            fields = []
            for name, config in thresholds.items():
                warn = config.get("warning", "N/A")
                crit = config.get("critical", "N/A")
                unit = config.get("unit", "")
                fields.append({"name": name, "value": f"⚠️ Warning: {warn}{unit}\n🔴 Critical: {crit}{unit}", "inline": True})
            embed = make_panel_embed("⚙️ Health Thresholds", "Current alert thresholds:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class MaintenancePanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, status_info: Dict = None) -> discord.Embed:
        if not status_info:
            status_info = {}
        fields = [
            metric_field("Maintenance Mode", status_info.get("enabled", "Unknown"), "ok" if not status_info.get("enabled") else "warning"),
            metric_field("Docker", status_info.get("docker_status", "Unknown"), "info"),
            metric_field("Last Cache Clear", status_info.get("last_cache_clear", "Never"), "info"),
            metric_field("Last Log Clean", status_info.get("last_log_clean", "Never"), "info"),
            metric_field("Dependencies", status_info.get("deps_status", "Unknown"), "info"),
            metric_field("DB Backup", status_info.get("last_db_backup", "Never"), "info"),
        ]
        return make_panel_embed(
            "🔧 Maintenance Center",
            "System maintenance and operational utilities.",
            0x9866A5,
            fields,
        )

    @discord.ui.button(label="Enable Maintenance", style=discord.ButtonStyle.secondary, emoji="🚧", row=0)
    async def enable_maintenance(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_enable_maintenance)
        embed = make_panel_embed("⚠️ Enable Maintenance Mode", "This will restrict user access. Continue?", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_enable_maintenance(self, interaction: discord.Interaction):
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            await mc.enable_maintenance()
            await interaction.followup.send("🚧 Maintenance mode **enabled**.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Disable Maintenance", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def disable_maintenance(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            await mc.disable_maintenance()
            embed = make_panel_embed("Maintenance Disabled", "Normal operations resumed.", 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Restart Docker", style=discord.ButtonStyle.danger, emoji="🐳", row=1)
    async def restart_docker(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        confirm = ConfirmView(on_confirm_callback=self._do_restart_docker)
        embed = make_panel_embed("⚠️ Restart Docker", "Restart Docker daemon? Running containers will stop.", 0xF39C12)
        await interaction.response.send_message(embed=embed, view=confirm, ephemeral=True)

    async def _do_restart_docker(self, interaction: discord.Interaction):
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            await mc.restart_docker()
            await interaction.followup.send("🐳 Docker daemon restarted.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ {e}", ephemeral=True)

    @discord.ui.button(label="Clear Cache", style=discord.ButtonStyle.secondary, emoji="🗑️", row=1)
    async def clear_cache(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            result = await mc.clear_cache()
            embed = make_panel_embed("Cache Cleared", result, 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Clean Logs", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def clean_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            result = await mc.clean_logs()
            embed = make_panel_embed("Logs Cleaned", result, 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Run Diagnostics", style=discord.ButtonStyle.primary, emoji="🔍", row=2)
    async def run_diagnostics(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.troubleshoot import Troubleshooter
            ts = Troubleshooter(self.bot)
            results = await ts.run_all_checks()
            problems = sum(1 for r in results.values() if r.get("status") in ("warning", "error"))
            fields = [{"name": k, "value": v.get("message", "N/A"), "inline": True} for k, v in results.items()]
            color = 0x2ECC71 if problems == 0 else 0xF39C12
            embed = make_panel_embed("🔍 Diagnostics Complete", f"**{problems}** issue(s) found.", color, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Health Check", style=discord.ButtonStyle.success, emoji="💓", row=2)
    async def health_check(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.health_monitor import HealthMonitor
            hm = HealthMonitor(self.bot)
            report = await hm.run_full_check()
            fields = [metric_field(k, v.get("value", "N/A"), v.get("status", "info")) for k, v in report.items()]
            embed = make_panel_embed("💓 Health Check", "System health results:", 0x2ECC71, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="DB Backup", style=discord.ButtonStyle.primary, emoji="💾", row=2)
    async def db_backup(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            result = await mc.backup_database()
            embed = make_panel_embed("Database Backup", result, 0x2ECC71)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Check Deps", style=discord.ButtonStyle.secondary, emoji="📦", row=2)
    async def check_deps(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            result = await mc.check_dependencies()
            fields = [{"name": dep, "value": status, "inline": True} for dep, status in result.items()]
            embed = make_panel_embed("📦 Dependencies", "Dependency status check:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Checklist", style=discord.ButtonStyle.primary, emoji="☑️", row=3)
    async def show_checklist(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            checklist = await mc.get_checklist()
            fields = []
            for item in checklist:
                done = "✅" if item.get("done") else "❌"
                fields.append({"name": f"{done} {item.get('task', 'Unknown')}", "value": item.get("description", ""), "inline": False})
            embed = make_panel_embed("☑️ Maintenance Checklist", "Full maintenance task list:", 0x9866A5, fields)
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=3)
    async def refresh_panel(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.maintenance import MaintenanceCenter
            mc = MaintenanceCenter(self.bot)
            status_info = await mc.get_status()
            embed = self._build_embed(status_info)
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            embed = self._build_embed()
            await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class VPSScorePanel(discord.ui.View):
    def __init__(self, bot: commands.Bot, user_id: int):
        super().__init__(timeout=180)
        self.bot = bot
        self.user_id = user_id

    def _grade(self, score: float) -> tuple:
        if score >= 90:
            return "A", "🟢", 0x2ECC71
        if score >= 80:
            return "B", "🔵", 0x3498DB
        if score >= 70:
            return "C", "🟡", 0xF39C12
        if score >= 60:
            return "D", "🟠", 0xE67E22
        return "F", "🔴", 0xE74C3C

    def _bar(self, pct: float) -> str:
        filled = int(pct / 10)
        return "█" * filled + "░" * (10 - filled) + f" {pct:.0f}%"

    def _build_embed(self, scores: dict = None) -> discord.Embed:
        if not scores:
            scores = {}
        fields = []
        avg = 0
        if scores:
            for name, data in scores.items():
                s = data.get("score", 0) if isinstance(data, dict) else data
                avg += s
                grade, icon, _ = self._grade(s)
                bar = self._bar(s)
                fields.append({
                    "name": f"{icon} {name}",
                    "value": f"**Grade: {grade}**\n{bar}",
                    "inline": True,
                })
            avg /= max(len(scores), 1)
        else:
            fields.append({"name": "No Data", "value": "No VPS scores available.", "inline": False})
        g, ic, c = self._grade(avg) if scores else ("-", "⚪", 0x95A5A6)
        return make_panel_embed(
            f"📊 VPS Health Scores (Avg: {ic} {g})",
            "Health grades for all monitored VPS nodes.",
            c, fields,
        )

    async def _fetch_scores(self) -> dict:
        try:
            from core.services import load_json, DATA_DIR
            data = load_json(os.path.join(DATA_DIR, "health_scores.json"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=0)
    async def refresh_scores(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        scores = await self._fetch_scores()
        embed = self._build_embed(scores)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.select(placeholder="Detail: select a VPS...", row=1, options=[])
    async def detail_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        scores = await self._fetch_scores()
        chosen = select.values[0]
        detail = scores.get(chosen, {})
        if isinstance(detail, dict):
            fields = []
            for k, v in detail.items():
                if k == "score":
                    g, ic, _ = self._grade(v)
                    fields.append({"name": "Grade", "value": f"{ic} {g} ({v:.0f}%)", "inline": True})
                elif isinstance(v, (int, float)):
                    fields.append({"name": k.replace("_", " ").title(), "value": f"{v:.1f}", "inline": True})
                else:
                    fields.append({"name": k.replace("_", " ").title(), "value": str(v), "inline": True})
            embed = make_panel_embed(f"🔍 {chosen} - Detail", "", 0x9866A5, fields)
        else:
            embed = make_panel_embed(f"🔍 {chosen}", f"Score: {detail}", 0x9866A5)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def _populate_select_options(self):
        scores = await self._fetch_scores()
        self.detail_select.options = [
            discord.SelectOption(label=n[:100], value=n, description=f"Score: {d.get('score', d):.0f}%" if isinstance(d, dict) else f"Score: {d}")
            for n, d in list(scores.items())[:25]
        ] or [discord.SelectOption(label="None", value="_none_")]

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class SmartDeployPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, nodes: list = None, preview: str = None) -> discord.Embed:
        if nodes:
            fields = []
            for n in nodes:
                name = n.get("name", "Unknown")
                cap = n.get("capacity", n.get("load", "?"))
                health = n.get("health", "unknown")
                icon = "🟢" if health in ("healthy", "online") else "🟡" if health == "warning" else "🔴"
                fields.append({"name": f"{icon} {name}", "value": f"Capacity: {cap}\nHealth: {health}", "inline": True})
        else:
            fields = [{"name": "No Nodes", "value": "No node data available.", "inline": False}]
        desc = preview or "Select a node to preview deployment placement."
        return make_panel_embed("🚀 Smart Deploy - Node Preview", desc, 0x9866A5, fields)

    async def _fetch_nodes(self) -> list:
        try:
            from core.services import load_json, DATA_DIR
            data = load_json(os.path.join(DATA_DIR, "nodes.json"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @discord.ui.button(label="Preview Deploy", style=discord.ButtonStyle.success, emoji="🎯", row=0)
    async def preview_deploy(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        nodes = await self._fetch_nodes()
        best = None
        best_score = -1
        for n in nodes:
            if n.get("health") in ("offline", "critical"):
                continue
            cap = n.get("capacity", n.get("load", 50))
            if isinstance(cap, str):
                try:
                    cap = int(cap.replace("%", ""))
                except ValueError:
                    cap = 50
            score = 100 - cap
            if score > best_score:
                best_score = score
                best = n
        preview = f"**Best node:** {best.get('name', 'N/A')}" if best else "No suitable node found."
        embed = self._build_embed(nodes, preview)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.secondary, emoji="🔃", row=0)
    async def refresh_nodes(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        nodes = await self._fetch_nodes()
        embed = self._build_embed(nodes)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class PredictivePanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, predictions: dict = None) -> discord.Embed:
        if not predictions:
            return make_panel_embed(
                "🔮 Predictive Analytics",
                "No prediction data available. Click **Refresh** to fetch.",
                0x95A5A6,
            )
        fields = []
        trends = predictions.get("trends", [])
        for t in trends[:8]:
            metric = t.get("metric", "Unknown")
            direction = t.get("direction", "→")
            estimate = t.get("estimate", "N/A")
            breach = t.get("breach_in", None)
            val = f"Direction: {direction}\nEstimate: {estimate}"
            if breach:
                val += f"\n⚠️ Threshold breach in **{breach}**"
            fields.append({"name": metric, "value": val, "inline": True})
        if not fields:
            fields.append({"name": "Status", "value": "All metrics nominal.", "inline": False})
        return make_panel_embed("🔮 Predictive Analytics", "Forecasted trends and threshold estimates.", 0x9866A5, fields)

    async def _fetch_predictions(self) -> dict:
        try:
            from core.services import load_json, DATA_DIR
            return load_json(os.path.join(DATA_DIR, "predictions.json")) or {}
        except Exception:
            return {}

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=0)
    async def refresh_predictions(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = await self._fetch_predictions()
        embed = self._build_embed(data)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class AnomalyPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, data: dict = None, history: bool = False) -> discord.Embed:
        if not data:
            return make_panel_embed("🧬 Anomaly Detection", "No data. Click **Scan Now** or **History**.", 0x95A5A6)
        title = "🧬 Anomaly Detection - History" if history else "🧬 Anomaly Detection"
        summary = data.get("summary", data.get("message", ""))
        anomalies = data.get("anomalies", [])
        fields = []
        if summary:
            fields.append({"name": "Summary", "value": str(summary), "inline": False})
        for a in anomalies[:8]:
            sev = a.get("severity", "info")
            ic = "🔴" if sev in ("critical", "high") else "🟡" if sev == "medium" else "🔵"
            fields.append({
                "name": f"{ic} {a.get('name', 'Anomaly')}",
                "value": a.get("description", f"Severity: {sev}"),
                "inline": False,
            })
        if not fields:
            fields.append({"name": "Status", "value": "No anomalies detected.", "inline": False})
        c = 0xE74C3C if any(a.get("severity") in ("critical", "high") for a in anomalies) else 0x9866A5
        return make_panel_embed(title, "", c, fields)

    async def _fetch_data(self, history: bool = False) -> dict:
        try:
            from core.services import load_json, DATA_DIR
            fname = "anomaly_history.json" if history else "anomalies.json"
            return load_json(os.path.join(DATA_DIR, fname)) or {}
        except Exception:
            return {}

    @discord.ui.button(label="Scan Now", style=discord.ButtonStyle.success, emoji="🔬", row=0)
    async def scan_now(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.services import load_json, DATA_DIR, save_json
            metrics = load_json(os.path.join(DATA_DIR, "metrics.json")) or {}
            anomalies = []
            for key, val in metrics.items():
                if isinstance(val, (int, float)) and val > 95:
                    anomalies.append({"name": key, "severity": "high", "description": f"Value {val} exceeds threshold."})
                elif isinstance(val, dict):
                    for sub, sv in val.items():
                        if isinstance(sv, (int, float)) and sv > 95:
                            anomalies.append({"name": f"{key}.{sub}", "severity": "medium", "description": f"Value {sv} is elevated."})
            result = {"summary": f"Scan complete. {len(anomalies)} anomaly(ies) found.", "anomalies": anomalies}
            try:
                save_json(os.path.join(DATA_DIR, "anomalies.json"), result)
            except Exception:
                pass
        except Exception as e:
            result = {"summary": f"Scan failed: {e}", "anomalies": []}
        embed = self._build_embed(result)
        await interaction.edit_original_response(embed=embed, view=self)

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, emoji="📜", row=0)
    async def show_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        data = await self._fetch_data(history=True)
        embed = self._build_embed(data, history=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class FullScanPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=300)
        self.bot = bot
        self.scan_done = False
        self.results = {}

    def _health_icon(self, status: str) -> str:
        return {"healthy": "🟢", "warning": "🟡", "critical": "🔴", "offline": "🔴"}.get(status, "⚪")

    def _build_embed(self, results: dict = None, running: bool = False) -> discord.Embed:
        if running:
            return make_panel_embed("🔍 Full System Scan", "⏳ Scanning in progress... please wait.", 0xF39C12)
        if not results:
            return make_panel_embed("🔍 Full System Scan", "Click **Run Scan** to perform a full system health check.", 0x95A5A6)
        overall = results.get("overall", "unknown")
        overall_icon = self._health_icon(overall)
        fields = []
        for check in results.get("checks", []):
            name = check.get("name", "Check")
            status = check.get("status", "unknown")
            detail = check.get("detail", "")
            fields.append({"name": f"{self._health_icon(status)} {name}", "value": detail or status, "inline": True})
        if not fields:
            fields.append({"name": "Result", "value": "Scan completed with no details.", "inline": False})
        return make_panel_embed(
            f"🔍 Full System Scan - {overall_icon} {overall.title()}",
            results.get("summary", "Scan completed."),
            status_color(overall), fields,
        )

    @discord.ui.button(label="Run Scan", style=discord.ButtonStyle.success, emoji="🔬", row=0)
    async def run_scan(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = self._build_embed(running=True)
        await interaction.edit_original_response(embed=embed, view=self)
        try:
            from core.services import load_json, DATA_DIR
            checks = []
            services_file = os.path.join(DATA_DIR, "services.json")
            services = load_json(services_file) or []
            svc_list = services if isinstance(services, list) else services.get("services", [])
            for svc in svc_list:
                name = svc.get("name", "Service")
                status = svc.get("status", "unknown")
                checks.append({"name": name, "status": status, "detail": svc.get("detail", f"Status: {status}")})
            if not checks:
                checks.append({"name": "General", "status": "healthy", "detail": "No services configured. System OK."})
            statuses = [c["status"] for c in checks]
            if "critical" in statuses:
                overall = "critical"
            elif "warning" in statuses or "degraded" in statuses:
                overall = "warning"
            elif all(s == "offline" for s in statuses):
                overall = "offline"
            else:
                overall = "healthy"
            count = len(checks)
            healthy = sum(1 for s in statuses if s in ("healthy", "online"))
            self.results = {
                "overall": overall,
                "summary": f"{healthy}/{count} services healthy.",
                "checks": checks,
            }
            self.scan_done = True
        except Exception as e:
            self.results = {"overall": "critical", "summary": f"Scan failed: {e}", "checks": []}
        embed = self._build_embed(self.results)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class DryRunPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot, action_type: str, params: dict = None):
        super().__init__(timeout=180)
        self.bot = bot
        self.action_type = action_type
        self.params = params or {}
        self.result = None

    def _risk_level(self, action: str) -> tuple:
        high_risk = ("purge", "delete", "destroy", "migrate", "reinstall")
        med_risk = ("restart", "update", "scale", "config_change")
        act = action.lower()
        for r in high_risk:
            if r in act:
                return "High", "🔴", 0xE74C3C
        for r in med_risk:
            if r in act:
                return "Medium", "🟡", 0xF39C12
        return "Low", "🟢", 0x2ECC71

    def _build_embed(self) -> discord.Embed:
        risk_name, risk_icon, risk_color = self._risk_level(self.action_type)
        fields = [
            {"name": "Action", "value": self.action_type, "inline": True},
            {"name": "Risk Level", "value": f"{risk_icon} {risk_name}", "inline": True},
        ]
        if self.params:
            param_str = "\n".join(f"• **{k}**: {v}" for k, v in list(self.params.items())[:10])
            fields.append({"name": "Parameters", "value": param_str, "inline": False})
        warnings = []
        if "purge" in self.action_type.lower():
            warnings.append("⚠️ This will permanently delete data.")
        if "migrate" in self.action_type.lower():
            warnings.append("⚠️ Downtime may occur during migration.")
        if "reinstall" in self.action_type.lower():
            warnings.append("⚠️ All existing data will be wiped.")
        if warnings:
            fields.append({"name": "Warnings", "value": "\n".join(warnings), "inline": False})
        fields.append({"name": "Result", "value": "Click **Confirm** to proceed or **Cancel** to abort.", "inline": False})
        return make_panel_embed(f"🧪 Dry Run - {self.action_type}", "Preview of planned action.", risk_color, fields)

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success, emoji="✅", row=0)
    async def confirm_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.result = "confirmed"
        embed = make_panel_embed(f"✅ Confirmed - {self.action_type}", "Action confirmed by operator.", 0x2ECC71)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger, emoji="❌", row=0)
    async def cancel_action(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        self.result = "cancelled"
        embed = make_panel_embed(f"❌ Cancelled - {self.action_type}", "Action aborted by operator.", 0x95A5A6)
        await interaction.followup.send(embed=embed, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class ConfigHistoryPanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot
        self.versions = []
        self.selected_a = None
        self.selected_b = None

    def _build_embed(self) -> discord.Embed:
        fields = []
        for i, v in enumerate(self.versions[:10]):
            ts = v.get("timestamp", "Unknown")
            label = v.get("label", f"Version {len(self.versions) - i}")
            ch = v.get("changed_by", "system")
            fields.append({"name": label, "value": f"⏰ {ts}\nBy: {ch}", "inline": True})
        if not fields:
            fields.append({"name": "No Versions", "value": "No configuration history available.", "inline": False})
        desc = ""
        if self.selected_a is not None and self.selected_b is not None:
            desc = f"Comparing: **{self.selected_a}** vs **{self.selected_b}**"
        return make_panel_embed("⏱️ Config Time Machine", desc or "Select versions to compare or restore.", 0x9866A5, fields)

    async def _fetch_versions(self) -> list:
        try:
            from core.services import load_json, DATA_DIR
            data = load_json(os.path.join(DATA_DIR, "config_history.json"))
            return data if isinstance(data, list) else []
        except Exception:
            return []

    @discord.ui.select(placeholder="Select a version...", row=0, options=[])
    async def version_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        val = select.values[0]
        if self.selected_a is None:
            self.selected_a = val
            await interaction.response.send_message(f"Selected **{val}** as first version. Now select second to compare.", ephemeral=True)
        else:
            self.selected_b = val
            await interaction.response.defer(ephemeral=True)
            await self._show_diff(interaction)

    async def _show_diff(self, interaction: discord.Interaction):
        try:
            v_a = next((v for v in self.versions if v.get("label") == self.selected_a), {})
            v_b = next((v for v in self.versions if v.get("label") == self.selected_b), {})
            data_a = v_a.get("data", {})
            data_b = v_b.get("data", {})
            diffs = []
            all_keys = set(list(data_a.keys()) + list(data_b.keys()))
            for k in sorted(all_keys):
                va = data_a.get(k, "-")
                vb = data_b.get(k, "-")
                if va != vb:
                    diffs.append(f"**{k}**: `{va}` → `{vb}`")
            if not diffs:
                diffs = ["No differences found."]
            embed = make_panel_embed(
                f"📊 Diff: {self.selected_a} → {self.selected_b}",
                "\n".join(diffs[:20]), 0x9866A5,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
            await interaction.followup.send(embed=embed, ephemeral=True)
        self.selected_a = None
        self.selected_b = None

    @discord.ui.button(label="Compare", style=discord.ButtonStyle.secondary, emoji="📊", row=1)
    async def compare_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        if self.selected_a is None or self.selected_b is None:
            return await interaction.response.send_message("Select two versions from the dropdown first.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        await self._show_diff(interaction)

    @discord.ui.button(label="Restore", style=discord.ButtonStyle.primary, emoji="🔄", row=1)
    async def restore_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        if self.selected_a is None:
            return await interaction.response.send_message("Select a version from the dropdown first.", ephemeral=True)
        embed = make_panel_embed(
            f"⚠️ Restore: {self.selected_a}?",
            "This will overwrite current config. React to confirm.",
            0xF39C12,
        )
        view = ConfirmView(
            on_confirm_callback=self._do_restore,
            message=f"Restore to {self.selected_a}?",
            timeout=60,
        )
        view.bot = self.bot
        view.version_label = self.selected_a
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def _do_restore(self, interaction: discord.Interaction):
        try:
            from core.services import load_json, save_json, DATA_DIR
            versions = await self._fetch_versions()
            target = next((v for v in versions if v.get("label") == interaction.view.version_label), None)
            if not target:
                await interaction.followup.send("Version not found.", ephemeral=True)
                return
            data = target.get("data", {})
            config_path = os.path.join(DATA_DIR, "config.json")
            current = load_json(config_path) or {}
            current.update(data)
            save_json(config_path, current)
            await interaction.followup.send(f"✅ Restored to **{interaction.view.version_label}**.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ Restore failed: {e}", ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class StatusPagePanel(discord.ui.View):
    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot

    def _build_embed(self, services: list = None) -> discord.Embed:
        if not services:
            return make_panel_embed("📡 Status Page", "No service data. Click **Refresh**.", 0x95A5A6)
        fields = []
        up_count = 0
        total = len(services)
        for svc in services:
            name = svc.get("name", "Service")
            status = svc.get("status", "unknown")
            uptime = svc.get("uptime", None)
            icon = "🟢" if status in ("healthy", "online") else "🟡" if status in ("warning", "degraded") else "🔴" if status in ("critical", "offline") else "⚪"
            val = f"Status: {status}"
            if uptime is not None:
                val += f"\nUptime: **{uptime}%**"
                if isinstance(uptime, (int, float)) and uptime >= 95:
                    up_count += 1
            else:
                up_count += 1 if status in ("healthy", "online") else 0
            fields.append({"name": f"{icon} {name}", "value": val, "inline": True})
        overall_pct = (up_count / total * 100) if total > 0 else 0
        header = f"Overall: {up_count}/{total} services operational ({overall_pct:.1f}% uptime)"
        c = 0x2ECC71 if overall_pct >= 99 else 0xF39C12 if overall_pct >= 90 else 0xE74C3C
        return make_panel_embed("📡 Public Status Page", header, c, fields)

    async def _fetch_services(self) -> list:
        try:
            from core.services import load_json, DATA_DIR
            data = load_json(os.path.join(DATA_DIR, "services.json"))
            if isinstance(data, list):
                return data
            return data.get("services", []) if isinstance(data, dict) else []
        except Exception:
            return []

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, emoji="🔃", row=0)
    async def refresh_status(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        services = await self._fetch_services()
        embed = self._build_embed(services)
        await interaction.edit_original_response(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class MaintenanceAdvPanel(discord.ui.View):
    SCOPES = [
        ("🌐", "Global"),
        ("🖥️", "Nodes"),
        ("💾", "Storage"),
        ("🔒", "Security"),
        ("📡", "Network"),
        ("📊", "Monitoring"),
        ("🔧", "Services"),
        ("🗄️", "Database"),
        ("📁", "Files"),
    ]

    def __init__(self, bot: commands.Bot):
        super().__init__(timeout=180)
        self.bot = bot
        self.active = False
        self.scope_states = {s[1]: False for s in self.SCOPES}

    def _build_embed(self) -> discord.Embed:
        state_icon = "🟢 ACTIVE" if self.active else "⚪ INACTIVE"
        fields = []
        for emoji, scope in self.SCOPES:
            on = self.scope_states.get(scope, False)
            fields.append({"name": f"{emoji} {scope}", "value": "✅ Enabled" if on else "❌ Disabled", "inline": True})
        return make_panel_embed(
            f"⚙️ Advanced Maintenance - {state_icon}",
            "Scoped maintenance toggles. Toggle individual scopes or use bulk actions.",
            0x2ECC71 if self.active else 0x95A5A6, fields,
        )

    @discord.ui.button(label="Enable All", style=discord.ButtonStyle.success, emoji="🟢", row=0)
    async def enable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        self.active = True
        for k in self.scope_states:
            self.scope_states[k] = True
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Disable All", style=discord.ButtonStyle.danger, emoji="🔴", row=0)
    async def disable_all(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        self.active = False
        for k in self.scope_states:
            self.scope_states[k] = False
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, emoji="📜", row=1)
    async def show_history(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            from core.services import load_json, DATA_DIR
            history = load_json(os.path.join(DATA_DIR, "maintenance_history.json")) or []
            entries = history if isinstance(history, list) else []
            fields = []
            for entry in entries[:6]:
                action = entry.get("action", "Unknown")
                ts = entry.get("timestamp", "Unknown")
                who = entry.get("user", "system")
                fields.append({"name": action, "value": f"⏰ {ts}\nBy: {who}", "inline": False})
            if not fields:
                fields.append({"name": "No History", "value": "No maintenance events recorded.", "inline": False})
            embed = make_panel_embed("📜 Maintenance History", "", 0x9866A5, fields)
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="Schedule", style=discord.ButtonStyle.primary, emoji="📅", row=1)
    async def schedule_maintenance(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        modal = ScheduleMaintenanceModal(self.bot)
        await interaction.response.send_modal(modal)

    @discord.ui.select(placeholder="Toggle a scope...", row=2, options=[])
    async def scope_select(self, interaction: discord.Interaction, select: discord.ui.Select):
        if not _check_permission(interaction.user):
            return await interaction.response.send_message("❌ No permission.", ephemeral=True)
        scope = select.values[0]
        current = self.scope_states.get(scope, False)
        self.scope_states[scope] = not current
        embed = self._build_embed()
        await interaction.response.edit_message(embed=embed, view=self)

    async def _populate_options(self):
        self.scope_select.options = [
            discord.SelectOption(label=f"{e} {s}", value=s, description="Enabled" if self.scope_states.get(s) else "Disabled")
            for e, s in self.SCOPES
        ]

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class ScheduleMaintenanceModal(discord.ui.Modal, title="Schedule Maintenance"):
    schedule_time = discord.ui.TextInput(
        label="Schedule (ISO format)",
        placeholder="e.g. 2026-08-20T02:00:00Z",
        required=True,
        max_length=32,
    )
    schedule_note = discord.ui.TextInput(
        label="Note (optional)",
        placeholder="Reason for maintenance...",
        required=False,
        max_length=256,
        style=discord.TextStyle.paragraph,
    )

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_submit(self, interaction: discord.Interaction):
        try:
            from core.services import load_json, save_json, DATA_DIR
            hist_path = os.path.join(DATA_DIR, "maintenance_history.json")
            history = load_json(hist_path) or []
            if not isinstance(history, list):
                history = []
            history.append({
                "action": f"Scheduled maintenance for {self.schedule_time.value}",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "user": str(interaction.user),
                "note": self.schedule_note.value or "",
            })
            save_json(hist_path, history)
            embed = make_panel_embed(
                "✅ Maintenance Scheduled",
                f"⏰ **When:** {self.schedule_time.value}\n📝 **Note:** {self.schedule_note.value or 'None'}",
                0x2ECC71,
            )
        except Exception as e:
            embed = make_panel_embed("Error", str(e), 0xE74C3C)
        await interaction.response.send_message(embed=embed, ephemeral=True)