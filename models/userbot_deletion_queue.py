"""
Queue of group messages the management bot could not delete (48h bot limit).

The management bot enqueues (chat_id, message_id) pairs here; a Telethon
user-bot worker (userbot/delete_worker.py) on the same server claims pending
rows and deletes the messages through the owner's user account, which has no
48-hour limit. Status flow:

    pending -> processing -> done
                    \      -> failed (after too many attempts)
                     \-> pending (flood control: retry later)

Claiming is atomic and reclaims rows stuck in 'processing' (worker crash).
"""

import logging

from pymysql.cursors import DictCursor

from config.database import get_db_connection

TABLE = "userbot_deletion_queue"
LOG_TAG = "[USERBOT-QUEUE]"


class UserbotDeletionQueue:
    """DAO for the userbot deletion queue."""

    # ------------------------------------------------------------------
    # Producer side (management bot)
    # ------------------------------------------------------------------
    @staticmethod
    def enqueue(chat_id: int, message_id: int, code: str | None = None) -> bool:
        """Queue a message for user-account deletion. Idempotent per message.

        Returns True when the row was (re)inserted, False when it already
        existed.
        """
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"""
                    INSERT IGNORE INTO {TABLE}
                        (chat_id, message_id, code)
                    VALUES (%s, %s, %s)
                    """,
                    (chat_id, message_id, code)
                )
                conn.commit()
                inserted = cursor.rowcount > 0
                if inserted:
                    logging.info(
                        f"{LOG_TAG} enqueued chat={chat_id} "
                        f"msg={message_id} code={code}"
                    )
                return inserted
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            logging.error(
                f"{LOG_TAG} enqueue failed chat={chat_id} "
                f"msg={message_id}: {e}"
            )
            return False

    # ------------------------------------------------------------------
    # Consumer side (userbot worker)
    # ------------------------------------------------------------------
    @staticmethod
    def claim_batch(limit: int = 25, stale_minutes: int = 15) -> list[dict]:
        """Atomically claim up to `limit` pending rows for processing.

        Rows stuck in 'processing' for longer than `stale_minutes` (worker
        crash mid-batch) are reclaimed automatically.
        """
        try:
            conn = get_db_connection()
            cursor = conn.cursor(DictCursor)
            try:
                cursor.execute(
                    f"""
                    UPDATE {TABLE}
                    SET status = 'processing',
                        attempts = attempts + 1,
                        claimed_at = NOW()
                    WHERE id IN (
                        SELECT id FROM (
                            SELECT id
                            FROM {TABLE}
                            WHERE status = 'pending'
                               OR (status = 'processing'
                                   AND updated_at < NOW() - INTERVAL %s MINUTE)
                            ORDER BY id
                            LIMIT %s
                        ) AS inner_q
                    )
                    """,
                    (stale_minutes, limit)
                )
                conn.commit()
                cursor.execute(
                    f"""
                    SELECT id, chat_id, message_id, code, attempts
                    FROM {TABLE}
                    WHERE status = 'processing'
                    ORDER BY id
                    LIMIT %s
                    """,
                    (limit,)
                )
                rows = cursor.fetchall()
                if rows:
                    logging.info(f"{LOG_TAG} claimed {len(rows)} row(s)")
                return rows
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            logging.error(f"{LOG_TAG} claim_batch failed: {e}")
            return []

    @staticmethod
    def mark_done(queue_id: int) -> None:
        UserbotDeletionQueue._update_status(queue_id, 'done', None)

    @staticmethod
    def mark_failed(queue_id: int, error: str) -> None:
        UserbotDeletionQueue._update_status(queue_id, 'failed', error)

    @staticmethod
    def retry_later(queue_id: int, error: str) -> None:
        """Put a claimed row back to pending (flood control, retry next cycle)."""
        UserbotDeletionQueue._update_status(queue_id, 'pending', error)

    @staticmethod
    def _update_status(queue_id: int, status: str, error: str | None) -> None:
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"""
                    UPDATE {TABLE}
                    SET status = %s, last_error = %s
                    WHERE id = %s
                    """,
                    (status, (error or "")[:500] or None, queue_id)
                )
                conn.commit()
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            logging.error(
                f"{LOG_TAG} status update failed id={queue_id} "
                f"status={status}: {e}"
            )

    # ------------------------------------------------------------------
    # Status / reporting
    # ------------------------------------------------------------------
    @staticmethod
    def counts() -> dict:
        """Return counts per status: {'pending': n, 'processing': n, ...}"""
        try:
            conn = get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute(
                    f"SELECT status, COUNT(*) FROM {TABLE} GROUP BY status"
                )
                counts = {
                    'pending': 0,
                    'processing': 0,
                    'done': 0,
                    'failed': 0,
                }
                for status, count in cursor.fetchall():
                    counts[status] = count
                return counts
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            logging.error(f"{LOG_TAG} counts failed: {e}")
            return {'pending': 0, 'processing': 0, 'done': 0, 'failed': 0}

    @staticmethod
    def recent_failed(limit: int = 5) -> list[dict]:
        """Most recent failed rows for the status command."""
        try:
            conn = get_db_connection()
            cursor = conn.cursor(DictCursor)
            try:
                cursor.execute(
                    f"""
                    SELECT chat_id, message_id, code, last_error, attempts
                    FROM {TABLE}
                    WHERE status = 'failed'
                    ORDER BY updated_at DESC
                    LIMIT %s
                    """,
                    (limit,)
                )
                return cursor.fetchall()
            finally:
                cursor.close()
                conn.close()
        except Exception as e:
            logging.error(f"{LOG_TAG} recent_failed failed: {e}")
            return []
