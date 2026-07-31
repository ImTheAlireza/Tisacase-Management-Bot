import pytest
import json
from unittest.mock import patch, MagicMock, AsyncMock
from models.design import Design
from utils.enums import DesignStatus
from handlers.editor import _notify_reviewers_of_edit
from handlers.design_management import pending_view_callback


def _make_raw_row(**overrides):
    base = {
        'id': 1,
        'code': 'TS001',
        'product_line_id': 1,
        'status': 'pending',
        'editor_user_id': 100,
        'editor_name': 'Ali',
        'reviewer_user_id': None,
        'reviewer_name': None,
        'mockup_file_ids': json.dumps(['mockup1']),
        'print_file_ids': json.dumps(['print1', 'print2']),
        'mockup_message_ids_reviewer': json.dumps({'2001': [10, 11]}),
        'created_at': None,
        'reviewed_at': None,
        'final_name': None,
        'metadata': None,
        'product_name': None,
        'product_icon': None,
    }
    base.update(overrides)
    return base


def _make_design(**overrides) -> Design:
    row = _make_raw_row(**overrides)
    return Design(**Design._parse_row(row))


@pytest.mark.asyncio
class TestNotifyReviewersOfEdit:

    @patch('handlers.editor.ProductLine.get_by_id')
    async def test_does_not_send_print_files_to_reviewer(self, mock_get_pl):
        mock_pl = MagicMock()
        mock_pl.icon = '📱'
        mock_pl.name_fa = 'قاب موبایل'
        mock_get_pl.return_value = mock_pl

        design = _make_design()
        design.file_types = {'mockup1': 'photo'}
        design.set_reviewer_messages = MagicMock()
        design.save_reviewer_messages = MagicMock()

        bot = MagicMock()
        bot.delete_message = AsyncMock()
        bot.send_photo = AsyncMock(return_value=MagicMock(message_id=101))
        bot.send_document = AsyncMock(return_value=MagicMock(message_id=102))
        bot.send_message = AsyncMock(return_value=MagicMock(message_id=103))

        await _notify_reviewers_of_edit(bot, design)

        # Ensure old messages were deleted
        assert bot.delete_message.call_count == 2
        # Ensure mockup photo was sent
        bot.send_photo.assert_called_once()
        # Ensure print files were NEVER sent as documents
        bot.send_document.assert_not_called()
        # Ensure reviewer message IDs were updated with mockup and button message
        design.set_reviewer_messages.assert_called_once_with(2001, [101])
        design.save_reviewer_messages.assert_called_once()


@pytest.mark.asyncio
class TestPendingViewCallback:

    @patch('handlers.design_management.User.get_by_id')
    @patch('handlers.design_management.Design.get_by_code')
    @patch('handlers.design_management.ProductLine.get_by_id')
    async def test_pending_view_does_not_send_print_files(self, mock_pl, mock_get_design, mock_get_user):
        user = MagicMock()
        user.is_active = True
        user.role = 'reviewer'
        user.is_sudo = False
        mock_get_user.return_value = user

        design = _make_design()
        design.set_reviewer_messages = MagicMock()
        design.save_reviewer_messages = MagicMock()
        mock_get_design.return_value = design

        mock_pl.return_value = MagicMock(icon='📱', name_fa='قاب موبایل')

        update = MagicMock()
        query = MagicMock()
        query.answer = AsyncMock()
        query.from_user.id = 2001
        query.data = "pending_view_TS001"
        update.callback_query = query

        context = MagicMock()
        context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=301))
        context.bot.send_photo = AsyncMock(return_value=MagicMock(message_id=302))
        context.bot.send_document = AsyncMock(return_value=MagicMock(message_id=303))

        await pending_view_callback(update, context)

        # Mockup sent via send_photo
        context.bot.send_photo.assert_called_once()
        # Print file should NOT be sent via send_document
        context.bot.send_document.assert_not_called()
        # Ensure reviewer message IDs were saved
        assert design.set_reviewer_messages.call_count >= 1
