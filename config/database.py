import pymysql
import logging
from pymysql.cursors import DictCursor
from dbutils.pooled_db import PooledDB
from config.settings import DB_CONFIG, DB_POOL_MIN_CACHED, DB_POOL_MAX_CACHED, DB_POOL_MAX_CONNECTIONS


_pool: PooledDB | None = None


def _get_pool() -> PooledDB:
    """Initialize pool lazily (once) and return it."""
    global _pool
    if _pool is None:
        _pool = PooledDB(
            creator=pymysql,
            maxconnections=DB_POOL_MAX_CONNECTIONS,
            mincached=DB_POOL_MIN_CACHED,
            maxcached=DB_POOL_MAX_CACHED,
            maxshared=0,
            blocking=True,
            ping=1,
            # Timeouts come from DB_CONFIG automatically
            host=DB_CONFIG['host'],
            user=DB_CONFIG['user'],
            password=DB_CONFIG['password'],
            database=DB_CONFIG['database'],
            charset=DB_CONFIG['charset'],
            connect_timeout=DB_CONFIG['connect_timeout'],
            read_timeout=DB_CONFIG['read_timeout'],
            write_timeout=DB_CONFIG['write_timeout'],
            autocommit=False,
        )
        logging.info(
            f"✅ Database connection pool initialized "
            f"(min_cached={DB_POOL_MIN_CACHED}, max_cached={DB_POOL_MAX_CACHED}, "
            f"max_connections={DB_POOL_MAX_CONNECTIONS})"
        )
    return _pool


def get_db_connection() -> pymysql.connections.Connection:
    """Get a connection from the pool."""
    try:
        return _get_pool().connection()
    except Exception as e:
        logging.error(f"❌ Failed to get connection from pool: {e}")
        raise


def test_connection() -> bool:
    """Test database connectivity."""
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


def init_legacy_tables() -> None:
    """Create legacy tables if they don't exist."""
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
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