import logging
import os
import shutil
import tempfile
import zipfile
from typing import Optional, Tuple
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaDocument,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from utils.decorators import require_role
from utils.state_manager import StateManager
from utils.enums import DesignStatus, EditorStage
from utils.helpers import safe_edit_message, delete_messages, send_with_retry
from utils.callback_lock import deduplicate_callback
from services.code_service import CodeService
from models.product_line import ProductLine
from models.design import Design
from models.user import User
from ui.keyboards import Keyboards

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INACTIVITY_TIMEOUT = 5 * 60  # 5 minutes in seconds


# ---------------------------------------------------------------------------
# Inactivity Timer
# ---------------------------------------------------------------------------

def _cancel_inactivity_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Cancel existing inactivity job if any."""
    job = context.user_data.get('inactivity_job')
    if job:
        job.schedule_removal()
        context.user_data.pop('inactivity_job', None)


def _reset_inactivity_timer(context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> None:
    """
    Reset the 5-minute inactivity timer.
    Called on every file upload and every button click.
    """
    _cancel_inactivity_job(context)

    job = context.job_queue.run_once(
        _handle_timeout,
        when=INACTIVITY_TIMEOUT,
        data={'chat_id': chat_id},
        name=f"inactivity_{chat_id}"
    )
    context.user_data['inactivity_job'] = job


async def _handle_timeout(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Called when user is inactive for 5 minutes.
    Deletes workspace message, deletes pending design from DB,
    and frees the code.
    """
    chat_id = context.job.data['chat_id']
    app = context.application

    user_data = app.user_data.get(chat_id, {})
    code = user_data.get('code')
    workspace_msg_id = user_data.get('workspace_message_id')
    is_editing = user_data.get('editing_existing', False)

    # ✅ 1. Delete pending design from DB (this frees the code) ONLY if not editing an existing design
    if code and not is_editing:
        try:
            from models.design import Design
            design = Design.get_by_code(code)
            if design and design.status == DesignStatus.PENDING:
                await Design.delete_completely(code, context.bot)
                logging.info(f"Timeout: Design {code} deleted due to inactivity")
        except Exception as e:
            logging.error(f"Timeout cleanup failed for {code}: {e}")

    # ✅ 2. Delete workspace message
    if workspace_msg_id:
        deleted = await delete_messages(context.bot, chat_id, [workspace_msg_id])
        if not deleted:
            logging.warning(f"Could not delete workspace message {workspace_msg_id} on timeout")

    # ✅ 3. Notify user
    try:
        msg_text = (
            " جلسه ویرایش طرح به دلیل عدم فعالیت بسته شد.\n"
            "طرح قبلی در دیتابیس حفظ شد ✅"
        ) if is_editing else (
            " جلسه ثبت طرح به دلیل عدم فعالیت بسته شد.\n"
            "کد آزاد شد ✅\n"
            "در صورت نیاز دوباره ثبت را شروع کنید."
        )
        await context.bot.send_message(
            chat_id=chat_id,
            text=msg_text
        )
    except Exception as e:
        logging.warning(f"Could not send timeout notice to {chat_id}: {e}")

    # ✅ 4. Clear memory state — only editor keys, preserve other user state
    if chat_id in app.user_data:
        StateManager.clear_editor_state(app.user_data[chat_id])

# ---------------------------------------------------------------------------
# State Helpers
# ---------------------------------------------------------------------------

def _get_stage_data(context: ContextTypes.DEFAULT_TYPE) -> dict:
    """Return current editor state."""
    return {
        'code':                 context.user_data.get('code'),
        'product_id':           context.user_data.get('product_id'),
        'product_name':         context.user_data.get('product_name'),
        'stage':                context.user_data.get('stage', EditorStage.MOCKUP),
        'mockup_files':         context.user_data.get('mockup_files', []),
        'print_files':          context.user_data.get('print_files', []),
        'workspace_message_id': context.user_data.get('workspace_message_id'),
        'editing_existing':     context.user_data.get('editing_existing', False),
    }


