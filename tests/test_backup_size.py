"""
Regression tests for the Telegram upload-limit guard when sending backups.

Bug being guarded: send_daily_backup used to call send_document regardless of
file size, so once public/ grew past Telegram's ~50 MB upload limit the backup
silently failed to reach the Sudo user.
"""
import os

# config.settings exits at import time when these env vars are missing, so
# provide dummies before importing anything that pulls in the settings module.
os.environ.setdefault('MAIN_BOT_TOKEN', '123456:test-token')
os.environ.setdefault('MAIN_ALIREZA_CHAT_ID', '111')
os.environ.setdefault('MAIN_NAZI_CHAT_ID', '222')
os.environ.setdefault('MAIN_LOG_GROUP_ID', '333')
os.environ.setdefault('MAIN_DB_HOST', 'localhost')
os.environ.setdefault('MAIN_DB_USER', 'root')
os.environ.setdefault('MAIN_DB_PASSWORD', '')
os.environ.setdefault('MAIN_DB_NAME', 'tisa_test')

import pytest
from unittest.mock import AsyncMock, MagicMock

import services.backup_service as bs
from services.backup_service import send_daily_backup


def _mock_summary():
    return {
        'date': '2026/08/19',
        'weekday': 'چهارشنبه',
        'today_lines': [],
        'pending_codes': [],
        'top_editor_today': None,
        'top_reviewer_today': None,
        'weekly': {'submitted_week': 0, 'approved_week': 0, 'rejected_week': 0},
        'system': {'total': 0, 'pending': 0, 'approved': 0},
    }


class TestSendDailyBackupSizeLimit:

    def _make_context(self):
        context = MagicMock()
        context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        context.bot.send_document = AsyncMock(return_value=MagicMock(message_id=2))
        return context

    @pytest.mark.asyncio
    async def test_oversized_backup_is_not_sent(self, monkeypatch, tmp_path):
        zip_dir = tmp_path / 'sub'
        zip_dir.mkdir()
        zip_path = zip_dir / 'backup.zip'
        zip_path.write_bytes(b'x' * 100)

        # Lower the limit so a 100-byte file is "oversized".
        monkeypatch.setattr(bs, 'TELEGRAM_UPLOAD_LIMIT_BYTES', 10)
        async def _fake_create_daily_backup_zip():
            return str(zip_path)

        monkeypatch.setattr(
            'services.backup_service.BackupService.create_daily_backup_zip',
            _fake_create_daily_backup_zip,
        )
        monkeypatch.setattr(
            'services.stats_service.StatsService.get_daily_summary',
            lambda: _mock_summary(),
        )
        monkeypatch.setattr('models.user.User.get_by_role', lambda role: [])

        context = self._make_context()
        await send_daily_backup(context)

        # The oversized file must NOT be sent as a document ...
        context.bot.send_document.assert_not_called()

        # ... but a clear warning must be delivered to Sudo.
        texts = [c.kwargs['text'] for c in context.bot.send_message.call_args_list
                 if 'text' in c.kwargs]
        assert any('قابل ارسال نیست' in t for t in texts)

    @pytest.mark.asyncio
    async def test_small_backup_is_sent(self, monkeypatch, tmp_path):
        zip_dir = tmp_path / 'sub'
        zip_dir.mkdir()
        zip_path = zip_dir / 'backup.zip'
        zip_path.write_bytes(b'x' * 100)

        # Default limit is huge; 100 bytes is clearly under it.
        async def _fake_create_daily_backup_zip():
            return str(zip_path)

        monkeypatch.setattr(
            'services.backup_service.BackupService.create_daily_backup_zip',
            _fake_create_daily_backup_zip,
        )
        monkeypatch.setattr(
            'services.stats_service.StatsService.get_daily_summary',
            lambda: _mock_summary(),
        )
        monkeypatch.setattr('models.user.User.get_by_role', lambda role: [])

        context = self._make_context()
        await send_daily_backup(context)

        context.bot.send_document.assert_called_once()
