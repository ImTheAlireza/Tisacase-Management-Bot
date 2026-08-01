"""
Regression tests for the Telegram log-forwarding handler and the flood-safe
group send helper.

Bug being guarded: main.TelegramLogHandler used to hold _recursion_guard=True
for the ENTIRE duration of its send loop, so every log record emitted while a
previous log message was still being sent was silently dropped. During a design
approval the logs arrive in a tight burst, therefore the "Mockup N FAILED"
lines never reached LOG_GROUP_ID — only successful sends were visible, which
made it look like failed mockups produced no logs at all.
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

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from main import TelegramLogHandler
from handlers.reviewer import _send_media_with_retry
from telegram.error import RetryAfter


def _make_retry_after(seconds: int) -> RetryAfter:
    """Build a RetryAfter exception across PTB versions (v20 vs v22 API)."""
    try:
        return RetryAfter(retry_after=seconds)
    except TypeError:
        return RetryAfter("flood", retry_after=seconds)


def _make_logger(name: str, handler: logging.Handler) -> logging.Logger:
    logger = logging.getLogger(name)
    logger.handlers = []
    logger.propagate = False
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    return logger


class TestTelegramLogHandler:

    @pytest.mark.asyncio
    async def test_burst_of_logs_is_not_dropped(self, monkeypatch):
        """Every record of a tight burst must reach the log group."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        handler = TelegramLogHandler(bot, -1001234567890)
        handler.setLevel(logging.INFO)

        logger = _make_logger('test_log_burst', handler)

        original_sleep = asyncio.sleep

        async def _no_delay(seconds):
            await original_sleep(0)

        monkeypatch.setattr('main.asyncio.sleep', _no_delay)

        total = 30
        for i in range(total):
            logger.info(f'log line {i}')

        # Give the background drain task time to finish.
        for _ in range(500):
            if not handler.message_queue and (
                handler._task is None or handler._task.done()
            ):
                break
            await original_sleep(0)

        assert bot.send_message.await_count == total
        assert handler.message_queue == []

        logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_queue_is_bounded_when_sender_is_slow(self, monkeypatch):
        """Memory stays bounded; the newest (usually the failure) logs survive."""
        bot = MagicMock()
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=1))
        handler = TelegramLogHandler(bot, -100)
        handler.setLevel(logging.INFO)

        logger = _make_logger('test_log_cap', handler)

        original_sleep = asyncio.sleep

        async def _no_delay(seconds):
            await original_sleep(0)

        monkeypatch.setattr('main.asyncio.sleep', _no_delay)

        total = handler.MAX_QUEUE_SIZE + 20
        for i in range(total):
            logger.info(f'log line {i}')

        # Emits are synchronous, so the drain task has not run yet; the queue
        # must be capped and must keep the newest records.
        assert len(handler.message_queue) == handler.MAX_QUEUE_SIZE
        assert handler.message_queue[-1] == f'log line {total - 1}'
        assert 'log line 0' not in handler.message_queue

        # Let the sender finish so the test exits cleanly.
        for _ in range(1000):
            if not handler.message_queue and (
                handler._task is None or handler._task.done()
            ):
                break
            await original_sleep(0)

        logger.removeHandler(handler)


class TestSendMediaWithRetry:

    @pytest.mark.asyncio
    async def test_retries_once_on_flood_control(self, monkeypatch):
        """A 429 RetryAfter must be retried after the requested delay."""
        calls = {'n': 0}
        sleeps = []

        async def send():
            calls['n'] += 1
            if calls['n'] == 1:
                raise _make_retry_after(1)
            return 'sent'

        original_sleep = asyncio.sleep

        async def _fast_sleep(seconds):
            sleeps.append(seconds)
            await original_sleep(0)

        monkeypatch.setattr('handlers.reviewer.asyncio.sleep', _fast_sleep)

        result = await _send_media_with_retry(send, 'Mockup 1/2')

        assert result == 'sent'
        assert calls['n'] == 2
        assert sleeps == [1]

    @pytest.mark.asyncio
    async def test_gives_up_after_max_retries(self, monkeypatch):
        """Persistent 429 must eventually raise instead of hanging forever."""
        sleeps = []

        async def send():
            raise _make_retry_after(2)

        original_sleep = asyncio.sleep

        async def _fast_sleep(seconds):
            sleeps.append(seconds)
            await original_sleep(0)

        monkeypatch.setattr('handlers.reviewer.asyncio.sleep', _fast_sleep)

        with pytest.raises(RetryAfter):
            await _send_media_with_retry(send, 'Print 1/1')

        assert sleeps == [2, 2, 2]