def _clear_editor_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fully reset editor state.
    NOTE: Does NOT delete the design from database - caller must handle that.
    """
    _cancel_inactivity_job(context)
    StateManager.clear_editor_state(context)

# ---------------------------------------------------------------------------
# Workspace Renderer
# ---------------------------------------------------------------------------

async def _render_stage(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
) -> None:
    """
    Edit the workspace message to reflect current stage.
    Single source of truth for all UI rendering.
    """
    state = _get_stage_data(context)
    code         = state['code']
    product_name = state['product_name']
    mockups      = state['mockup_files']
    prints       = state['print_files']
    stage        = state['stage']
    msg_id       = state['workspace_message_id']
    is_edit      = state['editing_existing']

    # Build text + markup based on stage
    if stage == EditorStage.MOCKUP:
        text, markup = Keyboards.get_mockup_stage(code, product_name, len(mockups), is_edit=is_edit)

    elif stage == EditorStage.PRINT:
        text, markup = Keyboards.get_print_stage(code, product_name, len(mockups), len(prints), is_edit=is_edit)

    elif stage == EditorStage.WORKSPACE:
        text, markup = Keyboards.get_workspace_stage(code, product_name, len(mockups), len(prints), is_edit=is_edit)

    elif stage == EditorStage.CONFIRM:
        text, markup = Keyboards.get_confirm_stage(code, product_name, len(mockups), len(prints), is_edit=is_edit)

    else:
        return

    # Edit workspace message
    if msg_id:
        try:
            await context.bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return
        except Exception as e:
            logging.warning(f"Could not edit workspace message: {e}")

    # Fallback: send new message
    try:
        msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        context.user_data['workspace_message_id'] = msg.message_id
    except Exception as e:
        logging.error(f"Could not send workspace message: {e}")


# ---------------------------------------------------------------------------
# Entry Point — Start New Design
# ---------------------------------------------------------------------------

async def load_design_for_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, design: Design) -> None:
    """
    Load an existing pending design into editor workflow for editing.

    Args:
        update: Telegram update (from callback query)
        context: Bot context
        design: Design object to edit
    """
    user_id = update.callback_query.from_user.id

    # Verify design can be edited
    if not design.can_be_edited_by(user_id):
        await update.callback_query.answer(
            "⚠️ فقط طرح‌های در انتظار را می‌توان ویرایش کرد",
            show_alert=True
        )
        return

    # Cleanup any existing session
    _clear_editor_state(context)

    # Load design into context
    product_line = ProductLine.get_by_id(design.product_line_id)

    context.user_data['code'] = design.code
    context.user_data['product_id'] = product_line.id
    context.user_data['product_name'] = product_line.name_fa
    context.user_data['stage'] = EditorStage.WORKSPACE  # Start in workspace view
    context.user_data['mockup_files'] = design.mockup_file_ids.copy()
    context.user_data['print_files'] = design.print_file_ids.copy()
    context.user_data['file_types'] = design.file_types.copy()
    context.user_data['editing_existing'] = True  # Flag to indicate edit mode

    # Send workspace message
    text, markup = Keyboards.get_workspace_stage(
        design.code,
        product_line.name_fa,
        len(design.mockup_file_ids),
        len(design.print_file_ids),
        is_edit=True
    )

    try:
        msg = await update.callback_query.message.reply_text(
            text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
        context.user_data['workspace_message_id'] = msg.message_id

        # Dismiss the callback
        await update.callback_query.edit_message_text(
            f"✏️ شما در حال ویرایش طرح {design.code} هستید.\n"
            f"فایل‌های فعلی بارگذاری شد."
        )

        # Start inactivity timer
        _reset_inactivity_timer(context, user_id)

        logging.info(f"Design {design.code} loaded for editing by user {user_id}")

    except Exception as e:
        logging.error(f"Failed to load design for edit: {e}")
        await update.callback_query.answer(
            "❌ خطا در بارگذاری طرح",
            show_alert=True
        )


@require_role('editor', 'sudo', rate_limit_action='code_generation')
async def start_new_design(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prefix: str
) -> None:
    """
    Entry point when editor taps a product line button.
    Generates code, enters mockup stage immediately.
    """
    user = context.user_data['db_user']
    user_id = user.user_id

    # ========== CLEANUP EXISTING SESSION ==========
    old_code = context.user_data.get('code')
    was_editing = context.user_data.get('editing_existing', False)
    if old_code:
        # Delete the incomplete design from database ONLY if it wasn't an existing design being edited
        if not was_editing:
            old_design = Design.get_by_code(old_code)
            if old_design and old_design.status == DesignStatus.PENDING:
                try:
                    old_design.delete()
                    logging.info(f"Deleted abandoned design {old_code} for user {user_id}")
                except Exception as e:
                    logging.error(f"Failed to delete abandoned design {old_code}: {e}")

        # Delete workspace message
        old_workspace_msg_id = context.user_data.get('workspace_message_id')
        if old_workspace_msg_id:
            deleted = await delete_messages(context.bot, user_id, [old_workspace_msg_id])
            if deleted:
                logging.info(f"Deleted old workspace message {old_workspace_msg_id} for user {user_id}")
            else:
                logging.warning(f"Could not delete old workspace message {old_workspace_msg_id} for user {user_id}")

        # Clear the old session completely
        _clear_editor_state(context)
    # =============================================

    # Validate product line
    product_line: Optional[ProductLine] = ProductLine.get_by_prefix(prefix)
    if not product_line:
        logging.warning(f"User {user_id} tried to start design with invalid prefix: {prefix}")
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد.")
        return

    if not product_line.is_fully_configured():
        missing: str = ', '.join(product_line.missing_groups())
        logging.warning(
            f"User {user_id} tried to start design for unconfigured line {prefix}. "
            f"Missing: {missing}"
        )
        await update.message.reply_text(
            f"⚠️ گروه‌های این خط تولید تنظیم نشده‌اند:\n{missing}\n\n"
            f"لطفا ابتدا از طریق منوی Sudo اقدام کنید."
        )
        return

    # Generate code
    try:
        code, design = CodeService.generate_code(prefix, user.user_id, user.first_name)
        logging.info(f"✅ Code {code} generated for user {user_id} ({user.first_name})")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")
        return

    product = ProductLine.get_by_id(design.product_line_id)

    # Set initial state — enter mockup stage directly
    context.user_data['code']         = code
    context.user_data['product_id']   = product.id
    context.user_data['product_name'] = product.name_fa
    context.user_data['stage']        = EditorStage.MOCKUP
    context.user_data['mockup_files'] = []
    context.user_data['print_files']  = []
    context.user_data['editing_existing'] = False

    # Send workspace message
    text, markup = Keyboards.get_mockup_stage(code, product.name_fa, 0)
    msg = await update.message.reply_text(
        text,
        reply_markup=markup,
        parse_mode="Markdown"
    )
    context.user_data['workspace_message_id'] = msg.message_id

    # Start inactivity timer
    _reset_inactivity_timer(context, update.effective_chat.id)

# ---------------------------------------------------------------------------
# File Handler — Stage Aware
# ---------------------------------------------------------------------------

@require_role('editor', 'sudo', rate_limit_action='file_upload')
async def handle_files(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Receives files from editor.
    Stage-aware: mockup stage → goes to mockup_files, print stage → print_files.
    Also handles restore ZIP file upload.
    """
    # ── Check for restore file upload ────────────────────────────────
    if context.user_data.get('awaiting_restore_file'):
        await _handle_restore_file(update, context)
        return

    stage: Optional[EditorStage] = context.user_data.get('stage')

    # Only accept files during active upload stages
    if stage not in (EditorStage.MOCKUP, EditorStage.PRINT):
        return

    code: Optional[str] = context.user_data.get('code')
    if not code:
        await update.message.reply_text("❌ جلسه طراحی یافت نشد. لطفا دوباره شروع کنید.")
        context.user_data.clear()
        return

    # Verify design still exists and is pending
    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(
            f"❌ طرح {code} در سیستم یافت نشد. جلسه منقضی شده است.\n"
            f"لطفا دوباره ثبت طرح را شروع کنید."
        )
        _clear_editor_state(context)
        return

    if design.status != DesignStatus.PENDING:
        await update.message.reply_text(
            f"❌ طرح {code} قبلا پردازش شده است.\n"
            f"جلسه شما منقضی شده است."
        )
        _clear_editor_state(context)
        return

    if design.editor_user_id != update.effective_user.id:
        await update.message.reply_text("❌ شما مالک این طرح نیستید.")
        _clear_editor_state(context)
        return

    # Extract file_id and determine type
    if update.message.photo:
        file_id: str = update.message.photo[-1].file_id
        file_type = 'photo'
    elif update.message.document:
        file_id = update.message.document.file_id
        file_type = 'document'
    else:
        await update.message.reply_text("❌ لطفا فقط عکس یا فایل ارسال کنید.")
        return

    # Store file type mapping
    context.user_data.setdefault('file_types', {})[file_id] = file_type

    # Add to correct list
    if stage == EditorStage.MOCKUP:
        context.user_data.setdefault('mockup_files', []).append(file_id)
        count = len(context.user_data['mockup_files'])
        await update.message.reply_text(f"✅ موکاپ {count} دریافت شد.")

    elif stage == EditorStage.PRINT:
        context.user_data.setdefault('print_files', []).append(file_id)
        count = len(context.user_data['print_files'])
        await update.message.reply_text(f"✅ فایل چاپی {count} دریافت شد.")

    # Reset inactivity timer on every file
    _reset_inactivity_timer(context, update.effective_chat.id)

    # Update workspace message
    await _render_stage(context, update.effective_chat.id)


