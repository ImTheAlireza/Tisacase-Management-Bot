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

import asyncio
from unittest.mock import AsyncMock

import pytest
from telegram.error import BadRequest

from utils.helpers import (
    safe_answer_callback,
    delete_group_message,
    DELETED_BY_BOT_CAPTION,
    DELETED_BY_BOT_TEXT,
    deleted_marker_caption,
    group_message_link,
)


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


class TestDeleteGroupMessage:

    async def _fast_sleep(self, monkeypatch):
        original_sleep = asyncio.sleep

        async def _no_delay(_):
            await original_sleep(0)

        monkeypatch.setattr('utils.helpers.asyncio.sleep', _no_delay)

    @pytest.mark.asyncio
    async def test_deletes_recent_message(self, monkeypatch):
        await self._fast_sleep(monkeypatch)
        bot = AsyncMock()
        bot.delete_message = AsyncMock(return_value=True)
        assert await delete_group_message(bot, -100, 42) == 'deleted'
        bot.delete_message.assert_awaited_once_with(chat_id=-100, message_id=42)
        bot.edit_message_caption.assert_not_awaited()
        bot.edit_message_text.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_old_message_is_hidden_via_caption_edit(self, monkeypatch):
        await self._fast_sleep(monkeypatch)
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=BadRequest(
            "Message can't be deleted for everyone"
        ))
        bot.edit_message_caption = AsyncMock(return_value=True)
        assert await delete_group_message(bot, -100, 42) == 'hidden'
        bot.edit_message_caption.assert_awaited_once_with(
            chat_id=-100,
            message_id=42,
            caption=deleted_marker_caption(-100, 42),
            reply_markup=None
        )

    @pytest.mark.asyncio
    async def test_old_message_hidden_via_text_edit_when_caption_edit_fails(
        self, monkeypatch
    ):
        await self._fast_sleep(monkeypatch)
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=BadRequest(
            "Message can't be deleted for everyone"
        ))
        bot.edit_message_caption = AsyncMock(side_effect=BadRequest("no caption"))
        bot.edit_message_text = AsyncMock(return_value=True)
        assert await delete_group_message(bot, -100, 42) == 'hidden'
        bot.edit_message_text.assert_awaited_once_with(
            chat_id=-100,
            message_id=42,
            text=DELETED_BY_BOT_TEXT,
            reply_markup=None
        )

    @pytest.mark.asyncio
    async def test_old_message_fails_when_no_edit_works(self, monkeypatch):
        await self._fast_sleep(monkeypatch)
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=BadRequest(
            "Message can't be deleted for everyone"
        ))
        bot.edit_message_caption = AsyncMock(side_effect=BadRequest("nope"))
        bot.edit_message_text = AsyncMock(side_effect=BadRequest("nope"))
        assert await delete_group_message(bot, -100, 42) == 'failed'

    @pytest.mark.asyncio
    async def test_other_errors_do_not_attempt_edit(self, monkeypatch):
        await self._fast_sleep(monkeypatch)
        bot = AsyncMock()
        bot.delete_message = AsyncMock(side_effect=RuntimeError("boom"))
        assert await delete_group_message(bot, -100, 42) == 'failed'
        bot.edit_message_caption.assert_not_awaited()
        bot.edit_message_text.assert_not_awaited()


class TestGroupMessageLink:

    def test_basic_group_link(self):
        # Real production ids: basic groups have no -100 prefix.
        assert group_message_link(-4608593336, 21456) == (
            "https://t.me/c/4608593336/21456"
        )

    def test_supergroup_link_strips_100_prefix(self):
        assert group_message_link(-1002140742633, 7) == (
            "https://t.me/c/2140742633/7"
        )

    def test_private_chat_has_no_link(self):
        assert group_message_link(5484684731, 42) is None

    def test_marker_caption_includes_link(self):
        caption = deleted_marker_caption(-4608593336, 21456)
        assert caption.startswith(DELETED_BY_BOT_CAPTION)
        assert "https://t.me/c/4608593336/21456" in caption

    def test_marker_caption_without_link_for_private_chat(self):
        assert deleted_marker_caption(123, 42) == DELETED_BY_BOT_CAPTION
