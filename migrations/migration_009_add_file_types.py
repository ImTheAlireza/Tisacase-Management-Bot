import logging


class Migration009:
    """Add file_types column to designs table"""

    name = "009_add_file_types"

    @staticmethod
    def up(cursor):
        logging.info("Adding file_types column to designs...")

        try:
            cursor.execute("""
                ALTER TABLE designs
                ADD COLUMN file_types JSON DEFAULT NULL
                AFTER print_file_ids
            """)
            logging.info("✅ Added file_types column to designs")
        except Exception as e:
            if 'Duplicate column' in str(e):
                logging.info("Column file_types already exists in designs, skipping")
            else:
                raise

    @staticmethod
    def down(cursor):
        cursor.execute("ALTER TABLE designs DROP COLUMN IF EXISTS file_types")
        logging.info("Migration 009 rolled back")
