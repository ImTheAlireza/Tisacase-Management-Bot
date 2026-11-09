"""Filename preservation and safe handling of old Telegram print files."""
from unittest.mock import AsyncMock, MagicMock, patch
import json

import pytest
from telegram import InputFile

from handlers.editor import (
    _handle_remove_file,
    _handle_submit_to_reviewer,
    handle_files,
    load_design_for_edit,
)
from handlers.my_designs import _handle_resubmit
from handlers.reviewer import (
    _resolve_mockup_kind,
    _send_prints_to_print_group,
    _telegram_path_extension,
    review_callback,
)
from migrations.migration_011_add_print_file_metadata import Migration011
from models.design import Design
from utils.enums import DesignStatus, EditorStage
from utils.state_manager import StateManager


SECRET = '123456:DO-NOT-LEAK-TOKEN'
FILE_URL = f'https://api.telegram.org/file/bot{SECRET}/documents/file_630'
PRINT_CHAT_ID = -100222


@pytest.fixture(autouse=True)
def no_pacing_or_rate_limit(monkeypatch):
    monkeypatch.setattr('utils.helpers.TELEGRAM_SEND_DELAY', 0)
    monkeypatch.setattr('handlers.reviewer.SUDO_USER_ID', 9000001)
    monkeypatch.setattr('utils.decorators.rate_limiter.check_rate_limit', lambda *args: (True, 0.0))


def make_file(data=b'content', path=FILE_URL, file_size=None):
    file = MagicMock()
    file.file_path = path
    file.file_size = len(data) if file_size is None else file_size
    file.file_unique_id = 'stable-unique-id'
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    return file


def make_bot(file, original_name=None, probe_error=None, group_error=None):
    bot = MagicMock()
    bot.get_file = AsyncMock(return_value=file)

    async def send_document(*args, **kwargs):
        chat_id = args[0] if args else kwargs['chat_id']
        if chat_id == 9000001:
            if probe_error:
                raise probe_error
            document = MagicMock(file_name=original_name, file_unique_id='stable-unique-id',
                                 file_size=file.file_size)
            return MagicMock(message_id=500, document=document)
        if group_error:
            raise group_error
        return MagicMock(message_id=123)

    bot.send_document = AsyncMock(side_effect=send_document)
    bot.delete_message = AsyncMock(return_value=True)
    bot.send_media_group = AsyncMock(side_effect=lambda chat_id, media: [
        MagicMock(message_id=200 + i) for i in range(len(media))
    ])
    bot.send_photo = AsyncMock(return_value=MagicMock(message_id=150))
    bot.send_message = AsyncMock(return_value=MagicMock(message_id=151))
    return bot


def make_design(**kwargs):
    defaults = dict(id=42, code='TS350', product_line_id=3,
                    editor_user_id=1001, editor_name='Ali',
                    print_file_ids=['p1'], mockup_file_ids=['m1'])
    defaults.update(kwargs)
    return Design(**defaults)


def editor():
    user = MagicMock()
    user.user_id = 1001
    user.first_name = 'Ali'
    user.is_active = True
    user.is_sudo = False
    user.get_effective_role.return_value = 'editor'
    user.auto_detect_files = False
    return user


def make_upload_context():
    context = MagicMock()
    context.user_data = {
        'code': 'TS350',
        'stage': EditorStage.PRINT,
        'mockup_files': ['m1'],
        'print_files': [],
        'product_name': 'قاب موبایل',
        'editing_existing': True,
        'workspace_message_id': 10,
    }
    context.job_queue.run_once.return_value = MagicMock()
    context.bot.edit_message_text = AsyncMock()
    return context


def make_document_update(name='طراحی.PSD', mime='application/octet-stream', fid='p1'):
    update = MagicMock()
    update.effective_user.id = 1001
    update.effective_chat.id = 1001
    update.message.photo = None
    update.message.document = MagicMock(file_id=fid, file_name=name, mime_type=mime)
    update.message.reply_text = AsyncMock()
    return update


def attach_query(update):
    query = MagicMock()
    query.message.text = 'ثبت'
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    update.callback_query = query
    return query


@pytest.mark.asyncio
async def test_upload_edit_save_and_delivery_keep_original_extension_and_bytes():
    """Cover the whole new-file path, not just a fabricated model field."""
    context = make_upload_context()
    update = make_document_update()
    design = make_design()
    design.save = MagicMock()
    with patch('models.user.User.get_by_id', return_value=editor()), \
         patch('handlers.editor.Design.get_by_code', return_value=design):
        await handle_files(update, context)
    assert context.user_data['print_file_metadata']['p1'] == {
        'file_name': 'طراحی.PSD', 'mime_type': 'application/octet-stream'
    }

    attach_query(update)
    with patch('handlers.editor.Design.get_by_code', return_value=design), \
         patch('handlers.editor._notify_reviewers_of_edit', new_callable=AsyncMock):
        await _handle_submit_to_reviewer(update, context, 1001)
    design.save.assert_called_once()
    assert design.print_file_metadata['p1']['file_name'] == 'طراحی.PSD'

    original = b'8BPS\x00\x01' + b'photoshop bytes'
    bot = make_bot(make_file(original))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    document = bot.send_document.await_args.kwargs['document']
    assert isinstance(document, InputFile)
    assert document.filename == 'TS350.psd'
    assert document.input_file_content == original
    assert SECRET not in document.filename
    assert result[0][1] is True and not result[0][2].startswith('warning: ')


