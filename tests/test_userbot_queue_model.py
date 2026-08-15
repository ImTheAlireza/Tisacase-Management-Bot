"""
Unit tests for the userbot deletion queue model (mocked DB layer).

The queue hands old group messages (that the bot cannot delete because of
Telegram's 48h limit) to the Telethon user-bot worker.
"""
import os

os.environ.setdefault('MAIN_BOT_TOKEN', '123456:test-token')
os.environ.setdefault('MAIN_ALIREZA_CHAT_ID', '111')
os.environ.setdefault('MAIN_NAZI_CHAT_ID', '222')
os.environ.setdefault('MAIN_LOG_GROUP_ID', '333')
os.environ.setdefault('MAIN_DB_HOST', 'localhost')
os.environ.setdefault('MAIN_DB_USER', 'root')
os.environ.setdefault('MAIN_DB_PASSWORD', '')
os.environ.setdefault('MAIN_DB_NAME', 'tisa_test')

from unittest.mock import MagicMock, patch

from models.userbot_deletion_queue import UserbotDeletionQueue


def _fake_conn(fetchall=None, fetchone=None, rowcount=1):
    """Build a fake pooled connection for the model under test."""
    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchall.return_value = fetchall or []
    cursor.fetchone.return_value = fetchone
    cursor.rowcount = rowcount
    return conn, cursor


class TestUserbotDeletionQueue:

    def test_enqueue_inserts_row(self):
        conn, cursor = _fake_conn(rowcount=1)
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            assert UserbotDeletionQueue.enqueue(-100, 42, 'TS001') is True

        sql = cursor.execute.call_args[0][0]
        assert 'INSERT IGNORE INTO userbot_deletion_queue' in sql
        assert cursor.execute.call_args[0][1] == (-100, 42, 'TS001')
        conn.commit.assert_called_once()

    def test_enqueue_duplicate_returns_false(self):
        conn, cursor = _fake_conn(rowcount=0)
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            assert UserbotDeletionQueue.enqueue(-100, 42, 'TS001') is False

    def test_claim_batch_returns_claimed_rows(self):
        rows = [
            {'id': 1, 'chat_id': -100, 'message_id': 42, 'code': 'TS001', 'attempts': 1},
        ]
        conn, cursor = _fake_conn(fetchall=rows)
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            result = UserbotDeletionQueue.claim_batch(limit=25, stale_minutes=15)

        assert result == rows
        sql = cursor.execute.call_args_list[0].args[0]
        assert 'UPDATE userbot_deletion_queue' in sql
        assert "WHERE status = 'processing'" in cursor.execute.call_args_list[1].args[0]
        conn.commit.assert_called()

    def test_mark_done_sets_status(self):
        conn, cursor = _fake_conn()
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            UserbotDeletionQueue.mark_done(7)

        sql, args = cursor.execute.call_args[0]
        assert "status = %s" in sql
        assert args == ('done', None, 7)

    def test_mark_failed_stores_error(self):
        conn, cursor = _fake_conn()
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            UserbotDeletionQueue.mark_failed(7, "boom")

        _, args = cursor.execute.call_args[0]
        assert args == ('failed', 'boom', 7)

    def test_retry_later_returns_to_pending(self):
        conn, cursor = _fake_conn()
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            UserbotDeletionQueue.retry_later(7, "flood wait 30s")

        _, args = cursor.execute.call_args[0]
        assert args == ('pending', 'flood wait 30s', 7)

    def test_counts_maps_statuses(self):
        conn, cursor = _fake_conn(
            fetchall=[('pending', 3), ('done', 2), ('failed', 1)]
        )
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            counts = UserbotDeletionQueue.counts()

        assert counts == {
            'pending': 3,
            'processing': 0,
            'done': 2,
            'failed': 1,
        }

    def test_recent_failed_returns_rows(self):
        rows = [{'chat_id': -100, 'message_id': 42, 'code': 'TS001',
                 'last_error': 'x', 'attempts': 5}]
        conn, cursor = _fake_conn(fetchall=rows)
        with patch('models.userbot_deletion_queue.get_db_connection',
                   return_value=conn):
            result = UserbotDeletionQueue.recent_failed()

        assert result == rows
