import logging
import json
from config.database import get_db_connection
from config.settings import SUDO_USER_ID
from utils.helpers import get_tehran_time, to_utc_naive
from utils.enums import DesignStatus

class ActivityService:
    @staticmethod
    def log_and_notify(bot, actor_id, action_type, entity_id, details_fa, silent=False):
        """Logs action to DB and sends a message to Sudo"""
        # 1. Log to Database
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO activity_logs (actor_id, action_type, entity_id, details) VALUES (%s, %s, %s, %s)",
                (actor_id, action_type, entity_id, details_fa)
            )
            conn.commit()
        except Exception as e:
            logging.error(f"Failed to log activity: {e}")
        finally:
            cursor.close()
            conn.close()

        # 2. Notify Sudo
        if not silent:
            try:
                import asyncio
                loop = asyncio.get_event_loop()
                text = f"🔔 **گزارش فعالیت**\n━━━━━━━━━━━━━\n{details_fa}"
                loop.create_task(bot.send_message(chat_id=SUDO_USER_ID, text=text, parse_mode="Markdown"))
            except Exception as e:
                logging.error(f"Sudo notification failed: {e}")

    @staticmethod
    def reset_user_stats(user_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE users SET stats_reset_at = NOW() WHERE user_id = %s", (user_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def reset_line_stats(line_id):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("UPDATE product_lines SET stats_reset_at = NOW() WHERE id = %s", (line_id,))
            conn.commit()
        finally:
            cursor.close()
            conn.close()