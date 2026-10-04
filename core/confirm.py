"""Two-Step Confirmation System for destructive operations.

Provides Discord UI views that require explicit confirmation before executing
dangerous actions like purge, reinstall, delete, restore, shutdown, and node ops.
Extremely destructive actions require a second confirmation step.
"""
import asyncio
import uuid
import logging
from datetime import datetime, timezone

import discord
import discord.ui

from core.services import metrics, bus

logger = logging.getLogger("turtle.confirm")

CONFIRM_TIMEOUT = 120
DOUBLE_CONFIRM_TIMEOUT = 180
EXTREME_ACTIONS = {
    "purge_deep", "reinstall_os", "delete_vps", "delete_all_vps",
    "full_lockdown", "restore_backup", "force_stop_all", "node_isolate_all",
    "emergency_restart", "db_wipe",
}

_pending: dict[str, dict] = {}


def gen_op_id():
    return str(uuid.uuid4())[:12]


def log_confirm_event(op_id, action, user_id, result, extra=""):
    entry = {
        "op_id": op_id,
        "action": action,
        "user_id": str(user_id),
        "result": result,
        "extra": extra,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    bus.emit("confirm.event", entry)
    metrics.inc(f"confirm.{result}")
    logger.info("Confirm %s: action=%s user=%s result=%s", op_id, action, user_id, result)
    return entry


def is_extreme(action):
    return action in EXTREME_ACTIONS


class SingleConfirmView(discord.ui.View):
    def __init__(self, action, user_id, target_desc, callback_fn, timeout=CONFIRM_TIMEOUT):
        super().__init__(timeout=timeout)
        self.action = action
        self.user_id = int(user_id)
        self.target_desc = target_desc
        self.callback_fn = callback_fn
        self.op_id = gen_op_id()
        self.result = None
        _pending[self.op_id] = {
            "action": action, "user_id": self.user_id,
            "created": datetime.now(timezone.utc).isoformat(),
        }

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.green, emoji="\u2705")
    async def confirm_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return
        self.result = "confirmed"
        log_confirm_event(self.op_id, self.action, self.user_id, "confirmed")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await self.callback_fn(True, self.op_id)
        except Exception as e:
            logger.error("Confirm callback failed: %s", e)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.red, emoji="\u274c")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return
        self.result = "cancelled"
        log_confirm_event(self.op_id, self.action, self.user_id, "cancelled")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await self.callback_fn(False, self.op_id)
        except Exception as e:
            logger.error("Confirm cancel callback failed: %s", e)

    async def on_timeout(self):
        self.result = "timeout"
        log_confirm_event(self.op_id, self.action, self.user_id, "timeout")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True

    async def on_timeout_send(self, message):
        await self.on_timeout()
        try:
            await message.edit(
                embed=discord.Embed(
                    title="\u23f0 Confirmation Expired",
                    description=f"Action `{self.action}` cancelled (timeout).",
                    color=0xffaa00,
                ),
                view=self,
            )
        except Exception:
            pass


class DoubleConfirmView(discord.ui.View):
    def __init__(self, action, user_id, target_desc, callback_fn, timeout=DOUBLE_CONFIRM_TIMEOUT):
        super().__init__(timeout=timeout)
        self.action = action
        self.user_id = int(user_id)
        self.target_desc = target_desc
        self.callback_fn = callback_fn
        self.op_id = gen_op_id()
        self.step = 1
        self.result = None
        _pending[self.op_id] = {
            "action": action, "user_id": self.user_id,
            "step": 1,
            "created": datetime.now(timezone.utc).isoformat(),
        }

    @discord.ui.button(label="I understand the risks", style=discord.ButtonStyle.red, emoji="\u26a0\ufe0f")
    async def step1_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return
        self.step = 2
        _pending[self.op_id]["step"] = 2
        button.disabled = True
        button.label = "\u2705 Acknowledged"
        button.style = discord.ButtonStyle.secondary
        self.children[1].disabled = False
        embed = discord.Embed(
            title="\u26a0\ufe0f Second Confirmation Required",
            description=(
                f"**Action:** `{self.action}`\n"
                f"**Target:** {self.target_desc}\n\n"
                f"This is a **destructive** operation. Type **CONFIRM** below to proceed."
            ),
            color=0xff3366,
        )
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Confirm Final", style=discord.ButtonStyle.red, emoji="\U0001f525", disabled=True)
    async def step2_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return
        self.result = "confirmed"
        log_confirm_event(self.op_id, self.action, self.user_id, "confirmed", "double_confirmed")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await self.callback_fn(True, self.op_id)
        except Exception as e:
            logger.error("Double confirm callback failed: %s", e)

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="\U0001f6ab")
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Not your confirmation.", ephemeral=True)
            return
        self.result = "cancelled"
        log_confirm_event(self.op_id, self.action, self.user_id, "cancelled")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(view=self)
        try:
            await self.callback_fn(False, self.op_id)
        except Exception as e:
            logger.error("Double confirm cancel callback failed: %s", e)

    async def on_timeout(self):
        self.result = "timeout"
        log_confirm_event(self.op_id, self.action, self.user_id, "timeout")
        _pending.pop(self.op_id, None)
        for child in self.children:
            child.disabled = True


def get_confirmation_embed(action, target_desc, extreme=False):
    color = 0xff3366 if extreme else 0xffaa00
    emoji = "\U0001f525" if extreme else "\u26a0\ufe0f"
    level = "EXTREME" if extreme else "DANGEROUS"
    return discord.Embed(
        title=f"{emoji} {level} Action Required",
        description=(
            f"**Action:** `{action}`\n"
            f"**Target:** {target_desc}\n\n"
            f"\u2022 Click **Confirm** to proceed\n"
            f"\u2022 Click **Cancel** to abort\n"
            f"\u2022 Auto-cancels after timeout"
        ),
        color=color,
    )


def request_confirmation(ctx, action, target_desc, callback_fn, extreme=None):
    if extreme is None:
        extreme = is_extreme(action)
    if extreme:
        view = DoubleConfirmView(action, ctx.author.id, target_desc, callback_fn)
        embed = get_confirmation_embed(action, target_desc, extreme=True)
    else:
        view = SingleConfirmView(action, ctx.author.id, target_desc, callback_fn)
        embed = get_confirmation_embed(action, target_desc, extreme=False)
    return embed, view


async def send_confirmation(ctx, action, target_desc, callback_fn, extreme=None):
    embed, view = request_confirmation(ctx, action, target_desc, callback_fn, extreme=extreme)
    msg = await ctx.send(embed=embed, view=view)
    asyncio.get_running_loop().create_task(view.on_timeout_send(msg))
    return msg
