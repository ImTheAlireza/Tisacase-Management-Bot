"""One-tap rejection and compatibility with previously sent prompts."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.reviewer import (
    REJECT_REASON_PROMPT,
    REJECT_REASON_STATE_KEY,
    handle_reject_reason_reply,
    review_callback,
)
from models.design import Design
from utils.enums import DesignStatus


SUDO_USER_ID = 9000001


@pytest.fixture(autouse=True)
def no_send_pacing(monkeypatch):
    monkeypatch.setattr('utils.helpers.TELEGRAM_SEND_DELAY', 0)
    monkeypatch.setattr('handlers.reviewer.SUDO_USER_ID', SUDO_USER_ID)


def make_user(user_id=2001):
    user = MagicMock()
    user.user_id = user_id
    user.first_name = 'Nazi'
    user.is_active = True
    user.is_sudo = False
    user.get_effective_role.return_value = 'reviewer'
    return user


def make_design():
    return Design(
        id=42, code='TS350', product_line_id=3,
        editor_user_id=1001, editor_name='Ali',
        mockup_file_ids=['mockup'], print_file_ids=['print'],
        mockup_message_ids_reviewer={'2001': [10]},
    )


def make_callback(action='reject', user_id=2001):
    query = MagicMock()
    query.data = f'{action}_TS350'
    query.from_user.id = user_id
    query.message.text = 'بررسی طرح'
    query.message.caption = None
    query.message.chat_id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    query.edit_message_caption = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.message = None
    update.effective_user.id = user_id
    update.callback_query = query
    return update, query


def make_context():
    context = MagicMock()
    context.user_data = {REJECT_REASON_STATE_KEY: {'10': 'TS350'}}
    context.bot.send_message = AsyncMock()
    return context


def make_product_line():
    return MagicMock(name_fa='قاب موبایل')


@pytest.mark.asyncio
async def test_one_tap_reject_updates_db_and_notifies_without_reason():
    design = make_design()
    user = make_user()
    update, query = make_callback()
    context = make_context()
    connection = MagicMock()
    connection.cursor.return_value.rowcount = 1

    with patch('models.user.User.get_by_id', return_value=user), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('models.design.get_db_connection', return_value=connection), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock) as cleanup, \
         patch('handlers.reviewer._log_to_group') as log:
        await review_callback(update, context)

    assert design.status == DesignStatus.REJECTED
    sql, params = connection.cursor.return_value.execute.call_args.args
    assert 'WHERE id = %s AND status = %s' in sql
    assert params[0] == DesignStatus.REJECTED
    assert params[1].startswith('TS350_REJ_')
    assert params[2:4] == (2001, 'Nazi')
    connection.commit.assert_called_once()
    assert REJECT_REASON_STATE_KEY not in context.user_data
    cleanup.assert_awaited_once_with(context, design, 2001, 'TS350')
    assert 'رد شد' in query.edit_message_text.await_args.kwargs['text']
    query.edit_message_caption.assert_not_awaited()
    query.message.reply_text.assert_not_awaited()
    assert 'REJECT TS350' in log.call_args.args[1]
    assert 'Reason' not in log.call_args.args[1]

    messages = {c.kwargs['chat_id']: c.kwargs['text']
                for c in context.bot.send_message.await_args_list}
    assert set(messages) == {1001, SUDO_USER_ID, 2001}
    assert 'طرح TS350 رد شد' in messages[1001]
    assert 'طرح TS350 رد شد' in messages[SUDO_USER_ID]
    assert 'رد شد: TS350' in messages[2001]
    assert all('دلیل رد' not in text for text in messages.values())


@pytest.mark.asyncio
async def test_reject_losing_db_race_has_no_notifications_or_cleanup():
    design = make_design()
    design.reject = MagicMock(return_value=False)
    update, query = make_callback()
    context = make_context()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock) as cleanup, \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    design.reject.assert_called_once_with(2001, 'Nazi')
    assert 'قبلاً' in query.edit_message_text.await_args.kwargs['text']
    context.bot.send_message.assert_not_awaited()
    cleanup.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_callback_answer_does_not_prevent_rejection():
    design = make_design()
    design.reject = MagicMock(return_value=True)
    update, query = make_callback()
    query.answer.side_effect = RuntimeError('query expired')
    context = make_context()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock), \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    design.reject.assert_called_once()
    assert any('طرح TS350 رد شد' in c.kwargs['text']
               for c in context.bot.send_message.await_args_list)


@pytest.mark.asyncio
async def test_notification_failure_for_one_recipient_does_not_block_others():
    design = make_design()
    design.reject = MagicMock(return_value=True)
    update, _ = make_callback()
    context = make_context()

    async def send_message(*, chat_id, text):
        if chat_id == design.editor_user_id:
            raise RuntimeError('user blocked bot')

    context.bot.send_message.side_effect = send_message
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock) as cleanup, \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    recipients = {c.kwargs['chat_id'] for c in context.bot.send_message.await_args_list}
    assert recipients == {1001, SUDO_USER_ID, 2001}
    cleanup.assert_awaited_once()


@pytest.mark.asyncio
async def test_simultaneous_reject_callbacks_are_deduplicated():
    design = make_design()
    design.reject = MagicMock(return_value=True)
    update1, _ = make_callback()
    update2, query2 = make_callback(user_id=2002)
    entered, resume = asyncio.Event(), asyncio.Event()

    async def wait_on_notification(*args, **kwargs):
        entered.set()
        await resume.wait()

    def get_user(uid):
        return make_user(uid)

    with patch('models.user.User.get_by_id', side_effect=get_user), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('handlers.reviewer._send_decision_notifications', side_effect=wait_on_notification), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock), \
         patch('handlers.reviewer._log_to_group'):
        first = asyncio.create_task(review_callback(update1, make_context()))
        await entered.wait()
        await review_callback(update2, make_context())
        resume.set()
        await first

    design.reject.assert_called_once()
    assert 'در حال پردازش' in query2.answer.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_legacy_pending_state_does_not_intercept_unrelated_text():
    update = MagicMock()
    update.message.text = 'some other command or text'
    update.message.chat.type = 'private'
    update.message.reply_to_message = None
    update.message.reply_text = AsyncMock()
    context = make_context()
    assert await handle_reject_reason_reply(update, context) is False
    update.message.reply_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_stale_legacy_prompt_cannot_reject_new_design_with_reused_code():
    update = MagicMock()
    update.effective_user.id = 2001
    update.message.chat.type = 'private'
    update.message.text = 'reason'
    update.message.reply_to_message.message_id = 10
    update.message.reply_to_message.caption = REJECT_REASON_PROMPT
    update.message.reply_text = AsyncMock()
    context = make_context()
    reused_design = make_design()
    reused_design.id = 99
    reused_design.mockup_message_ids_reviewer = {'2001': [20]}
    with patch('handlers.reviewer.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=reused_design), \
         patch('handlers.reviewer._finalize_rejection_with_reason', new_callable=AsyncMock) as finalize:
        assert await handle_reject_reason_reply(update, context) is True
    finalize.assert_not_awaited()
    assert 'قدیمی' in update.message.reply_text.await_args.args[0]
    assert REJECT_REASON_STATE_KEY not in context.user_data


@pytest.mark.asyncio
async def test_legacy_prompt_still_completes_the_original_pending_design():
    update = MagicMock()
    update.effective_user.id = 2001
    update.message.chat.type = 'private'
    update.message.text = 'previously requested reason'
    update.message.reply_to_message.message_id = 10
    update.message.reply_to_message.caption = REJECT_REASON_PROMPT
    update.message.reply_text = AsyncMock()
    context = make_context()
    design = make_design()
    user = make_user()
    with patch('handlers.reviewer.User.get_by_id', return_value=user), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer._finalize_rejection_with_reason', new_callable=AsyncMock) as finalize:
        assert await handle_reject_reason_reply(update, context) is True
    finalize.assert_awaited_once_with(update, context, 'TS350', 'previously requested reason', user, 42)


@pytest.mark.asyncio
async def test_non_reviewer_cannot_reject():
    user = make_user()
    user.get_effective_role.return_value = 'editor'
    update, query = make_callback()
    context = make_context()
    with patch('models.user.User.get_by_id', return_value=user), \
         patch('handlers.reviewer.Design.get_by_code') as get_design:
        await review_callback(update, context)
    get_design.assert_not_called()
    context.bot.send_message.assert_not_awaited()
    assert 'نقش' in query.answer.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_already_approved_design_cannot_be_rejected():
    design = make_design()
    design.status = DesignStatus.APPROVED
    design.reject = MagicMock()
    update, query = make_callback()
    context = make_context()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    design.reject.assert_not_called()
    context.bot.send_message.assert_not_awaited()
    assert 'قبلاً پردازش' in query.edit_message_text.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_rejection_notification_retries_telegram_flood_control(monkeypatch):
    from telegram.error import RetryAfter

    monkeypatch.setattr('utils.helpers.asyncio.sleep', AsyncMock())
    design = make_design()
    design.reject = MagicMock(return_value=True)
    update, _ = make_callback()
    context = make_context()
    attempts = {}

    async def send_message(*, chat_id, text):
        attempts[chat_id] = attempts.get(chat_id, 0) + 1
        if chat_id == design.editor_user_id and attempts[chat_id] == 1:
            raise RetryAfter(1)

    context.bot.send_message.side_effect = send_message
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=make_product_line()), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock), \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    assert attempts[design.editor_user_id] == 2
    assert SUDO_USER_ID in attempts
    assert 2001 in attempts


def test_rejection_code_migration_expands_only_when_needed():
    from migrations.migration_012_expand_design_code import Migration012
    import main

    with patch.object(main, 'init_legacy_tables'), \
         patch.object(main, 'CodeService'), \
         patch.object(main, 'MigrationManager') as manager:
        main.run_db_migrations()
    assert isinstance(manager.return_value.run_migrations.call_args.args[0][-1], Migration012)

    cursor = MagicMock()
    cursor.fetchone.return_value = (20,)
    Migration012.up(cursor)
    assert 'VARCHAR(40)' in cursor.execute.call_args.args[0]
    cursor.reset_mock()
    cursor.fetchone.return_value = (80,)
    Migration012.up(cursor)
    assert cursor.execute.call_count == 1  # never shrink a custom schema
    cursor.fetchone.return_value = None
    with pytest.raises(RuntimeError, match='designs.code'):
        Migration012.up(cursor)
