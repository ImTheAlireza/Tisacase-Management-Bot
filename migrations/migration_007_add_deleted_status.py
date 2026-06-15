import logging
from utils.enums import DesignStatus


class Migration007:
    """
    Add 'deleted' to designs.status ENUM to support soft-deletion
    of approved designs while preserving audit trail.
    """

    name = "007_add_deleted_status_to_designs"

    @staticmethod
    def up(cursor):
        logging.info("Adding 'deleted' to designs.status ENUM...")

        try:
            cursor.execute("""
                ALTER TABLE designs
                MODIFY COLUMN status
                ENUM('pending', 'approved', 'rejected', 'deleted')
                DEFAULT 'pending'
            """)
            logging.info("✅ designs.status ENUM updated with 'deleted'")
        except Exception as e:
            if 'Duplicate' in str(e) or 'already exists' in str(e):
                logging.info("Status ENUM already updated, skipping")
            else:
                raise

    @staticmethod
    def down(cursor):
        # Cannot safely remove an ENUM value if rows use it.
        # Just log — manual cleanup required.
        logging.warning(
            "Migration 007 rollback: cannot safely remove 'deleted' from ENUM "
            "if rows exist with that status. Manual cleanup required."
        )