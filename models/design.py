import pymysql
import json
import logging
import time
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive


class Design:
    """Design model for managing all product designs"""

    def __init__(self, id, code, product_line_id, status='pending',
                 editor_user_id=None, editor_name=None,
                 reviewer_user_id=None, reviewer_name=None,
                 mockup_file_ids=None, print_file_ids=None,
                 mockup_message_ids_reviewer=None,
                 created_at=None, reviewed_at=None,
                 final_name=None, metadata=None,
                 product_name=None, product_icon=None, **kwargs):
        self.id = id
        self.code = code
        self.product_line_id = product_line_id
        self.status = status
        self.editor_user_id = editor_user_id
        self.editor_name = editor_name
        self.reviewer_user_id = reviewer_user_id
        self.reviewer_name = reviewer_name
        self.mockup_file_ids = mockup_file_ids or []
        self.print_file_ids = print_file_ids or []
        self.mockup_message_ids_reviewer = mockup_message_ids_reviewer or {}
        self.created_at = created_at
        self.reviewed_at = reviewed_at
        self.final_name = final_name
        self.metadata = metadata
        
        # Joined fields from stats queries
        self.product_name = product_name
        self.product_icon = product_icon

    def set_reviewer_messages(self, reviewer_user_id, msg_ids):
        self.mockup_message_ids_reviewer[str(reviewer_user_id)] = msg_ids

    def get_reviewer_messages(self, reviewer_user_id):
        return self.mockup_message_ids_reviewer.get(str(reviewer_user_id), [])

    def all_reviewer_message_pairs(self):
        for key, msg_ids in self.mockup_message_ids_reviewer.items():
            if key == 'legacy':
                continue
            try:
                yield int(key), msg_ids
            except ValueError:
                continue

    @staticmethod
    def _parse_row(row):
        row['mockup_file_ids'] = json.loads(row['mockup_file_ids']) if row['mockup_file_ids'] else []
        row['print_file_ids'] = json.loads(row['print_file_ids']) if row['print_file_ids'] else []

        raw = row.get('mockup_message_ids_reviewer')
        if raw:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                parsed = {'legacy': parsed}
            row['mockup_message_ids_reviewer'] = parsed
        else:
            row['mockup_message_ids_reviewer'] = {}

        return row

    @staticmethod
    def get_by_code(code):
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("SELECT * FROM designs WHERE code = %s", (code,))
            row = cursor.fetchone()
            return Design(**Design._parse_row(row)) if row else None
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_pending_by_product_line(product_line_id):
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM designs
                WHERE product_line_id = %s AND status = 'pending'
                ORDER BY created_at DESC
            """, (product_line_id,))
            return [Design(**Design._parse_row(r)) for r in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_pending():
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT d.*, pl.name_fa as product_name, pl.icon as product_icon
                FROM designs d
                JOIN product_lines pl ON d.product_line_id = pl.id
                WHERE d.status = 'pending'
                ORDER BY d.created_at ASC
            """)
            return [Design(**Design._parse_row(r)) for r in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def save(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            mockup_json = json.dumps(self.mockup_file_ids, ensure_ascii=False)
            print_json = json.dumps(self.print_file_ids, ensure_ascii=False)
            reviewer_msgs_json = json.dumps(
                self.mockup_message_ids_reviewer, ensure_ascii=False
            ) if self.mockup_message_ids_reviewer else None

            if self.id:
                cursor.execute("""
                    UPDATE designs
                    SET mockup_file_ids = %s,
                        print_file_ids = %s,
                        mockup_message_ids_reviewer = %s,
                        status = %s,
                        reviewer_user_id = %s,
                        reviewer_name = %s,
                        reviewed_at = %s,
                        final_name = %s
                    WHERE id = %s
                """, (mockup_json, print_json, reviewer_msgs_json,
                      self.status, self.reviewer_user_id, self.reviewer_name,
                      self.reviewed_at, self.final_name, self.id))
            else:
                now_utc = to_utc_naive(get_tehran_time())
                cursor.execute("""
                    INSERT INTO designs
                    (code, product_line_id, status, editor_user_id, editor_name,
                     mockup_file_ids, print_file_ids, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.code, self.product_line_id, self.status,
                      self.editor_user_id, self.editor_name,
                      mockup_json, print_json, now_utc))
                self.id = cursor.lastrowid

            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to save design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def approve(self, reviewer_user_id, reviewer_name):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE designs
                SET status = 'approved',
                    reviewer_user_id = %s,
                    reviewer_name = %s,
                    reviewed_at = %s
                WHERE id = %s AND status = 'pending'
            """, (reviewer_user_id, reviewer_name,
                  to_utc_naive(get_tehran_time()), self.id))

            affected = cursor.rowcount
            conn.commit()

            if affected == 0:
                return False

            self.status = 'approved'
            self.reviewer_user_id = reviewer_user_id
            self.reviewer_name = reviewer_name
            self.lock_code()
            logging.info(f"✅ Design {self.code} approved by {reviewer_name}")
            return True

        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to approve design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def reject(self, reviewer_user_id, reviewer_name):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            # Append REJ to physically free the original code, but keep log intact
            rej_code = f"{self.code}_REJ_{int(time.time())}"
            cursor.execute("""
                UPDATE designs
                SET status = 'rejected',
                    code = %s,
                    reviewer_user_id = %s,
                    reviewer_name = %s,
                    reviewed_at = %s
                WHERE id = %s AND status = 'pending'
            """, (rej_code, reviewer_user_id, reviewer_name,
                  to_utc_naive(get_tehran_time()), self.id))

            affected = cursor.rowcount
            conn.commit()

            if affected == 0:
                return False

            self.status = 'rejected'
            self.reviewer_user_id = reviewer_user_id
            self.reviewer_name = reviewer_name
            # Deliberately NOT changing self.code to keep the response smooth for the user view.
            logging.info(f"❌ Design {self.code} rejected and moved to {rej_code}")
            return True

        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to reject design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def lock_code(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            now_utc = to_utc_naive(get_tehran_time())
            cursor.execute("""
                INSERT INTO designs_locked_codes
                (code, product_line_id, locked_at, is_manual)
                VALUES (%s, %s, %s, FALSE)
                ON DUPLICATE KEY UPDATE locked_at = %s
            """, (self.code, self.product_line_id, now_utc, now_utc))
            conn.commit()
        except Exception as e:
            logging.error(f"Failed to lock code {self.code}: {e}")
        finally:
            cursor.close()
            conn.close()

    def delete(self):
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM designs WHERE id = %s", (self.id,))
            conn.commit()
            logging.info(f"🗑️ Design {self.code} deleted")
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to delete design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()