@pytest.mark.asyncio
async def test_original_filename_beats_telegram_path_and_mime_for_album():
    design = make_design(
        print_file_ids=['p1', 'p2'],
        print_file_metadata={
            'p1': {'file_name': r'C:\art\original.AI', 'mime_type': 'application/pdf'},
            'p2': {'file_name': '../print.PDF', 'mime_type': 'application/pdf'},
        },
    )
    data = b'%PDF-1.7\nexample'
    bot = make_bot(make_file(data, path=FILE_URL + '.pdf'))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    media = bot.send_media_group.await_args.kwargs['media']
    assert [m.media.filename for m in media] == ['TS350_1.ai', 'TS350_2.pdf']
    assert [m.media.input_file_content for m in media] == [data, data]
    assert all(ok for _, ok, _ in results)
    assert all(SECRET not in m.media.filename for m in media)


@pytest.mark.parametrize(('path', 'expected'), [
    (FILE_URL, None),
    (FILE_URL + '.PSD?download=1', 'psd'),
    ('documents/file_630', None),
    ('https://api.telegram.org/file/botX/folder.with.dots/file_630', None),
    ('/files/file_630.jpg', 'jpg'),
    (None, None),
])
def test_extension_comes_only_from_url_basename(path, expected):
    assert _telegram_path_extension(path) == expected


@pytest.mark.asyncio
async def test_legacy_extless_mockup_type_is_unknown_not_the_url_host():
    bot = make_bot(make_file())
    assert await _resolve_mockup_kind(bot, 'm1', {}) == 'unknown'


@pytest.mark.asyncio
@pytest.mark.parametrize(('data', 'extension'), [
    (b'8BPS\x00\x01' + b'old photoshop', 'psd'),
    (b'8BPS\x00\x02' + b'old large photoshop', 'psb'),
    (b'\x89PNG\r\n\x1a\n' + b'old image', 'png'),
    (b'\xff\xd8\xff' + b'old jpeg', 'jpg'),
    (b'RIFF\x10\x00\x00\x00WEBP' + b'old webp', 'webp'),
])
async def test_legacy_format_can_be_recovered_from_unambiguous_signature(data, extension, caplog):
    bot = make_bot(make_file(data))
    design = make_design()
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    document = bot.send_document.await_args.kwargs['document']
    assert document.filename == f'TS350.{extension}'
    assert document.input_file_content == data
    assert result[0][1]
    assert SECRET not in document.filename
    assert SECRET not in caplog.text
    assert SECRET not in str(result)


@pytest.mark.asyncio
async def test_ambiguous_legacy_file_recovers_telegram_document_name_privately(caplog):
    # Illustrator files can also have a PDF header; don't guess .pdf or .png.
    data = b'%PDF-1.7\nmay be AI'
    bot = make_bot(make_file(data), original_name='original.AI')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    probe, send = bot.send_document.await_args_list
    assert probe.kwargs['chat_id'] == 9000001
    assert probe.kwargs['document'] == 'p1'
    assert probe.kwargs['disable_notification'] is True
    bot.delete_message.assert_awaited_once_with(chat_id=9000001, message_id=500)
    assert send.args[0] == PRINT_CHAT_ID
    assert send.kwargs['document'].filename == 'TS350.ai'
    assert send.kwargs['document'].input_file_content == data
    assert results[0][1] and not results[0][2].startswith('warning: ')
    assert SECRET not in str(results) and SECRET not in caplog.text


@pytest.mark.asyncio
async def test_unknown_original_name_is_not_sent_to_print_group():
    bot = make_bot(make_file(b'%PDF-1.7\nmay be AI'), original_name=None)
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_count == 1
    assert bot.send_document.await_args.kwargs['chat_id'] == 9000001
    bot.delete_message.assert_awaited_once()
    assert not results[0][1] and 'پسوند اصلی بازیابی نشد' in results[0][2]
    record.assert_not_called()


@pytest.mark.asyncio
async def test_truncated_download_uses_original_id_not_partial_bytes():
    file = make_file(b'truncated', file_size=100)
    bot = make_bot(file, original_name='design.psd')
    design = make_design(print_file_metadata={
        'p1': {'file_name': 'design.psd', 'mime_type': 'application/octet-stream'}
    })
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'] == 'p1'
    assert 'دانلود کامل نشد' in bot.send_document.await_args.kwargs['caption']
    assert results[0][1] and results[0][2].startswith('warning: ')


@pytest.mark.asyncio
async def test_download_exception_does_not_leak_url_and_uses_id(caplog):
    file = make_file()
    file.download_as_bytearray.side_effect = RuntimeError(FILE_URL)
    bot = make_bot(file, original_name='design.psd')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'] == 'p1'
    assert SECRET not in caplog.text
    assert SECRET not in bot.send_document.await_args.kwargs['caption']
    assert results[0][1] and results[0][2].startswith('warning: ')


@pytest.mark.asyncio
async def test_unknown_size_sends_by_id_without_downloading():
    file = make_file()
    file.file_size = None
    bot = make_bot(file, original_name='design.psd')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    file.download_as_bytearray.assert_not_awaited()
    assert bot.send_document.await_args.kwargs['document'] == 'p1'
    assert results[0][2].startswith('warning: ')


