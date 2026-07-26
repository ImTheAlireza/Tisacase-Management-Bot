import logging


class Migration008:
    """Add stats_reset_at column to users and product_lines"""

    name = "008_add_stats_reset"

    @staticmethod
    def up(cursor):
        logging.info("Adding stats_reset_at columns...")

        for table in ['users', 'product_lines']:
            try:
                cursor.execute(f"""
                    ALTER TABLE {table}
                    ADD COLUMN stats_reset_at DATETIME DEFAULT NULL
                """)
                logging.info(f"✅ Added stats_reset_at to {table}")
            except Exception as e:
                if 'Duplicate column' in str(e):
                    logging.info(f"Column stats_reset_at already exists in {table}, skipping")
                else:
                    raise

    @staticmethod
    def down(cursor):
        for table in ['users', 'product_lines']:
            cursor.execute(f"ALTER TABLE {table} DROP COLUMN IF EXISTS stats_reset_at")
        logging.info("Migration 008 rolled back")
