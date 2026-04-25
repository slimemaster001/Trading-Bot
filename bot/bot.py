"""
Trading Bot — Discord Bot
Slash commands for on-demand portfolio reports.
Scheduled notifications are sent via notify.py (called by Claude routines).

Commands:
  /positions  — all open trades with live P&L
  /report     — today's trading summary
  /status     — quick portfolio health check
  /watchlist  — current watchlist from research notes
"""

import discord
from discord import app_commands
import os
import re
import json
from datetime import datetime

# ── Config ────────────────────────────────────────────────────────────────────

BOT_TOKEN   = os.environ["DISCORD_BOT_TOKEN"]
GUILD_ID    = int(os.environ["DISCORD_GUILD_ID"])   # your server ID
MY_USER_ID  = int(os.environ["DISCORD_USER_ID"])    # your personal Discord user ID

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ── Helpers ───────────────────────────────────────────────────────────────────

def read_file(relative_path: str) -> str:
    """Read a memory/strategy file relative to the project root."""
    full_path = os.path.join(BASE_DIR, relative_path)
    try:
        with open(full_path, "r", encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return f"⚠️ File not found: {relative_path}"


def parse_active_positions(trade_log: str) -> list[dict]:
    """Extract the active positions table from trade_log.md."""
    positions = []
    in_table = False
    for line in trade_log.splitlines():
        if "## Active Positions" in line:
            in_table = True
            continue
        if in_table and line.startswith("##"):
            break
        if in_table and "|" in line and "---" not in line and "Ticker" not in line:
            cols = [c.strip() for c in line.split("|") if c.strip()]
            if len(cols) >= 7 and cols[0] != "*(none yet)*":
                positions.append({
                    "ticker":    cols[0],
                    "entry_date": cols[1],
                    "entry_price": cols[2],
                    "shares":    cols[3],
                    "size":      cols[4],
                    "thesis":    cols[5][:40] + "..." if len(cols[5]) > 40 else cols[5],
                    "stop":      cols[6],
                })
    return positions


def parse_latest_eod(research_notes: str) -> str:
    """Pull the most recent EOD Summary block from research_notes.md."""
    match = re.search(r"### EOD Summary.*?(?=###|\Z)", research_notes, re.DOTALL)
    if match:
        return match.group(0).strip()
    return "No EOD summary found yet — check back after market close."


def parse_watchlist(research_notes: str) -> str:
    """Pull the active watchlist table from research_notes.md."""
    match = re.search(r"## Active Watchlist.*?(?=##|\Z)", research_notes, re.DOTALL)
    if match:
        return match.group(0).strip()
    return "Watchlist is empty."


# ── Discord Client ─────────────────────────────────────────────────────────────

intents = discord.Intents.default()
client  = discord.Client(intents=intents)
tree    = app_commands.CommandTree(client)
GUILD   = discord.Object(id=GUILD_ID)


@client.event
async def on_ready():
    await tree.sync(guild=GUILD)
    print(f"✅ Trading bot online as {client.user} | Guild: {GUILD_ID}")


# ── Slash Commands ─────────────────────────────────────────────────────────────

@tree.command(name="positions", description="Show all open trading positions with P&L", guild=GUILD)
async def cmd_positions(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    trade_log = read_file("memory/trade_log.md")
    positions = parse_active_positions(trade_log)

    if not positions:
        await interaction.followup.send(
            "📭 **No open positions right now.**\nCash is a position — waiting for the right setup.",
            ephemeral=True
        )
        return

    lines = ["📊 **Open Positions**", f"*Updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}*", "```"]
    lines.append(f"{'Ticker':<8} {'Entry':>8} {'Stop':>8} {'Size':>6}  Thesis")
    lines.append("─" * 60)
    for p in positions:
        lines.append(f"{p['ticker']:<8} {p['entry_price']:>8} {p['stop']:>8} {p['size']:>6}  {p['thesis']}")
    lines.append("```")
    lines.append("_Use `/report` for today's full summary._")

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.command(name="report", description="Get today's trading summary and EOD log", guild=GUILD)
async def cmd_report(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    research_notes = read_file("memory/research_notes.md")
    eod = parse_latest_eod(research_notes)

    # Truncate if too long for Discord (2000 char limit)
    if len(eod) > 1800:
        eod = eod[:1800] + "\n\n_[truncated — see research_notes.md for full report]_"

    msg = f"📈 **Daily Report**\n\n{eod}"
    await interaction.followup.send(msg, ephemeral=True)


@tree.command(name="status", description="Quick portfolio health check", guild=GUILD)
async def cmd_status(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    trade_log      = read_file("memory/trade_log.md")
    research_notes = read_file("memory/research_notes.md")
    agent_lessons  = read_file("memory/agent_lessons.md")
    guardrails     = read_file("config/guardrails.md")

    positions = parse_active_positions(trade_log)
    n_positions = len(positions)

    # Trading mode
    mode_match = re.search(r"TRADING_MODE\s*=\s*(\w+)", guardrails)
    mode = mode_match.group(1) if mode_match else "UNKNOWN"
    mode_emoji = "🟡" if mode == "PAPER" else "🟢"

    # Latest market stance
    stance_match = re.search(r"\*\*Overall stance:\*\*\s*(.+)", research_notes)
    stance = stance_match.group(1).strip() if stance_match else "Not yet assessed"

    lines = [
        "⚡ **Portfolio Status**",
        f"",
        f"{mode_emoji} **Mode:** {mode} trading",
        f"📂 **Open positions:** {n_positions}",
        f"🧭 **Market stance:** {stance}",
        f"",
        f"_Use `/positions` for trade details or `/report` for today's summary._"
    ]

    await interaction.followup.send("\n".join(lines), ephemeral=True)


@tree.command(name="watchlist", description="Show current stock watchlist", guild=GUILD)
async def cmd_watchlist(interaction: discord.Interaction):
    await interaction.response.defer(ephemeral=True)

    research_notes = read_file("memory/research_notes.md")
    watchlist_section = parse_watchlist(research_notes)

    if len(watchlist_section) > 1800:
        watchlist_section = watchlist_section[:1800] + "\n_[truncated]_"

    await interaction.followup.send(
        f"👀 **Current Watchlist**\n\n{watchlist_section}",
        ephemeral=True
    )


# ── Run ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    client.run(BOT_TOKEN)