@pytest.mark.asyncio
async def test_failed_original_id_delivery_is_reported_not_marked_as_success():
    file = make_file(b'%PDF-1.7\nunknown')
    bot = make_bot(file)
    bot.send_document.side_effect = RuntimeError('invalid file id')
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert results[0][1] is False
    assert 'پسوند اصلی بازیابی نشد' in results[0][2]
    record.assert_not_called()


@pytest.mark.asyncio
async def test_approval_leaves_persistent_alert_for_unverified_print():
    design = make_design(file_types={'m1': 'photo'})
    design.approve = MagicMock(return_value=True)
    bot = make_bot(make_file(b'%PDF-1.7\nambiguous'))
    user = MagicMock(user_id=2001, first_name='Nazi', is_active=True, is_sudo=True)
    user.get_effective_role.return_value = 'reviewer'
    product = MagicMock(id=3, name_fa='قاب موبایل', group_print=PRINT_CHAT_ID, group_products=-100111)
    product.is_fully_configured.return_value = True
    query = MagicMock()
    query.data = 'approve_TS350'
    query.from_user.id = 2001
    query.message.text = 'بررسی'
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.message = None
    update.effective_user.id = 2001
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}
    context.bot = bot
    with patch('models.user.User.get_by_id', return_value=user), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=product), \
         patch('handlers.reviewer.DesignGroupMessage.record'), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock), \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    assert 'پسوند اصلی بازیابی نشد' in query.edit_message_text.await_args.kwargs['text']
    reviewer_messages = [c.kwargs['text'] for c in bot.send_message.await_args_list
                         if c.kwargs['chat_id'] == 2001]
    assert len(reviewer_messages) == 1
    assert 'ناموفق یا نامشخص' in reviewer_messages[0]
    assert '/resendprint TS350' in reviewer_messages[0]
    sudo_messages = [c.kwargs['text'] for c in bot.send_message.await_args_list
                     if c.kwargs['chat_id'] == 9000001]
    assert any('/resendprint TS350' in message for message in sudo_messages)
    assert any('پسوند اصلی بازیابی نشد' in message for message in sudo_messages)
    assert SECRET not in reviewer_messages[0]


class TestPrintMetadataModel:
    def test_parse_missing_malformed_and_valid_metadata(self):
        base = {'code': 'TS350', 'mockup_file_ids': '[]', 'print_file_ids': '[]'}
        assert Design._parse_row(base.copy())['print_file_metadata'] == {}
        assert Design._parse_row({**base, 'print_file_metadata': '{bad'})['print_file_metadata'] == {}
        metadata = {'p1': {'file_name': 'طراحی.psd', 'mime_type': 'image/psd'}}
        assert Design._parse_row({**base, 'print_file_metadata': json.dumps(metadata)})['print_file_metadata'] == metadata
        assert Design._parse_row({**base, 'print_file_metadata': metadata})['print_file_metadata'] == metadata
        assert Design._parse_row({**base, 'print_file_metadata': '[]'})['print_file_metadata'] == {}

    @pytest.mark.parametrize('design_id', [None, 42])
    def test_save_persists_metadata_on_insert_and_update(self, design_id):
        design = make_design(id=design_id, print_file_metadata={
            'p1': {'file_name': 'طراحی.psd', 'mime_type': 'image/psd'}
        })
        connection = MagicMock()
        with patch('models.design.get_db_connection', return_value=connection):
            design.save()
        sql, values = connection.cursor.return_value.execute.call_args.args
        assert 'print_file_metadata' in sql
        assert any(isinstance(value, str) and 'طراحی.psd' in value for value in values)
        connection.commit.assert_called_once()

    def test_unserializable_metadata_rolls_back_instead_of_losing_name(self):
        design = make_design(print_file_metadata={'p1': {'file_name': object()}})
        connection = MagicMock()
        with patch('models.design.get_db_connection', return_value=connection):
            with pytest.raises(TypeError):
                design.save()
        connection.rollback.assert_called_once()
        connection.commit.assert_not_called()


@pytest.mark.asyncio
async def test_edit_load_and_resubmission_copy_metadata_without_aliasing():
    metadata = {'p1': {'file_name': 'original.psd', 'mime_type': 'image/psd'}}
    old = make_design(code='TS350_REJ_12345678', status=DesignStatus.REJECTED,
                      print_file_metadata=metadata)
    new = make_design(code='TS351', print_file_ids=[])
    new.save = MagicMock()
    update = MagicMock()
    update.callback_query.from_user.id = 1001
    update.callback_query.answer = AsyncMock()
    context = make_upload_context()
    product = MagicMock(code_prefix='TS')
    with patch('handlers.my_designs.Design.get_by_code', return_value=old), \
         patch('handlers.my_designs.ProductLine.get_by_id', return_value=product), \
         patch('handlers.my_designs.User.get_by_id', return_value=editor()), \
         patch('services.code_service.CodeService.generate_code', return_value=('TS351', new)), \
         patch('handlers.editor.load_design_for_edit', new_callable=AsyncMock) as load:
        await _handle_resubmit(update, context, old.code)
    new.save.assert_called_once()
    load.assert_awaited_once_with(update, context, new)
    assert new.print_file_metadata == metadata
    new.print_file_metadata['p1']['file_name'] = 'changed.psd'
    assert old.print_file_metadata['p1']['file_name'] == 'original.psd'

    # A normal edit also gets an independent snapshot.
    pending = make_design(print_file_metadata=metadata)
    pending.can_be_edited_by = MagicMock(return_value=True)
    update.callback_query.message.reply_text = AsyncMock(return_value=MagicMock(message_id=11))
    update.callback_query.edit_message_text = AsyncMock()
    with patch('handlers.editor.User.get_by_id', return_value=editor()), \
         patch('handlers.editor.ProductLine.get_by_id', return_value=MagicMock(id=3, name_fa='قاب موبایل')):
        await load_design_for_edit(update, context, pending)
    context.user_data['print_file_metadata']['p1']['file_name'] = 'new.psd'
    assert pending.print_file_metadata['p1']['file_name'] == 'original.psd'


