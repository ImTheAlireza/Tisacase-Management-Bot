import pymysql
import logging
from config.database import get_db_connection
from utils.helpers import get_tehran_time, to_utc_naive


class DesignGroupMessage:
    """
    Represents a single file that was sent to a group after design approval.
    One record per file per group message.
    """

    def __init__(self, id, design_id, code, group_type, chat_id,
                 message_id, file_id, file_index=0, sent_at=None):
        self.id = id
        self.design_id = design_id
        self.code = code
        self.group_type = group_type   # 'products' or 'print'
        self.chat_id = chat_id
        self.message_id = message_id
        self.file_id = file_id
        self.file_index = file_index
        self.sent_at = sent_at

    @staticmethod
    def record(design_id, code, group_type, chat_id, message_id, file_id, file_index=0):
        """
        Insert a single sent-file record.

        Args:
            design_id:   designs.id
            code:        design code e.g. 'TS001'
            group_type:  'products' or 'print'
            chat_id:     Telegram chat ID of the group
            message_id:  Telegram message ID in that group
            file_id:     original Telegram file_id that was sent
            file_index:  0-based position in the sequence
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            now_utc = to_utc_naive(get_tehran_time())
            cursor.execute("""
                INSERT INTO design_group_messages
                (design_id, code, group_type, chat_id, message_id,
                 file_id, file_index, sent_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    file_id = VALUES(file_id),
                    file_index = VALUES(file_index),
                    sent_at = VALUES(sent_at)
            """, (design_id, code, group_type, chat_id, message_id,
                  file_id, file_index, now_utc))
            conn.commit()
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to record group message for {code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_code(code):
        """
        Get all group messages for a design code.
        Returns list of dicts ordered by group_type, file_index.
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM design_group_messages
                WHERE code = %s
                ORDER BY group_type, file_index
            """, (code,))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def get_by_code_and_type(code, group_type):
        """
        Get group messages for a specific code and group type.
        Useful for targeted deletion.
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM design_group_messages
                WHERE code = %s AND group_type = %s
                ORDER BY file_index
            """, (code, group_type))
            return cursor.fetchall()
        finally:
            cursor.close()
            conn.close()

    @staticmethod
    def delete_by_code(code):
        """Delete all records for a code (after files deleted from groups)"""
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM design_group_messages WHERE code = %s", (code,)
            )
            deleted = cursor.rowcount
            conn.commit()
            logging.info(f"🗑️ Deleted {deleted} group message records for {code}")
            return deleted
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to delete group message records for {code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()