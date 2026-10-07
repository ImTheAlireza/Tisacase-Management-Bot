"""
Tests for the sudo toggle "تشخیص خودکار موکاپ و چاپی".

When the option is ON the editor sees a single upload screen:
a plain photo is stored as a mockup, anything sent as a document is stored
as a print file, and the only action button is "✅ اتمام ارسال".
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
from models.bot_settings import BotSettings
from handlers.editor import handle_files, start_new_design, _handle_auto_done
from handlers.sudo import auto_detect_callback


def _callbacks(markup) -> set:
    """All callback_data values inside an inline keyboard."""
    return {
        btn.callback_data
        for row in markup.inline_keyboard
        for btn in row
        if btn.callback_data
    }


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


def _sudo_user() -> MagicMock:
    user = MagicMock()
    user.is_active = True
    user.is_sudo = True
    user.get_effective_role.return_value = 'editor'
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
        """The two-step flow keeps its own buttons when auto detect is off."""
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
        cbs = _callbacks(markup)
        assert cbs == {"clear_confirmed_auto", "clear_cancelled_auto"}


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

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_photo_goes_to_mockups(self, mock_user, mock_design):
        mock_user.return_value = _sudo_user()
        mock_design.return_value = _pending_design()

        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        })
        update = self._update(photo=[MagicMock(file_id='photo_1')])

        await handle_files(update, context)

        assert context.user_data['mockup_files'] == ['photo_1']
        assert context.user_data['print_files'] == []
        assert context.user_data['file_types']['photo_1'] == 'photo'
        update.message.reply_text.assert_awaited_with("✅ موکاپ 1 دریافت شد.")

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_document_goes_to_prints(self, mock_user, mock_design):
        mock_user.return_value = _sudo_user()
        mock_design.return_value = _pending_design()

        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.AUTO,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        })
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
        """A session that started before the toggle still auto-sorts."""
        mock_user.return_value = _sudo_user()
        mock_design.return_value = _pending_design()

        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.MOCKUP,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        })
        await handle_files(self._update(document=MagicMock(file_id='doc_9')), context)
        assert context.user_data['print_files'] == ['doc_9']

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_classic_mockup_stage_still_takes_documents(
        self, mock_user, mock_design
    ):
        """With the option OFF nothing changes: mockup stage keeps everything."""
        mock_user.return_value = _sudo_user()
        mock_design.return_value = _pending_design()

        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.MOCKUP,
            'auto_detect': False,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        })
        await handle_files(self._update(document=MagicMock(file_id='doc_2')), context)

        assert context.user_data['mockup_files'] == ['doc_2']
        assert context.user_data['print_files'] == []

    @patch('handlers.editor.Design.get_by_code')
    @patch('handlers.editor.User.get_by_id')
    async def test_confirm_stage_still_ignores_files(self, mock_user, mock_design):
        mock_user.return_value = _sudo_user()
        mock_design.return_value = _pending_design()

        context = _make_context({
            'code': 'TS001',
            'stage': EditorStage.CONFIRM,
            'auto_detect': True,
            'mockup_files': [],
            'print_files': [],
            'workspace_message_id': 5,
        })
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

    def _patched_session(self, auto_enabled: bool):
        product_line = MagicMock()
        product_line.id = 1
        product_line.name_fa = 'قاب موبایل'
        product_line.is_fully_configured.return_value = True

        design = MagicMock()
        design.product_line_id = 1

        return [
            patch('handlers.editor.User.get_by_id', return_value=_sudo_user()),
            patch('handlers.editor.BotSettings.is_auto_detect_enabled',
                  return_value=auto_enabled),
            patch('handlers.editor.ProductLine.get_by_prefix', return_value=product_line),
            patch('handlers.editor.ProductLine.get_by_id', return_value=product_line),
            patch('handlers.editor.CodeService.generate_code',
                  return_value=('TS001', design)),
        ]

    async def test_enters_auto_stage_when_enabled(self):
        context = _make_context({})
        update = self._update()

        patches = self._patched_session(True)
        for p in patches:
            p.start()
        try:
            await start_new_design(update, context, 'TS')
        finally:
            for p in patches:
                p.stop()

        assert context.user_data['stage'] == EditorStage.AUTO
        assert context.user_data['auto_detect'] is True

        sent_markup = update.message.reply_text.await_args.kwargs['reply_markup']
        cbs = _callbacks(sent_markup)
        assert "stage_auto_done" in cbs
        assert "stage_mockup_done" not in cbs
        assert "stage_print_done" not in cbs

    async def test_enters_mockup_stage_when_disabled(self):
        context = _make_context({})
        update = self._update()

        patches = self._patched_session(False)
        for p in patches:
            p.start()
        try:
            await start_new_design(update, context, 'TS')
        finally:
            for p in patches:
                p.stop()

        assert context.user_data['stage'] == EditorStage.MOCKUP
        assert context.user_data['auto_detect'] is False

        sent_markup = update.message.reply_text.await_args.kwargs['reply_markup']
        assert _callbacks(sent_markup) == {
            "stage_mockup_done", "stage_mockup_clear", "cancel_submission"
        }


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
# Setting storage
# ---------------------------------------------------------------------------

class TestBotSettingsStore:

    def _db(self, stored_value):
        cursor = MagicMock()
        cursor.fetchone.return_value = (
            None if stored_value is None else (stored_value,)
        )
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def test_reads_enabled_flag(self):
        conn, _ = self._db('1')
        with patch('models.bot_settings.get_db_connection', return_value=conn):
            assert BotSettings.is_auto_detect_enabled() is True

    def test_reads_disabled_flag(self):
        conn, _ = self._db('0')
        with patch('models.bot_settings.get_db_connection', return_value=conn):
            assert BotSettings.is_auto_detect_enabled() is False

    def test_defaults_to_off_when_never_set(self):
        conn, _ = self._db(None)
        with patch('models.bot_settings.get_db_connection', return_value=conn):
            assert BotSettings.is_auto_detect_enabled() is False

    def test_defaults_to_off_on_db_error(self):
        with patch('models.bot_settings.get_db_connection',
                   side_effect=RuntimeError('db down')):
            assert BotSettings.is_auto_detect_enabled() is False

    def test_set_writes_flag(self):
        conn, cursor = self._db(None)
        with patch('models.bot_settings.get_db_connection', return_value=conn):
            BotSettings.set_auto_detect_enabled(True)
            BotSettings.set_auto_detect_enabled(False)

        params = [call.args[1] for call in cursor.execute.call_args_list]
        assert params == [
            ('auto_detect_files', '1', '1'),
            ('auto_detect_files', '0', '0'),
        ]
        assert conn.commit.call_count == 2


# ---------------------------------------------------------------------------
# Sudo toggle
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestSudoToggle:

    def _update(self, data: str) -> MagicMock:
        update = MagicMock()
        update.callback_query = _make_query(data)
        update.message = None
        return update

    @patch('models.bot_settings.BotSettings.set_auto_detect_enabled')
    @patch('handlers.sudo.User.get_by_id')
    async def test_turns_option_on(self, mock_user, mock_set):
        mock_user.return_value = _sudo_user()
        update = self._update("autodetect_on")
        context = _make_context({})

        await auto_detect_callback(update, context)

        mock_set.assert_called_once_with(True)
        update.callback_query.edit_message_text.assert_awaited_once()

    @patch('models.bot_settings.BotSettings.set_auto_detect_enabled')
    @patch('handlers.sudo.User.get_by_id')
    async def test_turns_option_off(self, mock_user, mock_set):
        mock_user.return_value = _sudo_user()
        update = self._update("autodetect_off")
        context = _make_context({})

        await auto_detect_callback(update, context)

        mock_set.assert_called_once_with(False)

    @patch('models.bot_settings.BotSettings.set_auto_detect_enabled')
    @patch('handlers.sudo.User.get_by_id')
    async def test_non_sudo_is_rejected(self, mock_user, mock_set):
        user = MagicMock()
        user.is_active = True
        user.is_sudo = False
        mock_user.return_value = user

        update = self._update("autodetect_on")
        context = _make_context({})

        await auto_detect_callback(update, context)

        mock_set.assert_not_called()

    @patch('models.bot_settings.BotSettings.is_auto_detect_enabled')
    @patch('handlers.sudo.User.get_by_id')
    async def test_status_panel_shows_toggle_button(self, mock_user, mock_enabled):
        from handlers.sudo import auto_detect_command

        mock_user.return_value = _sudo_user()
        mock_enabled.return_value = True

        update = MagicMock()
        update.message.reply_text = AsyncMock()
        context = _make_context({})

        await auto_detect_command(update, context)

        markup = update.message.reply_text.await_args.kwargs['reply_markup']
        assert _callbacks(markup) == {"autodetect_off"}


# ---------------------------------------------------------------------------
# Migration wiring
# ---------------------------------------------------------------------------

class TestBotSettingsMigration:

    def test_up_creates_bot_settings_table(self):
        from migrations.migration_010_add_bot_settings import Migration010

        cursor = MagicMock()
        Migration010.up(cursor)

        sql = cursor.execute.call_args.args[0]
        assert "CREATE TABLE IF NOT EXISTS bot_settings" in sql
        assert "setting_key" in sql and "setting_value" in sql

    def test_down_drops_table(self):
        from migrations.migration_010_add_bot_settings import Migration010

        cursor = MagicMock()
        Migration010.down(cursor)
        assert "DROP TABLE IF EXISTS bot_settings" in cursor.execute.call_args.args[0]

    def test_registered_in_migration_list(self):
        import main
        from migrations.migration_010_add_bot_settings import Migration010

        assert Migration010.name == "010_add_bot_settings"

        with patch.object(main, 'init_legacy_tables'), \
             patch.object(main, 'CodeService'), \
             patch.object(main, 'MigrationManager') as manager_cls:
            main.run_db_migrations()

        applied = manager_cls.return_value.run_migrations.call_args.args[0]
        assert any(isinstance(m, Migration010) for m in applied)
        assert isinstance(applied[-1], Migration010)


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
        from handlers.editor import editor_callbacks

        mock_user.return_value = _sudo_user()
        context = _make_context(self._session())

        await editor_callbacks(self._update("stage_auto_done"), context)

        assert context.user_data['stage'] == EditorStage.CONFIRM
        # The confirm screen is rendered on the workspace message
        context.bot.edit_message_text.assert_awaited()

    @patch('handlers.editor.User.get_by_id')
    async def test_auto_clear_asks_confirmation_then_empties_both_lists(
        self, mock_user
    ):
        from handlers.editor import editor_callbacks

        mock_user.return_value = _sudo_user()
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
        from handlers.editor import editor_callbacks

        mock_user.return_value = _sudo_user()
        context = _make_context(self._session(stage=EditorStage.WORKSPACE))

        await editor_callbacks(self._update("stage_goto_auto"), context)

        assert context.user_data['stage'] == EditorStage.AUTO

    @patch('handlers.editor.User.get_by_id')
    async def test_legacy_add_buttons_reuse_auto_stage(self, mock_user):
        """Stale keyboards from an auto session must not open a per-type stage."""
        from handlers.editor import editor_callbacks

        mock_user.return_value = _sudo_user()
        context = _make_context(self._session(stage=EditorStage.WORKSPACE))

        await editor_callbacks(self._update("stage_goto_mockup"), context)
        assert context.user_data['stage'] == EditorStage.AUTO

        await editor_callbacks(self._update("stage_goto_print"), context)
        assert context.user_data['stage'] == EditorStage.AUTO

    @patch('handlers.editor.User.get_by_id')
    async def test_classic_done_buttons_still_work(self, mock_user):
        from handlers.editor import editor_callbacks

        mock_user.return_value = _sudo_user()
        context = _make_context(self._session(
            stage=EditorStage.MOCKUP, auto_detect=False
        ))

        await editor_callbacks(self._update("stage_mockup_done"), context)
        assert context.user_data['stage'] == EditorStage.PRINT

        await editor_callbacks(self._update("stage_print_done"), context)
        assert context.user_data['stage'] == EditorStage.CONFIRM
