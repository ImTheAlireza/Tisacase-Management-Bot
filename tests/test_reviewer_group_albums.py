"""
Tests for album (media group) delivery of approved files to group_products
and group_print in handlers/reviewer.py.
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock

from telegram import InputMediaPhoto, InputMediaDocument
from models.design import Design
from handlers.reviewer import (
    MAX_MEDIA_GROUP_ITEMS,
    _send_mockups_to_products_group,
    _send_prints_to_print_group,
)
from config.settings import MAX_FILE_SIZE_DOWNLOAD_MB


PRODUCTS_CHAT_ID = -100111
PRINT_CHAT_ID = -100222


@pytest.fixture(autouse=True)
def no_send_pacing(monkeypatch):
    """Keep the shared send helper fast by disabling its pacing delay."""
    monkeypatch.setattr('utils.helpers.TELEGRAM_SEND_DELAY', 0)


def _make_design(mockups=None, prints=None, file_types=None) -> Design:
    return Design(
        id=1,
        code='TS001',
        product_line_id=1,
        mockup_file_ids=mockups or [],
        print_file_ids=prints or [],
        file_types=file_types or {},
    )


def _make_product_line() -> MagicMock:
    pl = MagicMock()
    pl.group_products = PRODUCTS_CHAT_ID
    pl.group_print = PRINT_CHAT_ID
    pl.name_fa = 'قاب موبایل'
    return pl


def _make_file(file_path='files/photo.png', file_size=1024, data=b'filedata'):
    """Fake telegram File object."""
    file = MagicMock()
    file.file_path = file_path
    file.file_size = file_size
    file.download_as_bytearray = AsyncMock(return_value=bytearray(data))
    return file


def _make_bot(files=None, media_group_error=None):
    bot = MagicMock()
    bot._next_id = 100

    def _next_message(*args, **kwargs):
        bot._next_id += 1
        return MagicMock(message_id=bot._next_id)

    async def _send_media_group(chat_id, media):
        if media_group_error is not None:
            raise media_group_error
        msgs = []
        for _ in media:
            bot._next_id += 1
            msgs.append(MagicMock(message_id=bot._next_id))
        return msgs

    bot.send_photo = AsyncMock(side_effect=_next_message)
    bot.send_document = AsyncMock(side_effect=_next_message)
    bot.send_media_group = AsyncMock(side_effect=_send_media_group)
    bot.get_file = AsyncMock(side_effect=files or (lambda fid: _make_file()))
    return bot


def _album_media(bot) -> list:
    """Media list passed to the (single) send_media_group call."""
    return bot.send_media_group.await_args.kwargs['media']


# ---------------------------------------------------------------------------
# Mockups → products group
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestMockupsToProductsGroup:

    async def test_single_mockup_is_not_an_album(self):
        design = _make_design(mockups=['m1'], file_types={'m1': 'photo'})
        design.file_types = {'m1': 'photo'}
        bot = _make_bot()

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        bot.send_media_group.assert_not_awaited()
        bot.send_photo.assert_awaited_once()
        assert bot.send_photo.await_args.args[0] == PRODUCTS_CHAT_ID
        assert bot.send_photo.await_args.kwargs['photo'] == 'm1'
        assert results == [('m1', True, results[0][2])]
        assert results[0][1] is True
        record.assert_called_once()
        assert record.call_args.kwargs['group_type'] == 'products'
        assert record.call_args.kwargs['message_id'] == 101

    async def test_multiple_mockups_sent_as_one_album(self):
        design = _make_design(
            mockups=['m1', 'm2', 'm3'],
            file_types={'m1': 'photo', 'm2': 'photo', 'm3': 'document'},
        )
        bot = _make_bot()

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        bot.send_media_group.assert_awaited_once()
        assert bot.send_media_group.await_args.args[0] == PRODUCTS_CHAT_ID
        media = _album_media(bot)
        assert len(media) == 3
        assert isinstance(media[0], InputMediaPhoto)
        assert isinstance(media[1], InputMediaPhoto)
        assert isinstance(media[2], InputMediaDocument)
        assert [m.media for m in media] == ['m1', 'm2', 'm3']
        # Caption only on the first album item
        assert media[0].caption == 'کد: TS001 (1-3/3)'
        assert media[1].caption == ''
        assert media[2].caption == ''
        # Nothing sent individually
        bot.send_photo.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        # Every album message is recorded for later cleanup
        assert record.call_count == 3
        assert [c.kwargs['message_id'] for c in record.call_args_list] == [101, 102, 103]
        assert [c.kwargs['file_index'] for c in record.call_args_list] == [0, 1, 2]
        assert all(ok for _, ok, _ in results)

    async def test_more_than_ten_mockups_are_split_across_albums(self):
        mockups = [f'm{i}' for i in range(MAX_MEDIA_GROUP_ITEMS + 1)]
        design = _make_design(mockups=mockups, file_types={fid: 'photo' for fid in mockups})
        bot = _make_bot()

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        # 11 files → 9 + 2 albums (no lone trailing item)
        assert bot.send_media_group.await_count == 2
        sizes = [len(c.kwargs['media']) for c in bot.send_media_group.await_args_list]
        assert sizes == [MAX_MEDIA_GROUP_ITEMS - 1, 2]
        captions = [c.kwargs['media'][0].caption for c in bot.send_media_group.await_args_list]
        assert captions == ['کد: TS001 (1-9/11)', 'کد: TS001 (10-11/11)']
        bot.send_photo.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        assert len(results) == MAX_MEDIA_GROUP_ITEMS + 1
        assert record.call_count == MAX_MEDIA_GROUP_ITEMS + 1
        assert [c.kwargs['file_index'] for c in record.call_args_list] == list(range(11))

    async def test_twenty_files_split_into_two_full_albums(self):
        mockups = [f'm{i}' for i in range(2 * MAX_MEDIA_GROUP_ITEMS)]
        design = _make_design(mockups=mockups, file_types={fid: 'photo' for fid in mockups})
        bot = _make_bot()

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        sizes = [len(c.kwargs['media']) for c in bot.send_media_group.await_args_list]
        assert sizes == [MAX_MEDIA_GROUP_ITEMS, MAX_MEDIA_GROUP_ITEMS]

    async def test_album_failure_falls_back_to_individual_sends(self):
        design = _make_design(mockups=['m1', 'm2'], file_types={'m1': 'photo', 'm2': 'photo'})
        bot = _make_bot(media_group_error=RuntimeError('album rejected'))

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        bot.send_media_group.assert_awaited_once()
        assert bot.send_photo.await_count == 2
        assert all(ok for _, ok, _ in results)

    async def test_unknown_type_falls_back_to_photo_then_document(self):
        design = _make_design(mockups=['legacy1', 'legacy2'])
        bot = _make_bot()
        # Type lookup fails → 'unknown' → no album, individual sends
        bot.get_file = AsyncMock(side_effect=RuntimeError('no file info'))
        bot.send_photo = AsyncMock(side_effect=RuntimeError('not a photo'))

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        bot.send_media_group.assert_not_awaited()
        assert bot.send_document.await_count == 2
        assert all(ok for _, ok, _ in results)

    async def test_unknown_type_resolved_from_file_path_is_sent_in_album(self):
        design = _make_design(mockups=['legacy1', 'legacy2'])
        bot = _make_bot(files=lambda fid: _make_file(file_path='documents/file.pdf'))

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_mockups_to_products_group(bot, design, _make_product_line(), 'TS001')

        bot.send_media_group.assert_awaited_once()
        media = _album_media(bot)
        assert all(isinstance(m, InputMediaDocument) for m in media)
        assert all(ok for _, ok, _ in results)


# ---------------------------------------------------------------------------
# Print files → print group
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPrintsToPrintGroup:

    async def test_single_print_keeps_single_document_behaviour(self):
        design = _make_design(prints=['p1'])
        bot = _make_bot()

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_prints_to_print_group(
                bot, design, ['p1'], PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        bot.send_media_group.assert_not_awaited()
        bot.send_document.assert_awaited_once()
        assert bot.send_document.await_args.args[0] == PRINT_CHAT_ID
        assert bot.send_document.await_args.kwargs['caption'] == 'قاب موبایل - TS001'
        assert results[0][1] is True
        assert record.call_args.kwargs['group_type'] == 'print'

    async def test_multiple_prints_sent_as_album_with_renamed_files(self):
        design = _make_design(prints=['p1', 'p2', 'p3'])
        bot = _make_bot(files=lambda fid: _make_file(file_path=f'files/{fid}.pdf'))

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_prints_to_print_group(
                bot, design, ['p1', 'p2', 'p3'], PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        bot.send_media_group.assert_awaited_once()
        assert bot.send_media_group.await_args.args[0] == PRINT_CHAT_ID
        media = _album_media(bot)
        assert len(media) == 3
        assert all(isinstance(m, InputMediaDocument) for m in media)
        assert [m.media.filename for m in media] == [
            'TS001_1.pdf', 'TS001_2.pdf', 'TS001_3.pdf'
        ]
        assert media[0].caption == 'قاب موبایل - TS001 (1-3/3)'
        assert media[1].caption == ''
        assert media[2].caption == ''
        bot.send_document.assert_not_awaited()
        assert record.call_count == 3
        assert [c.kwargs['message_id'] for c in record.call_args_list] == [101, 102, 103]
        assert all(ok for _, ok, _ in results)

    async def test_more_than_ten_prints_are_split_across_albums(self):
        prints = [f'p{i}' for i in range(MAX_MEDIA_GROUP_ITEMS + 1)]
        design = _make_design(prints=prints)
        bot = _make_bot(files=lambda fid: _make_file(file_path='files/x.psd'))

        with patch('handlers.reviewer.DesignGroupMessage.record') as record:
            results = await _send_prints_to_print_group(
                bot, design, prints, PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        # 11 files → 9 + 2 albums, numbered by global position
        assert bot.send_media_group.await_count == 2
        sizes = [len(c.kwargs['media']) for c in bot.send_media_group.await_args_list]
        assert sizes == [MAX_MEDIA_GROUP_ITEMS - 1, 2]
        second_album = bot.send_media_group.await_args_list[1].kwargs['media']
        assert [m.media.filename for m in second_album] == ['TS001_10.psd', 'TS001_11.psd']
        assert second_album[0].caption == 'قاب موبایل - TS001 (10-11/11)'
        bot.send_document.assert_not_awaited()
        assert len(results) == MAX_MEDIA_GROUP_ITEMS + 1
        assert record.call_count == MAX_MEDIA_GROUP_ITEMS + 1

    async def test_large_print_is_sent_by_file_id_without_download(self):
        large_size = (MAX_FILE_SIZE_DOWNLOAD_MB + 5) * 1024 * 1024
        files = {
            'p1': _make_file(file_path='files/p1.psd', file_size=large_size),
            'p2': _make_file(file_path='files/p2.psd', file_size=1024),
        }
        design = _make_design(prints=['p1', 'p2'])
        bot = _make_bot(files=lambda fid: files[fid])

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            await _send_prints_to_print_group(
                bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        media = _album_media(bot)
        # Large file goes by file_id (original name), the small one is renamed
        assert media[0].media == 'p1'
        assert media[1].media.filename == 'TS001_2.psd'
        files['p1'].download_as_bytearray.assert_not_awaited()
        files['p2'].download_as_bytearray.assert_awaited_once()
        # Large files keep their original name — warned in the album caption
        assert 'فایل بزرگ' in media[0].caption
        assert 'TS001_1.psd' in media[0].caption

    async def test_upload_size_cap_splits_prints_into_more_albums(self, monkeypatch):
        # Budget fits two 1 KB files per album
        monkeypatch.setattr('handlers.reviewer.MEDIA_GROUP_MAX_UPLOAD_BYTES', 2500)
        prints = [f'p{i}' for i in range(4)]
        design = _make_design(prints=prints)
        bot = _make_bot(files=lambda fid: _make_file(file_path='files/x.pdf', file_size=1024))

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_prints_to_print_group(
                bot, design, prints, PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        assert bot.send_media_group.await_count == 2
        sizes = [len(c.kwargs['media']) for c in bot.send_media_group.await_args_list]
        assert sizes == [2, 2]
        captions = [c.kwargs['media'][0].caption for c in bot.send_media_group.await_args_list]
        assert captions == ['قاب موبایل - TS001 (1-2/4)', 'قاب موبایل - TS001 (3-4/4)']
        bot.send_document.assert_not_awaited()
        assert all(ok for _, ok, _ in results)

    async def test_album_failure_falls_back_to_individual_sends(self):
        design = _make_design(prints=['p1', 'p2'])
        bot = _make_bot(media_group_error=RuntimeError('album rejected'))

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_prints_to_print_group(
                bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        bot.send_media_group.assert_awaited_once()
        assert bot.send_document.await_count == 2
        captions = [c.kwargs['caption'] for c in bot.send_document.await_args_list]
        assert captions == ['قاب موبایل - TS001 (1/2)', 'قاب موبایل - TS001 (2/2)']
        assert all(ok for _, ok, _ in results)

    async def test_failed_prepare_is_reported_and_does_not_block_the_album(self):
        design = _make_design(prints=['p1', 'p2'])

        async def _get_file(fid):
            if fid == 'p1':
                raise RuntimeError('file gone')
            return _make_file(file_path='files/x.png')

        bot = _make_bot()
        bot.get_file = AsyncMock(side_effect=_get_file)

        with patch('handlers.reviewer.DesignGroupMessage.record'):
            results = await _send_prints_to_print_group(
                bot, design, ['p1', 'p2'], PRINT_CHAT_ID, 'TS001', 'قاب موبایل'
            )

        bot.send_media_group.assert_not_awaited()  # only one file left
        bot.send_document.assert_awaited_once()
        assert results[0][0] == 'p1' and results[0][1] is False
        assert results[1][0] == 'p2' and results[1][1] is True


# ---------------------------------------------------------------------------
# End-to-end wiring through review_callback
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestApproveCallbackUsesAlbums:

    async def test_approve_with_multiple_files_sends_albums(self):
        from handlers.reviewer import review_callback

        design = _make_design(
            mockups=['m1', 'm2'],
            prints=['p1', 'p2'],
            file_types={'m1': 'photo', 'm2': 'photo'},
        )
        design.approve = MagicMock(return_value=True)
        design.get_reviewer_messages = MagicMock(return_value=[])
        design.all_reviewer_message_pairs = MagicMock(return_value=[])
        design.set_reviewer_messages = MagicMock()
        design.save_reviewer_messages = MagicMock()

        pl = _make_product_line()
        pl.id = 1
        pl.icon = '📱'
        pl.is_fully_configured = MagicMock(return_value=True)

        user = MagicMock()
        user.user_id = 555
        user.first_name = 'Nazi'
        user.is_active = True
        user.is_sudo = True
        user.get_effective_role = MagicMock(return_value='reviewer')

        bot = _make_bot(files=lambda fid: _make_file(file_path=f'files/{fid}.png'))
        context = MagicMock()
        context.bot = bot
        context.user_data = {}

        query = MagicMock()
        query.data = 'approve_TS001'
        query.from_user.id = 555
        query.answer = AsyncMock()
        query.edit_message_text = AsyncMock()
        query.edit_message_caption = AsyncMock()
        query.message.text = 'placeholder'
        query.message.chat.id = 555

        update = MagicMock()
        update.callback_query = query
        update.effective_user.id = 555

        with patch('models.user.User.get_by_id', return_value=user), \
                patch('handlers.reviewer.Design.get_by_code', return_value=design), \
                patch('handlers.reviewer.ProductLine.get_by_id', return_value=pl), \
                patch('handlers.reviewer.DesignGroupMessage.record') as record, \
                patch('handlers.reviewer._log_to_group'):
            await review_callback(update, context)

        albums = {c.args[0]: c.kwargs['media'] for c in bot.send_media_group.await_args_list}
        assert set(albums) == {PRODUCTS_CHAT_ID, PRINT_CHAT_ID}
        assert len(albums[PRODUCTS_CHAT_ID]) == 2
        assert len(albums[PRINT_CHAT_ID]) == 2
        # No file goes out individually when an album fits
        bot.send_photo.assert_not_awaited()
        bot.send_document.assert_not_awaited()
        assert record.call_count == 4
        assert 'تایید شد' in query.edit_message_text.await_args.kwargs['text']
