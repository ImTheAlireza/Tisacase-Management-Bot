import logging
from typing import Optional

from config.database import get_db_connection


# Setting keys stored in the bot_settings table
AUTO_DETECT_FILES_KEY = 'auto_detect_files'


class BotSettings:
    """
    Key/value store for runtime options Sudo can toggle from the menu.

    Values are persisted in the `bot_settings` table so they survive a
    restart or a redeploy.
    """

    # ------------------------------------------------------------------
    # Generic accessors
    # ------------------------------------------------------------------

    @staticmethod
    def get(key: str, default: Optional[str] = None) -> Optional[str]:
        """Read a single setting value (or `default` when not stored)."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT setting_value FROM bot_settings WHERE setting_key = %s",
                (key,)
            )
            row = cursor.fetchone()
            return row[0] if row else default
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def set(key: str, value) -> None:
        """Insert or update a single setting value."""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                INSERT INTO bot_settings (setting_key, setting_value)
                VALUES (%s, %s)
                ON DUPLICATE KEY UPDATE setting_value = %s
            """, (key, str(value), str(value)))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to save setting {key}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    # ------------------------------------------------------------------
    # Auto detection of mockup vs print files
    # ------------------------------------------------------------------

    @staticmethod
    def is_auto_detect_enabled() -> bool:
        """
        Whether the bot should auto sort uploaded files
        (photo → mockup, document → print).

        Any read failure falls back to the classic two-step flow so an
        editor session is never blocked by this option.
        """
        try:
            return BotSettings.get(AUTO_DETECT_FILES_KEY, '0') == '1'
        except Exception as e:
            logging.error(f"Failed to read {AUTO_DETECT_FILES_KEY}, defaulting to off: {e}")
            return False

    @staticmethod
    def set_auto_detect_enabled(enabled: bool) -> None:
        """Turn auto detection on/off."""
        BotSettings.set(AUTO_DETECT_FILES_KEY, '1' if enabled else '0')
        logging.info(f"🤖 Auto detect files {'enabled' if enabled else 'disabled'}")
