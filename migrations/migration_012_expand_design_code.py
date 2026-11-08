import logging


class Migration012:
    """Leave room for the rejection suffix without truncating original codes."""

    name = "012_expand_design_code"

    @staticmethod
    def up(cursor):
        # Existing codes can use all 20 original characters; rejecting adds
        # '_REJ_' plus eight random characters. Avoid shrinking custom schemas.
        cursor.execute("""
            SELECT CHARACTER_MAXIMUM_LENGTH
            FROM information_schema.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = 'designs' AND COLUMN_NAME = 'code'
        """)
        row = cursor.fetchone()
        if not row or row[0] is None:
            raise RuntimeError('Could not inspect designs.code length')
        if int(row[0]) < 33:
            cursor.execute("ALTER TABLE designs MODIFY COLUMN code VARCHAR(40) NOT NULL")
            logging.info('Expanded designs.code to hold rejected design codes')

    # Do not provide an automatic down migration: shortening the column could
    # truncate rejected design codes or fail while live rows still use them.