# ---------------------------------------------------------------------------
# Callbacks — Stage Transitions
# ---------------------------------------------------------------------------

def _editor_key(update, context) -> str:
    code = context.user_data.get('code', 'unknown')
    data = update.callback_query.data
    return f"editor_{code}_{data}"


@require_role('editor', 'sudo')
@deduplicate_callback(_editor_key)
async def editor_callbacks(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Central callback handler for all editor stage transitions.
    """
    query = update.callback_query
    await query.answer()

    data: str = query.data
    chat_id: int = query.from_user.id

    # Reset inactivity timer on every button click
    _reset_inactivity_timer(context, chat_id)

    # -----------------------------------------------------------------------
    # Stage transitions
    # -----------------------------------------------------------------------

    if data == "stage_mockup_done":
        await _handle_mockup_done(query, context, chat_id)

    elif data == "stage_print_done":
        await _handle_print_done(query, context, chat_id)

    elif data == "stage_goto_mockup":
        context.user_data['stage'] = EditorStage.MOCKUP
        await _render_stage(context, chat_id)

    elif data == "stage_goto_print":
        context.user_data['stage'] = EditorStage.PRINT
        await _render_stage(context, chat_id)

    elif data == "back_to_workspace":
        context.user_data['stage'] = EditorStage.WORKSPACE
        await _render_stage(context, chat_id)

    # -----------------------------------------------------------------------
    # Clear flows
    # -----------------------------------------------------------------------

    elif data == "stage_mockup_clear":
        await _handle_clear_request(query, context, chat_id, stage="mockup")

    elif data == "stage_print_clear":
        await _handle_clear_request(query, context, chat_id, stage="print")

    elif data == "clear_confirmed_mockup":
        context.user_data['mockup_files'] = []
        context.user_data['stage'] = EditorStage.MOCKUP
        await _render_stage(context, chat_id)

    elif data == "clear_confirmed_print":
        context.user_data['print_files'] = []
        context.user_data['stage'] = EditorStage.PRINT
        await _render_stage(context, chat_id)

    elif data in ("clear_cancelled_mockup", "clear_cancelled_print"):
        # Just re-render current stage
        await _render_stage(context, chat_id)

    # -----------------------------------------------------------------------
    # Confirm stage
    # -----------------------------------------------------------------------

    elif data == "confirm_submit":
        await _handle_confirm_submit(query, context, chat_id)

    elif data == "submit_to_reviewer":
        await _handle_submit_to_reviewer(update, context, chat_id)

    elif data == "preview_files":
        await _handle_preview_files(query, context, chat_id)

    # -----------------------------------------------------------------------
    # Cancel
    # -----------------------------------------------------------------------

    elif data == "cancel_submission":
        await _handle_cancel(query, context)

    elif data == "cancel_editing":
        await _handle_cancel_editing(update, query, context)

    # -----------------------------------------------------------------------
    # File management
    # -----------------------------------------------------------------------

    elif data == "manage_mockups":
        await _handle_manage_files(query, context, chat_id, stage="mockup")

    elif data == "manage_prints":
        await _handle_manage_files(query, context, chat_id, stage="print")

    elif data.startswith("remove_mockup_"):
        index = int(data.split("_")[-1])
        await _handle_remove_file(query, context, chat_id, stage="mockup", index=index)

    elif data.startswith("remove_print_"):
        index = int(data.split("_")[-1])
        await _handle_remove_file(query, context, chat_id, stage="print", index=index)

    elif data == "manage_clear_mockup":
        context.user_data['mockup_files'] = []
        context.user_data['stage'] = EditorStage.WORKSPACE
        await _render_stage(context, chat_id)

    elif data == "manage_clear_print":
        context.user_data['print_files'] = []
        context.user_data['stage'] = EditorStage.WORKSPACE
        await _render_stage(context, chat_id)

    elif data == "manage_back":
        context.user_data['stage'] = EditorStage.WORKSPACE
        await _render_stage(context, chat_id)


# ---------------------------------------------------------------------------
# Stage Handlers
# ---------------------------------------------------------------------------

async def _handle_mockup_done(query, context, chat_id: int) -> None:
    """User pressed 'اتمام ثبت موکاپ'"""
    mockups = context.user_data.get('mockup_files', [])

    if not mockups:
        await query.answer(
            "❌ حداقل یک موکاپ باید ارسال شود",
            show_alert=True
        )
        return

    # Move to print stage
    context.user_data['stage'] = EditorStage.PRINT
    await _render_stage(context, chat_id)


async def _handle_print_done(query, context, chat_id: int) -> None:
    """User pressed 'اتمام ثبت فایل چاپی'"""
    prints = context.user_data.get('print_files', [])

    if not prints:
        await query.answer(
            "❌ حداقل یک فایل چاپی باید ارسال شود",
            show_alert=True
        )
        return

    # Move to confirm stage
    context.user_data['stage'] = EditorStage.CONFIRM
    await _render_stage(context, chat_id)


async def _handle_confirm_submit(query, context, chat_id: int) -> None:
    """User pressed 'ثبت نهایی' from workspace"""
    mockups = context.user_data.get('mockup_files', [])
    prints  = context.user_data.get('print_files', [])

    if not mockups:
        await query.answer("❌ حداقل یک موکاپ باید ارسال شود", show_alert=True)
        return

    if not prints:
        await query.answer("❌ حداقل یک فایل چاپی باید ارسال شود", show_alert=True)
        return

    context.user_data['stage'] = EditorStage.CONFIRM
    await _render_stage(context, chat_id)


async def _handle_clear_request(query, context, chat_id: int, stage: str) -> None:
    """Show clear confirmation dialog"""
    text, markup = Keyboards.get_clear_confirmation(stage)
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Could not edit for clear confirmation: {e}")


async def _handle_manage_files(query, context, chat_id: int, stage: str) -> None:
    """Show file management view for individual file removal"""
    code = context.user_data.get('code', '')
    product_name = context.user_data.get('product_name', '')
    files = context.user_data.get(f'{stage}_files', [])

    text, markup = Keyboards.get_manage_files_stage(stage, files, code, product_name)
    try:
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Could not edit for file management: {e}")


async def _handle_remove_file(query, context, chat_id: int, stage: str, index: int) -> None:
    """Remove a single file at the given index"""
    files_key = f'{stage}_files'
    files = context.user_data.get(files_key, [])

    if index < 0 or index >= len(files):
        await query.answer("❌ فایل یافت نشد", show_alert=True)
        return

    removed = files.pop(index)
    context.user_data[files_key] = files

    # Also remove from file_types if present
    file_types = context.user_data.get('file_types', {})
    file_types.pop(removed, None)
    context.user_data['file_types'] = file_types

    # Re-render the management view
    code = context.user_data.get('code', '')
    product_name = context.user_data.get('product_name', '')
    text, markup = Keyboards.get_manage_files_stage(stage, files, code, product_name)

    try:
        await query.edit_message_text(
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Could not edit for file removal: {e}")

    stage_label = "موکاپ" if stage == "mockup" else "فایل چاپی"
    await query.answer(f"❌ {stage_label} {index + 1} حذف شد")


async def _notify_reviewers_of_edit(bot, design: Design) -> None:
    """
    After an editor updates a pending design, delete old reviewer messages
    and resend updated mockups so reviewers see the latest version.
    Note: Print files are not sent to reviewers.
    """
    code = design.code
    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else ""

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"approve_{code}"),
            InlineKeyboardButton("❌ رد",    callback_data=f"reject_{code}")
        ]
    ])

    for reviewer_id, old_msg_ids in design.all_reviewer_message_pairs():
        # Delete old messages with the shared safe/paced delete helper.
        await delete_messages(bot, reviewer_id, old_msg_ids)

        # Resend updated mockups. Each chunk is retried independently so a 429
        # or a bad media item in one chunk does not abort the whole resend.
        new_msg_ids = []
        mockups = design.mockup_file_ids
        file_types = design.file_types

        if mockups:
            if len(mockups) == 1:
                fid = mockups[0]
                caption = f"📦 {pl_name} | کد: {code}\n👤 طراح: {design.editor_name}\n\n🔄 بروزرسانی شده"
                is_photo = file_types.get(fid) == 'photo'
                try:
                    if is_photo:
                        m = await send_with_retry(
                            lambda: bot.send_photo(reviewer_id, photo=fid, caption=caption, reply_markup=markup),
                            f"Resend updated design {code} photo to reviewer {reviewer_id}"
                        )
                    else:
                        m = await send_with_retry(
                            lambda: bot.send_document(reviewer_id, document=fid, caption=caption, reply_markup=markup),
                            f"Resend updated design {code} document to reviewer {reviewer_id}"
                        )
                    new_msg_ids.append(m.message_id)
                except Exception as e:
                    logging.error(f"Failed to resend design {code} to reviewer {reviewer_id}: {e}")
            else:
                for chunk_start in range(0, len(mockups), 10):
                    chunk = mockups[chunk_start:chunk_start + 10]
                    media_group = []
                    for i, fid in enumerate(chunk):
                        idx = chunk_start + i
                        cap = (
                            f"📦 {pl_name} | کد: {code} ({idx+1}/{len(mockups)})\n"
                            f"👤 طراح: {design.editor_name}\n🔄 بروزرسانی شده"
                        ) if idx == 0 else ""
                        is_photo = file_types.get(fid) == 'photo'
                        if is_photo:
                            media_group.append(InputMediaPhoto(media=fid, caption=cap))
                        else:
                            media_group.append(InputMediaDocument(media=fid, caption=cap))
                    try:
                        msgs = await send_with_retry(
                            lambda media_group=media_group: bot.send_media_group(reviewer_id, media=media_group),
                            f"Resend updated design {code} media chunk {chunk_start // 10 + 1} to reviewer {reviewer_id}"
                        )
                        new_msg_ids.extend([m.message_id for m in msgs])
                    except Exception as e:
                        logging.error(
                            f"Failed to resend design {code} chunk {chunk_start // 10 + 1} "
                            f"to reviewer {reviewer_id}: {e}"
                        )

                # Action buttons as separate message
                if new_msg_ids:
                    try:
                        m = await send_with_retry(
                            lambda: bot.send_message(
                                reviewer_id,
                                f"📋 بررسی {pl_name} — {code}\n🔄 بروزرسانی شده",
                                reply_markup=markup,
                                reply_to_message_id=new_msg_ids[-1]
                            ),
                            f"Resend updated design {code} action buttons to reviewer {reviewer_id}"
                        )
                        new_msg_ids.append(m.message_id)
                    except Exception as e:
                        logging.error(f"Failed to resend action buttons for {code} to reviewer {reviewer_id}: {e}")

        # Save new message IDs
        if new_msg_ids:
            design.set_reviewer_messages(reviewer_id, new_msg_ids)

    # Save updated reviewer message IDs
    try:
        design.save_reviewer_messages()
    except Exception as e:
        logging.error(f"Failed to save reviewer message IDs for {code}: {e}")


async def _handle_preview_files(query, context, chat_id: int) -> None:
    """
    Send mockups as media group + prints as individual documents.
    Called from confirm stage.
    """
    mockups = context.user_data.get('mockup_files', [])
    prints  = context.user_data.get('print_files', [])
    code    = context.user_data.get('code', '')

    await query.answer()

    # Send mockups as media group (chunked to Telegram's 10-item limit)
    if mockups:
        if len(mockups) == 1:
            try:
                await send_with_retry(
                    lambda: query.message.chat.send_photo(
                        photo=mockups[0],
                        caption=f"🎨 موکاپ 1/1 — {code}"
                    ),
                    f"Preview mockup 1/1 for {code}"
                )
            except Exception as e:
                logging.error(f"Failed to send mockup preview: {e}")
                await query.message.chat.send_message("❌ خطا در ارسال موکاپ‌ها.")
        else:
            failed_chunks = 0
            for chunk_start in range(0, len(mockups), 10):
                chunk = mockups[chunk_start:chunk_start + 10]
                chunk_num = chunk_start // 10 + 1
                media_group = [
                    InputMediaPhoto(
                        media=fid,
                        caption=f"🎨 موکاپ {chunk_start+i+1}/{len(mockups)} — {code}"
                        if i == 0 and chunk_num == 1 else ""
                    )
                    for i, fid in enumerate(chunk)
                ]
                try:
                    await send_with_retry(
                        lambda media_group=media_group: query.message.chat.send_media_group(media=media_group),
                        f"Preview mockup chunk {chunk_num} for {code}"
                    )
                except Exception as e:
                    failed_chunks += 1
                    logging.error(f"Failed to send mockup preview chunk {chunk_num}: {e}")
            if failed_chunks:
                await query.message.chat.send_message(
                    f"❌ خطا در ارسال {failed_chunks} بخش از موکاپ‌ها."
                )

    # Send prints individually as documents
    if prints:
        unique_prints = list(dict.fromkeys(prints))
        for i, fid in enumerate(unique_prints):
            try:
                await send_with_retry(
                    lambda fid=fid, i=i: query.message.chat.send_document(
                        document=fid,
                        caption=f"🖨 فایل چاپی {i+1}/{len(unique_prints)} — {code}"
                    ),
                    f"Preview print {i+1}/{len(unique_prints)} for {code}"
                )
            except Exception as e:
                logging.error(f"Failed to send print preview {i}: {e}")


async def _handle_cancel(query, context) -> None:
    """Cancel and delete design"""
    code: Optional[str] = context.user_data.get('code')

    if code:
        design: Optional[Design] = Design.get_by_code(code)
        if design:
            design.delete()

    _clear_editor_state(context)

    await safe_edit_message(
        query,
        f"❌ طرح لغو شد. کد آزاد شد."
    )


async def _handle_cancel_editing(
    update: Update,
    query,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel editing an existing design without deleting it, return to design detail."""
    code: Optional[str] = context.user_data.get('code')
    _clear_editor_state(context)

    if code:
        from handlers.my_designs import _show_design_detail
        await _show_design_detail(update, context, code)
    else:
        await safe_edit_message(query, "❌ ویرایش لغو شد.")


# ---------------------------------------------------------------------------
# Submit to Reviewer
# ---------------------------------------------------------------------------

async def _handle_submit_to_reviewer(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int
) -> None:
    """
    Final submission.
    Saves files to design, sends to all reviewers.
    """
    query = update.callback_query
    code:        str  = context.user_data['code']
    mockups:     list = context.user_data.get('mockup_files', [])
    prints:      list = context.user_data.get('print_files', [])
    product_name: str = context.user_data['product_name']
    editor_name:  str = context.user_data['db_user'].first_name
    file_types:  dict = context.user_data.get('file_types', {})

    # Final validation
    if not mockups or not prints:
        await query.answer(
            "❌ لطفا حداقل یک موکاپ و یک فایل چاپی بفرستید.",
            show_alert=True
        )
        return

    editing_mode = context.user_data.get('editing_existing', False)

    if editing_mode:
        await safe_edit_message(query, "⏳ در حال بروزرسانی طرح...")
    else:
        await safe_edit_message(query, "⏳ در حال ارسال برای تایید...")

    # Save files to design
    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await safe_edit_message(query, "❌ خطا: طرح در دیتابیس یافت نشد.")
        _clear_editor_state(context)
        return

    # Race condition guard: re-check status before saving in edit mode
    if editing_mode and design.status != DesignStatus.PENDING:
        await safe_edit_message(
            query,
            f"❌ طرح {code} توسط ناظر پردازش شده است.\n"
            f"ویرایش امکان‌پذیر نیست."
        )
        _clear_editor_state(context)
        return

    design.mockup_file_ids = mockups
    design.print_file_ids  = prints
    design.file_types = file_types

    try:
        design.save()
    except Exception as e:
        logging.error(f"Failed to save design {code}: {e}")
        await safe_edit_message(
            query,
            f"❌ خطا در ذخیره طرح. لطفا دوباره تلاش کنید.\n"
            f"کد {code} همچنان برای شما رزرو است."
        )
        return

    # If editing, update reviewers with new files
    if editing_mode:
        # Delete old reviewer messages and resend updated files
        await _notify_reviewers_of_edit(context.bot, design)

        await safe_edit_message(
            query,
            f"✅ طرح {code} بروزرسانی شد.\n\n"
            f"📎 موکاپ: {len(mockups)} فایل\n"
            f"🖨 چاپ: {len(prints)} فایل\n\n"
            f"⏳ طرح همچنان در انتظار بررسی ناظر است."
        )
        _clear_editor_state(context)
        logging.info(f"Design {code} updated by user {editor_name}")
        return

    # New submission - send to reviewers
    # Get reviewers
    reviewers: list[User] = User.get_by_role('reviewer')
    if not reviewers:
        await safe_edit_message(query, "❌ هیچ ناظری در سیستم ثبت نشده است.")
        design.delete()
        _clear_editor_state(context)
        return

    # Build reviewer markup
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"approve_{code}"),
            InlineKeyboardButton("❌ رد",    callback_data=f"reject_{code}")
        ]
    ])

    # Send to all reviewers sequentially (paced/retried per Telegram request).
    async def send_to_reviewer(reviewer: User) -> Tuple[User, bool, list[int]]:
        """Send design to a single reviewer. Returns (reviewer, success, msg_ids)"""
        msg_ids: list[int] = []
        try:
            if total_mockups == 1:
                caption = f"📦 {product_name} | کد: {code}\n👤 طراح: {editor_name}"
                fid = mockups[0]
                # Use stored file type instead of file_id prefix
                is_photo = file_types.get(fid) == 'photo'
                if is_photo:
                    m = await send_with_retry(
                        lambda: context.bot.send_photo(
                            reviewer.user_id, photo=fid,
                            caption=caption, reply_markup=markup
                        ),
                        f"Send design {code} photo to reviewer {reviewer.user_id}"
                    )
                else:
                    m = await send_with_retry(
                        lambda: context.bot.send_document(
                            reviewer.user_id, document=fid,
                            caption=caption, reply_markup=markup
                        ),
                        f"Send design {code} document to reviewer {reviewer.user_id}"
                    )
                msg_ids.append(m.message_id)

            else:
                # Send mockups chunked to Telegram's 10-item limit
                for chunk_start in range(0, total_mockups, 10):
                    chunk = mockups[chunk_start:chunk_start + 10]
                    media_group = []
                    for i, fid in enumerate(chunk):
                        idx = chunk_start + i
                        cap = (
                            f"📦 {product_name} | کد: {code} ({idx+1}/{total_mockups})\n"
                            f"👤 طراح: {editor_name}"
                        ) if idx == 0 else ""
                        # Use stored file type instead of file_id prefix
                        is_photo = file_types.get(fid) == 'photo'
                        if is_photo:
                            media_group.append(InputMediaPhoto(media=fid, caption=cap))
                        else:
                            media_group.append(InputMediaDocument(media=fid, caption=cap))

                    try:
                        msgs = await send_with_retry(
                            lambda media_group=media_group: context.bot.send_media_group(
                                reviewer.user_id, media=media_group
                            ),
                            f"Send design {code} chunk {chunk_start // 10 + 1} to reviewer {reviewer.user_id}"
                        )
                        msg_ids.extend([m.message_id for m in msgs])
                    except Exception as e:
                        logging.error(
                            f"Failed to send design {code} chunk {chunk_start // 10 + 1} "
                            f"to reviewer {reviewer.user_id}: {e}"
                        )
                        raise

                # Action buttons as separate message (reply to last mockup)
                if msg_ids:
                    m = await send_with_retry(
                        lambda: context.bot.send_message(
                            reviewer.user_id,
                            f"📋 بررسی {product_name} — {code}",
                            reply_markup=markup,
                            reply_to_message_id=msg_ids[-1]
                        ),
                        f"Send design {code} action buttons to reviewer {reviewer.user_id}"
                    )
                    msg_ids.append(m.message_id)

            return reviewer, bool(msg_ids), msg_ids

        except Exception as e:
            logging.error(f"Failed to send design {code} to reviewer {reviewer.user_id}: {e}")
            if msg_ids:
                await delete_messages(context.bot, reviewer.user_id, msg_ids)
            return reviewer, False, []

    # Execute sends sequentially to avoid burst/flood failures across reviewers.
    total_mockups: int = len(mockups)
    results = []
    for reviewer in reviewers:
        results.append(await send_to_reviewer(reviewer))

    # Process results
    successful_sends: int = 0
    for result in results:
        if isinstance(result, Exception):
            logging.error(f"Reviewer send task failed: {result}")
            continue
        reviewer, success, msg_ids = result
        if success and msg_ids:
            design.set_reviewer_messages(reviewer.user_id, msg_ids)
            successful_sends += 1

    # Save reviewer message IDs
    if successful_sends > 0:
        try:
            design.save()
        except Exception as e:
            logging.error(f"Failed to save reviewer message IDs for {code}: {e}")

    # Handle total failure
    if successful_sends == 0:
        await safe_edit_message(
            query,
            "❌ خطا در ارسال به ناظران. طرح ذخیره شده اما هیچ ناظری اطلاع‌رسانی نشد.\n"
            "لطفا به Sudo اطلاع دهید."
        )
        _clear_editor_state(context)
        return

    # Success
    result_text = (
        f"✅ طرح {code} ثبت و برای تایید ارسال شد.\n"
        f"⏳ منتظر بررسی ناظر..."
    )

    if successful_sends < len(reviewers):
        result_text += (
            f"\n⚠️ ارسال به {len(reviewers) - successful_sends} ناظر ناموفق بود."
        )

    await safe_edit_message(query, result_text)

    # Clean up state
    _clear_editor_state(context)


