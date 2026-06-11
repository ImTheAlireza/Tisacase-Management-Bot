from config.settings import SUDO_USER_ID, NAZI_CHAT_ID
import logging

class Migration001:
    """Create users table and insert initial users"""
    
    name = "001_add_users_table"
    
    @staticmethod
    def up(cursor):
        """Create users table"""
        logging.info("Creating users table...")
        
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(255),
                first_name VARCHAR(255),
                last_name VARCHAR(255),
                role ENUM('sudo', 'editor', 'reviewer') NOT NULL DEFAULT 'editor',
                is_sudo BOOLEAN DEFAULT FALSE,
                active_role ENUM('sudo', 'editor', 'reviewer') DEFAULT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                added_by BIGINT,
                added_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                last_active DATETIME,
                metadata JSON,
                INDEX idx_role (role),
                INDEX idx_active (is_active),
                INDEX idx_sudo (is_sudo)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)
        
        # Insert sudo user (you)
        cursor.execute("""
            INSERT INTO users 
            (user_id, first_name, role, is_sudo, active_role, is_active, added_at)
            VALUES (%s, 'علیرضا', 'sudo', TRUE, 'sudo', TRUE, NOW())
            ON DUPLICATE KEY UPDATE 
            is_sudo = TRUE, 
            role = 'sudo',
            active_role = 'sudo'
        """, (SUDO_USER_ID,))
        
        # Insert Nazi as default reviewer
        cursor.execute("""
            INSERT INTO users 
            (user_id, first_name, role, is_active, added_by, added_at)
            VALUES (%s, 'نازی', 'reviewer', TRUE, %s, NOW())
            ON DUPLICATE KEY UPDATE 
            role = 'reviewer',
            is_active = TRUE
        """, (NAZI_CHAT_ID, SUDO_USER_ID))
        
        logging.info("✅ Users table created and initial users added")
    
    @staticmethod
    def down(cursor):
        """Rollback: drop users table"""
        cursor.execute("DROP TABLE IF EXISTS users")
        logging.info("Users table dropped")