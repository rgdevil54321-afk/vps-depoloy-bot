"""
Turtle Nodes - Animated Progress Screen Engine
Premium processing screens for all operations.
"""
import asyncio
import time
import random
import string
import discord

SPINNERS = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
DONE = "✅"
FAIL = "❌"
PENDING = "○"
ACTIVE = "→"


def _op_id(prefix="OP"):
    return f"{prefix}-{random.randint(10000, 99999)}"


def _bar(pct, length=20):
    filled = int(length * pct / 100)
    return f"[{'█' * filled}{'░' * (length - filled)}] {pct}%"


def _elapsed(start):
    s = int(time.time() - start)
    if s < 60:
        return f"{s}s"
    m, s = divmod(s, 60)
    return f"{m}m {s}s"


def _eta(start, pct):
    if pct <= 0:
        return "calculating..."
    elapsed = time.time() - start
    remaining = elapsed * (100 - pct) / pct
    if remaining < 1:
        return "<1s"
    if remaining < 60:
        return f"~{int(remaining)}s"
    m, s = divmod(int(remaining), 60)
    return f"~{m}m {s}s"


class ProgressScreen:
    """
    Animated progress screen for Discord operations.

    Usage:
        screen = ProgressScreen(ctx, title="CREATING VPS", op_prefix="VPS")
        await screen.start()
        await screen.step(0, "Validating request...")
        await screen.step(1, "Allocating resources...")
        ...
        await screen.complete("VPS created successfully!")
    """

    def __init__(self, ctx_or_inter, title="", op_prefix="OP", color=0x5865F2):
        self.ctx = ctx_or_inter
        self.title = title
        self.op_id = _op_id(op_prefix)
        self.color = color
        self.steps = []
        self.current_step = 0
        self.total_steps = 0
        self.pct = 0
        self.start_time = time.time()
        self.msg = None
        self.done = False
        self._spinner_idx = 0
        self._spinning = False

    def set_steps(self, steps):
        self.steps = steps
        self.total_steps = len(steps)

    def _build_embed(self, status_text, step_label="", step_detail="", extra_fields=None):
        elapsed = _elapsed(self.start_time)
        eta = _eta(self.start_time, self.pct)

        em = discord.Embed(title=f"⚡ {self.title}", color=self.color)

        em.add_field(name="Progress", value=f"`{_bar(self.pct)}`", inline=False)

        checklist = ""
        for i, (desc, state) in enumerate(self.steps):
            if state == "done":
                icon = DONE
            elif state == "active":
                icon = ACTIVE
            elif state == "fail":
                icon = FAIL
            else:
                icon = PENDING
            checklist += f"{icon} {desc}\n"

        if checklist:
            em.add_field(name="Status", value=checklist, inline=False)

        if step_label:
            em.add_field(name="Step", value=f"{step_label}/{self.total_steps}", inline=True)
        if step_detail:
            em.add_field(name="Action", value=step_detail, inline=True)
        em.add_field(name="Elapsed", value=elapsed, inline=True)
        if self.pct > 0:
            em.add_field(name="ETA", value=eta, inline=True)
        em.add_field(name="Operation", value=f"`#{self.op_id}`", inline=True)

        em.set_footer(text=f"{status_text} • Turtle Nodes")

        if extra_fields:
            for name, value in extra_fields.items():
                em.add_field(name=name, value=value, inline=False)

        return em

    async def start(self, initial_message="Initializing..."):
        self.start_time = time.time()
        em = self._build_embed("PROCESSING", "0", initial_message)
        try:
            if hasattr(self.ctx, 'followup'):
                self.msg = await self.ctx.followup.send(embed=em, ephemeral=True)
            else:
                self.msg = await self.ctx.send(embed=em)
        except Exception:
            try:
                self.msg = await self.ctx.send(embed=em)
            except Exception:
                pass

    async def update(self, pct, step_num=None, step_detail="", status_override=None):
        self.pct = min(pct, 100)
        if step_num is not None:
            self.current_step = step_num
        status = status_override or "PROCESSING"

        for i in range(len(self.steps)):
            if i < self.current_step:
                if self.steps[i][1] != "fail":
                    self.steps[i] = (self.steps[i][0], "done")
            elif i == self.current_step:
                self.steps[i] = (self.steps[i][0], "active")
            else:
                self.steps[i] = (self.steps[i][0], "pending")

        em = self._build_embed(status, str(self.current_step + 1), step_detail)
        if self.msg:
            try:
                await self.msg.edit(embed=em)
            except Exception:
                pass

    async def step_done(self, pct, step_num, step_detail=""):
        if step_num < len(self.steps):
            self.steps[step_num] = (self.steps[step_num][0], "done")
        await self.update(pct, step_num + 1, step_detail)

    async def step_fail(self, step_num, error_detail=""):
        if step_num < len(self.steps):
            self.steps[step_num] = (self.steps[step_num][0], "fail")
        self.pct = int(100 * step_num / self.total_steps) if self.total_steps else 0
        await self.update(self.pct, step_num, error_detail, "FAILED")

    async def complete(self, message="Operation completed successfully!", extra_fields=None):
        self.done = True
        self.pct = 100
        for i in range(len(self.steps)):
            self.steps[i] = (self.steps[i][0], "done")

        em = discord.Embed(title=f"✅ {self.title}", color=0x00ff88)
        em.add_field(name="Progress", value=f"`{_bar(100)}`", inline=False)
        em.add_field(name="Result", value=message, inline=False)
        em.add_field(name="Elapsed", value=_elapsed(self.start_time), inline=True)
        em.add_field(name="Operation", value=f"`#{self.op_id}`", inline=True)

        checklist = ""
        for desc, state in self.steps:
            checklist += f"{DONE} {desc}\n"
        if checklist:
            em.add_field(name="Completed", value=checklist, inline=False)

        if extra_fields:
            for name, value in extra_fields.items():
                em.add_field(name=name, value=value, inline=False)

        em.set_footer(text=f"Completed in {_elapsed(self.start_time)} • Turtle Nodes")
        if self.msg:
            try:
                await self.msg.edit(embed=em)
            except Exception:
                pass

    async def fail(self, message="Operation failed.", error="", extra_fields=None):
        self.done = True

        em = discord.Embed(title=f"❌ {self.title} FAILED", color=0xff3366)

        checklist = ""
        for desc, state in self.steps:
            if state == "done":
                checklist += f"{DONE} {desc}\n"
            elif state == "fail":
                checklist += f"{FAIL} {desc}\n"
            else:
                checklist += f"{PENDING} {desc}\n"
        if checklist:
            em.add_field(name="Status", value=checklist, inline=False)

        em.add_field(name="Error", value=f"```{error[:500]}```" if error else "Unknown error", inline=False)
        em.add_field(name="Elapsed", value=_elapsed(self.start_time), inline=True)
        em.add_field(name="Operation", value=f"`#{self.op_id}`", inline=True)

        if extra_fields:
            for name, value in extra_fields.items():
                em.add_field(name=name, value=value, inline=False)

        em.set_footer(text=f"Failed after {_elapsed(self.start_time)} • Turtle Nodes")
        if self.msg:
            try:
                await self.msg.edit(embed=em)
            except Exception:
                pass

    async def spinner(self, text="Processing", interval=0.4):
        """Show a spinner animation while a single operation runs."""
        self._spinning = True
        while self._spinning:
            spin_char = SPINNERS[self._spinner_idx % len(SPINNERS)]
            self._spinner_idx += 1
            if self.msg:
                em = discord.Embed(
                    title=f"⚡ {self.title}",
                    description=f"{spin_char} {text}...",
                    color=self.color
                )
                em.add_field(name="Elapsed", value=_elapsed(self.start_time), inline=True)
                em.add_field(name="Operation", value=f"`#{self.op_id}`", inline=True)
                em.set_footer(text="Processing • Turtle Nodes")
                try:
                    await self.msg.edit(embed=em)
                except Exception:
                    pass
            await asyncio.sleep(interval)

    def stop_spinner(self):
        self._spinning = False


async def quick_progress(ctx, title, steps, async_fn, op_prefix="OP", color=0x5865F2):
    """
    Convenience wrapper: run an async function with a progress screen.

    steps: list of step descriptions
    async_fn: async callable that receives (screen) and must call screen.step_done() as it goes
    """
    screen = ProgressScreen(ctx, title=title, op_prefix=op_prefix, color=color)
    screen.set_steps([(s, "pending") for s in steps])
    await screen.start()
    try:
        await async_fn(screen)
    except Exception as e:
        await screen.fail("An error occurred.", str(e))
        raise
    return screen