@pytest.mark.asyncio
async def test_removing_print_metadata_respects_duplicate_file_ids():
    context = make_upload_context()
    context.user_data.update({
        'print_files': ['p1', 'p1'],
        'file_types': {'p1': 'document'},
        'print_file_metadata': {'p1': {'file_name': 'design.psd'}},
    })
    query = MagicMock()
    query.edit_message_text = AsyncMock()
    query.answer = AsyncMock()
    await _handle_remove_file(query, context, 1001, 'print', 0)
    assert 'p1' in context.user_data['print_file_metadata']
    assert 'p1' in context.user_data['file_types']
    await _handle_remove_file(query, context, 1001, 'print', 0)
    assert 'p1' not in context.user_data['print_file_metadata']
    assert 'p1' not in context.user_data['file_types']
    StateManager.clear_editor_state(context)
    assert 'print_file_metadata' not in context.user_data


def test_migration_is_registered_and_idempotent():
    import main
    with patch.object(main, 'init_legacy_tables'), \
         patch.object(main, 'CodeService'), \
         patch.object(main, 'MigrationManager') as manager:
        main.run_db_migrations()
    migrations = manager.return_value.run_migrations.call_args.args[0]
    assert isinstance(migrations[-2], Migration011)
    cursor = MagicMock()
    Migration011.up(cursor)
    assert 'ADD COLUMN print_file_metadata JSON' in cursor.execute.call_args.args[0]
    cursor.execute.side_effect = RuntimeError('Duplicate column name')
    Migration011.up(cursor)
    cursor.execute.side_effect = RuntimeError('permission denied')
    with pytest.raises(RuntimeError, match='permission denied'):
        Migration011.up(cursor)


@pytest.mark.asyncio
async def test_photoshop_mime_without_filename_does_not_mislabel_psb():
    data = b'8BPS\x00\x02' + b'large photoshop document'
    design = make_design(print_file_metadata={
        'p1': {'file_name': None, 'mime_type': 'application/x-photoshop'}
    })
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.psb'


@pytest.mark.asyncio
async def test_large_batch_of_warnings_stays_within_caption_limit():
    prints = [f'p{i}' for i in range(10)]
    design = make_design(print_file_ids=prints)
    file = make_file(b'', file_size=25 * 1024 * 1024)
    bot = make_bot(file, original_name='original.ai')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, design, prints, PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    media = bot.send_media_group.await_args.kwargs['media']
    assert len(media[0].caption) < 1024
    assert 'فایل دیگر' in media[0].caption
    assert all(ok and detail.startswith('warning: ') for _, ok, detail in results)


def test_bot_does_not_start_with_failed_migration():
    import main
    with patch.object(main, 'init_legacy_tables'), \
         patch.object(main, 'CodeService') as code_service, \
         patch.object(main, 'MigrationManager') as manager:
        manager.return_value.run_migrations.return_value = False
        with pytest.raises(RuntimeError, match='migrations failed'):
            main.run_db_migrations()
        code_service.cleanup_orphaned_designs.assert_not_called()


@pytest.mark.asyncio
async def test_album_builds_real_telegram_multipart_with_distinct_attachments():
    """Mocks of send_media_group hide malformed attach:// serialization."""
    from telegram import Bot
    from telegram.request import BaseRequest
    from handlers.reviewer import _print_media

    class CaptureRequest(BaseRequest):
        def __init__(self):
            self.data = None

        @property
        def read_timeout(self):
            return 5.0

        async def initialize(self):
            pass

        async def shutdown(self):
            pass

        async def do_request(self, url, method, request_data=None, **kwargs):
            self.data = request_data
            messages = [
                {'message_id': i + 1, 'date': 1700000000,
                 'chat': {'id': PRINT_CHAT_ID, 'type': 'supergroup'}}
                for i in range(3)
            ]
            return 200, json.dumps({'ok': True, 'result': messages}).encode()

    request = CaptureRequest()
    bot = Bot('123456:testing', request=request)
    media = [
        _print_media({'send_by_id': False, 'filename': 'TS350_1.psd', 'data': b'one'}, 'caption'),
        _print_media({'send_by_id': False, 'filename': 'TS350_2.png', 'data': b'two'}, ''),
        _print_media({'send_by_id': True, 'file_id': 'existing_file_id'}, ''),
    ]
    await bot.send_media_group(PRINT_CHAT_ID, media=media)

    serialized = json.loads(request.data.json_parameters['media'])
    multipart = request.data.multipart_data
    assert serialized[0]['media'].startswith('attach://')
    assert serialized[1]['media'].startswith('attach://')
    first_key = serialized[0]['media'][len('attach://'):]
    second_key = serialized[1]['media'][len('attach://'):]
    assert first_key != second_key
    assert multipart[first_key][:2] == ('TS350_1.psd', b'one')
    assert multipart[second_key][:2] == ('TS350_2.png', b'two')
    assert serialized[2]['media'] == 'existing_file_id'


