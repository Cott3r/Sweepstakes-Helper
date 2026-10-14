"""Notification service dispatching daily summary reports via Discord or Telegram webhooks."""

from typing import Any, Dict, Optional
import httpx

from config.settings import settings
from utils.logger import logger


class Notifier:
    """Sends structured summary alerts to Discord or Telegram."""

    def __init__(self):
        self.discord_url = settings.DISCORD_WEBHOOK_URL
        self.telegram_token = settings.TELEGRAM_BOT_TOKEN
        self.telegram_chat_id = settings.TELEGRAM_CHAT_ID

    async def send_daily_summary(self, summary_data: Dict[str, Any]) -> None:
        """Sends summary to all configured notification channels."""
        message = self.format_summary_text(summary_data)
        logger.info("Dispatching daily sweepstakes summary report...")

        if self.discord_url:
            await self._send_discord(summary_data)
        if self.telegram_token and self.telegram_chat_id:
            await self._send_telegram(message)

    def format_summary_text(self, data: Dict[str, Any]) -> str:
        """Formats the daily summary into a human-readable text block."""
        date_str = data.get("date", "Today")
        discovered = data.get("discovered_today", 0)
        completed = data.get("completed_today", 0)
        failed = data.get("failed_today", 0)
        pending_manual = data.get("pending_manual", 0)
        pending_automated = data.get("pending_automated", 0)

        lines = [
            f"🎁 **Sweepstakes Helper Austria - Daily Report** ({date_str})",
            "────────────────────────────────────────",
            f"✨ New Sweepstakes Discovered: **{discovered}**",
            f"✅ Completed Entries Today:     **{completed}**",
            f"🔔 Pending Manual Actions:      **{pending_manual}**",
            f"⏳ Pending Automated Queue:     **{pending_automated}**",
            f"⚠️ Failed Attempts:             **{failed}**",
            "────────────────────────────────────────",
        ]
        if pending_manual > 0:
            lines.append("👉 Run `python main.py manual` to solve pending interactive entries!")

        return "\n".join(lines)

    async def _send_discord(self, data: Dict[str, Any]) -> None:
        """Sends rich embed message to Discord webhook."""
        if not self.discord_url:
            return

        date_str = data.get("date", "Today")
        discovered = data.get("discovered_today", 0)
        completed = data.get("completed_today", 0)
        failed = data.get("failed_today", 0)
        pending_manual = data.get("pending_manual", 0)
        pending_automated = data.get("pending_automated", 0)

        embed = {
            "title": f"🎁 Daily Sweepstakes Report ({date_str})",
            "color": 0x2ECC71 if completed > 0 else 0x3498DB,
            "description": "Daily automated batch execution finished for Austrian sweepstakes.",
            "fields": [
                {"name": "✨ Discovered", "value": str(discovered), "inline": True},
                {"name": "✅ Completed", "value": str(completed), "inline": True},
                {"name": "⚠️ Failed", "value": str(failed), "inline": True},
                {"name": "🔔 Manual Action Needed", "value": str(pending_manual), "inline": True},
                {"name": "⏳ Automated Pending", "value": str(pending_automated), "inline": True},
            ],
            "footer": {"text": "Sweepstakes-Helper-AT"}
        }

        payload = {
            "username": "Sweepstakes Helper (AT)",
            "embeds": [embed]
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(self.discord_url, json=payload)
                if res.status_code in (200, 204):
                    logger.info("Discord notification sent successfully.")
                else:
                    logger.warning(f"Discord webhook responded with status {res.status_code}: {res.text}")
        except Exception as e:
            err_msg = str(e) or repr(e)
            logger.error(f"Failed to deliver Discord notification ({type(e).__name__}): {err_msg}")

    async def _send_telegram(self, text: str) -> None:
        """Sends message via Telegram Bot API."""
        if not self.telegram_token or not self.telegram_chat_id:
            return

        telegram_api_url = f"https://api.telegram.org/bot{self.telegram_token}/sendMessage"
        payload = {
            "chat_id": self.telegram_chat_id,
            "text": text,
            "parse_mode": "Markdown"
        }

        try:
            async with httpx.AsyncClient(timeout=20.0) as client:
                res = await client.post(telegram_api_url, json=payload)
                if res.status_code == 200:
                    logger.info("Telegram notification sent successfully.")
                else:
                    logger.warning(f"Telegram API responded with status {res.status_code}: {res.text}")
        except Exception as e:
            err_msg = str(e) or repr(e)
            logger.error(f"Failed to deliver Telegram notification ({type(e).__name__}): {err_msg}")
