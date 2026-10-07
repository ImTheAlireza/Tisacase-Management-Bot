import logging


class Migration010:
    """
    Create the bot_settings key/value table.

    Holds runtime options that Sudo can toggle from the menu and that must
    survive a restart / redeploy (e.g. auto detection of mockup vs print files).
    """

    name = "010_add_bot_settings"

    @staticmethod
    def up(cursor):
        logging.info("Creating bot_settings table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_settings (
                setting_key VARCHAR(100) PRIMARY KEY,
                setting_value VARCHAR(255) NOT NULL,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logging.info("✅ bot_settings table ready")

    @staticmethod
    def down(cursor):
        cursor.execute("DROP TABLE IF EXISTS bot_settings")
        logging.info("Migration 010 rolled back")
