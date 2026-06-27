import logging
import asyncio
from typing import Optional
from telegram import (
    Update,
    InputMediaPhoto,
    InputMediaDocument,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes


from utils.decorators import require_role
from utils.enums import DesignStatus, EditorStage
from utils.helpers import safe_edit_message, delete_messages
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

    # ✅ 1. Delete pending design from DB (this frees the code)
    if code:
        try:
            from models.design import Design
            design = Design.get_by_code(code)
            if design and design.status == DesignStatus.PENDING:
                await Design.delete_completely(code, context.bot)
        except Exception as e:
            logging.error(f"Timeout cleanup failed for {code}: {e}")

    # ✅ 2. Delete workspace message
    if workspace_msg_id:
        try:
            await context.bot.delete_message(
                chat_id=chat_id,
                message_id=workspace_msg_id
            )
        except Exception as e:
            logging.warning(f"Could not delete workspace message on timeout: {e}")

    # ✅ 3. Notify user
    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=(
                " جلسه ثبت طرح به دلیل عدم فعالیت بسته شد.\n"
                "کد آزاد شد ✅\n"
                "در صورت نیاز دوباره ثبت را شروع کنید."
            )
        )
    except Exception as e:
        logging.warning(f"Could not send timeout notice to {chat_id}: {e}")

    # ✅ 4. Clear memory state
    if chat_id in app.user_data:
        app.user_data[chat_id].clear()

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
    }


def _clear_editor_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Fully reset editor state.
    NOTE: Does NOT delete the design from database - caller must handle that.
    """
    _cancel_inactivity_job(context)
    for key in [
        'code', 'product_id', 'product_name', 'stage',
        'mockup_files', 'print_files', 'workspace_message_id',
    ]:
        context.user_data.pop(key, None)

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

    # Build text + markup based on stage
    if stage == EditorStage.MOCKUP:
        text, markup = Keyboards.get_mockup_stage(code, product_name, len(mockups))

    elif stage == EditorStage.PRINT:
        text, markup = Keyboards.get_print_stage(code, product_name, len(mockups), len(prints))

    elif stage == EditorStage.WORKSPACE:
        text, markup = Keyboards.get_workspace_stage(code, product_name, len(mockups), len(prints))

    elif stage == EditorStage.CONFIRM:
        text, markup = Keyboards.get_confirm_stage(code, product_name, len(mockups), len(prints))

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

@require_role('editor', 'sudo')
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
    if old_code:
        # Delete the incomplete design from database
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
            try:
                await context.bot.delete_message(
                    chat_id=user_id,
                    message_id=old_workspace_msg_id
                )
                logging.info(f"Deleted old workspace message {old_workspace_msg_id} for user {user_id}")
            except Exception as e:
                logging.warning(f"Could not delete old workspace message: {e}")
        
        # Clear the old session completely
        _clear_editor_state(context)
    # =============================================

    # Validate product line
    product_line: Optional[ProductLine] = ProductLine.get_by_prefix(prefix)
    if not product_line:
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد.")
        return

    if not product_line.is_fully_configured():
        missing: str = ', '.join(product_line.missing_groups())
        await update.message.reply_text(
            f"⚠️ گروه‌های این خط تولید تنظیم نشده‌اند:\n{missing}\n\n"
            f"لطفا ابتدا از طریق منوی Sudo اقدام کنید."
        )
        return

    # Generate code
    try:
        code, design = CodeService.generate_code(prefix, user.user_id, user.first_name)
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

@require_role('editor', 'sudo')
async def handle_files(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    Receives files from editor.
    Stage-aware: mockup stage → goes to mockup_files, print stage → print_files.
    """
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

    # Extract file_id
    if update.message.photo:
        file_id: str = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("❌ لطفا فقط عکس یا فایل ارسال کنید.")
        return

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

    elif data == "workspace_clear_mockup":
        await _handle_clear_request(query, context, chat_id, stage="mockup")

    elif data == "workspace_clear_print":
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


