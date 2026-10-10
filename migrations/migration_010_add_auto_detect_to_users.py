import logging


class Migration010:
    """
    Add a per-editor auto_detect_files flag to the users table.

    Every editor can turn "auto detection of mockup vs print files" on or off
    for their own submissions: photos are stored as mockups and documents as
    print files, and the two per-type finish buttons are replaced by a single
    "اتمام ارسال" button.
    """

    name = "010_add_auto_detect_to_users"

    @staticmethod
    def up(cursor):
        logging.info("Adding auto_detect_files column to users...")

        try:
            cursor.execute("""
                ALTER TABLE users
                ADD COLUMN auto_detect_files BOOLEAN NOT NULL DEFAULT FALSE
            """)
            logging.info("✅ Added auto_detect_files column to users")
        except Exception as e:
            if 'Duplicate column' in str(e):
                logging.info("Column auto_detect_files already exists in users, skipping")
            else:
                raise

    @staticmethod
    def down(cursor):
        cursor.execute("ALTER TABLE users DROP COLUMN IF EXISTS auto_detect_files")
        logging.info("Migration 010 rolled back")
