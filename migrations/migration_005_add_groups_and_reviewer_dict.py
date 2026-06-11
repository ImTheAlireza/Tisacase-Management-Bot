import logging
import json


class Migration005:
    """
    - Add group_products and group_print columns to product_lines
    - Restructure mockup_message_ids_reviewer from flat list to dict {user_id: [msg_ids]}
    """

    name = "005_add_groups_and_reviewer_dict"

    @staticmethod
    def up(cursor):
        logging.info("Adding group columns to product_lines...")

        for col, definition in [
            ('group_products', 'BIGINT DEFAULT NULL'),
            ('group_print', 'BIGINT DEFAULT NULL')
        ]:
            try:
                cursor.execute(f"""
                    ALTER TABLE product_lines ADD COLUMN {col} {definition}
                """)
            except Exception as e:
                if 'Duplicate column' in str(e):
                    logging.info(f"Column {col} already exists, skipping")
                else:
                    raise

        logging.info("Restructuring mockup_message_ids_reviewer to dict format...")

        cursor.execute("""
            SELECT id, editor_user_id, mockup_message_ids_reviewer
            FROM designs
            WHERE status = 'pending'
              AND mockup_message_ids_reviewer IS NOT NULL
              AND mockup_message_ids_reviewer != 'null'
              AND mockup_message_ids_reviewer != '[]'
        """)

        rows = cursor.fetchall()
        converted = 0

        for row in rows:
            design_id = row[0]
            editor_user_id = row[1]
            raw = row[2]

            try:
                existing = json.loads(raw) if isinstance(raw, str) else raw

                # Already a dict — skip
                if isinstance(existing, dict):
                    continue

                # Flat list — we don't know which reviewer it belonged to.
                # Store under key "legacy" so it's preserved but won't match any real user_id.
                # These old pending designs will just not have deletable reviewer messages — acceptable.
                new_format = {"legacy": existing}

                cursor.execute("""
                    UPDATE designs
                    SET mockup_message_ids_reviewer = %s
                    WHERE id = %s
                """, (json.dumps(new_format, ensure_ascii=False), design_id))

                converted += 1

            except Exception as e:
                logging.warning(f"Could not convert reviewer msg ids for design {design_id}: {e}")

        logging.info(f"✅ Migration 005 complete. Converted {converted} pending design(s). "
                     f"Group columns added to product_lines.")

    @staticmethod
    def down(cursor):
        cursor.execute("""
            ALTER TABLE product_lines
            DROP COLUMN IF EXISTS group_products,
            DROP COLUMN IF EXISTS group_print
        """)
        logging.info("Migration 005 rolled back (group columns removed; reviewer dict NOT reverted)")
