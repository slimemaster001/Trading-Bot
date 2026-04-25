"""
notify.py — Discord DM sender for Claude routines

Called by the agent at the end of each routine to send a private DM
directly to the user. No channel required — goes straight to your inbox.

Usage (from within a Claude routine or any Python script):
    from bot.notify import send_dm
    send_dm("📊 DAILY LOG — 2026-04-25\nPortfolio: +0.8% today...")

Or run standalone:
    python bot/notify.py "Your message here"
"""

import os
import sys
import requests


def send_dm(message: str) -> bool:
    """
    Send a private DM to the configured Discord user.
    Returns True on success, False on failure.
    """
    bot_token = os.environ.get("DISCORD_BOT_TOKEN")
    user_id   = os.environ.get("DISCORD_USER_ID")

    if not bot_token or not user_id:
        print("❌ Missing DISCORD_BOT_TOKEN or DISCORD_USER_ID environment variables.")
        return False

    headers = {
        "Authorization": f"Bot {bot_token}",
        "Content-Type":  "application/json",
    }

    # Step 1: Open (or get existing) DM channel with the user
    dm_response = requests.post(
        "https://discord.com/api/v10/users/@me/channels",
        headers=headers,
        json={"recipient_id": user_id},
        timeout=10,
    )

    if dm_response.status_code not in (200, 201):
        print(f"❌ Failed to open DM channel: {dm_response.status_code} — {dm_response.text}")
        return False

    channel_id = dm_response.json()["id"]

    # Step 2: Send the message
    # Discord has a 2000 character limit per message — split if needed
    chunks = [message[i:i+1990] for i in range(0, len(message), 1990)]

    for chunk in chunks:
        msg_response = requests.post(
            f"https://discord.com/api/v10/channels/{channel_id}/messages",
            headers=headers,
            json={"content": chunk},
            timeout=10,
        )
        if msg_response.status_code not in (200, 201):
            print(f"❌ Failed to send message: {msg_response.status_code} — {msg_response.text}")
            return False

    print(f"✅ Discord DM sent ({len(message)} chars)")
    return True


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python bot/notify.py 'Your message here'")
        sys.exit(1)

    message = " ".join(sys.argv[1:])
    success = send_dm(message)
    sys.exit(0 if success else 1)
