import logging
from utils.enums import DesignStatus


class Migration006:
    """
    Create design_group_messages table to store per-file message IDs
    for every file sent to group_products and group_print after approval.

    This enables future features like:
    - Deleting a design's files from groups by code
    - Re-sending specific files
    - Auditing what was sent where
    """

    name = "006_create_design_group_messages"

    @staticmethod
    def up(cursor):
        logging.info("Creating design_group_messages table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS design_group_messages (
                id INT AUTO_INCREMENT PRIMARY KEY,

                -- Link to the design
                design_id INT NOT NULL,
                code VARCHAR(20) NOT NULL,

                -- Which group this was sent to
                group_type ENUM('products', 'print') NOT NULL,
                chat_id BIGINT NOT NULL,

                -- Telegram message reference
                message_id BIGINT NOT NULL,

                -- Original file reference
                file_id VARCHAR(255) NOT NULL,
                file_index INT NOT NULL DEFAULT 0,

                sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,

                -- Prevent duplicate records for same message
                UNIQUE KEY unique_msg (chat_id, message_id),

                INDEX idx_design_id (design_id),
                INDEX idx_code (code),
                INDEX idx_group_type (group_type),
                INDEX idx_chat_message (chat_id, message_id),
                INDEX idx_sent_at (sent_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        logging.info("✅ design_group_messages table created")

    @staticmethod
    def down(cursor):
        cursor.execute("DROP TABLE IF EXISTS design_group_messages")
        logging.info("design_group_messages table dropped")
