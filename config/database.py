import pymysql
import logging
from config.settings import DB_CONFIG

def get_db_connection():
    """Create a database connection"""
    try:
        conn = pymysql.connect(**DB_CONFIG)
        return conn
    except pymysql.Error as e:
        logging.error(f"❌ Database connection error: {e}")
        raise

def test_connection():
    """Test database connection"""
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        cursor.close()
        conn.close()
        logging.info("✅ Database connection successful")
        return True
    except Exception as e:
        logging.error(f"❌ Database connection test failed: {e}")
        return False

def init_legacy_tables():
    """
    Create legacy tables if they don't exist
    This ensures backward compatibility with existing data
    """
    conn = get_db_connection()
    cursor = conn.cursor()
    
    try:
        # ============= Legacy Mobile Design Tables =============
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_designs (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                mockup_file_ids JSON NOT NULL,
                print_file_ids JSON NOT NULL,
                mockup_message_ids_nazi JSON,
                created_at DATETIME NOT NULL,
                INDEX idx_code (code),
                INDEX idx_designer (designer_chat_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS design_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                final_name TEXT,
                status ENUM('approved', 'rejected') NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_code (code),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS locked_codes (
                code VARCHAR(20) PRIMARY KEY,
                locked_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        # ============= Legacy Sticker Tables =============
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS pending_stickers (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) UNIQUE NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                mockup_file_ids JSON NOT NULL,
                print_file_ids JSON NOT NULL,
                mockup_message_ids_nazi JSON,
                created_at DATETIME NOT NULL,
                INDEX idx_code (code),
                INDEX idx_designer (designer_chat_id)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS sticker_log (
                id INT AUTO_INCREMENT PRIMARY KEY,
                code VARCHAR(20) NOT NULL,
                final_name TEXT,
                status ENUM('approved', 'rejected') NOT NULL,
                designer_chat_id BIGINT NOT NULL,
                created_at DATETIME NOT NULL,
                INDEX idx_code (code),
                INDEX idx_status (status)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS locked_sticker_codes (
                code VARCHAR(20) PRIMARY KEY,
                locked_at DATETIME NOT NULL
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """)

        conn.commit()
        logging.info("✅ Legacy tables verified/created")
        
    except Exception as e:
        logging.error(f"❌ Error creating legacy tables: {e}")
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()