import pymysql
import json
import logging
import time
from typing import Optional
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive
from utils.enums import DesignStatus


class Design:
    """Design model for managing all product designs"""

    def __init__(
        self,
        id: Optional[int],
        code: str,
        product_line_id: int,
        status: str = DesignStatus.PENDING,
        editor_user_id: Optional[int] = None,
        editor_name: Optional[str] = None,
        reviewer_user_id: Optional[int] = None,
        reviewer_name: Optional[str] = None,
        mockup_file_ids: Optional[list] = None,
        print_file_ids: Optional[list] = None,
        mockup_message_ids_reviewer: Optional[dict] = None,
        created_at=None,
        reviewed_at=None,
        final_name: Optional[str] = None,
        metadata=None,
        product_name: Optional[str] = None,
        product_icon: Optional[str] = None,
        **kwargs
    ):
        self.id = id
        self.code = code
        self.product_line_id = product_line_id
        self.status = DesignStatus(status) if isinstance(status, str) else status
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
        self.product_name = product_name
        self.product_icon = product_icon

        if kwargs:
            logging.warning(f"Design.__init__ received unknown kwargs: {list(kwargs.keys())}")

    def set_reviewer_messages(self, reviewer_user_id: int, msg_ids: list) -> None:
        self.mockup_message_ids_reviewer[str(reviewer_user_id)] = msg_ids

    def get_reviewer_messages(self, reviewer_user_id: int) -> list:
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
    def _parse_row(row: dict) -> dict:
        """Parse JSON fields from DB row with validation."""
        # mockup_file_ids
        try:
            row['mockup_file_ids'] = json.loads(row['mockup_file_ids']) if row['mockup_file_ids'] else []
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Invalid JSON in mockup_file_ids for design {row.get('code')}: {e}")
            row['mockup_file_ids'] = []

        # print_file_ids
        try:
            row['print_file_ids'] = json.loads(row['print_file_ids']) if row['print_file_ids'] else []
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Invalid JSON in print_file_ids for design {row.get('code')}: {e}")
            row['print_file_ids'] = []

        # mockup_message_ids_reviewer
        try:
            raw = row.get('mockup_message_ids_reviewer')
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    parsed = {'legacy': parsed}
                row['mockup_message_ids_reviewer'] = parsed
            else:
                row['mockup_message_ids_reviewer'] = {}
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Invalid JSON in mockup_message_ids_reviewer for design {row.get('code')}: {e}")
            row['mockup_message_ids_reviewer'] = {}

        return row

    @staticmethod
    def get_by_code(code: str) -> Optional['Design']:
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
    def get_pending_by_product_line(product_line_id: int) -> list['Design']:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM designs
                WHERE product_line_id = %s AND status = %s
                ORDER BY created_at DESC
            """, (product_line_id, DesignStatus.PENDING))
            return [Design(**Design._parse_row(r)) for r in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_all_pending() -> list['Design']:
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT d.*, pl.name_fa as product_name, pl.icon as product_icon
                FROM designs d
                JOIN product_lines pl ON d.product_line_id = pl.id
                WHERE d.status = %s
                ORDER BY d.created_at ASC
            """, (DesignStatus.PENDING,))
            return [Design(**Design._parse_row(r)) for r in cursor.fetchall()]
        finally:
            cursor.close()
            conn.close()

    def save(self) -> None:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            try:
                mockup_json = json.dumps(self.mockup_file_ids, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logging.error(f"Failed to serialize mockup_file_ids: {e}")
                mockup_json = '[]'

            try:
                print_json = json.dumps(self.print_file_ids, ensure_ascii=False)
            except (TypeError, ValueError) as e:
                logging.error(f"Failed to serialize print_file_ids: {e}")
                print_json = '[]'

            try:
                reviewer_msgs_json = json.dumps(
                    self.mockup_message_ids_reviewer, ensure_ascii=False
                ) if self.mockup_message_ids_reviewer else None
            except (TypeError, ValueError) as e:
                logging.error(f"Failed to serialize mockup_message_ids_reviewer: {e}")
                reviewer_msgs_json = None

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
                      self.status.value, self.reviewer_user_id, self.reviewer_name,
                      self.reviewed_at, self.final_name, self.id))
            else:
                now_utc = to_utc_naive(get_tehran_time())
                cursor.execute("""
                    INSERT INTO designs
                    (code, product_line_id, status, editor_user_id, editor_name,
                     mockup_file_ids, print_file_ids, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.code, self.product_line_id, self.status.value,
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

    def approve(self, reviewer_user_id: int, reviewer_name: str) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("""
                UPDATE designs
                SET status = %s,
                    reviewer_user_id = %s,
                    reviewer_name = %s,
                    reviewed_at = %s
                WHERE id = %s AND status = %s
            """, (DesignStatus.APPROVED, reviewer_user_id, reviewer_name,
                  to_utc_naive(get_tehran_time()), self.id, DesignStatus.PENDING))

            affected = cursor.rowcount
            conn.commit()

            if affected == 0:
                return False

            self.status = DesignStatus.APPROVED
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

    def reject(self, reviewer_user_id: int, reviewer_name: str) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            rej_code = f"{self.code}_REJ_{int(time.time())}"
            cursor.execute("""
                UPDATE designs
                SET status = %s,
                    code = %s,
                    reviewer_user_id = %s,
                    reviewer_name = %s,
                    reviewed_at = %s
                WHERE id = %s AND status = %s
            """, (DesignStatus.REJECTED, rej_code, reviewer_user_id, reviewer_name,
                  to_utc_naive(get_tehran_time()), self.id, DesignStatus.PENDING))

            affected = cursor.rowcount
            conn.commit()

            if affected == 0:
                return False

            self.status = DesignStatus.REJECTED
            self.reviewer_user_id = reviewer_user_id
            self.reviewer_name = reviewer_name
            logging.info(f"❌ Design {self.code} rejected and moved to {rej_code}")
            return True

        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to reject design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def lock_code(self) -> None:
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

    def delete(self) -> None:
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