# ---------------------------------------------------------------------------
# Restore Handler
# ---------------------------------------------------------------------------

async def _handle_restore_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Handle ZIP file upload for restore"""
    context.user_data.pop('awaiting_restore_file', None)

    # Check it's a document, not a photo
    if not update.message.document:
        await update.message.reply_text("❌ لطفاً فایل ZIP را به صورت document ارسال کنید.")
        return

    doc = update.message.document
    if not doc.file_name or not doc.file_name.endswith('.zip'):
        await update.message.reply_text("❌ فایل باید فرمت .zip داشته باشد.")
        return

    # Check file size (max 100MB)
    if doc.file_size and doc.file_size > 100 * 1024 * 1024:
        await update.message.reply_text("❌ فایل بیش از ۱۰۰ مگابایت است.")
        return

    status_msg = await update.message.reply_text("⏳ در حال دانلود فایل بکاپ...")

    try:
        # Download the file
        file = await context.bot.get_file(doc.file_id)
        temp_dir = tempfile.mkdtemp()
        zip_path = os.path.join(temp_dir, doc.file_name)
        await file.download_to_drive(zip_path)

        await status_msg.edit_text("🔍 بررسی فایل بکاپ...")

        # Find SQL file in ZIP
        from services.restore_service import RestoreService
        sql_name = RestoreService.find_sql_in_zip(zip_path)
        if not sql_name:
            await status_msg.edit_text("❌ فایل SQL در بکاپ یافت نشد.\nفرمت بکاپ نامعتبر است.")
            shutil.rmtree(temp_dir, ignore_errors=True)
            return

        # Extract SQL file
        sql_path = os.path.join(temp_dir, sql_name)
        with zipfile.ZipFile(zip_path, 'r') as z:
            z.extract(sql_name, temp_dir)

        # Confirm with user
        context.user_data['restore_pending'] = {
            'zip_path': zip_path,
            'sql_path': sql_path,
            'temp_dir': temp_dir,
            'doc_name': doc.file_name,
        }

        await status_msg.edit_text(
            f"⚠️ تایید ریستور\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"📁 فایل: {doc.file_name}\n"
            f"📦 حجم: {doc.file_size / 1024:.0f} KB\n\n"
            f"این عملیات:\n"
            f"• دیتابیس فعلی را پاک و با بکاپ جایگزین می‌کند\n"
            f"• فایل‌های public را بازنویسی می‌کند\n"
            f"• ربات ریستارت می‌شود\n\n"
            f"آیا مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، ریستور کن", callback_data="confirm_restore"),
                InlineKeyboardButton("❌ انصراف", callback_data="cancel_restore"),
            ]])
        )

    except Exception as e:
        logging.error(f"Restore download failed: {e}")
        await status_msg.edit_text(f"❌ خطا در دانلود فایل: {e}")
        context.user_data.pop('restore_pending', None)