@pytest.mark.asyncio
async def test_legacy_generated_pdf_path_is_not_treated_as_original_filename():
    # A generated .pdf path can belong to a PDF-compatible Illustrator file.
    data = b'%PDF-1.7\npossible native Illustrator data'
    bot = make_bot(make_file(data, path=FILE_URL + '.pdf'), original_name='original.ai')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.ai'
    assert results[0][1] is True


@pytest.mark.asyncio
async def test_legacy_signature_wins_over_misleading_generated_path():
    data = b'8BPS\x00\x01' + b'photoshop data'
    bot = make_bot(make_file(data, path=FILE_URL + '.png'))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.psd'


@pytest.mark.asyncio
async def test_large_extless_file_warning_never_exposes_bot_token(caplog):
    file = make_file(b'', file_size=25 * 1024 * 1024)
    bot = make_bot(file, original_name='original.psd')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    file.download_as_bytearray.assert_not_awaited()
    assert bot.send_document.await_args.kwargs['document'] == 'p1'
    assert 'فایل بزرگ' in bot.send_document.await_args.kwargs['caption']
    assert SECRET not in bot.send_document.await_args.kwargs['caption']
    assert SECRET not in str(results)
    assert SECRET not in caplog.text


@pytest.mark.asyncio
async def test_approval_leaves_persistent_alert_when_print_delivery_fails():
    design = make_design(file_types={'m1': 'photo'})
    design.approve = MagicMock(return_value=True)
    bot = make_bot(make_file(b'%PDF-1.7\nambiguous'))
    bot.send_document.side_effect = RuntimeError('file no longer available')
    user = MagicMock(user_id=2001, first_name='Nazi', is_active=True, is_sudo=True)
    user.get_effective_role.return_value = 'reviewer'
    product = MagicMock(id=3, name_fa='قاب موبایل', group_print=PRINT_CHAT_ID, group_products=-100111)
    product.is_fully_configured.return_value = True
    query = MagicMock()
    query.data = 'approve_TS350'
    query.from_user.id = 2001
    query.message.text = 'بررسی'
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.message = None
    update.effective_user.id = 2001
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}
    context.bot = bot
    with patch('models.user.User.get_by_id', return_value=user), \
         patch('handlers.reviewer.Design.get_by_code', return_value=design), \
         patch('handlers.reviewer.ProductLine.get_by_id', return_value=product), \
         patch('handlers.reviewer.DesignGroupMessage.record'), \
         patch('handlers.reviewer._cleanup_after_decision', new_callable=AsyncMock), \
         patch('handlers.reviewer._log_to_group'):
        await review_callback(update, context)
    reviewer_messages = [c.kwargs['text'] for c in bot.send_message.await_args_list
                         if c.kwargs['chat_id'] == 2001]
    assert len(reviewer_messages) == 1
    assert 'ناموفق یا نامشخص' in reviewer_messages[0]
    assert 'Sudo' in reviewer_messages[0]


@pytest.mark.asyncio
async def test_print_send_error_redacts_bot_token_from_log_and_summary(monkeypatch, caplog):
    monkeypatch.setattr('handlers.reviewer.BOT_TOKEN', SECRET)
    bot = make_bot(make_file(b'%PDF-1.7\nambiguous'),
                   group_error=RuntimeError(f'network failure: {FILE_URL}'))
    design = make_design(print_file_metadata={'p1': {'file_name': 'original.ai'}})
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert results[0][1] is False
    assert SECRET not in caplog.text
    assert SECRET not in str(results)
    assert '[REDACTED]' in results[0][2]


@pytest.mark.asyncio
async def test_real_telegram_getfile_response_expands_to_url_not_original_name():
    """Reproduce why splitting the whole path at the last dot returned file_630."""
    from telegram import Bot
    from telegram.request import BaseRequest

    class FileRequest(BaseRequest):
        @property
        def read_timeout(self):
            return 5.0

        async def initialize(self):
            pass

        async def shutdown(self):
            pass

        async def do_request(self, url, method, request_data=None, **kwargs):
            payload = {'ok': True, 'result': {
                'file_id': 'file-id', 'file_unique_id': 'unique-id',
                'file_path': 'documents/file_630', 'file_size': 8,
            }}
            return 200, json.dumps(payload).encode()

    file = await Bot('123456:testing', request=FileRequest()).get_file('file-id')
    assert file.file_path.endswith('/documents/file_630')
    assert file.file_path.startswith('https://api.telegram.org/')
    assert not hasattr(file, 'file_name')
    assert _telegram_path_extension(file.file_path) is None
    # The old expression saw the dot in api.telegram.org and embedded the URL
    # path (including the token) in the filename, whose basename became file_630.
    old_extension = file.file_path.rsplit('.', 1)[-1].lower()
    assert old_extension.endswith('/documents/file_630')
    assert '123456:testing' in old_extension
    # TDLib's multipart handler sanitizes the filename as a path, retaining
    # only its final component. This is how TS350 + a URL-tail becomes file_630.
    from pathlib import PurePosixPath
    assert PurePosixPath(f'TS350.{old_extension}').name == 'file_630'
    # The old large-file caption used that same filename verbatim, potentially
    # exposing the token even though the uploaded filename was sanitized.
    old_caption = f"⚠️ TS350.{old_extension} (فایل بزرگ — نام تغییر نکرد)"
    assert '123456:testing' in old_caption


