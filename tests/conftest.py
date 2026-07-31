import pytest
import pymysql
import os
import logging
from config.settings import DB_CONFIG


TEST_DB_CONFIG = {
    **DB_CONFIG,
    'database': os.getenv('TEST_DB_NAME', 'selfnit4_tisa_test'),
    'connect_timeout': 5,
    'read_timeout':    10,
    'write_timeout':   10,
}


def get_test_connection() -> pymysql.connections.Connection:
    """Get a direct connection to the test database."""
    config = {k: v for k, v in TEST_DB_CONFIG.items()
              if k not in ('connect_timeout', 'read_timeout', 'write_timeout')}
    return pymysql.connect(**config)


def _verify_test_db() -> None:
    """
    Safety check: make sure we're connected to the TEST database,
    not the production one. Prevents accidental data wipe.
    """
    prod_db: str = DB_CONFIG['database']
    test_db: str = TEST_DB_CONFIG['database']

    if prod_db == test_db:
        raise RuntimeError(
            f"❌ DANGER: TEST_DB_NAME is the same as production DB ({prod_db})!\n"
            f"Set TEST_DB_NAME in your .env to a different database."
        )

    if 'test' not in test_db.lower():
        raise RuntimeError(
            f"❌ DANGER: Test database name '{test_db}' does not contain 'test'.\n"
            f"Refusing to run to prevent accidental data loss."
        )


def _create_test_schema(cursor) -> None:
    """Create all tables needed for integration tests."""
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
            metadata JSON
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS product_lines (
            id INT AUTO_INCREMENT PRIMARY KEY,
            code_prefix VARCHAR(10) UNIQUE NOT NULL,
            name_en VARCHAR(50) NOT NULL,
            name_fa VARCHAR(50) NOT NULL,
            icon VARCHAR(10) DEFAULT '',
            code_format VARCHAR(50) DEFAULT '{prefix}{counter:03d}',
            counter_start INT DEFAULT 1,
            counter_end INT DEFAULT 999,
            has_mockup BOOLEAN DEFAULT TRUE,
            has_print_file BOOLEAN DEFAULT TRUE,
            is_active BOOLEAN DEFAULT TRUE,
            display_order INT DEFAULT 0,
            group_products BIGINT DEFAULT NULL,
            group_print BIGINT DEFAULT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
            metadata JSON
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS designs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            code VARCHAR(20) UNIQUE NOT NULL,
            product_line_id INT NOT NULL,
            status ENUM('pending', 'approved', 'rejected', 'deleted') DEFAULT 'pending',
            editor_user_id BIGINT NOT NULL,
            editor_name VARCHAR(255),
            reviewer_user_id BIGINT,
            reviewer_name VARCHAR(255),
            mockup_file_ids JSON NOT NULL,
            print_file_ids JSON NOT NULL,
            mockup_message_ids_reviewer JSON,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_at DATETIME,
            final_name TEXT,
            metadata JSON
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS designs_locked_codes (
            code VARCHAR(20) PRIMARY KEY,
            product_line_id INT NOT NULL,
            locked_by BIGINT,
            locked_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            is_manual BOOLEAN DEFAULT FALSE,
            notes TEXT
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)

    # ← THIS WAS MISSING
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS design_group_messages (
            id INT AUTO_INCREMENT PRIMARY KEY,
            design_id INT NOT NULL,
            code VARCHAR(20) NOT NULL,
            group_type ENUM('products', 'print') NOT NULL,
            chat_id BIGINT NOT NULL,
            message_id BIGINT NOT NULL,
            file_id VARCHAR(255) NOT NULL,
            file_index INT NOT NULL DEFAULT 0,
            sent_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE KEY unique_msg (chat_id, message_id),
            INDEX idx_design_id (design_id),
            INDEX idx_code (code),
            INDEX idx_group_type (group_type),
            INDEX idx_chat_message (chat_id, message_id),
            INDEX idx_sent_at (sent_at)
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
    """)


def _drop_test_schema(cursor) -> None:
    """Drop all test tables."""
    cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
    for table in [
        'design_group_messages',  # ← ADD THIS — drop before designs (foreign key)
        'designs_locked_codes',
        'designs',
        'product_lines',
        'users'
    ]:
        cursor.execute(f"DROP TABLE IF EXISTS {table}")
    cursor.execute("SET FOREIGN_KEY_CHECKS = 1")


# ---------------------------------------------------------------------------
# Pytest fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope='session')
def test_db():
    """
    Session-scoped: creates schema once, tears it down after all tests.
    """
    # Safety check before touching any DB
    _verify_test_db()

    conn = get_test_connection()
    cursor = conn.cursor()
    try:
        _drop_test_schema(cursor)
        _create_test_schema(cursor)
        conn.commit()
        logging.info(f"✅ Test schema created in {TEST_DB_CONFIG['database']}")
    except Exception as e:
        conn.rollback()
        raise RuntimeError(f"Failed to create test schema: {e}")
    finally:
        cursor.close()
        conn.close()

    yield TEST_DB_CONFIG

    # Teardown — drop all tables but keep the DB itself
    conn = get_test_connection()
    cursor = conn.cursor()
    try:
        _drop_test_schema(cursor)
        conn.commit()
        logging.info("✅ Test schema dropped")
    finally:
        cursor.close()
        conn.close()


@pytest.fixture(autouse=True)
def clean_tables(request):
    """
    Truncates all data before each test.
    Runs automatically for every integration test.
    """
    if "integration" not in request.node.nodeid:
        yield
        return
    request.getfixturevalue("test_db")
    conn = get_test_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("SET FOREIGN_KEY_CHECKS = 0")
        for table in ['designs_locked_codes', 'designs', 'product_lines', 'users']:
            cursor.execute(f"TRUNCATE TABLE {table}")
        cursor.execute("SET FOREIGN_KEY_CHECKS = 1")
        conn.commit()
    finally:
        cursor.close()
        conn.close()
    yield


@pytest.fixture
def db_conn(test_db):
    """Direct DB connection for integration tests."""
    conn = get_test_connection()
    yield conn
    conn.close()


@pytest.fixture
def seed_product_line(db_conn):
    """Insert a standard TS product line and return its ID."""
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO product_lines
        (code_prefix, name_en, name_fa, icon, counter_start,
         counter_end, is_active, display_order)
        VALUES ('TS', 'case', 'قاب موبایل', '📱', 1, 999, TRUE, 1)
    """)
    db_conn.commit()
    pl_id = cursor.lastrowid
    cursor.close()
    return pl_id


@pytest.fixture
def seed_editor(db_conn):
    """Insert a standard editor user and return user_id."""
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, first_name, role, is_active)
        VALUES (1001, 'Ali', 'editor', TRUE)
    """)
    db_conn.commit()
    cursor.close()
    return 1001


@pytest.fixture
def seed_reviewer(db_conn):
    """Insert a standard reviewer user and return user_id."""
    cursor = db_conn.cursor()
    cursor.execute("""
        INSERT INTO users (user_id, first_name, role, is_active)
        VALUES (2001, 'Nazi', 'reviewer', TRUE)
    """)
    db_conn.commit()
    cursor.close()
    return 2001