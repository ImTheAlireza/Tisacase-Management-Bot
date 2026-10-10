"""
Tests for the per-editor option "تشخیص خودکار موکاپ و چاپی".

Every editor can turn it on/off for themselves. When it is ON their session
skips the two upload stages: a plain photo is stored as a mockup, anything
sent as a document is stored as a print file, and the only action button is
"✅ اتمام ارسال".
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
from unittest.mock import patch, MagicMock, AsyncMock

from utils.enums import EditorStage, DesignStatus
from ui.keyboards import Keyboards
from models.user import User
from handlers.editor import (
    handle_files,
    start_new_design,
    editor_callbacks,
    _handle_auto_done,
    auto_detect_command,
    auto_detect_callback,
)


@pytest.fixture(autouse=True)
def no_rate_limit(monkeypatch):
    """Keep the shared rate limiter out of the way of these tests."""
    monkeypatch.setattr(
        'utils.decorators.rate_limiter.check_rate_limit',
        lambda user_id, action: (True, 0.0)
    )


def _callbacks(markup) -> set:
    """All callback_data values inside an inline keyboard."""
    return {
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    }


def _buttons(markup) -> list:
    """All reply-keyboard button labels of a ReplyKeyboardMarkup."""
    return [btn.text for row in markup.keyboard for btn in row]


def _make_context(user_data: dict) -> MagicMock:
    context = MagicMock()
    context.user_data = user_data
    context.bot.edit_message_text = AsyncMock()
    context.bot.send_message = AsyncMock(return_value=MagicMock(message_id=7))
    context.job_queue.run_once = MagicMock(return_value=MagicMock())
    return context


def _make_query(data: str, user_id: int = 1001) -> MagicMock:
    query = MagicMock()
    query.data = data
    query.from_user.id = user_id
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    return query


def _editor_user(auto_detect: bool = False) -> MagicMock:
    """An active editor whose personal auto-detect flag is `auto_detect`."""
    user = MagicMock()
    user.user_id = 1001
    user.is_active = True
    user.is_sudo = False
    user.role = 'editor'
    user.get_effective_role.return_value = 'editor'
    user.auto_detect_files = auto_detect
    # Keep the in-memory flag in sync like the real model does
    user.set_auto_detect_enabled = MagicMock(
        side_effect=lambda enabled: setattr(user, 'auto_detect_files', bool(enabled))
    )
    return user


def _pending_design(editor_user_id: int = 1001) -> MagicMock:
    design = MagicMock()
    design.status = DesignStatus.PENDING
    design.editor_user_id = editor_user_id
    return design


# ---------------------------------------------------------------------------
# Keyboards
# ---------------------------------------------------------------------------

class TestAutoStageKeyboard:

    def test_auto_stage_has_single_finish_button(self):
        _, markup = Keyboards.get_auto_stage("TS001", "قاب موبایل", 2, 1)

        cbs = _callbacks(markup)
        assert "stage_auto_done" in cbs
        assert "stage_auto_clear" in cbs
        # The per-type finish buttons must not exist in this screen
        assert "stage_mockup_done" not in cbs
        assert "stage_print_done" not in cbs

    def test_auto_stage_finish_button_label(self):
        _, markup = Keyboards.get_auto_stage("TS001", "قاب موبایل", 0, 0)
        assert markup.inline_keyboard[0][0].text == "✅ اتمام ارسال"

    def test_auto_stage_edit_mode_uses_cancel_editing(self):
        _, markup = Keyboards.get_auto_stage("TS001", "قاب موبایل", 0, 0, is_edit=True)
        assert markup.inline_keyboard[-1][0].callback_data == "cancel_editing"

    def test_classic_stage_keyboards_unchanged(self):
        """The two-step flow keeps its own buttons when the option is off."""
        _, mockup_markup = Keyboards.get_mockup_stage("TS001", "قاب موبایل", 1)
        _, print_markup = Keyboards.get_print_stage("TS001", "قاب موبایل", 1, 1)

        assert "stage_mockup_done" in _callbacks(mockup_markup)
        assert "stage_print_done" in _callbacks(print_markup)
        assert "stage_auto_done" not in _callbacks(mockup_markup)
        assert "stage_auto_done" not in _callbacks(print_markup)

    def test_workspace_offers_auto_upload_when_enabled(self):
        _, markup = Keyboards.get_workspace_stage(
            "TS001", "قاب موبایل", 1, 1, auto_detect=True
        )
        cbs = _callbacks(markup)
        assert "stage_goto_auto" in cbs
        assert "stage_goto_mockup" not in cbs
        assert "stage_goto_print" not in cbs
        # Management + final submit buttons stay available
        assert "manage_mockups" in cbs
        assert "manage_prints" in cbs
        assert "confirm_submit" in cbs

    def test_workspace_unchanged_when_disabled(self):
        _, markup = Keyboards.get_workspace_stage("TS001", "قاب موبایل", 1, 1)
        cbs = _callbacks(markup)
        assert "stage_goto_mockup" in cbs
        assert "stage_goto_print" in cbs
        assert "stage_goto_auto" not in cbs

    def test_clear_confirmation_for_auto_stage(self):
        _, markup = Keyboards.get_clear_confirmation("auto")
        assert _callbacks(markup) == {"clear_confirmed_auto", "clear_cancelled_auto"}


class TestMainMenuButton:

    def test_editors_get_the_option_button(self):
        user = MagicMock()
        user.is_sudo = False
        user.get_effective_role.return_value = 'editor'

        with patch('ui.keyboards.ProductLine.get_all_active', return_value=[]), \
             patch('ui.keyboards.User.is_privileged_user', return_value=False):
            keyboard = Keyboards.get_main_menu(user)

        assert "🤖 تشخیص خودکار" in _buttons(keyboard)

    def test_reviewers_do_not_get_the_option_button(self):
        user = MagicMock()
        user.is_sudo = False
        user.get_effective_role.return_value = 'reviewer'

        with patch('ui.keyboards.ProductLine.get_all_active', return_value=[]), \
             patch('ui.keyboards.User.is_privileged_user', return_value=False):
            keyboard = Keyboards.get_main_menu(user)

        assert "🤖 تشخیص خودکار" not in _buttons(keyboard)

    def test_sudo_panel_no_longer_has_the_option(self):
        """The global sudo switch was replaced by the per-editor option."""
        user = MagicMock()
        user.is_sudo = True
        user.get_effective_role.return_value = 'sudo'

        with patch('ui.keyboards.ProductLine.get_all_active', return_value=[]), \
             patch('ui.keyboards.User.is_privileged_user', return_value=True):
            keyboard = Keyboards.get_main_menu(user)

        assert "🤖 تشخیص خودکار" not in _buttons(keyboard)

    def test_sudo_handler_is_gone(self):
        import handlers.sudo as sudo_handlers

        assert not hasattr(sudo_handlers, 'auto_detect_command')
        assert not hasattr(sudo_handlers, 'auto_detect_callback')


# ---------------------------------------------------------------------------
# File routing
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestHandleFilesAutoDetect:

    def _update(self, *, photo=None, document=None) -> MagicMock:
        update = MagicMock()
        update.effective_user.id = 1001
        update.effective_chat.id = 1001
        update.message.photo = photo
        update.message.document = document
        update.message.reply_text = AsyncMock()
        return update

    def _session(self, **overrides) -> dict:
        session = {
            'code': 'TS001',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        }
        session.update(overrides)
        return session

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_photo_goes_to_mockups(self, mock_user, mock_design):
        mock_user.return_value = _editor_user(auto_detect=True)
        mock_design.return_value = _pending_design()

        context = _make_context(self._session())
        update = self._update(photo=[MagicMock(file_id='photo_1')])

        await handle_files(update, context)

        assert context.user_data['mockup_files'] == ['photo_1']
        assert context.user_data['print_files'] == []
        assert context.user_data['file_types']['photo_1'] == 'photo'
        update.message.reply_text.assert_awaited_with("✅ موکاپ 1 دریافت شد.")

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_document_goes_to_prints(self, mock_user, mock_design):
        mock_user.return_value = _editor_user(auto_detect=True)
        mock_design.return_value = _pending_design()

        context = _make_context(self._session())
        update = self._update(document=MagicMock(file_id='doc_1'))

        await handle_files(update, context)

        assert context.user_data['print_files'] == ['doc_1']
        assert context.user_data['mockup_files'] == []
        assert context.user_data['file_types']['doc_1'] == 'document'
        update.message.reply_text.assert_awaited_with("✅ فایل چاپی 1 دریافت شد.")

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_files_routed_by_type_even_in_legacy_mockup_stage(
        self, mock_user, mock_design
    ):
        """A session started before the toggle still auto-sorts."""
        mock_user.return_value = _editor_user(auto_detect=True)
        mock_design.return_value = _pending_design()

        context = _make_context(self._session(stage=EditorStage.MOCKUP))
        await handle_files(self._update(document=MagicMock(file_id='doc_9')), context)
        assert context.user_data['print_files'] == ['doc_9']

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_classic_mockup_stage_still_takes_documents(
        self, mock_user, mock_design
    ):
        """With the option OFF nothing changes: mockup stage keeps everything."""
        mock_user.return_value = _editor_user(auto_detect=False)
        mock_design.return_value = _pending_design()

        context = _make_context(self._session(
            stage=EditorStage.MOCKUP, auto_detect=False
        ))
        await handle_files(self._update(document=MagicMock(file_id='doc_2')), context)

        assert context.user_data['mockup_files'] == ['doc_2']
        assert context.user_data['print_files'] == []

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_confirm_stage_still_ignores_files(self, mock_user, mock_design):
        mock_user.return_value = _editor_user(auto_detect=True)
        mock_design.return_value = _pending_design()

        context = _make_context(self._session(stage=EditorStage.CONFIRM))
        await handle_files(self._update(photo=[MagicMock(file_id='p')]), context)

        assert context.user_data['mockup_files'] == []
        assert context.user_data['print_files'] == []


# ---------------------------------------------------------------------------
# Session start
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestStartNewDesign:

    def _update(self) -> MagicMock:
        update = MagicMock()
        update.effective_user.id = 1001
        update.effective_chat.id = 1001
        update.message.reply_text = AsyncMock(
            return_value=MagicMock(message_id=9)
        )
        return update

    def _patched_session(self, user):
        product_line = MagicMock()
        product_line.id = 1
        product_line.name_fa = 'قاب موبایل'
        product_line.is_fully_configured.return_value = True

        design = MagicMock()
        design.product_line_id = 1

        return [
            patch('handlers.editor.User.get_by_id', return_value=user),
            patch('handlers.editor.ProductLine.get_by_prefix', return_value=product_line),
            patch('handlers.editor.ProductLine.get_by_id', return_value=product_line),
            patch('handlers.editor.CodeService.generate_code',
                  return_value=('TS001', design)),
        ]

    async def _start(self, user):
        context = _make_context({})
        update = self._update()
        patches = self._patched_session(user)
        for p in patches:
            p.start()
        try:
            await start_new_design(update, context, 'TS')
        finally:
            for p in patches:
                p.stop()
        return context, update

    async def test_enters_auto_stage_when_editor_enabled_it(self):
        context, update = await self._start(_editor_user(auto_detect=True))

        assert context.user_data['stage'] == EditorStage.AUTO
        assert context.user_data['auto_detect'] is True

        cbs = _callbacks(update.message.reply_text.await_args.kwargs['reply_markup'])
        assert "stage_auto_done" in cbs
        assert "stage_mockup_done" not in cbs
        assert "stage_print_done" not in cbs

    async def test_enters_mockup_stage_when_editor_disabled_it(self):
        context, update = await self._start(_editor_user(auto_detect=False))

        assert context.user_data['stage'] == EditorStage.MOCKUP
        assert context.user_data['auto_detect'] is False

        cbs = _callbacks(update.message.reply_text.await_args.kwargs['reply_markup'])
        assert cbs == {"stage_mockup_done", "stage_mockup_clear", "cancel_submission"}

    @patch('handlers.editor.User.get_by_id')
    async def test_other_editors_are_not_affected(self, mock_user):
        """The flag is read from the user who started the design."""
        other = _editor_user(auto_detect=False)
        other.user_id = 2002
        mock_user.return_value = other

        context = _make_context({})
        update = self._update()
        patches = self._patched_session(other)
        for p in patches:
            p.start()
        try:
            await start_new_design(update, context, 'TS')
        finally:
            for p in patches:
                p.stop()

        assert context.user_data['auto_detect'] is False


@pytest.mark.asyncio
class TestLoadDesignForEdit:

    @patch('handlers.editor.User.get_by_id')
    async def test_edit_session_snapshots_the_editor_flag(self, mock_user):
        from handlers.editor import load_design_for_edit

        mock_user.return_value = _editor_user(auto_detect=True)

        design = MagicMock()
        design.product_line_id = 1
        design.code = 'TS001'
        design.mockup_file_ids = ['m1']
        design.print_file_ids = ['p1']
        design.file_types = {}
        design.can_be_edited_by.return_value = True

        update = MagicMock()
        update.callback_query.from_user.id = 1001
        update.callback_query.message.reply_text = AsyncMock(
            return_value=MagicMock(message_id=11)
        )
        update.callback_query.edit_message_text = AsyncMock()
        context = _make_context({})

        with patch('handlers.editor.ProductLine.get_by_id') as mock_pl:
            mock_pl.return_value = MagicMock(id=1, name_fa='قاب موبایل')
            await load_design_for_edit(update, context, design)

        assert context.user_data['auto_detect'] is True
        assert context.user_data['stage'] == EditorStage.WORKSPACE

        markup = update.callback_query.message.reply_text.await_args.kwargs['reply_markup']
        assert "stage_goto_auto" in _callbacks(markup)


# ---------------------------------------------------------------------------
# Finish button
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestAutoDone:

    async def test_moves_to_confirm_when_both_lists_filled(self):
        context = _make_context({
            'code': 'TS001',
            'product_name': 'قاب موبایل',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': ['photo_1'],
            'print_files': ['doc_1'],
            'workspace_message_id': 5,
        })
        query = _make_query("stage_auto_done")

        await _handle_auto_done(query, context, 1001)

        assert context.user_data['stage'] == EditorStage.CONFIRM

    async def test_blocked_without_mockup(self):
        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': ['doc_1'],
            'workspace_message_id': 5,
        })
        query = _make_query("stage_auto_done")

        await _handle_auto_done(query, context, 1001)

        assert context.user_data['stage'] == EditorStage.AUTO
        assert "موکاپ" in query.answer.await_args.kwargs['text']

    async def test_blocked_without_print_file(self):
        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': ['photo_1'],
            'print_files': [],
            'workspace_message_id': 5,
        })
        query = _make_query("stage_auto_done")

        await _handle_auto_done(query, context, 1001)

        assert context.user_data['stage'] == EditorStage.AUTO
        assert "چاپی" in query.answer.await_args.kwargs['text']


# ---------------------------------------------------------------------------
# Callback dispatcher (the wiring registered in main.py)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestEditorCallbackDispatch:

    def _update(self, data: str) -> MagicMock:
        update = MagicMock()
        update.callback_query = _make_query(data)
        update.message = None
        return update

    def _session(self, **overrides) -> dict:
        session = {
            'code': 'TS001',
            'product_name': 'قاب موبایل',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': ['photo_1'],
            'print_files': ['doc_1'],
            'workspace_message_id': 5,
            'editing_existing': False,
        }
        session.update(overrides)
        return session

    @patch('handlers.editor.User.get_by_id')
    async def test_auto_done_reaches_confirm_stage(self, mock_user):
        mock_user.return_value = _editor_user(auto_detect=True)
        context = _make_context(self._session())

        await editor_callbacks(self._update("stage_auto_done"), context)

        assert context.user_data['stage'] == EditorStage.CONFIRM
        # The confirm screen is rendered on the workspace message
        context.bot.edit_message_text.assert_awaited()

    @patch('handlers.editor.User.get_by_id')
    async def test_auto_clear_asks_confirmation_then_empties_both_lists(
        self, mock_user
    ):
        mock_user.return_value = _editor_user(auto_detect=True)
        context = _make_context(self._session())

        update = self._update("stage_auto_clear")
        await editor_callbacks(update, context)
        markup = update.callback_query.edit_message_text.await_args.kwargs['reply_markup']
        assert "clear_confirmed_auto" in _callbacks(markup)

        await editor_callbacks(self._update("clear_confirmed_auto"), context)

        assert context.user_data['mockup_files'] == []
        assert context.user_data['print_files'] == []
        assert context.user_data['stage'] == EditorStage.AUTO

    @patch('handlers.editor.User.get_by_id')
    async def test_workspace_add_file_button_opens_auto_stage(self, mock_user):
        mock_user.return_value = _editor_user(auto_detect=True)
        context = _make_context(self._session(stage=EditorStage.WORKSPACE))

        await editor_callbacks(self._update("stage_goto_auto"), context)

        assert context.user_data['stage'] == EditorStage.AUTO

    @patch('handlers.editor.User.get_by_id')
    async def test_legacy_add_buttons_reuse_auto_stage(self, mock_user):
        """Stale keyboards from an auto session must not open a per-type stage."""
        mock_user.return_value = _editor_user(auto_detect=True)
        context = _make_context(self._session(stage=EditorStage.WORKSPACE))

        await editor_callbacks(self._update("stage_goto_mockup"), context)
        assert context.user_data['stage'] == EditorStage.AUTO

        await editor_callbacks(self._update("stage_goto_print"), context)
        assert context.user_data['stage'] == EditorStage.AUTO

    @patch('handlers.editor.User.get_by_id')
    async def test_classic_done_buttons_still_work(self, mock_user):
        mock_user.return_value = _editor_user(auto_detect=False)
        context = _make_context(self._session(
            stage=EditorStage.MOCKUP, auto_detect=False
        ))

        await editor_callbacks(self._update("stage_mockup_done"), context)
        assert context.user_data['stage'] == EditorStage.PRINT

        await editor_callbacks(self._update("stage_print_done"), context)
        assert context.user_data['stage'] == EditorStage.CONFIRM


# ---------------------------------------------------------------------------
# Per-user storage
# ---------------------------------------------------------------------------

class TestUserAutoDetectFlag:

    def test_default_is_off(self):
        assert User(user_id=1001).auto_detect_files is False

    def test_reads_flag_from_row(self):
        assert User(user_id=1001, auto_detect_files=1).auto_detect_files is True
        assert User(user_id=1001, auto_detect_files=0).auto_detect_files is False

    def test_setter_writes_only_that_user(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value = cursor

        user = User(user_id=1001)
        with patch('models.user.get_db_connection', return_value=conn):
            user.set_auto_detect_enabled(True)

        sql, params = cursor.execute.call_args.args
        assert "UPDATE users SET auto_detect_files" in sql
        assert params == (True, 1001)
        assert conn.commit.call_count == 1
        assert user.auto_detect_files is True

    def test_setter_rolls_back_on_error(self):
        conn = MagicMock()
        cursor = MagicMock()
        cursor.execute.side_effect = RuntimeError('db down')
        conn.cursor.return_value = cursor

        user = User(user_id=1001)
        with patch('models.user.get_db_connection', return_value=conn):
            with pytest.raises(RuntimeError):
                user.set_auto_detect_enabled(True)

        conn.rollback.assert_called_once()
        assert user.auto_detect_files is False


# ---------------------------------------------------------------------------
# Personal toggle panel
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestPersonalToggle:

    def _update(self, data: str) -> MagicMock:
        update = MagicMock()
        update.callback_query = _make_query(data)
        update.message = None
        return update

    @patch('handlers.editor.User.get_by_id')
    async def test_editor_turns_it_on_for_himself(self, mock_user):
        user = _editor_user(auto_detect=False)
        mock_user.return_value = user

        update = self._update("autodetect_on")
        context = _make_context({})

        await auto_detect_callback(update, context)

        user.set_auto_detect_enabled.assert_called_once_with(True)
        # The panel now offers to turn it off again
        markup = update.callback_query.edit_message_text.await_args.kwargs['reply_markup']
        assert _callbacks(markup) == {"autodetect_off"}
        assert "🟢 روشن" in update.callback_query.edit_message_text.await_args.args[0]

    @patch('handlers.editor.User.get_by_id')
    async def test_editor_turns_it_off(self, mock_user):
        user = _editor_user(auto_detect=True)
        mock_user.return_value = user

        update = self._update("autodetect_off")
        context = _make_context({})

        await auto_detect_callback(update, context)

        user.set_auto_detect_enabled.assert_called_once_with(False)
        assert user.auto_detect_files is False

    @patch('handlers.editor.User.get_by_id')
    async def test_status_panel_shows_toggle_button(self, mock_user):
        mock_user.return_value = _editor_user(auto_detect=True)

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = _make_context({})

        await auto_detect_command(update, context)

        markup = update.message.reply_text.await_args.kwargs['reply_markup']
        assert _callbacks(markup) == {"autodetect_off"}

    @patch('handlers.editor.User.get_by_id')
    async def test_non_editor_cannot_toggle(self, mock_user):
        reviewer = MagicMock()
        reviewer.is_active = True
        reviewer.is_sudo = False
        reviewer.get_effective_role.return_value = 'reviewer'
        mock_user.return_value = reviewer

        update = self._update("autodetect_on")
        context = _make_context({})

        await auto_detect_callback(update, context)

        update.callback_query.edit_message_text.assert_not_awaited()

    @patch('handlers.editor.User.get_by_id')
    async def test_save_failure_is_reported(self, mock_user):
        user = _editor_user(auto_detect=False)
        user.set_auto_detect_enabled = MagicMock(side_effect=RuntimeError('db down'))
        mock_user.return_value = user

        update = self._update("autodetect_on")
        context = _make_context({})

        await auto_detect_callback(update, context)

        # The last answer is the error toast
        assert "ناموفق" in update.callback_query.answer.await_args.kwargs['text']


# ---------------------------------------------------------------------------
# Migration wiring
# ---------------------------------------------------------------------------

class TestAutoDetectMigration:

    def test_up_adds_users_column(self):
        from migrations.migration_010_add_auto_detect_to_users import Migration010

        cursor = MagicMock()
        Migration010.up(cursor)

        sql = cursor.execute.call_args.args[0]
        assert "ALTER TABLE users" in sql
        assert "auto_detect_files BOOLEAN NOT NULL DEFAULT FALSE" in sql

    def test_up_is_idempotent_on_duplicate_column(self):
        from migrations.migration_010_add_auto_detect_to_users import Migration010

        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Duplicate column name 'auto_detect_files'")
        Migration010.up(cursor)  # must not raise

    def test_down_drops_column(self):
        from migrations.migration_010_add_auto_detect_to_users import Migration010

        cursor = MagicMock()
        Migration010.down(cursor)
        sql = cursor.execute.call_args.args[0]
        assert "DROP COLUMN IF EXISTS auto_detect_files" in sql

    def test_registered_in_migration_list(self):
        import main
        from migrations.migration_010_add_auto_detect_to_users import Migration010

        assert Migration010.name == "010_add_auto_detect_to_users"

        with patch.object(main, 'init_legacy_tables'), \
             patch.object(main, 'CodeService'), \
             patch.object(main, 'MigrationManager') as manager_cls:
            main.run_db_migrations()

        applied = manager_cls.return_value.run_migrations.call_args.args[0]
        assert any(isinstance(m, Migration010) for m in applied)
        assert isinstance(applied[-1], Migration010)
