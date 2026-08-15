import logging


class Migration010:
    """Create userbot_deletion_queue table for self-bot group cleanup."""

    name = "010_create_userbot_deletion_queue"

    @staticmethod
    def up(cursor):
        logging.info("Creating userbot_deletion_queue table...")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS userbot_deletion_queue (
                id INT AUTO_INCREMENT PRIMARY KEY,
                chat_id BIGINT NOT NULL,
                message_id BIGINT NOT NULL,
                code VARCHAR(20) DEFAULT NULL,
                status ENUM('pending', 'processing', 'done', 'failed')
                    NOT NULL DEFAULT 'pending',
                attempts INT NOT NULL DEFAULT 0,
                last_error VARCHAR(500) DEFAULT NULL,
                claimed_at DATETIME DEFAULT NULL,
                created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
                    ON UPDATE CURRENT_TIMESTAMP,
                UNIQUE KEY unique_msg (chat_id, message_id),
                INDEX idx_status (status),
                INDEX idx_created_at (created_at)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        logging.info("✅ Created userbot_deletion_queue table")

    @staticmethod
    def down(cursor):
        cursor.execute("DROP TABLE IF EXISTS userbot_deletion_queue")
        logging.info("Migration 010 rolled back")
