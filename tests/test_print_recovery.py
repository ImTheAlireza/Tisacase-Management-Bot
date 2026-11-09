"""The recovery command never silently duplicates or guesses a print job."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from handlers.print_recovery import (
    PENDING_KEY,
    resend_print_callback,
    resend_print_command,
)
from models.design import Design
from utils.callback_lock import callback_lock
from utils.enums import DesignStatus
from utils.state_manager import StateManager


SUDO_ID = 9000001
PRINT_CHAT_ID = -100222


@pytest.fixture(autouse=True)
def fast_telegram(monkeypatch):
    monkeypatch.setattr('handlers.reviewer.SUDO_USER_ID', SUDO_ID)
    monkeypatch.setattr('utils.helpers.TELEGRAM_SEND_DELAY', 0)


def make_design(prints=None, status=DesignStatus.APPROVED, id=42):
    return Design(id=id, code='TS350', product_line_id=3, status=status,
                  print_file_ids=['p1'] if prints is None else prints)


def make_user(sudo=True):
    return MagicMock(user_id=SUDO_ID, first_name='Admin', is_sudo=sudo, is_active=True)


def make_product(chat_id=PRINT_CHAT_ID):
    return MagicMock(id=3, name_fa='قاب موبایل', group_print=chat_id)


def command_update():
    update = MagicMock()
    update.effective_user.id = SUDO_ID
    update.effective_chat.id = SUDO_ID
    update.effective_chat.type = 'private'
    update.callback_query = None
    update.message.reply_text = AsyncMock(return_value=MagicMock(message_id=700))
    return update


def callback_update(data='resendprint_confirm', message_id=700, chat_id=SUDO_ID):
    update = MagicMock()
    update.effective_user.id = SUDO_ID
    update.message = None
    query = MagicMock()
    query.data = data
    query.message.message_id = message_id
    query.message.chat_id = chat_id
    query.message.text = 'درخواست ارسال مجدد'
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query
    return update


def context(args):
    ctx = MagicMock()
    ctx.args = args
    ctx.user_data = {}
    ctx.bot.send_document = AsyncMock()
    ctx.bot.delete_message = AsyncMock()
    return ctx


async def prepare(ctx, design, product=None, update=None):
    update = update or command_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product or make_product()):
        await resend_print_command(update, ctx)
    return update


@pytest.mark.asyncio
async def test_command_requires_sudo_and_private_chat():
    ctx = context(['TS350'])
    update = command_update()
    with patch('models.user.User.get_by_id', return_value=make_user(sudo=False)), \
         patch('handlers.print_recovery.Design.get_by_code') as lookup:
        await resend_print_command(update, ctx)
    assert 'Sudo' in update.message.reply_text.await_args.args[0]
    lookup.assert_not_called()
    assert PENDING_KEY not in ctx.user_data

    update = command_update()
    update.effective_chat.type = 'supergroup'
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code') as lookup:
        await resend_print_command(update, ctx)
    assert 'خصوصی' in update.message.reply_text.await_args.args[0]
    lookup.assert_not_called()
    assert PENDING_KEY not in ctx.user_data


@pytest.mark.asyncio
@pytest.mark.parametrize('status', [DesignStatus.PENDING, DesignStatus.REJECTED, DesignStatus.DELETED])
async def test_command_refuses_unapproved_designs(status):
    ctx = context(['TS350'])
    update = await prepare(ctx, make_design(status=status))
    assert 'تاییدشده' in update.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data
    ctx.bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_missing_legacy_ids_and_missing_group_are_reported():
    ctx = context(['TS350'])
    update = await prepare(ctx, make_design(prints=[]))
    assert 'شناسه فایل چاپی' in update.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data

    update = await prepare(ctx, make_design(), make_product(chat_id=None))
    assert 'گروه چاپ' in update.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data


@pytest.mark.asyncio
async def test_multiple_prints_require_a_specific_number():
    ctx = context(['ts350'])
    update = await prepare(ctx, make_design(prints=['p1', 'p2']))
    assert '/resendprint TS350 1' in update.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data
    ctx.bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('args', [
    [], ['TS350', 'nope'], ['TS350', '0'], ['TS350', '4'],
    ['TS350', '1', '../psd'], ['TS350', '1', '.psd'], ['../../escape'],
    ['TS350', '1', 'psd', 'extra'],
])
async def test_bad_arguments_never_prepare_or_send(args):
    ctx = context(args)
    update = await prepare(ctx, make_design(prints=['p1', 'p2', 'p3']))
    update.message.reply_text.assert_awaited_once()
    assert PENDING_KEY not in ctx.user_data
    ctx.bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_confirmation_sends_exactly_one_selected_file_once():
    design = make_design(prints=['p1', 'p2', 'p3'])
    product = make_product()
    ctx = context(['ts350', '2', 'AI'])
    command = await prepare(ctx, design, product)
    text = command.message.reply_text.await_args.args[0]
    assert 'پیام‌های قبلی حذف نمی‌شوند' in text
    assert '.ai' in text and 'شما تعیین' in text
    pending = ctx.user_data[PENDING_KEY]
    assert pending['index'] == 1 and pending['extension'] == 'ai'
    ctx.bot.send_document.assert_not_awaited()

    callback = callback_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product), \
         patch('handlers.print_recovery._send_prints_to_print_group', new_callable=AsyncMock,
               return_value=[('p2', True, 'warning: فرمت دستی')]) as send:
        await resend_print_callback(callback, ctx)
        await resend_print_callback(callback, ctx)  # a delayed duplicate tap
    send.assert_awaited_once_with(
        ctx.bot, design, ['p1', 'p2', 'p3'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل',
        selected_indices={1}, extension_overrides={'p2': 'ai'},
    )
    assert PENDING_KEY not in ctx.user_data
    assert 'دوباره به گروه چاپ ارسال شد' in callback.callback_query.edit_message_text.await_args.kwargs['text']
    assert 'پیام‌های قبلی حذف نشده‌اند' in callback.callback_query.edit_message_text.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_cancel_expired_or_foreign_confirmation_does_not_send():
    design = make_design()
    ctx = context(['TS350'])
    await prepare(ctx, design)
    foreign = callback_update(message_id=701)
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery._send_prints_to_print_group', new_callable=AsyncMock) as send:
        await resend_print_callback(foreign, ctx)
        assert PENDING_KEY in ctx.user_data  # a different button cannot consume it
        cancel = callback_update(data='resendprint_cancel')
        await resend_print_callback(cancel, ctx)
        send.assert_not_awaited()
    assert PENDING_KEY not in ctx.user_data
    assert 'لغو شد' in cancel.callback_query.edit_message_text.await_args.kwargs['text']

    await prepare(ctx, design)
    ctx.user_data[PENDING_KEY]['expires'] = 0
    expired = callback_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery._send_prints_to_print_group', new_callable=AsyncMock) as send:
        await resend_print_callback(expired, ctx)
    assert 'منقضی' in expired.callback_query.edit_message_text.await_args.kwargs['text']
    send.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize('change', ['id', 'files', 'status', 'group'])
async def test_confirmation_rechecks_design_snapshot(change):
    design = make_design(prints=['p1', 'p2'])
    product = make_product()
    ctx = context(['TS350', '1'])
    await prepare(ctx, design, product)
    if change == 'id':
        design.id += 1
    elif change == 'files':
        design.print_file_ids = ['other']
    elif change == 'status':
        design.status = DesignStatus.REJECTED
    else:
        product.group_print = -100333
    callback = callback_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product), \
         patch('handlers.print_recovery._send_prints_to_print_group', new_callable=AsyncMock) as send:
        await resend_print_callback(callback, ctx)
    send.assert_not_awaited()
    assert 'تغییر کرده' in callback.callback_query.edit_message_text.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_resend_waits_for_ongoing_approval_and_does_not_send():
    design = make_design()
    ctx = context(['TS350'])
    await prepare(ctx, design)
    assert await callback_lock.acquire('review_TS350')
    try:
        callback = callback_update()
        with patch('models.user.User.get_by_id', return_value=make_user()), \
             patch('handlers.print_recovery._send_prints_to_print_group', new_callable=AsyncMock) as send:
            await resend_print_callback(callback, ctx)
        send.assert_not_awaited()
        assert 'در حال پردازش' in callback.callback_query.edit_message_text.await_args.kwargs['text']
    finally:
        await callback_lock.release('review_TS350')


@pytest.mark.asyncio
async def test_concurrent_confirmation_clicks_send_only_once():
    design = make_design()
    ctx = context(['TS350'])
    await prepare(ctx, design)
    sending = asyncio.Event()
    release = asyncio.Event()

    async def slow_send(*args, **kwargs):
        sending.set()
        await release.wait()
        return [('p1', True, 'ok')]

    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=make_product()), \
         patch('handlers.print_recovery._send_prints_to_print_group', new=AsyncMock(side_effect=slow_send)) as send:
        first = asyncio.create_task(resend_print_callback(callback_update(), ctx))
        await sending.wait()
        await resend_print_callback(callback_update(), ctx)
        release.set()
        await first
    send.assert_awaited_once()


@pytest.mark.asyncio
async def test_confirmed_old_file_is_recovered_and_uploaded_end_to_end():
    design = make_design(prints=['p1', 'p2', 'p3'])
    product = make_product()
    ctx = context(['TS350', '2'])
    data = b'%PDF-1.7\nIllustrator file bytes'
    file = MagicMock(file_size=len(data), file_unique_id='unique')
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    ctx.bot.get_file = AsyncMock(return_value=file)

    async def send_document(*args, **kwargs):
        if kwargs.get('chat_id') == SUDO_ID:
            return MagicMock(message_id=800, document=MagicMock(
                file_name='original.ai', file_unique_id='unique', file_size=len(data)))
        return MagicMock(message_id=801)
    ctx.bot.send_document = AsyncMock(side_effect=send_document)
    await prepare(ctx, design, product)
    ctx.bot.send_document.assert_not_awaited()

    callback = callback_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product), \
         patch('handlers.reviewer.DesignGroupMessage.record') as record:
        await resend_print_callback(callback, ctx)
        await resend_print_callback(callback, ctx)
    assert ctx.bot.send_document.await_count == 2  # one Sudo probe + one group delivery
    ctx.bot.get_file.assert_awaited_once_with('p2')
    ctx.bot.delete_message.assert_awaited_once_with(chat_id=SUDO_ID, message_id=800)
    group_send = ctx.bot.send_document.await_args
    assert group_send.args[0] == PRINT_CHAT_ID
    assert group_send.kwargs['document'].filename == 'TS350_2.ai'
    assert group_send.kwargs['document'].input_file_content == data
    assert record.call_args.kwargs['file_index'] == 1


def test_cancel_clears_resend_confirmation_state():
    data = {PENDING_KEY: {'code': 'TS350'}, 'db_user': object()}
    StateManager.clear_all_workflow_state(data)
    assert PENDING_KEY not in data and data['db_user'] is not None


@pytest.mark.asyncio
async def test_repair_forwarded_old_file_when_original_id_is_missing():
    from handlers.print_recovery import FORWARDED_FILE_KEY, handle_print_repair_file, repair_print_command

    design = make_design(prints=[])
    product = make_product()
    ctx = context(['ts350'])
    command = command_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        await repair_print_command(command, ctx)
    assert FORWARDED_FILE_KEY in ctx.user_data
    assert 'فوروارد' in command.message.reply_text.await_args.args[0]
    ctx.bot.send_document.assert_not_awaited()

    forwarded = command_update()
    forwarded.message.document = MagicMock(file_id='forwarded-id', file_name='file_630',
                                           mime_type='application/octet-stream')
    forwarded.message.reply_text = AsyncMock(return_value=MagicMock(message_id=700))
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        assert await handle_print_repair_file(forwarded, ctx) is True
    assert FORWARDED_FILE_KEY not in ctx.user_data
    assert ctx.user_data[PENDING_KEY]['source'] == 'forwarded'
    assert 'تعلق فایل' in forwarded.message.reply_text.await_args.args[0]
    ctx.bot.send_document.assert_not_awaited()  # still waiting for confirmation

    # A complete minimal 1x1 RGB Photoshop document, not just a magic prefix.
    import struct
    data = (b'8BPS' + struct.pack('>H6sHIIHH', 1, b'\0' * 6, 3, 1, 1, 8, 3)
            + b'\0' * 12 + b'\0\0' + b'\xff\x00\x00')
    file = MagicMock(file_size=len(data), file_unique_id='unique')
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    ctx.bot.get_file = AsyncMock(return_value=file)
    ctx.bot.send_document = AsyncMock(return_value=MagicMock(message_id=801))
    callback = callback_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product), \
         patch('handlers.reviewer.DesignGroupMessage.record') as record:
        await resend_print_callback(callback, ctx)
    ctx.bot.get_file.assert_awaited_once_with('forwarded-id')
    upload = ctx.bot.send_document.await_args
    assert upload.args[0] == PRINT_CHAT_ID
    assert upload.kwargs['document'].filename == 'TS350.psd'
    assert upload.kwargs['document'].input_file_content == data
    assert design.print_file_ids == [] and design.print_file_metadata == {}
    assert record.call_args.kwargs['file_id'] == 'forwarded-id'
    assert 'دوباره به گروه چاپ ارسال شد' in callback.callback_query.edit_message_text.await_args.kwargs['text']


@pytest.mark.asyncio
async def test_forwarded_unknown_format_can_use_explicit_admin_extension():
    from handlers.print_recovery import handle_print_repair_file, repair_print_command

    design = make_design(prints=[])
    product = make_product()
    ctx = context(['TS350', 'ai'])
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        await repair_print_command(command_update(), ctx)

    forwarded = command_update()
    forwarded.message.document = MagicMock(file_id='forwarded', file_name='file_631', mime_type=None)
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        assert await handle_print_repair_file(forwarded, ctx)
    assert '.ai' in forwarded.message.reply_text.await_args.args[0]

    data = b'%PDF-1.7\npossibly illustrator'
    file = MagicMock(file_size=len(data))
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    ctx.bot.get_file = AsyncMock(return_value=file)
    ctx.bot.send_document = AsyncMock(return_value=MagicMock(message_id=801))
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product), \
         patch('handlers.reviewer.DesignGroupMessage.record'):
        await resend_print_callback(callback_update(), ctx)
    assert ctx.bot.send_document.await_args.kwargs['document'].filename == 'TS350.ai'
    assert ctx.bot.send_document.await_args.kwargs['document'].input_file_content == data
    assert 'دستی' in ctx.bot.send_document.await_args.kwargs['caption']


@pytest.mark.asyncio
async def test_forward_flow_requires_private_document_and_still_authorized_sudo():
    from handlers.print_recovery import FORWARDED_FILE_KEY, handle_print_repair_file, repair_print_command

    design = make_design(prints=[])
    ctx = context(['TS350'])
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=make_product()):
        await repair_print_command(command_update(), ctx)
    photo = command_update()
    photo.message.document = None
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user()):
        assert await handle_print_repair_file(photo, ctx)
    assert 'سند' in photo.message.reply_text.await_args.args[0]
    assert FORWARDED_FILE_KEY in ctx.user_data

    doc = command_update()
    doc.message.document = MagicMock(file_id='file', file_name='photo.jpg', mime_type='image/jpeg')
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user(sudo=False)):
        assert await handle_print_repair_file(doc, ctx)
    assert FORWARDED_FILE_KEY not in ctx.user_data and PENDING_KEY not in ctx.user_data
    ctx.bot.send_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_expired_or_changed_forward_repair_is_not_accepted():
    from handlers.print_recovery import FORWARDED_FILE_KEY, handle_print_repair_file, repair_print_command

    design = make_design(prints=[])
    product = make_product()
    ctx = context(['TS350'])
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        await repair_print_command(command_update(), ctx)
    ctx.user_data[FORWARDED_FILE_KEY]['expires'] = 0
    doc = command_update()
    doc.message.document = MagicMock(file_id='file', file_name=None, mime_type=None)
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user()):
        assert await handle_print_repair_file(doc, ctx)
    assert 'منقضی' in doc.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data

    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        await repair_print_command(command_update(), ctx)
    design.id = 43
    with patch('handlers.print_recovery.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code', return_value=design), \
         patch('handlers.print_recovery.ProductLine.get_by_id', return_value=product):
        assert await handle_print_repair_file(doc, ctx)
    assert 'تغییر کرده' in doc.message.reply_text.await_args.args[0]
    assert PENDING_KEY not in ctx.user_data and FORWARDED_FILE_KEY not in ctx.user_data


@pytest.mark.asyncio
async def test_normal_file_route_is_unchanged_without_repair_state():
    import main

    update = command_update()
    ctx = context([])
    with patch.object(main, 'handle_files', new_callable=AsyncMock) as editor:
        await main.route_uploaded_file(update, ctx)
    editor.assert_awaited_once_with(update, ctx)


@pytest.mark.asyncio
async def test_file_route_prioritizes_active_repair():
    import main

    update = command_update()
    ctx = context([])
    with patch.object(main, 'handle_print_repair_file', new_callable=AsyncMock, return_value=True), \
         patch.object(main, 'handle_files', new_callable=AsyncMock) as editor:
        await main.route_uploaded_file(update, ctx)
    editor.assert_not_awaited()


def test_cancel_clears_forward_repair_state():
    from handlers.print_recovery import FORWARDED_FILE_KEY

    data = {FORWARDED_FILE_KEY: {'code': 'TS350'}, 'db_user': object()}
    StateManager.clear_all_workflow_state(data)
    assert FORWARDED_FILE_KEY not in data and data['db_user'] is not None


@pytest.mark.asyncio
async def test_repair_does_not_intercept_active_editor_session():
    from handlers.print_recovery import FORWARDED_FILE_KEY, handle_print_repair_file, repair_print_command

    ctx = context(['TS350'])
    ctx.user_data['code'] = 'TS999'
    update = command_update()
    with patch('models.user.User.get_by_id', return_value=make_user()), \
         patch('handlers.print_recovery.Design.get_by_code') as lookup:
        await repair_print_command(update, ctx)
    assert 'ویرایش' in update.message.reply_text.await_args.args[0]
    assert FORWARDED_FILE_KEY not in ctx.user_data
    lookup.assert_not_called()

    ctx.user_data[FORWARDED_FILE_KEY] = {'chat_id': SUDO_ID}
    assert await handle_print_repair_file(update, ctx) is False
    assert FORWARDED_FILE_KEY not in ctx.user_data


def test_starting_editor_can_cancel_only_recovery_state():
    from handlers.print_recovery import FORWARDED_FILE_KEY

    data = {PENDING_KEY: {'code': 'TS350'}, FORWARDED_FILE_KEY: {'code': 'TS351'},
            'selected_line_id': 3, 'db_user': object()}
    StateManager.clear_print_recovery_state(data)
    assert PENDING_KEY not in data and FORWARDED_FILE_KEY not in data
    assert data['selected_line_id'] == 3 and data['db_user'] is not None


@pytest.mark.asyncio
async def test_backup_restore_always_takes_precedence_over_forward_repair():
    import main
    from handlers.print_recovery import FORWARDED_FILE_KEY, repair_print_command
    from handlers.sudo import restore_command

    ctx = context(['TS350'])
    ctx.user_data[FORWARDED_FILE_KEY] = {'chat_id': SUDO_ID}
    ctx.user_data[PENDING_KEY] = {'some': 'old confirmation'}
    update = command_update()
    with patch('models.user.User.get_by_id', return_value=make_user()):
        await restore_command(update, ctx)
    assert ctx.user_data['awaiting_restore_file'] is True
    assert FORWARDED_FILE_KEY not in ctx.user_data and PENDING_KEY not in ctx.user_data
    with patch('models.user.User.get_by_id', return_value=make_user()):
        await repair_print_command(update, ctx)
    assert 'ریستور' in update.message.reply_text.await_args.args[0]
    assert FORWARDED_FILE_KEY not in ctx.user_data

    # Even if a stale state somehow coexists, let the existing restore handler
    # receive the archive instead of the print repair workflow.
    ctx.user_data[FORWARDED_FILE_KEY] = {'chat_id': SUDO_ID}
    with patch.object(main, 'handle_files', new_callable=AsyncMock) as editor:
        await main.route_uploaded_file(update, ctx)
    editor.assert_awaited_once_with(update, ctx)
    assert FORWARDED_FILE_KEY not in ctx.user_data
    ctx.bot.send_document.assert_not_awaited()