@pytest.mark.asyncio
async def test_mime_without_filename_cannot_override_actual_file_signature():
    data = b'8BPS\x00\x01' + b'photoshop data'
    design = make_design(print_file_metadata={
        'p1': {'file_name': None, 'mime_type': 'image/png'}
    })
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.psd'


@pytest.mark.asyncio
async def test_album_retry_rebuilds_attachments_without_changing_contents(monkeypatch):
    from telegram.error import RetryAfter

    monkeypatch.setattr('utils.helpers.asyncio.sleep', AsyncMock())
    original = b'8BPS\x00\x01' + b'photoshop bytes'
    bot = make_bot(make_file(original))
    bot.send_media_group.side_effect = [
        RetryAfter(1), [MagicMock(message_id=201), MagicMock(message_id=202)]
    ]
    design = make_design(print_file_ids=['p1', 'p2'], print_file_metadata={
        'p1': {'file_name': 'one.psd'}, 'p2': {'file_name': 'two.psd'}
    })
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        results = await _send_prints_to_print_group(
            bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    first = bot.send_media_group.await_args_list[0].kwargs['media']
    second = bot.send_media_group.await_args_list[1].kwargs['media']
    assert first[0].media.attach_uri != second[0].media.attach_uri
    assert [m.media.input_file_content for m in first] == [original, original]
    assert [m.media.input_file_content for m in second] == [original, original]
    assert all(ok for _, ok, _ in results)


@pytest.mark.asyncio
async def test_photo_sent_in_print_stage_is_detected_as_jpeg():
    context = make_upload_context()
    update = make_document_update()
    update.message.document = None
    update.message.photo = [MagicMock(file_id='photo1')]
    design = make_design()
    with patch('models.user.User.get_by_id', return_value=editor()), \
         patch('handlers.editor.Design.get_by_code', return_value=design):
        await handle_files(update, context)
    metadata = context.user_data['print_file_metadata']
    assert metadata['photo1'] == {'file_name': None, 'mime_type': 'image/jpeg'}
    assert 'فشرده' in update.message.reply_text.await_args.args[0]
    design.print_file_metadata = metadata
    data = b'\xff\xd8\xff' + b'telegram compressed photo'
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        await _send_prints_to_print_group(
            bot, design, ['photo1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    document = bot.send_document.await_args.kwargs['document']
    assert document.filename == 'TS350.jpg'
    assert document.input_file_content == data


@pytest.mark.asyncio
@pytest.mark.parametrize(('code', 'expected'), [
    ('TS/350', 'TS_350.psd'),
    ('طرح350', 'طرح350.psd'),
])
async def test_code_filename_is_safe_without_discarding_unicode(code, expected):
    design = make_design(print_file_metadata={'p1': {'file_name': 'original.psd'}})
    bot = make_bot(make_file(b'8BPS\x00\x01data'))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, code, 'قاب موبایل'
        )
    assert bot.send_document.await_args.kwargs['document'].filename == expected


@pytest.mark.asyncio
async def test_real_ptb_legacy_ai_recovery_request_and_reupload():
    """Exercise PTB's real request/response parsing, without contacting Telegram."""
    from telegram import Bot
    from telegram.request import BaseRequest

    data = b'%PDF-1.7\nIllustrator-compatible bytes'

    class TelegramRequest(BaseRequest):
        def __init__(self):
            self.calls = []

        @property
        def read_timeout(self):
            return 5.0

        async def initialize(self):
            pass

        async def shutdown(self):
            pass

        async def do_request(self, url, method, request_data=None, **kwargs):
            if method == 'GET':
                self.calls.append(('download',))
                return 200, data
            action = url.rsplit('/', 1)[-1]
            params = dict(request_data.parameters)
            self.calls.append((action, params, dict(request_data.multipart_data)))
            if action == 'getFile':
                result = {'file_id': 'p1', 'file_unique_id': 'stable-id',
                          'file_size': len(data), 'file_path': 'documents/file_630'}
            elif action == 'sendDocument':
                is_probe = params['chat_id'] == 9000001
                result = {
                    'message_id': 501 if is_probe else 502,
                    'date': 0,
                    'chat': {'id': params['chat_id'], 'type': 'private' if is_probe else 'supergroup'},
                    'document': {'file_id': 'p1', 'file_unique_id': 'stable-id',
                                 'file_name': 'original.ai' if is_probe else 'TS350.ai',
                                 'file_size': len(data)},
                }
            elif action == 'deleteMessage':
                result = True
            else:
                raise AssertionError(action)
            return 200, json.dumps({'ok': True, 'result': result}).encode()

    request = TelegramRequest()
    bot = Bot('123456:testing', request=request)
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert [call[0] for call in request.calls] == [
        'getFile', 'download', 'sendDocument', 'deleteMessage', 'sendDocument'
    ]
    probe = request.calls[2]
    assert probe[1]['chat_id'] == 9000001 and probe[1]['document'] == 'p1'
    assert probe[1]['disable_notification'] is True
    upload = request.calls[4]
    assert upload[1]['chat_id'] == PRINT_CHAT_ID
    assert upload[2]['document'][:2] == ('TS350.ai', data)
    assert result[0][1] is True


@pytest.mark.asyncio
@pytest.mark.parametrize(('unique_id', 'size'), [
    ('another-file', 18),
    ('stable-unique-id', 999),
])
async def test_recovered_name_is_rejected_if_file_identity_or_size_changes(unique_id, size):
    data = b'%PDF-1.7\nambiguous'
    file = make_file(data)
    bot = make_bot(file)
    bot.send_document.return_value = MagicMock(
        message_id=500,
        document=MagicMock(file_name='original.ai', file_unique_id=unique_id, file_size=size),
    )
    bot.send_document.side_effect = None
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert results[0][1] is False
    assert bot.send_document.await_count == 1  # only the private probe
    bot.delete_message.assert_awaited_once_with(chat_id=9000001, message_id=500)
    record.assert_not_called()


@pytest.mark.asyncio
async def test_private_probe_never_uses_a_group_as_sudo(monkeypatch):
    monkeypatch.setattr('handlers.reviewer.SUDO_USER_ID', PRINT_CHAT_ID)
    bot = make_bot(make_file(b'%PDF-1.7\nambiguous'), original_name='original.ai')
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert results[0][1] is False
    bot.send_document.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
async def test_id_only_fallback_refuses_mismatched_original_name():
    file = make_file(b'', file_size=25 * 1024 * 1024)
    bot = make_bot(file, original_name='original.pdf')
    design = make_design(print_file_metadata={'p1': {'file_name': 'original.ai'}})
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert not result[0][1] and 'متفاوت' in result[0][2]
    assert bot.send_document.await_count == 1  # only Sudo's temporary copy
    record.assert_not_called()


@pytest.mark.asyncio
async def test_probe_deletion_failure_does_not_hide_success(caplog):
    data = b'%PDF-1.7\nambiguous'
    bot = make_bot(make_file(data), original_name='original.ai')
    bot.delete_message.side_effect = RuntimeError('delete denied')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert result[0][1]
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.ai'
    assert 'Temporary print copy was not deleted' in caplog.text


@pytest.mark.asyncio
async def test_unrecoverable_file_does_not_block_valid_album_peer():
    data = b'%PDF-1.7\nambiguous'
    design = make_design(print_file_ids=['p1', 'p2'],
                         print_file_metadata={'p2': {'file_name': 'known.ai'}})
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    by_id = {fid: ok for fid, ok, _ in results}
    assert by_id == {'p1': False, 'p2': True}
    assert bot.send_document.await_count == 2  # private probe, then valid p2
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350_2.ai'
    record.assert_called_once()
    assert record.call_args.kwargs['file_index'] == 1


@pytest.mark.asyncio
async def test_confirmed_manual_extension_applies_only_to_selected_file():
    data = b'%PDF-1.7\nambiguous'
    bot = make_bot(make_file(data))
    design = make_design(print_file_ids=['p1', 'p2', 'p3'])
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, design, design.print_file_ids, PRINT_CHAT_ID, 'TS350', 'قاب موبایل',
            selected_indices={1}, extension_overrides={'p2': 'ai'},
        )
    bot.get_file.assert_awaited_once_with('p2')
    bot.delete_message.assert_not_awaited()  # explicit override does not probe
    assert bot.send_document.await_count == 1
    document = bot.send_document.await_args.kwargs['document']
    assert document.filename == 'TS350_2.ai' and document.input_file_content == data
    assert result[0][0] == 'p2' and result[0][1]
    assert 'دستی' in result[0][2]
    assert 'دستی' in bot.send_document.await_args.kwargs['caption']
    assert record.call_args.kwargs['file_index'] == 1


@pytest.mark.asyncio
async def test_network_error_during_album_does_not_automatically_duplicate_prints():
    from telegram.error import TimedOut

    design = make_design(print_file_ids=['p1', 'p2'], print_file_metadata={
        'p1': {'file_name': 'one.psd'}, 'p2': {'file_name': 'two.psd'}
    })
    bot = make_bot(make_file(b'8BPS\x00\x01content'))
    bot.send_media_group.side_effect = TimedOut('response lost after sending')
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, design, design.print_file_ids, PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert len(results) == 2 and all(not ok and 'نامشخص' in detail for _, ok, detail in results)
    bot.send_document.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
async def test_incomplete_album_response_records_known_messages_and_does_not_resend():
    design = make_design(print_file_ids=['p1', 'p2'], print_file_metadata={
        'p1': {'file_name': 'one.psd'}, 'p2': {'file_name': 'two.psd'}
    })
    bot = make_bot(make_file(b'8BPS\x00\x01content'))
    bot.send_media_group.return_value = [MagicMock(message_id=301)]
    bot.send_media_group.side_effect = None
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        results = await _send_prints_to_print_group(
            bot, design, design.print_file_ids, PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert results[0][:2] == ('p1', True)
    assert results[1][0:2] == ('p2', False) and 'ناقص' in results[1][2]
    bot.send_document.assert_not_awaited()
    record.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize('name', ['original.bin', 'original.dat', 'original.tmp'])
async def test_generic_probe_extension_is_not_guessed_as_print_format(name):
    bot = make_bot(make_file(b'%PDF-1.7\nambiguous'), original_name=name)
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert not result[0][1]
    assert bot.send_document.await_count == 1  # the probe only
    record.assert_not_called()


@pytest.mark.asyncio
async def test_tiff_based_raw_photo_uses_original_document_name_not_guess():
    # Camera RAW formats can share the TIFF header; it isn't enough to say tif.
    data = b'II*\x00\x08\x00\x00\x00camera raw content'
    bot = make_bot(make_file(data), original_name='camera.CR2')
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert result[0][1]
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.cr2'


@pytest.mark.asyncio
async def test_two_bm_bytes_alone_are_not_enough_to_guess_bitmap():
    data = b'BMnot necessarily a bitmap'
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert not result[0][1]
    assert bot.send_document.await_count == 1  # private probe only
    record.assert_not_called()


@pytest.mark.asyncio
async def test_valid_bitmap_signature_is_recovered_from_legacy_bytes():
    # Minimal 1x1, 24-bit BMP with a 40-byte DIB header and padded scanline.
    import struct

    pixels = b'\x00\x00\xff\x00'
    data = (b'BM' + struct.pack('<IHHI', 58, 0, 0, 54)
            + struct.pack('<IiiHHIIiiII', 40, 1, 1, 1, 24, 0, 4, 2835, 2835, 0, 0)
            + pixels)
    assert len(data) == 58
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert result[0][1]
    assert bot.send_document.await_args.kwargs['document'].filename == 'TS350.bmp'


@pytest.mark.asyncio
@pytest.mark.parametrize('size', [0, -1])
async def test_known_empty_or_invalid_size_print_is_never_sent(size):
    file = make_file(b'', file_size=size)
    bot = make_bot(file, original_name='original.psd')
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert not result[0][1] and 'خالی' in result[0][2]
    bot.send_document.assert_not_awaited()
    file.download_as_bytearray.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['png', 'jpeg', 'psd'])
async def test_recovered_file_reopens_as_real_image_with_identical_bytes(kind):
    """Decode complete sample images, not just fake magic-byte prefixes."""
    import io
    import struct

    image_module = pytest.importorskip('PIL.Image')
    if kind == 'psd':
        # Minimal valid RGB PSD: 1x1 red pixel, three uncompressed channels.
        header = b'8BPS' + struct.pack('>H6sHIIHH', 1, b'\0' * 6, 3, 1, 1, 8, 3)
        data = header + b'\0' * 12 + b'\0\0' + b'\xff\x00\x00'
        expected = 'psd'
    else:
        buffer = io.BytesIO()
        image_module.new('RGB', (2, 2), (255, 0, 0)).save(buffer, format=kind.upper())
        data = buffer.getvalue()
        expected = 'jpg' if kind == 'jpeg' else kind
    before = image_module.open(io.BytesIO(data))
    before.load()
    bot = make_bot(make_file(data))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    sent = bot.send_document.await_args.kwargs['document']
    assert result[0][1] and sent.filename == f'TS350.{expected}'
    assert sent.input_file_content == data
    after = image_module.open(io.BytesIO(sent.input_file_content))
    after.load()
    assert after.format == before.format and after.size == before.size
    assert list(after.getdata()) == list(before.getdata())


@pytest.mark.asyncio
@pytest.mark.parametrize(('declared', 'content'), [
    ('design.png', b'8BPS\x00\x01actual photoshop'),
    ('design.psd', b'8BPS\x00\x02actually a large photoshop document'),
    ('design.pdf', b'\x89PNG\r\n\x1a\nactually a png'),
])
async def test_known_suffix_conflicting_with_content_is_not_sent(declared, content):
    design = make_design(print_file_metadata={'p1': {'file_name': declared}})
    bot = make_bot(make_file(content))
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert not result[0][1] and 'هماهنگ نیست' in result[0][2]
    bot.send_document.assert_not_awaited()
    record.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('suffix', ['jpg', 'jpeg', 'jpe', 'jfif'])
async def test_jpeg_suffix_variants_are_not_incorrectly_reported_as_mismatches(suffix):
    design = make_design(print_file_metadata={'p1': {'file_name': f'design.{suffix}'}})
    bot = make_bot(make_file(b'\xff\xd8\xffjpeg bytes'))
    with patch('handlers.reviewer.DesignGroupMessage.record'):
        result = await _send_prints_to_print_group(
            bot, design, ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل'
        )
    assert result[0][1]
    assert bot.send_document.await_args.kwargs['document'].filename == f'TS350.{suffix}'


@pytest.mark.asyncio
async def test_manual_override_cannot_label_known_psd_bytes_as_png():
    bot = make_bot(make_file(b'8BPS\x00\x01photoshop'))
    with patch('handlers.reviewer.DesignGroupMessage.record') as record:
        result = await _send_prints_to_print_group(
            bot, make_design(), ['p1'], PRINT_CHAT_ID, 'TS350', 'قاب موبایل',
            extension_overrides={'p1': 'png'},
        )
    assert not result[0][1] and 'هماهنگ نیست' in result[0][2]
    bot.send_document.assert_not_awaited()
    record.assert_not_called()
