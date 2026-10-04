"""Discord GUI Layer - Views, Selects, Modals, and pagination for Turtle Nodes."""
import os
import asyncio
import logging
from datetime import datetime, timezone

import discord
import discord.ui

logger = logging.getLogger("turtle.gui")

_EMOJI_MAP = {
    "healthy": "\U0001f7e2", "ok": "\U0001f7e2", "online": "\U0001f7e2",
    "running": "\U0001f7e2", "connected": "\U0001f7e2",
    "warning": "\U0001f7e1", "degraded": "\U0001f7e1",
    "critical": "\U0001f534", "error": "\U0001f534",
    "offline": "\u26ab", "disconnected": "\u26ab", "unknown": "\u26ab",
}


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------

def make_embed(title, description="", color=0x1A1A1A, fields=None, footer=None):
    embed = discord.Embed(title=title, description=description, color=color,
                          timestamp=datetime.now(timezone.utc))
    if fields:
        for f in fields:
            if isinstance(f, dict):
                embed.add_field(name=f.get("name", "\u200b"),
                                value=f.get("value", "\u200b"),
                                inline=f.get("inline", True))
    if footer:
        embed.set_footer(text=footer)
    else:
        embed.set_footer(text="Turtle Nodes Control Panel")
    return embed


def status_emoji(status):
    return _EMOJI_MAP.get(str(status).lower(), "\u26ab")


def progress_bar(percent, length=10):
    filled = round(max(0, min(100, percent)) / 100 * length)
    return f"{'█' * filled}{'░' * (length - filled)} {percent:.1f}%"


def format_time(seconds):
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    parts.append(f"{minutes}m")
    return " ".join(parts)


def _get_user_level(user_id):
    from core.services import load_json as _lj, DATA_DIR as _dd
    path = os.path.join(_dd, "admin_levels.json")
    levels = _lj(path, {})
    return levels.get(str(user_id), 0)


def _audit(action, user_id, target="", details="", level="info"):
    try:
        from core.security import audit
        audit.log(action=action, user_id=str(user_id),
                  target=str(target), details=str(details), level=level)
    except Exception:
        pass


def _err_embed(msg):
    return make_embed("\u274c Error", str(msg), color=0xFF3366)


def _success_embed(title, desc=""):
    return make_embed(f"\u2705 {title}", desc, color=0x00FF88)


# ---------------------------------------------------------------------------
# 1. PaginatorView
# ---------------------------------------------------------------------------

class PaginatorView(discord.ui.View):
    def __init__(self, pages, title="", color=0x1A1A1A, timeout=120):
        super().__init__(timeout=timeout)
        self.pages = pages
        self.title = title
        self.color = color
        self.current = 0
        self.max_pages = len(pages)
        self._build_select()

    def _build_select(self):
        if self.max_pages <= 1:
            return
        opts = []
        for i, p in enumerate(self.pages[:25]):
            label = p.get("title", f"Page {i + 1}")[:100]
            opts.append(discord.SelectOption(label=label, value=str(i),
                                             default=(i == self.current)))
        sel = discord.ui.Select(options=opts, placeholder="Jump to page...")
        sel.callback = self._select_callback
        self.add_item(sel)

    async def _select_callback(self, interaction):
        self.current = int(interaction.data["values"][0])
        await self._show_page(interaction)

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary, row=1)
    async def prev_button(self, interaction, button):
        if self.current > 0:
            self.current -= 1
            await self._show_page(interaction)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary, row=1)
    async def next_button(self, interaction, button):
        if self.current < self.max_pages - 1:
            self.current += 1
            await self._show_page(interaction)

    async def _show_page(self, interaction):
        page = self.pages[self.current]
        embed = make_embed(
            page.get("title", self.title),
            page.get("description", ""),
            page.get("color", self.color),
            page.get("fields"),
            footer=f"Page {self.current + 1}/{self.max_pages}",
        )
        await interaction.response.edit_message(embed=embed, view=self)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 2. ConfirmView
# ---------------------------------------------------------------------------

class ConfirmView(discord.ui.View):
    def __init__(self, on_confirm, on_cancel=None, timeout=60):
        super().__init__(timeout=timeout)
        self.on_confirm = on_confirm
        self.on_cancel = on_cancel
        self.result = None
        self.confirmation_done = False

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction, button):
        if self.confirmation_done:
            return
        self.confirmation_done = True
        self.result = True
        for child in self.children:
            child.disabled = True
        if self.on_confirm:
            await self.on_confirm(interaction)
        else:
            await interaction.response.edit_message(view=self)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.danger)
    async def cancel(self, interaction, button):
        if self.confirmation_done:
            return
        self.confirmation_done = True
        self.result = False
        for child in self.children:
            child.disabled = True
        if self.on_cancel:
            await self.on_cancel(interaction)
        else:
            await interaction.response.edit_message(
                embed=_success_embed("Cancelled"), view=self)

    async def on_timeout(self):
        self.confirmation_done = True
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 3. BotControlPanel
# ---------------------------------------------------------------------------

class BotControlPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    def _check(self, user_id):
        return _get_user_level(user_id) >= 90

    async def _run_action(self, interaction, func, label):
        if not self._check(interaction.user.id):
            _audit("permission_denied", interaction.user.id, label, level="warning")
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied. Level 90+ required."), ephemeral=True)
        await interaction.response.defer()
        try:
            result = await func()
            _audit(f"bot_{label}", interaction.user.id, label)
            await interaction.followup.send(embed=_success_embed(label, str(result)))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Start", style=discord.ButtonStyle.success, row=0)
    async def start(self, interaction, button):
        await self._run_action(interaction, lambda: self.bot_core_action("start"), "Start")

    @discord.ui.button(label="Stop", style=discord.ButtonStyle.danger, row=0)
    async def stop(self, interaction, button):
        await self._run_action(interaction, lambda: self.bot_core_action("stop"), "Stop")

    @discord.ui.button(label="Restart", style=discord.ButtonStyle.primary, row=0)
    async def restart(self, interaction, button):
        async def _do():
            from core.bot_core import reload_bot_config
            await reload_bot_config()
            return "Config reloaded. Bot restart scheduled."
        await self._run_action(interaction, _do, "Restart")

    @discord.ui.button(label="Reload", style=discord.ButtonStyle.secondary, row=0)
    async def reload(self, interaction, button):
        async def _do():
            from core.bot_core import reload_bot_config
            return await reload_bot_config()
        await self._run_action(interaction, _do, "Reload Config")

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, row=1)
    async def status(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.bot_core import get_full_status
            data = await get_full_status(self.bot)
            st = data["status"]
            embed = make_embed("\u26a1 Bot Status", "", fields=[
                {"name": "Uptime", "value": f"`{st['uptime']}`", "inline": True},
                {"name": "Latency", "value": f"`{st['latency_ms']}ms`", "inline": True},
                {"name": "Guilds", "value": f"`{st['guild_count']}`", "inline": True},
                {"name": "Gateway", "value": f"`{st['gateway_status']}`", "inline": True},
                {"name": "Health", "value": f"`{'OK' if data['health_ok'] else 'DEGRADED'}`",
                 "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Metrics", style=discord.ButtonStyle.secondary, row=1)
    async def metrics_btn(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.bot_core import get_bot_metrics
            m = await get_bot_metrics(self.bot)
            embed = make_embed("\U0001f4ca Bot Metrics", "", fields=[
                {"name": "Commands Executed", "value": f"`{m['commands_executed']}`", "inline": True},
                {"name": "Errors", "value": f"`{m['errors_count']}`", "inline": True},
                {"name": "VPS Deployed", "value": f"`{m['vps_deployed']}`", "inline": True},
                {"name": "VPS Deleted", "value": f"`{m['vps_deleted']}`", "inline": True},
                {"name": "Events", "value": f"`{m['events_processed']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Health", style=discord.ButtonStyle.secondary, row=1)
    async def health(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.services import health
            results = await asyncio.to_thread(health.run_all)
            fields = []
            for name, r in results.items():
                emoji = status_emoji(r.get("status", "unknown"))
                fields.append({"name": f"{emoji} {name}",
                               "value": f"`{r.get('status', '?').upper()}` - {r.get('detail', 'N/A')}",
                               "inline": True})
            embed = make_embed("\U0001fa7a Health Check", "", fields=fields)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Diagnostics", style=discord.ButtonStyle.secondary, row=1)
    async def diagnostics(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.diagnostics import DiagnosticsRunner
            runner = DiagnosticsRunner(self.bot)
            results = await runner.run_full_diagnostics()
            fields = runner.get_report_embed_fields(results)
            overall = runner.get_overall_status(results)
            embed = make_embed(f"\U0001f50d Diagnostics - {overall.upper()}",
                               "", fields=fields[:25])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def bot_core_action(self, action):
        if action == "start":
            return "Bot is already running."
        elif action == "stop":
            return "Stop requested. Bot will shut down."
        return f"Action '{action}' completed."

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 4. DatabasePanel
# ---------------------------------------------------------------------------

class DatabasePanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot
        self._backup_select = None
        self._build_backup_select()

    def _build_backup_select(self):
        try:
            from core.db_manager import DBManager
            db = DBManager()
            backups = db.list_backups()
        except Exception:
            backups = []
        opts = []
        for b in backups[:25]:
            name = b.get("name", "?")
            size = b.get("size_bytes", 0)
            opts.append(discord.SelectOption(
                label=name[:100], description=f"{size} bytes"))
        if not opts:
            opts.append(discord.SelectOption(label="No backups", value="none"))
        sel = discord.ui.Select(options=opts, placeholder="Select backup...")
        sel.callback = self._backup_callback
        self.add_item(sel)
        self._backup_select = sel

    async def _backup_callback(self, interaction):
        val = interaction.data["values"][0]
        if val == "none":
            return await interaction.response.send_message(
                embed=_err_embed("No backups available."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.backup_center import BackupManager
            from core.db_manager import DBManager
            bm = BackupManager(DBManager())
            result = bm.restore_backup(val)
            _audit("backup_restore", interaction.user.id, val)
            await interaction.followup.send(
                embed=_success_embed("Backup Restored",
                                     f"Restored `{val}` - {result['file_count']} file(s)"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    @discord.ui.button(label="Status", style=discord.ButtonStyle.secondary, row=0)
    async def status(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            db = DBManager()
            s = await asyncio.to_thread(db.get_status)
            embed = make_embed("\U0001f4c2 Database Status", "", fields=[
                {"name": "Files", "value": f"`{s['total_files']}`", "inline": True},
                {"name": "Size", "value": f"`{s['total_size_human']}`", "inline": True},
                {"name": "Health", "value": f"`{s['health_status']}`", "inline": True},
                {"name": "Corrupt", "value": f"`{s['corrupt_count']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Health", style=discord.ButtonStyle.secondary, row=0)
    async def health(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            db = DBManager()
            h = await asyncio.to_thread(db.health_check)
            embed = make_embed("\U0001fa7a Database Health", "", fields=[
                {"name": "Healthy", "value": f"`{h['healthy_count']}`", "inline": True},
                {"name": "Corrupt", "value": f"`{len(h['corrupt_files'])}`", "inline": True},
                {"name": "Total", "value": f"`{h['total_count']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Backup", style=discord.ButtonStyle.success, row=0)
    async def backup(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.backup_center import BackupManager
            from core.db_manager import DBManager
            bm = BackupManager(DBManager())
            result = await asyncio.to_thread(bm.create_backup)
            _audit("backup_create", interaction.user.id, result.get("name", ""))
            await interaction.followup.send(
                embed=_success_embed("Backup Created",
                                     f"`{result['name']}` - {result['file_count']} files"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Restore", style=discord.ButtonStyle.danger, row=0)
    async def restore(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.send_message(
            embed=make_embed("Select a backup below to restore."), ephemeral=True)

    @discord.ui.button(label="Integrity", style=discord.ButtonStyle.secondary, row=1)
    async def integrity(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            db = DBManager()
            report = await asyncio.to_thread(db.integrity_check)
            embed = make_embed("\U0001f50d Integrity Check", "", fields=[
                {"name": "Valid", "value": f"`{report['valid_files']}`", "inline": True},
                {"name": "Invalid", "value": f"`{report['invalid_files']}`", "inline": True},
                {"name": "Total", "value": f"`{report['total_files']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Tables", style=discord.ButtonStyle.secondary, row=1)
    async def tables(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            db = DBManager()
            stats = await asyncio.to_thread(db.get_table_stats)
            lines = [f"`{s['name']}` - {s['record_count']} records, {s['size_bytes']}B"
                     for s in stats[:15]]
            embed = make_embed("\U0001f4cb Tables", "\n".join(lines) or "No tables found.")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Cleanup", style=discord.ButtonStyle.secondary, row=1)
    async def cleanup(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            db = DBManager()
            count = await asyncio.to_thread(db.cleanup_temp)
            _audit("db_cleanup", interaction.user.id, f"removed={count}")
            await interaction.followup.send(
                embed=_success_embed("Cleanup", f"Removed `{count}` temp file(s)"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 5. MonitoringPanel
# ---------------------------------------------------------------------------

class MonitoringPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    @discord.ui.button(label="Refresh", style=discord.ButtonStyle.primary, row=0)
    async def refresh(self, interaction, button):
        await interaction.response.defer()
        try:
            from core.monitoring import MonitoringDashboard
            dashboard = MonitoringDashboard(self.bot)
            embed = await dashboard.render()
            _audit("monitoring_refresh", interaction.user.id)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 6. LogViewer
# ---------------------------------------------------------------------------

class LogViewer(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot
        opts = [
            discord.SelectOption(label="Live", value="live", description="Last 50 lines"),
            discord.SelectOption(label="Errors", value="errors", description="ERROR entries"),
            discord.SelectOption(label="Warnings", value="warnings", description="WARNING entries"),
            discord.SelectOption(label="Admin", value="admin", description="Admin actions"),
            discord.SelectOption(label="DB", value="db", description="Database logs"),
            discord.SelectOption(label="VPS", value="vps", description="VPS operations"),
            discord.SelectOption(label="Payment", value="payment", description="Billing/payment"),
        ]
        sel = discord.ui.Select(options=opts, placeholder="Log type...")
        sel.callback = self._select_callback
        self.add_item(sel)

    async def _select_callback(self, interaction):
        from core.security import security
        uid = interaction.user.id
        if _get_user_level(uid) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        log_type = interaction.data["values"][0]
        await interaction.response.defer()
        try:
            from core.logging_center import LogManager
            lm = LogManager()
            category_map = {
                "live": None, "errors": "ERROR", "warnings": "WARNING",
                "admin": "security", "db": "db", "vps": "vps", "payment": "billing",
            }
            cat = category_map.get(log_type)
            if cat and log_type not in ("errors", "warnings"):
                lines = await asyncio.to_thread(lm.filter_by_category, cat, 30)
            elif cat:
                lines = await asyncio.to_thread(lm.filter_by_level, cat, 30)
            else:
                lines = await asyncio.to_thread(lm.tail, 30)
            content = "".join(lines[-30:]) if lines else "No log entries found."
            if len(content) > 1900:
                content = content[-1900:]
            _audit("log_view", uid, log_type)
            await interaction.followup.send(
                embed=make_embed(f"\U0001f4dc Logs - {log_type.title()}",
                                 f"```\n{content}\n```"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Search", style=discord.ButtonStyle.secondary, row=1)
    async def search(self, interaction, button):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        modal = SearchModal(title="Search Logs", placeholder="Enter search query...")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger, row=1)
    async def clear(self, interaction, button):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        async def _confirm(ia):
            try:
                from core.logging_center import LogManager
                lm = LogManager()
                await asyncio.to_thread(lm.clear)
                _audit("log_clear", ia.user.id)
                await ia.response.edit_message(
                    embed=_success_embed("Logs Cleared"), view=None)
            except Exception as e:
                await ia.response.edit_message(embed=_err_embed(e), view=None)
        view = ConfirmView(on_confirm=_confirm)
        await interaction.response.send_message(
            embed=make_embed("Clear all logs?", "This cannot be undone."),
            view=view, ephemeral=True)

    @discord.ui.button(label="Export", style=discord.ButtonStyle.secondary, row=1)
    async def export(self, interaction, button):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.logging_center import LogManager, LOG_PATH
            import time as _t
            export_path = os.path.join(os.path.dirname(LOG_PATH),
                                       f"export_{int(_t.time())}.log")
            lm = LogManager()
            count = await asyncio.to_thread(lm.export_logs, export_path)
            _audit("log_export", interaction.user.id, f"lines={count}")
            await interaction.followup.send(
                embed=_success_embed("Logs Exported", f"`{count}` lines to `{export_path}`"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 7. EmergencyPanel
# ---------------------------------------------------------------------------

class EmergencyPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot
        self._emg = None

    def _get_manager(self):
        if self._emg is None:
            from core.emergency import EmergencyManager
            self._emg = EmergencyManager(self.bot)
        return self._emg

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    @discord.ui.button(label="Toggle Maintenance", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_maintenance(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        state = mgr.get_state()
        new_val = not state.get("maintenance_mode", False)
        mgr.set_state("maintenance_mode", new_val, interaction.user.id)
        emoji = "\U0001f534" if new_val else "\U0001f7e2"
        await interaction.response.send_message(
            embed=_success_embed("Maintenance Mode",
                                 f"{emoji} Now `{'BLOCKED' if new_val else 'Active'}`"),
            ephemeral=True)

    @discord.ui.button(label="Toggle Purchases", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_purchases(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        state = mgr.get_state()
        new_val = not state.get("purchases_disabled", False)
        mgr.set_state("purchases_disabled", new_val, interaction.user.id)
        emoji = "\U0001f534" if new_val else "\U0001f7e2"
        await interaction.response.send_message(
            embed=_success_embed("Purchases",
                                 f"{emoji} Now `{'BLOCKED' if new_val else 'Active'}`"),
            ephemeral=True)

    @discord.ui.button(label="Toggle VPS Deploy", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_vps(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        state = mgr.get_state()
        new_val = not state.get("vps_deployment_disabled", False)
        mgr.set_state("vps_deployment_disabled", new_val, interaction.user.id)
        emoji = "\U0001f534" if new_val else "\U0001f7e2"
        await interaction.response.send_message(
            embed=_success_embed("VPS Deployment",
                                 f"{emoji} Now `{'BLOCKED' if new_val else 'Active'}`"),
            ephemeral=True)

    @discord.ui.button(label="Toggle Terminal", style=discord.ButtonStyle.secondary, row=0)
    async def toggle_terminal(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        state = mgr.get_state()
        new_val = not state.get("terminal_disabled", False)
        mgr.set_state("terminal_disabled", new_val, interaction.user.id)
        emoji = "\U0001f534" if new_val else "\U0001f7e2"
        await interaction.response.send_message(
            embed=_success_embed("Terminal Access",
                                 f"{emoji} Now `{'BLOCKED' if new_val else 'Active'}`"),
            ephemeral=True)

    @discord.ui.button(label="Full Lockdown", style=discord.ButtonStyle.danger, row=1)
    async def full_lockdown(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        async def _confirm(ia):
            mgr = self._get_manager()
            mgr.full_lockdown(ia.user.id)
            await ia.response.edit_message(
                embed=_success_embed("Full Lockdown",
                                     "All features have been disabled."), view=None)
        view = ConfirmView(on_confirm=_confirm)
        await interaction.response.send_message(
            embed=make_embed("\U0001f6a8 Full Lockdown",
                             "This will disable ALL features. Are you sure?"),
            view=view, ephemeral=True)

    @discord.ui.button(label="Full Unlock", style=discord.ButtonStyle.success, row=1)
    async def full_unlock(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        mgr.full_unlock(interaction.user.id)
        await interaction.response.send_message(
            embed=_success_embed("Full Unlock", "All features have been re-enabled."),
            ephemeral=True)

    @discord.ui.button(label="State", style=discord.ButtonStyle.secondary, row=1)
    async def show_state(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        mgr = self._get_manager()
        fields = mgr.get_status_embed_fields()
        await interaction.response.send_message(
            embed=make_embed("\U0001f6a8 Emergency State", "", fields=fields), ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 8. ConfigPanel
# ---------------------------------------------------------------------------

class ConfigPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot
        self._selected_category = None
        opts = [
            discord.SelectOption(label=cat, value=cat)
            for cat in ["Discord", "Docker", "VPS", "Billing",
                        "Maintenance", "Backups", "Monitoring", "Security", "Logging"]
        ]
        sel = discord.ui.Select(options=opts, placeholder="Select category...")
        sel.callback = self._cat_callback
        self.add_item(sel)

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    async def _cat_callback(self, interaction):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        self._selected_category = interaction.data["values"][0]
        try:
            from core.config_center import ConfigManager
            cm = ConfigManager()
            cat_data = cm.get_category(self._selected_category)
            fields = [{"name": f"`{k}`", "value": f"`{v}`", "inline": True}
                      for k, v in cat_data.items()]
            await interaction.response.send_message(
                embed=make_embed(f"\u2699\ufe0f {self._selected_category}", "", fields=fields),
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    @discord.ui.button(label="Edit", style=discord.ButtonStyle.primary, row=1)
    async def edit(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        modal = ConfigEditModal(
            key=self._selected_category or "MAIN_ADMIN_ID",
            current_value="",
        )
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Reset", style=discord.ButtonStyle.danger, row=1)
    async def reset(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        async def _confirm(ia):
            try:
                from core.config_center import ConfigManager
                cm = ConfigManager()
                cm.reset_all()
                _audit("config_reset_all", ia.user.id)
                await ia.response.edit_message(
                    embed=_success_embed("Config Reset", "All values restored to defaults."),
                    view=None)
            except Exception as e:
                await ia.response.edit_message(embed=_err_embed(e), view=None)
        view = ConfirmView(on_confirm=_confirm)
        await interaction.response.send_message(
            embed=make_embed("Reset all config?", "This cannot be undone."),
            view=view, ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 9. ConfigEditModal
# ---------------------------------------------------------------------------

class ConfigEditModal(discord.ui.Modal, title="Edit Configuration"):
    def __init__(self, key, current_value):
        super().__init__()
        self.key = key
        self.current_value = current_value
        self.result = None
        self.value_input = discord.ui.TextInput(
            label=f"Value for {key}",
            default=str(current_value),
            style=discord.TextStyle.short,
            required=True,
            max_length=500,
        )
        self.add_item(self.value_input)

    async def on_submit(self, interaction):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        raw = self.value_input.value.strip()
        try:
            from core.config_center import ConfigManager
            cm = ConfigManager()
            current = cm.get(self.key)
            if isinstance(current, bool):
                value = raw.lower() in ("true", "1", "yes", "on")
            elif isinstance(current, int):
                value = int(raw)
            elif current is None:
                value = None if raw.lower() in ("none", "null", "") else raw
            else:
                value = raw
            result = cm.set(self.key, value)
            if result["success"]:
                _audit("config_edit", interaction.user.id, self.key,
                       f"{result['old_value']} -> {result['new_value']}")
                await interaction.response.send_message(
                    embed=_success_embed(
                        "Config Updated",
                        f"`{self.key}`: `{result['old_value']}` \u2192 `{result['new_value']}`"),
                    ephemeral=True)
            else:
                await interaction.response.send_message(
                    embed=_err_embed(result.get("error", "Validation failed")),
                    ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)


# ---------------------------------------------------------------------------
# 10. TerminalView
# ---------------------------------------------------------------------------

class TerminalView(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    @discord.ui.button(label="Run", style=discord.ButtonStyle.success, row=0)
    async def run(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        modal = TerminalInputModal()
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="History", style=discord.ButtonStyle.secondary, row=0)
    async def history(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        try:
            from core.terminal import AdminTerminal
            term = AdminTerminal(self.bot)
            records = term.get_history(15)
            lines = []
            for r in records:
                icon = "\u2705" if r.get("success") else "\u274c"
                lines.append(f"{icon} `{r.get('command', '?')}` - {r.get('execution_time_ms', 0)}ms")
            content = "\n".join(lines) or "No command history."
            await interaction.response.send_message(
                embed=make_embed("\U0001f4dc Command History", f"```\n{content}\n```"),
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    @discord.ui.button(label="Clear", style=discord.ButtonStyle.danger, row=0)
    async def clear(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        try:
            from core.terminal import AdminTerminal
            term = AdminTerminal(self.bot)
            term.command_history.clear()
            term.command_log.clear()
            _audit("terminal_clear", interaction.user.id)
            await interaction.response.send_message(
                embed=_success_embed("Terminal Cleared"), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    @discord.ui.button(label="Commands", style=discord.ButtonStyle.secondary, row=1)
    async def commands(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        try:
            from core.terminal import AdminTerminal
            term = AdminTerminal(self.bot)
            grouped = term.get_command_list()
            fields = []
            for cat, cmds in grouped.items():
                lines = [f"`{c['command']}` - {c['description']}" for c in cmds]
                fields.append({"name": cat.title(), "value": "\n".join(lines), "inline": False})
            await interaction.response.send_message(
                embed=make_embed("\U0001f4c2 Terminal Commands", "", fields=fields[:25]),
                ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 11. TerminalInputModal
# ---------------------------------------------------------------------------

class TerminalInputModal(discord.ui.Modal, title="Terminal Command"):
    def __init__(self):
        super().__init__()
        self.result = None
        self.cmd_input = discord.ui.TextInput(
            label="Command",
            placeholder="e.g. bot status, db backup, docker ps...",
            style=discord.TextStyle.short,
            required=True,
            max_length=200,
        )
        self.add_item(self.cmd_input)

    async def on_submit(self, interaction):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        cmd = self.cmd_input.value.strip()
        await interaction.response.defer(ephemeral=True)
        try:
            from core.terminal import AdminTerminal
            term = AdminTerminal(interaction.client)
            result = await term.execute(cmd)
            _audit("terminal_exec", interaction.user.id, cmd)
            output = result.get("output", "No output.")
            if len(output) > 1800:
                output = output[:1800] + "\n... (truncated)"
            icon = "\u2705" if result.get("success") else "\u274c"
            embed = make_embed(
                f"{icon} Terminal - `{cmd}`",
                f"```\n{output}\n```",
                footer=f"Execution time: {result.get('execution_time_ms', 0)}ms",
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e), ephemeral=True)


# ---------------------------------------------------------------------------
# 12. SearchModal
# ---------------------------------------------------------------------------

class SearchModal(discord.ui.Modal, title="Search"):
    def __init__(self, title="Search", placeholder="Enter query..."):
        super().__init__(title=title)
        self.result = None
        self.query_input = discord.ui.TextInput(
            label="Query",
            placeholder=placeholder,
            style=discord.TextStyle.short,
            required=True,
            max_length=200,
        )
        self.add_item(self.query_input)

    async def on_submit(self, interaction):
        self.result = self.query_input.value.strip()
        await interaction.response.defer()
        try:
            from core.logging_center import LogManager
            lm = LogManager()
            lines = await asyncio.to_thread(lm.search, self.result, None, 30)
            if not lines:
                content = "No results found."
            else:
                content = "".join(lines[-30:])
            if len(content) > 1900:
                content = content[-1900:]
            _audit("log_search", interaction.user.id, self.result)
            await interaction.followup.send(
                embed=make_embed(f"\U0001f50d Search: `{self.result}`",
                                 f"```\n{content}\n```"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))


# ---------------------------------------------------------------------------
# 13. AnalyticsPanel
# ---------------------------------------------------------------------------

class AnalyticsPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    async def _show(self, interaction, label, func):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            embed = await asyncio.to_thread(func)
            _audit(f"analytics_{label}", interaction.user.id)
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Overview", style=discord.ButtonStyle.primary, row=0)
    async def overview(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_overview(self.bot)
            from core.analytics import AnalyticsEngine as _AE
            fmt = _AE.format_number
            embed = make_embed("\U0001f4ca Analytics Overview", "", fields=[
                {"name": "Users", "value": f"`{fmt(d['total_users'])}`", "inline": True},
                {"name": "Total VPS", "value": f"`{fmt(d['total_vps'])}`", "inline": True},
                {"name": "Running", "value": f"`{fmt(d['running_vps'])}`", "inline": True},
                {"name": "Stopped", "value": f"`{fmt(d['stopped_vps'])}`", "inline": True},
                {"name": "Expired", "value": f"`{fmt(d['expired_vps'])}`", "inline": True},
                {"name": "Credits", "value": f"`{d['total_credits']}`", "inline": True},
                {"name": "Commands", "value": f"`{fmt(d['total_commands_executed'])}`", "inline": True},
                {"name": "Errors", "value": f"`{fmt(d['total_errors'])}`", "inline": True},
                {"name": "Uptime", "value": f"`{d['bot_uptime']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="VPS", style=discord.ButtonStyle.secondary, row=0)
    async def vps(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_vps_stats(self.bot)
            by_plan = "\n".join(f"`{k}`: {v}" for k, v in list(d["by_plan"].items())[:8])
            embed = make_embed("\U0001f4bb VPS Analytics", "", fields=[
                {"name": "Total", "value": f"`{d['total_count']}`", "inline": True},
                {"name": "Expired", "value": f"`{d['expired_count']}`", "inline": True},
                {"name": "Avg Age", "value": f"`{d['avg_age_days']}d`", "inline": True},
                {"name": "By Plan", "value": by_plan or "`N/A`", "inline": False},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Revenue", style=discord.ButtonStyle.secondary, row=0)
    async def revenue(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_revenue_stats(self.bot)
            embed = make_embed("\U0001f4b0 Revenue", "", fields=[
                {"name": "Total Spent", "value": f"`${d['total_spent']}`", "inline": True},
                {"name": "Transactions", "value": f"`{d['transaction_count']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Growth", style=discord.ButtonStyle.secondary, row=1)
    async def growth(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_growth_data(self.bot)
            embed = make_embed("\U0001f4c8 Growth (30d)", "", fields=[
                {"name": "Users Joined", "value": f"`{d['total_users_30d']}`", "inline": True},
                {"name": "VPS Created", "value": f"`{d['total_vps_30d']}`", "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Commands", style=discord.ButtonStyle.secondary, row=1)
    async def commands(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_command_stats()
            top_lines = [f"`{c['command']}`: {c['count']}" for c in d["top_commands"][:10]]
            embed = make_embed("\U0001f4dc Command Stats", "", fields=[
                {"name": "Total", "value": f"`{d['total_commands']}`", "inline": True},
                {"name": "Unique", "value": f"`{d['unique_commands_tracked']}`", "inline": True},
                {"name": "Top Commands", "value": "\n".join(top_lines) or "`N/A`",
                 "inline": False},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Resources", style=discord.ButtonStyle.secondary, row=1)
    async def resources(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.analytics import AnalyticsEngine
            ae = AnalyticsEngine(self.bot)
            d = ae.get_resource_usage(self.bot)
            embed = make_embed("\U0001f4bb Resource Allocation", "", fields=[
                {"name": "RAM", "value": f"`{d['total_ram_allocated_display']}`", "inline": True},
                {"name": "vCPU", "value": f"`{d['total_cpu_allocated']}`", "inline": True},
                {"name": "Disk", "value": f"`{d['total_disk_allocated_display']}`", "inline": True},
                {"name": "Bandwidth", "value": f"`{d['total_bandwidth_allocated']} GB`",
                 "inline": True},
            ])
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 14. SecurityPanel
# ---------------------------------------------------------------------------

class SecurityPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    @discord.ui.button(label="Audit Log", style=discord.ButtonStyle.secondary, row=0)
    async def audit_log(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.security import audit
            entries = await asyncio.to_thread(audit.get_recent, 15)
            lines = []
            for e in entries:
                lines.append(
                    f"[{e.get('level', '?').upper()}] `{e.get('action', '?')}` "
                    f"by `{e.get('user_id', '?')}` \u2014 {e.get('details', '')}")
            content = "\n".join(lines) or "No audit entries."
            if len(content) > 1900:
                content = content[-1900:]
            await interaction.followup.send(
                embed=make_embed("\U0001f4dd Audit Log", f"```\n{content}\n```"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Rate Limits", style=discord.ButtonStyle.secondary, row=0)
    async def rate_limits(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        try:
            from core.security import security
            report = security.get_security_report()
            embed = make_embed("\u26a1 Rate Limits & Security", "", fields=[
                {"name": "Active Limits",
                 "value": f"`{report['active_rate_limits']}`", "inline": True},
                {"name": "Lockdown",
                 "value": f"`{'ACTIVE' if report['lockdown_active'] else 'Inactive'}`",
                 "inline": True},
            ])
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    @discord.ui.button(label="Lockdown", style=discord.ButtonStyle.danger, row=0)
    async def lockdown(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        try:
            from core.security import security
            new_state = not security.lockdown_mode
            security.set_lockdown(new_state, admin_id=str(interaction.user.id))
            _audit("lockdown_toggle", interaction.user.id, f"state={new_state}")
            state_text = "ACTIVATED" if new_state else "DEACTIVATED"
            await interaction.response.send_message(
                embed=_success_embed(f"Lockdown {state_text}"), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    @discord.ui.button(label="Permissions", style=discord.ButtonStyle.secondary, row=0)
    async def permissions(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        from core.security import PermissionManager
        fields = []
        for name, level in sorted(PermissionManager.LEVELS.items(),
                                  key=lambda x: -x[1]):
            perms = PermissionManager.PERMISSIONS.get(level, [])
            perm_str = ", ".join(perms[:5])
            if len(perms) > 5:
                perm_str += f" +{len(perms) - 5} more"
            fields.append({"name": f"Lvl {level} - {name}",
                           "value": f"`{perm_str}`", "inline": False})
        await interaction.response.send_message(
            embed=make_embed("\U0001f512 Permission Levels", "", fields=fields),
            ephemeral=True)

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


# ---------------------------------------------------------------------------
# 15. BackupPanel
# ---------------------------------------------------------------------------

class BackupPanel(discord.ui.View):
    def __init__(self, bot):
        super().__init__(timeout=120)
        self.bot = bot
        self._build_select()

    def _build_select(self):
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            backups = bm.list_backups()
        except Exception:
            backups = []
        opts = []
        for b in backups[:25]:
            name = b.get("name", "?")
            count = b.get("file_count", 0)
            opts.append(discord.SelectOption(label=name[:100],
                                             description=f"{count} files"))
        if not opts:
            opts.append(discord.SelectOption(label="No backups", value="none"))
        sel = discord.ui.Select(options=opts, placeholder="Select backup...")
        sel.callback = self._select_callback
        self.add_item(sel)

    async def _select_callback(self, interaction):
        if _get_user_level(interaction.user.id) < 90:
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        val = interaction.data["values"][0]
        if val == "none":
            return await interaction.response.send_message(
                embed=_err_embed("No backups available."), ephemeral=True)
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            info = bm._backup_info(val)
            if info:
                fields = [
                    {"name": "Name", "value": f"`{info['name']}`", "inline": True},
                    {"name": "Files", "value": f"`{info['file_count']}`", "inline": True},
                    {"name": "Size", "value": f"`{info['size_bytes']}B`", "inline": True},
                    {"name": "Created", "value": f"`{info.get('created_at', 'N/A')}`", "inline": True},
                    {"name": "Verified", "value": f"`{info.get('verified', False)}`", "inline": True},
                ]
                await interaction.response.send_message(
                    embed=make_embed(f"\U0001f4e6 {val}", "", fields=fields), ephemeral=True)
            else:
                await interaction.response.send_message(
                    embed=_err_embed("Backup info not found."), ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(
                embed=_err_embed(e), ephemeral=True)

    def _check(self, uid):
        return _get_user_level(uid) >= 90

    @discord.ui.button(label="Create", style=discord.ButtonStyle.success, row=0)
    async def create(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            result = await asyncio.to_thread(bm.create_backup)
            _audit("backup_create", interaction.user.id, result.get("name", ""))
            await interaction.followup.send(
                embed=_success_embed("Backup Created",
                                     f"`{result['name']}` - {result['file_count']} files"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="List", style=discord.ButtonStyle.secondary, row=0)
    async def list_btn(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            backups = bm.list_backups()
            lines = [f"`{b['name']}` - {b['file_count']} files, {b['size_bytes']}B"
                     for b in backups[:15]]
            embed = make_embed("\U0001f4e6 Backups",
                               "\n".join(lines) or "No backups found.")
            await interaction.followup.send(embed=embed)
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Verify", style=discord.ButtonStyle.secondary, row=0)
    async def verify(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            backups = bm.list_backups()
            if not backups:
                return await interaction.followup.send(
                    embed=_err_embed("No backups to verify."))
            latest = backups[0]["name"]
            result = await asyncio.to_thread(bm.verify_backup, latest)
            icon = "\u2705" if result["verified"] else "\u274c"
            await interaction.followup.send(
                embed=_success_embed(
                    f"{icon} Verify `{latest}`",
                    f"Valid: `{result['valid_count']}/{result['file_count']}`"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    @discord.ui.button(label="Cleanup", style=discord.ButtonStyle.danger, row=0)
    async def cleanup(self, interaction, button):
        if not self._check(interaction.user.id):
            return await interaction.response.send_message(
                embed=_err_embed("Permission denied."), ephemeral=True)
        await interaction.response.defer()
        try:
            from core.db_manager import DBManager
            from core.backup_center import BackupManager
            bm = BackupManager(DBManager())
            deleted = await asyncio.to_thread(bm.cleanup_old_backups)
            _audit("backup_cleanup", interaction.user.id, f"deleted={deleted}")
            await interaction.followup.send(
                embed=_success_embed("Cleanup", f"Deleted `{deleted}` old backup(s)"))
        except Exception as e:
            await interaction.followup.send(embed=_err_embed(e))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
