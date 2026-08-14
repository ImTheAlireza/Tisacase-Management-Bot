"""
Tests for utils.helpers.safe_answer_callback.

Bug being guarded: answering a stale/duplicate callback query raises
BadRequest("Query is too old and response timeout expired or query id is
invalid"), which used to crash review_callback before any approve/reject
work happened — the button press was silently lost.
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

from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from utils.helpers import safe_answer_callback


class TestSafeAnswerCallback:

    @pytest.mark.asyncio
    async def test_returns_true_on_success(self):
        query = AsyncMock()
        assert await safe_answer_callback(query) is True
        query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_stale_query_error(self):
        """A stale query must not raise — the handler's work must continue."""
        query = AsyncMock()
        query.answer.side_effect = BadRequest(
            "Query is too old and response timeout expired or query id is invalid"
        )
        assert await safe_answer_callback(query) is False
        query.answer.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_swallows_arbitrary_errors(self):
        query = AsyncMock()
        query.answer.side_effect = RuntimeError("network gone")
        assert await safe_answer_callback(query) is False
