"""Security Center GUI - dedicated admin panel showing all security subsystems.

Displays overall security status, purge protection, audit logging, backup
protection, API security, node security, secret protection, anomaly detection,
database security, dependency security, with action buttons.
"""
import discord
import discord.ui
import logging
from datetime import datetime, timezone

from core.services import metrics, bus, load_json, DATA_DIR

logger = logging.getLogger("turtle.security_center")


class SecurityCenterView(discord.ui.View):
    def __init__(self, bot, user_id):
        super().__init__(timeout=300)
        self.bot = bot
        self.user_id = int(user_id)

    def _check_user(self, interaction):
        return interaction.user.id == self.user_id

    @discord.ui.button(label="Run Security Audit", style=discord.ButtonStyle.green, row=0)
    async def run_audit(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.secret_guard import secret_guard
            from core.db_guard import db_guard
            from core.dep_check import dep_checker
            from core.node_isolation import node_isolation
            from core.fail_guard import fail_guard
            from core.immutable_audit import immutable_audit
            from core.action_rate_limiter import action_rate_limiter
            from core.session_security import session_manager
            from core.backup_guard import backup_guard
            from core.health_verify import health_verifier

            secret_scan = secret_guard.scan_data_dir()
            db_health = db_guard.check_db_health()
            dep_status = dep_checker.get_status()
            node_status = node_isolation.get_status()
            fail_status = fail_guard.get_stats()
            audit_verified, _, audit_msg = immutable_audit.verify_chain()
            rl_stats = action_rate_limiter.get_global_stats()
            session_stats = session_manager.get_active_sessions()
            backup_status = backup_guard.get_status()
            hv_stats = health_verifier.get_stats()

            score = 100
            issues = []
            if secret_scan:
                score -= len(secret_scan) * 5
                issues.append(f"{len(secret_scan)} secret exposure(s) found")
            if db_health.get("issues"):
                score -= len(db_health["issues"]) * 10
                issues.append(f"{len(db_health['issues'])} DB issue(s)")
            if not audit_verified:
                score -= 30
                issues.append("Audit chain verification failed")
            if node_status.get("isolated_count", 0) > 0:
                score -= node_status["isolated_count"] * 5
                issues.append(f"{node_status['isolated_count']} isolated node(s)")
            if fail_status.get("currently_blocked", 0) > 0:
                score -= fail_status["currently_blocked"] * 10
                issues.append(f"{fail_status['currently_blocked']} blocked operation(s)")
            score = max(0, min(100, score))

            if score >= 80:
                color = 0x00ff88
                status = "\U0001f7e2 SECURE"
            elif score >= 50:
                color = 0xffaa00
                status = "\U0001f7e1 ATTENTION"
            else:
                color = 0xff3366
                status = "\U0001f534 AT RISK"

            embed = discord.Embed(
                title="\U0001f6e1\ufe0f Security Center Audit",
                description=f"**Overall Status:** {status}\n**Security Score:** {score}/100",
                color=color,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="\U0001f512 Secret Protection",
                value=f"Exposures: {len(secret_scan)}" + ("\n" + "\n".join(f"• {i['file']}" for i in secret_scan[:3])) if secret_scan else "Clean",
                inline=True)
            embed.add_field(name="\U0001f4c4 Database Security",
                value=f"Files: {db_health['healthy']}/{db_health['total_files']} OK" +
                      (f"\nIssues: {len(db_health['issues'])}" if db_health["issues"] else ""),
                inline=True)
            embed.add_field(name="\U0001f4dc Audit Chain",
                value=f"{'Verified' if audit_verified else 'BROKEN'}\n{audit_msg}",
                inline=True)
            embed.add_field(name="\U0001f310 Node Security",
                value=f"Isolated: {node_status.get('isolated_count', 0)}\nAuto-isolate: {'ON' if node_status.get('auto_isolate_enabled') else 'OFF'}",
                inline=True)
            embed.add_field(name="\u26a0\ufe0f Fail Guard",
                value=f"Blocked: {fail_status.get('currently_blocked', 0)}\nEscalated: {fail_status.get('escalated', 0)}",
                inline=True)
            embed.add_field(name="\U0001f510 Session Security",
                value=f"Active sessions: {len(session_stats)}",
                inline=True)
            embed.add_field(name="\U0001f4be Backup Protection",
                value=f"Protected: {backup_status.get('protected_count', 0)}\nVerified: {backup_status.get('verified_count', 0)}",
                inline=True)
            embed.add_field(name="\U0001f6e0\ufe0f Dependency Security",
                value=f"Vulnerabilities: {dep_status.get('vulnerabilities_found', 0)}\nTracked: {dep_status.get('tracked_deps', 0)}",
                inline=True)
            embed.add_field(name="\U0001f6e1\ufe0f Rate Limiting",
                value=f"Active buckets: {rl_stats.get('active_buckets', 0)}\nBlocked: {rl_stats.get('blocked_users', 0)}",
                inline=True)
            if issues:
                embed.add_field(name="\u26a0\ufe0f Issues Found", value="\n".join(f"• {i}" for i in issues[:5]), inline=False)
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Audit Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="View Audit Logs", style=discord.ButtonStyle.blurple, row=0)
    async def view_logs(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.immutable_audit import immutable_audit
            stats = immutable_audit.get_stats()
            recent = immutable_audit.get_recent(10)
            embed = discord.Embed(
                title="\U0001f4dc Immutable Audit Logs",
                description=f"**Total entries:** {stats['total']}\n**Chain status:** Verified",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc),
            )
            if recent:
                lines = []
                for e in recent:
                    lines.append(f"`{e['op_id']}` {e.get('action','?')} by <@{e.get('user_id','?')}> - {e.get('result','?')}")
                embed.add_field(name="Recent Actions", value="\n".join(lines[:10]), inline=False)
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Security Alerts", style=discord.ButtonStyle.red, row=1)
    async def view_alerts(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.security_alerts import security_alerts
            stats = security_alerts.get_stats()
            alerts = security_alerts.get_alerts(10, unack_only=True)
            embed = discord.Embed(
                title="\U0001f6a8 Security Alerts",
                description=f"**Unacknowledged:** {stats.get('unacknowledged', 0)}\n**Total:** {stats.get('total_alerts', 0)}",
                color=0xff3366 if stats.get('unacknowledged', 0) > 0 else 0x00ff88,
                timestamp=datetime.now(timezone.utc),
            )
            if alerts:
                lines = []
                for a in alerts:
                    lines.append(f"{a.get('emoji','')} [{a.get('severity','?')}] {a.get('title','?')}")
                embed.add_field(name="Active Alerts", value="\n".join(lines[:8]), inline=False)
            else:
                embed.add_field(name="Status", value="No active alerts", inline=False)
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Protected Resources", style=discord.ButtonStyle.blurple, row=1)
    async def protected_resources(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.backup_guard import backup_guard
            protected = backup_guard.get_status()
            embed = discord.Embed(
                title="\U0001f6e1\ufe0f Protected Resources",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="\U0001f4be Backup Protection",
                value=f"Protected: {protected.get('protected_count', 0)}\nVerified: {protected.get('verified_count', 0)}",
                inline=True)
            try:
                from core.node_isolation import node_isolation
                ns = node_isolation.get_status()
                embed.add_field(name="\U0001f310 Node Security",
                    value=f"Isolated: {ns.get('isolated_count', 0)}\nThreshold: {ns.get('failure_threshold', 5)}",
                    inline=True)
            except Exception:
                pass
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Emergency Lockdown", style=discord.ButtonStyle.danger, row=2)
    async def emergency_lockdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.emergency_lockdown import emergency_lockdown
            status = emergency_lockdown.get_status()
            embed = discord.Embed(
                title="\U0001f6d1 Emergency Lockdown",
                description=f"**Status:** {'ACTIVE' if status.get('active') else 'Inactive'}\n"
                           f"**Reason:** {status.get('reason', 'None')}\n"
                           f"**Blocked features:** {status.get('blocked_count', 0)}",
                color=0xff3366 if status.get('active') else 0x00ff88,
                timestamp=datetime.now(timezone.utc),
            )
            view = LockdownConfirmView(self.bot, self.user_id)
            await interaction.edit_original_response(embed=embed, view=view)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Node Isolation", style=discord.ButtonStyle.secondary, row=2)
    async def node_isolation_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.node_isolation import node_isolation
            status = node_isolation.get_status()
            isolated = status.get("isolated_nodes", {})
            embed = discord.Embed(
                title="\U0001f517 Node Isolation Status",
                color=0xffaa00 if isolated else 0x00ff88,
                timestamp=datetime.now(timezone.utc),
            )
            if isolated:
                for nid, info in list(isolated.items())[:10]:
                    embed.add_field(name=f"Node {nid}",
                        value=f"Reason: {info.get('reason','?')}\nSince: {info.get('isolated_at','?')[:16]}",
                        inline=True)
            else:
                embed.description = "No nodes are currently isolated."
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Backup Protection", style=discord.ButtonStyle.green, row=2)
    async def backup_protection(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.backup_guard import backup_guard
            status = backup_guard.get_status()
            embed = discord.Embed(
                title="\U0001f4be Backup Protection",
                color=0x00ff88,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="Protected Backups",
                value=str(status.get("protected_count", 0)), inline=True)
            embed.add_field(name="Verified Backups",
                value=str(status.get("verified_count", 0)), inline=True)
            protected = status.get("protected_backups", [])
            if protected:
                embed.add_field(name="Protected List", value="\n".join(protected[:10]), inline=False)
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Security Settings", style=discord.ButtonStyle.secondary, row=3)
    async def security_settings(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self._check_user(interaction):
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.node_isolation import node_isolation
            from core.action_rate_limiter import action_rate_limiter
            from core.db_guard import db_guard

            node_status = node_isolation.get_status()
            rl_stats = action_rate_limiter.get_global_stats()
            db_status = db_guard.get_status()

            embed = discord.Embed(
                title="\u2699\ufe0f Security Settings",
                color=0x5865F2,
                timestamp=datetime.now(timezone.utc),
            )
            embed.add_field(name="\U0001f517 Node Isolation",
                value=f"Auto-isolate: {'ON' if node_status.get('auto_isolate_enabled') else 'OFF'}\n"
                      f"Failure threshold: {node_status.get('failure_threshold', 5)}",
                inline=True)
            embed.add_field(name="\u23f3 Rate Limiting",
                value=f"Active buckets: {rl_stats.get('active_buckets', 0)}\n"
                      f"Blocked users: {rl_stats.get('blocked_users', 0)}",
                inline=True)
            embed.add_field(name="\U0001f4c4 DB Protection",
                value=f"Auto-backup: {'ON' if db_status.get('auto_backup_enabled') else 'OFF'}\n"
                      f"Protected files: {db_status.get('protected_files', 0)}",
                inline=True)
            embed.set_footer(text="Turtle Nodes Security Center")
            await interaction.edit_original_response(embed=embed, view=self)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True


class LockdownConfirmView(discord.ui.View):
    def __init__(self, bot, user_id):
        super().__init__(timeout=60)
        self.bot = bot
        self.user_id = int(user_id)

    @discord.ui.button(label="ACTIVATE LOCKDOWN", style=discord.ButtonStyle.danger)
    async def confirm_lockdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        await interaction.response.defer()
        try:
            from core.emergency_lockdown import emergency_lockdown
            success, msg = emergency_lockdown.activate_lockdown(
                self.user_id, reason="Emergency lockdown via Security Center")
            if success:
                embed = discord.Embed(
                    title="\U0001f6d1 LOCKDOWN ACTIVATED",
                    description="All systems are now locked down.\nUse Security Center to deactivate.",
                    color=0xff3366,
                )
            else:
                embed = discord.Embed(title="Lockdown Failed", description=msg, color=0xff3366)
            await interaction.edit_original_response(embed=embed, view=None)
        except Exception as e:
            await interaction.edit_original_response(
                embed=discord.Embed(title="Error", description=str(e)[:500], color=0xff3366))

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_lockdown(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not authorized.", ephemeral=True)
            return
        embed = discord.Embed(title="Lockdown Cancelled", color=0x00ff88)
        await interaction.response.edit_message(embed=embed, view=None)


def build_security_center_embed(bot=None):
    try:
        from core.secret_guard import secret_guard
        from core.db_guard import db_guard
        from core.dep_check import dep_checker
        from core.node_isolation import node_isolation
        from core.fail_guard import fail_guard
        from core.immutable_audit import immutable_audit
        from core.action_rate_limiter import action_rate_limiter
        from core.backup_guard import backup_guard
        from core.security_alerts import security_alerts
        from core.health_verify import health_verifier

        secret_scan = secret_guard.scan_data_dir()
        db_health = db_guard.check_db_health()
        dep_status = dep_checker.get_status()
        node_status = node_isolation.get_status()
        fail_status = fail_guard.get_stats()
        audit_stats = immutable_audit.get_stats()
        rl_stats = action_rate_limiter.get_global_stats()
        backup_status = backup_guard.get_status()
        alert_stats = security_alerts.get_stats()
        hv_stats = health_verifier.get_stats()

        score = 100
        if secret_scan: score -= len(secret_scan) * 5
        if db_health.get("issues"): score -= len(db_health["issues"]) * 10
        if node_status.get("isolated_count", 0) > 0: score -= node_status["isolated_count"] * 5
        if fail_status.get("currently_blocked", 0) > 0: score -= fail_status["currently_blocked"] * 10
        score = max(0, min(100, score))

        if score >= 80:
            color = 0x00ff88; status = "\U0001f7e2 SECURE"
        elif score >= 50:
            color = 0xffaa00; status = "\U0001f7e1 ATTENTION"
        else:
            color = 0xff3366; status = "\U0001f534 AT RISK"

        embed = discord.Embed(
            title="\U0001f6e1\ufe0f SECURITY CENTER",
            description=f"**Status:** {status}  |  **Score:** {score}/100",
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.add_field(name="\U0001f512 Secret Protection",
            value=f"Exposures: {len(secret_scan)}" if secret_scan else "Clean",
            inline=True)
        embed.add_field(name="\U0001f4c4 Database",
            value=f"{db_health['healthy']}/{db_health['total_files']} OK",
            inline=True)
        embed.add_field(name="\U0001f4dc Audit Log",
            value=f"{audit_stats['total']} entries",
            inline=True)
        embed.add_field(name="\U0001f310 Nodes",
            value=f"Isolated: {node_status.get('isolated_count',0)}",
            inline=True)
        embed.add_field(name="\u26a0\ufe0f Fail Guard",
            value=f"Blocked: {fail_status.get('currently_blocked',0)}",
            inline=True)
        embed.add_field(name="\U0001f4be Backups",
            value=f"Protected: {backup_status.get('protected_count',0)}",
            inline=True)
        embed.add_field(name="\U0001f6a8 Alerts",
            value=f"Unacked: {alert_stats.get('unacknowledged',0)}",
            inline=True)
        embed.add_field(name="\u23f3 Rate Limits",
            value=f"Blocked: {rl_stats.get('blocked_users',0)}",
            inline=True)
        embed.add_field(name="\U0001f6e0\ufe0f Dependencies",
            value=f"Vuln: {dep_status.get('vulnerabilities_found',0)}",
            inline=True)
        embed.set_footer(text="Turtle Nodes Security Center")
        return embed
    except Exception as e:
        logger.error("Failed to build security center embed: %s", e)
        return discord.Embed(
            title="\U0001f6e1\ufe0f SECURITY CENTER",
            description="Security subsystems initializing...",
            color=0x5865F2,
        )
