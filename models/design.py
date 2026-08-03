import pymysql
import json
import logging
import time
import uuid
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
        file_types: Optional[dict] = None,
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
        # Store file types: {file_id: 'photo'|'document'}
        self.file_types = file_types or {}

        if kwargs:
            logging.warning(f"Design.__init__ received unknown kwargs: {list(kwargs.keys())}")

    def set_reviewer_messages(self, reviewer_user_id: int, msg_ids: list) -> None:
        self.mockup_message_ids_reviewer[str(reviewer_user_id)] = msg_ids

    def get_reviewer_messages(self, reviewer_user_id: int) -> list:
        return self.mockup_message_ids_reviewer.get(str(reviewer_user_id), [])

    def save_reviewer_messages(self) -> None:
        """
        ✅ SAFE — Targeted update of mockup_message_ids_reviewer only.

        Called after sending mockup files to a reviewer's PV.
        Stores the message IDs so they can be deleted later
        when the design is approved/rejected/deleted.

        IMPORTANT:
        - Only updates mockup_message_ids_reviewer column.
        - Never touches file IDs, status, or any other field.
        - Preserves existing reviewer entries (other reviewers not affected).
        """
        if not self.id:
            logging.error(
                f"save_reviewer_messages: design {self.code} has no id"
            )
            return

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            reviewer_json = json.dumps(
                self.mockup_message_ids_reviewer,
                ensure_ascii=False
            )
            cursor.execute("""
                UPDATE designs
                SET mockup_message_ids_reviewer = %s
                WHERE id = %s
            """, (reviewer_json, self.id))
            conn.commit()
            logging.info(
                f"✅ Reviewer messages saved for design {self.code}: "
                f"{self.mockup_message_ids_reviewer}"
            )
        except Exception as e:
            conn.rollback()
            logging.error(
                f"Failed to save reviewer messages for {self.code}: {e}"
            )
            raise
        finally:
            cursor.close()
            conn.close()

    def all_reviewer_message_pairs(self):
        for key, msg_ids in self.mockup_message_ids_reviewer.items():
            if key == 'legacy':
                continue
            try:
                yield int(key), msg_ids
            except ValueError:
                continue

    def can_be_edited_by(self, user_id: int) -> bool:
        """
        Check if this design can be edited by the given user.

        Args:
            user_id: User ID attempting to edit

        Returns:
            True if design is pending and user is the owner
        """
        return (
            self.status == DesignStatus.PENDING and
            self.editor_user_id == user_id
        )


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

        # file_types
        try:
            row['file_types'] = json.loads(row['file_types']) if row.get('file_types') else {}
        except (json.JSONDecodeError, TypeError) as e:
            logging.error(f"Invalid JSON in file_types for design {row.get('code')}: {e}")
            row['file_types'] = {}

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

    @staticmethod
    def get_pending_by_reviewer_message(
        reviewer_user_id: int,
        message_id: int
    ) -> Optional['Design']:
        """
        Find a pending design whose reviewer mockup messages contain message_id.

        Used when completing the two-step reject flow from a reply. The stored
        reviewer message list may also include the action-button message, so only
        the first N ids (N = mockup count) are considered valid reply targets.
        """
        conn = get_db_connection()
        cursor = conn.cursor(pymysql.cursors.DictCursor)
        try:
            cursor.execute("""
                SELECT * FROM designs
                WHERE status = %s
                  AND mockup_message_ids_reviewer IS NOT NULL
                ORDER BY created_at ASC
            """, (DesignStatus.PENDING,))
            for row in cursor.fetchall():
                design = Design(**Design._parse_row(row))
                msg_ids = design.get_reviewer_messages(reviewer_user_id)
                mockup_msg_ids = msg_ids[:len(design.mockup_file_ids)]
                if str(message_id) in {str(mid) for mid in mockup_msg_ids}:
                    return design
            return None
        finally:
            cursor.close()
            conn.close()

    def save(self) -> None:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            logging.info(
                f"[DESIGN] save START | code={self.code} | id={self.id} | "
                f"status={self.status} | mockup_files={len(self.mockup_file_ids)} | "
                f"print_files={len(self.print_file_ids)}"
            )

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

            try:
                file_types_json = json.dumps(self.file_types, ensure_ascii=False) if self.file_types else '{}'
            except (TypeError, ValueError) as e:
                logging.error(f"Failed to serialize file_types: {e}")
                file_types_json = '{}'

            if self.id:
                logging.info(
                    f"[DESIGN] save UPDATE branch | code={self.code} | id={self.id}"
                )
                cursor.execute("""
                    UPDATE designs
                    SET mockup_file_ids = %s,
                        print_file_ids = %s,
                        mockup_message_ids_reviewer = %s,
                        file_types = %s,
                        status = %s,
                        reviewer_user_id = %s,
                        reviewer_name = %s,
                        reviewed_at = %s,
                        final_name = %s
                    WHERE id = %s
                """, (mockup_json, print_json, reviewer_msgs_json, file_types_json,
                      self.status.value, self.reviewer_user_id, self.reviewer_name,
                      self.reviewed_at, self.final_name, self.id))
            else:
                logging.info(
                    f"[DESIGN] save INSERT branch | code={self.code}"
                )
                now_utc = to_utc_naive(get_tehran_time())
                cursor.execute("""
                    INSERT INTO designs
                    (code, product_line_id, status, editor_user_id, editor_name,
                     mockup_file_ids, print_file_ids, file_types, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """, (self.code, self.product_line_id, self.status.value,
                      self.editor_user_id, self.editor_name,
                      mockup_json, print_json, file_types_json, now_utc))
                self.id = cursor.lastrowid
                logging.info(
                    f"[DESIGN] save INSERT DONE | code={self.code} | new_id={self.id}"
                )

            conn.commit()
            logging.info(
                f"[DESIGN] save DONE | code={self.code} | id={self.id} | "
                f"rows_affected={cursor.rowcount}"
            )
        except Exception as e:
            conn.rollback()
            logging.exception(
                f"[DESIGN] save FAILED | code={self.code} | error={e}"
            )
            raise
        finally:
            cursor.close()
            conn.close()

    def approve(self, reviewer_user_id: int, reviewer_name: str) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            logging.info(
                f"[DESIGN] approve START | code={self.code} | id={self.id} | "
                f"reviewer={reviewer_name}({reviewer_user_id})"
            )

            conn.begin()
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
            logging.info(
                f"[DESIGN] approve UPDATE result | code={self.code} | "
                f"affected_rows={affected}"
            )

            if affected == 0:
                conn.rollback()
                logging.warning(
                    f"[DESIGN] approve LOST RACE | code={self.code} | "
                    f"affected=0 — another reviewer already processed"
                )
                return False

            now_utc = to_utc_naive(get_tehran_time())
            cursor.execute("""
                INSERT INTO designs_locked_codes
                (code, product_line_id, locked_at, is_manual)
                VALUES (%s, %s, %s, FALSE)
                ON DUPLICATE KEY UPDATE locked_at = %s
            """, (self.code, self.product_line_id, now_utc, now_utc))

            logging.info(
                f"[DESIGN] approve LOCK INSERT | code={self.code} | "
                f"product_line_id={self.product_line_id}"
            )

            conn.commit()

            self.status = DesignStatus.APPROVED
            self.reviewer_user_id = reviewer_user_id
            self.reviewer_name = reviewer_name
            logging.info(
                f"[DESIGN] approve DONE | code={self.code} | "
                f"reviewer={reviewer_name} | status=APPROVED"
            )
            return True

        except Exception as e:
            conn.rollback()
            logging.exception(
                f"[DESIGN] approve FAILED | code={self.code} | error={e}"
            )
            raise
        finally:
            cursor.close()
            conn.close()

    def reject(self, reviewer_user_id: int, reviewer_name: str) -> bool:
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            rej_code = f"{self.code}_REJ_{uuid.uuid4().hex[:8]}"
            conn.begin()
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
            conn.rollback()
            logging.error(f"Failed to lock code {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()

    def delete(self) -> None:
        """
        ✅ SAFE — Pure DB row deletion only.
        - Does NOT delete Telegram messages.
        - Does NOT delete design_group_messages records.
        - Does NOT clear reviewer message IDs.
        - Caller is responsible for Telegram cleanup before calling this.
        """
        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM designs WHERE id = %s", (self.id,))
            conn.commit()
            logging.info(f"🗑️ Design row {self.code} (id={self.id}) deleted from database")
        except Exception as e:
            conn.rollback()
            logging.error(f"Failed to delete design {self.code}: {e}")
            raise
        finally:
            cursor.close()
            conn.close()


    @staticmethod
    async def delete_completely(code: str, bot) -> dict:
        """
        ✅ SAFE FULL DELETION — The ONLY method that deletes a design completely.

        Order of operations:
        1. Delete Telegram messages from groups (APPROVED only)
        2. Delete design_group_messages records (APPROVED only)
        3. Delete Telegram messages from reviewer PVs (PENDING only)
           NOTE: Does NOT clear mockup_message_ids_reviewer in DB — history preserved.
        4. Free locked code from designs_locked_codes
        5. Delete the design row from designs table

        Returns:
            {
                'code': str,
                'status': str,
                'group_messages_deleted': int,
                'reviewer_messages_deleted': int,
                'database_deleted': bool,
                'errors': list[str]
            }
        """
        from models.design_group_message import DesignGroupMessage
        from utils.helpers import delete_messages

        result = {
            'code': code,
            'status': 'not_found',
            'group_messages_deleted': 0,
            'reviewer_messages_deleted': 0,
            'database_deleted': False,
            'errors': []
        }

        design = Design.get_by_code(code)
        if not design:
            result['errors'].append(f"Design {code} not found")
            logging.warning(f"delete_completely: design {code} not found")
            return result

        result['status'] = design.status.value

        # --------------------------------------------------
        # 1️⃣ Delete group messages from Telegram (APPROVED)
        # ✅ Only approved designs have files in groups
        # --------------------------------------------------
        if design.status == DesignStatus.APPROVED:
            group_msgs = DesignGroupMessage.get_by_code(code)

            for record in group_msgs:
                deleted = await delete_messages(
                    bot,
                    record['chat_id'],
                    [record['message_id']]
                )
                result['group_messages_deleted'] += deleted
                if not deleted:
                    result['errors'].append(
                        f"Group msg {record['message_id']}: delete failed"
                    )

            # --------------------------------------------------
            # 2️⃣ Delete design_group_messages records from DB
            # ✅ Only AFTER Telegram deletion attempt
            # ✅ This is correct — group records are no longer
            #    needed once design is deleted
            # --------------------------------------------------
            try:
                DesignGroupMessage.delete_by_code(code)
                logging.info(
                    f"✅ Deleted {len(group_msgs)} group message records for {code}"
                )
            except Exception as e:
                result['errors'].append(f"Failed to delete group records: {e}")
                logging.error(f"Could not delete group message records for {code}: {e}")

        # --------------------------------------------------
        # 3️⃣ Delete reviewer PV messages from Telegram (PENDING)
        # ✅ Only pending designs have messages in reviewer PVs
        # ✅ IMPORTANT: We delete Telegram messages only.
        #    We do NOT modify mockup_message_ids_reviewer in DB.
        #    This preserves the audit trail of who reviewed what.
        # --------------------------------------------------
        if design.status == DesignStatus.PENDING:
            for reviewer_id, msg_ids in design.all_reviewer_message_pairs():
                deleted = await delete_messages(bot, reviewer_id, msg_ids)
                result['reviewer_messages_deleted'] += deleted
                failed = len(msg_ids) - deleted
                if failed:
                    result['errors'].append(
                        f"Reviewer {reviewer_id}: {failed}/{len(msg_ids)} message deletes failed"
                    )

        # --------------------------------------------------
        # 4️⃣ Free locked code
        # ✅ Always do this regardless of status
        # ✅ Allows code to be reused
        # --------------------------------------------------
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    "DELETE FROM designs_locked_codes WHERE code = %s",
                    (code,)
                )
                conn.commit()
                logging.info(f"🔓 Code {code} freed from designs_locked_codes")
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            result['errors'].append(f"Failed to free locked code: {e}")
            logging.error(f"Could not free locked code {code}: {e}")

        # --------------------------------------------------
        # 5️⃣ Delete design row from database
        # ✅ Always last step
        # ✅ design.delete() is a pure DB row deletion
        # --------------------------------------------------
        try:
            design.delete()
            result['database_deleted'] = True
            logging.info(
                f"✅ Design {code} fully deleted. "
                f"Group msgs: {result['group_messages_deleted']}, "
                f"Reviewer msgs: {result['reviewer_messages_deleted']}"
            )
        except Exception as e:
            result['errors'].append(f"Database deletion failed: {e}")
            logging.error(f"Database deletion failed for {code}: {e}")

        return result
