import logging


class Migration011:
    """Keep the original print document names and MIME types per design."""

    name = "011_add_print_file_metadata"

    @staticmethod
    def up(cursor):
        try:
            cursor.execute("""
                ALTER TABLE designs
                ADD COLUMN print_file_metadata JSON DEFAULT NULL
                AFTER file_types
            """)
            logging.info("Added print_file_metadata column to designs")
        except Exception as e:
            if 'Duplicate column' in str(e):
                logging.info("Column print_file_metadata already exists, skipping")
            else:
                raise

    @staticmethod
    def down(cursor):
        cursor.execute("ALTER TABLE designs DROP COLUMN IF EXISTS print_file_metadata")