async def _handle_preview_files(query, context, chat_id: int) -> None:
    """
    Send mockups as media group + prints as individual documents.
    Called from confirm stage.
    """
    mockups = context.user_data.get('mockup_files', [])
    prints  = context.user_data.get('print_files', [])
    code    = context.user_data.get('code', '')

    await query.answer()

    # Send mockups as media group
    if mockups:
        try:
            if len(mockups) == 1:
                await query.message.chat.send_photo(
                    photo=mockups[0],
                    caption=f"🎨 موکاپ 1/1 — {code}"
                )
            else:
                media_group = [
                    InputMediaPhoto(
                        media=fid,
                        caption=f"🎨 موکاپ {i+1}/{len(mockups)} — {code}"
                        if i == 0 else ""
                    )
                    for i, fid in enumerate(mockups)
                ]
                await query.message.chat.send_media_group(media=media_group)
        except Exception as e:
            logging.error(f"Failed to send mockup preview: {e}")
            await query.message.chat.send_message("❌ خطا در ارسال موکاپ‌ها.")

    # Send prints individually as documents
    if prints:
        unique_prints = list(dict.fromkeys(prints))
        for i, fid in enumerate(unique_prints):
            try:
                await query.message.chat.send_document(
                    document=fid,
                    caption=f"🖨 فایل چاپی {i+1}/{len(unique_prints)} — {code}"
                )
                await asyncio.sleep(0.3)
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

    # Final validation
    if not mockups or not prints:
        await query.answer(
            "❌ لطفا حداقل یک موکاپ و یک فایل چاپی بفرستید.",
            show_alert=True
        )
        return

    await safe_edit_message(query, "⏳ در حال ارسال برای تایید...")

    # Save files to design
    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await safe_edit_message(query, "❌ خطا: طرح در دیتابیس یافت نشد.")
        _clear_editor_state(context)
        return

    design.mockup_file_ids = mockups
    design.print_file_ids  = prints

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

    # Send to each reviewer
    successful_sends: int = 0
    total_mockups:    int = len(mockups)

    for reviewer in reviewers:
        msg_ids: list[int] = []
        try:
            if total_mockups == 1:
                caption = f"📦 {product_name} | کد: {code}\n👤 طراح: {editor_name}"
                fid = mockups[0]
                if isinstance(fid, str) and fid.startswith(('AgAC', 'AQA')):
                    m = await context.bot.send_photo(
                        reviewer.user_id, photo=fid,
                        caption=caption, reply_markup=markup
                    )
                else:
                    m = await context.bot.send_document(
                        reviewer.user_id, document=fid,
                        caption=caption, reply_markup=markup
                    )
                msg_ids.append(m.message_id)

            else:
                media_group = []
                for i, fid in enumerate(mockups):
                    cap = (
                        f"📦 {product_name} | کد: {code} ({i+1}/{total_mockups})\n"
                        f"👤 طراح: {editor_name}"
                    ) if i == 0 else ""
                    if isinstance(fid, str) and fid.startswith(('AgAC', 'AQA')):
                        media_group.append(InputMediaPhoto(media=fid, caption=cap))
                    else:
                        media_group.append(InputMediaDocument(media=fid, caption=cap))

                msgs = await context.bot.send_media_group(
                    reviewer.user_id, media=media_group
                )
                msg_ids.extend([m.message_id for m in msgs])

                # Action buttons as separate message
                m = await context.bot.send_message(
                    reviewer.user_id,
                    f"📋 بررسی {product_name} — {code}",
                    reply_markup=markup,
                    reply_to_message_id=msg_ids[-1]
                )
                msg_ids.append(m.message_id)

            design.set_reviewer_messages(reviewer.user_id, msg_ids)
            successful_sends += 1

        except Exception as e:
            logging.error(f"Failed to send design {code} to reviewer {reviewer.user_id}: {e}")

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