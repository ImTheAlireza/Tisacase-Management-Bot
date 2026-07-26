import logging
import asyncio
import time
from typing import Optional

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import ContextTypes

from config.settings import SUDO_USER_ID, NAZI_CHAT_ID
from models.design import Design
from models.product_line import ProductLine
from models.design_group_message import DesignGroupMessage
from models.user import User
from utils.helpers import safe_edit_message, delete_messages, format_datetime_persian
from utils.enums import DesignStatus
from utils.callback_lock import deduplicate_callback


# ===========================================================================
# HELPER: Centralized privilege check
# ===========================================================================

def is_privileged(user_id: int) -> bool:
    """
    Check if user is privileged (Sudo or Nazi).
    Uses centralized User.is_privileged_user() method.
    """
    return User.is_privileged_user(user_id)


# ===========================================================================
# DESIGN INFO COMMAND — "اطلاعات طرح"
# ===========================================================================

async def design_info_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for /designinfo command.
    Asks user to input a design code.
    """
    user_id: int = update.effective_user.id
    if not is_privileged(user_id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    context.user_data['awaiting_design_code'] = 'info'
    await update.message.reply_text(
        "🔍 کد طرح مورد نظر را وارد کنید:\n"
        "مثال: TS001\n\n"
        "برای لغو /cancel بزنید."
    )


# ===========================================================================
# DELETE DESIGN COMMAND — "حذف طرح"
# ===========================================================================

async def delete_design_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for /deletedesign command.
    Asks user to input a code to delete.
    """
    user_id: int = update.effective_user.id
    if not is_privileged(user_id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    context.user_data['awaiting_design_code'] = 'delete'
    await update.message.reply_text(
        "🗑 کد طرح تایید شده‌ای که می‌خواهید حذف کنید را وارد کنید:\n"
        "مثال: TS001\n\n"
        "⚠️ این عملیات فایل‌ها را از گروه‌ها حذف کرده و کد را آزاد می‌کند.\n\n"
        "برای لغو /cancel بزنید."
    )


# ===========================================================================
# HANDLE TEXT INPUT FOR DESIGN CODE
# ===========================================================================

async def handle_design_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handles text input when awaiting a design code.
    Returns True if it consumed the message, False otherwise.
    """
    mode: Optional[str] = context.user_data.get('awaiting_design_code')
    if mode not in ('info', 'delete'):
        return False

    code: str = update.message.text.strip().upper()
    context.user_data.pop('awaiting_design_code', None)

    if mode == 'info':
        await _send_design_info(update, context, code)
    elif mode == 'delete':
        await _send_delete_confirmation(update, context, code)

    return True


# ===========================================================================
# SEND FULL DESIGN INFO
# ===========================================================================

async def _send_design_info(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    code: str
) -> None:
    """
    Fetch and display full design info + files.
    Improved with:
    - Enum status handling
    - Better file sending with fallback
    - Proper error handling
    """
    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(f"❌ طرح با کد '{code}' یافت نشد.")
        return

    product_line: Optional[ProductLine] = ProductLine.get_by_id(design.product_line_id)
    pl_name: str = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    # Status mapping with Enum
    status_map = {
        DesignStatus.PENDING:  "⏳ در انتظار بررسی",
        DesignStatus.APPROVED: "✅ تایید شده",
        DesignStatus.REJECTED: "❌ رد شده",
        DesignStatus.DELETED:  "🗑 حذف شده",
    }
    status_text: str = status_map.get(design.status, design.status.value)

    # Build info text
    lines = [
        f"📋 اطلاعات طرح: {code}",
        "━━━━━━━━━━━━━━━━━━",
        f"📦 خط تولید:  {pl_name}",
        f"👤 طراح:       {design.editor_name or 'نامشخص'}",
        f"📅 تاریخ ثبت: {format_datetime_persian(design.created_at)}",
        f"🔖 وضعیت:    {status_text}",
    ]

    if design.status in (DesignStatus.APPROVED, DesignStatus.REJECTED, DesignStatus.DELETED):
        if design.reviewer_name:
            lines.append(f"✅ ناظر:      {design.reviewer_name}")
            lines.append(f"📅 تاریخ بررسی: {format_datetime_persian(design.reviewed_at)}")

    lines.append(f"\n🖼 موکاپ: {len(design.mockup_file_ids)} فایل")
    lines.append(f"🖨 فایل چاپی: {len(design.print_file_ids)} فایل")

    info_text: str = '\n'.join(lines)

    # Send info message
    await update.message.reply_text(info_text)

    # Helper: safe file sending with fallback
    async def send_file_safe(chat_id: int, file_id: str, caption: str) -> None:
        """Try to send as photo first, fallback to document"""
        try:
            # Try as photo
            await context.bot.send_photo(chat_id, photo=file_id, caption=caption)
        except Exception:
            # Fallback to document
            try:
                await context.bot.send_document(chat_id, document=file_id, caption=caption)
            except Exception as e:
                logging.error(f"Failed to send file {file_id}: {e}")
                await context.bot.send_message(
                    chat_id,
                    f"⚠️ خطا در ارسال فایل: {caption}\nFile ID: {file_id[:20]}..."
                )

    # Send mockup files
    if design.mockup_file_ids:
        await update.message.reply_text("🖼 موکاپ‌ها:")
        for i, fid in enumerate(design.mockup_file_ids):
            cap: str = f"موکاپ {i+1}/{len(design.mockup_file_ids)} — {code}"
            await send_file_safe(update.effective_chat.id, fid, cap)
            await asyncio.sleep(0.3)
    else:
        await update.message.reply_text("⚠️ موکاپی برای این طرح ذخیره نشده.")

    # Send print files
    if design.print_file_ids:
        unique_prints: list = list(dict.fromkeys(design.print_file_ids))
        await update.message.reply_text("🖨 فایل‌های چاپی:")
        for i, fid in enumerate(unique_prints):
            cap: str = f"فایل چاپی {i+1}/{len(unique_prints)} — {code}"
            # Print files should always be documents
            try:
                await context.bot.send_document(
                    update.effective_chat.id, document=fid, caption=cap
                )
                await asyncio.sleep(0.3)
            except Exception as e:
                logging.error(f"Failed to send print {i} for {code}: {e}")
                await update.message.reply_text(f"⚠️ فایل چاپی {i+1} قابل ارسال نیست.")
    else:
        await update.message.reply_text("⚠️ فایل چاپی برای این طرح ذخیره نشده.")


# ===========================================================================
# SEND DELETE CONFIRMATION
# ===========================================================================

async def _send_delete_confirmation(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    code: str
) -> None:
    """
    Show mockup + confirm/cancel buttons before deleting.
    Only approved designs can be deleted.
    """
    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(f"❌ طرح با کد '{code}' یافت نشد.")
        return

    if design.status != DesignStatus.APPROVED:
        status_map = {
            DesignStatus.PENDING:  'در انتظار',
            DesignStatus.REJECTED: 'رد شده',
            DesignStatus.DELETED:  'حذف شده'
        }
        st: str = status_map.get(design.status, design.status.value)
        await update.message.reply_text(
            f"❌ فقط طرح‌های تایید شده قابل حذف هستند.\n"
            f"وضعیت {code}: {st}"
        )
        return

    product_line: Optional[ProductLine] = ProductLine.get_by_id(design.product_line_id)
    pl_name: str = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    # Check if group messages are tracked
    group_msgs: list = DesignGroupMessage.get_by_code(code)
    group_msg_count: int = len(group_msgs)

    confirm_text: str = (
        f"⚠️ تایید حذف طرح\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔖 کد: {code}\n"
        f"📦 خط تولید: {pl_name}\n"
        f"👤 طراح: {design.editor_name or 'نامشخص'}\n"
        f"✅ ناظر: {design.reviewer_name or 'نامشخص'}\n"
        f"📅 تایید: {format_datetime_persian(design.reviewed_at)}\n\n"
        f"📨 پیام‌های قابل حذف از گروه‌ها: {group_msg_count}\n\n"
        f"🚨 آیا مطمئن هستید؟ این عملیات برگشت‌پذیر نیست."
    )

    markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"confirm_delete_{code}"),
        InlineKeyboardButton("❌ انصراف", callback_data="cancel_delete")
    ]])

    # Send first mockup as preview if available
    if design.mockup_file_ids:
        fid: str = design.mockup_file_ids[0]
        try:
            # Try sending as photo with caption
            await context.bot.send_photo(
                update.effective_chat.id,
                photo=fid,
                caption=confirm_text,
                reply_markup=markup
            )
            return
        except Exception:
            # Fallback to document
            try:
                await context.bot.send_document(
                    update.effective_chat.id,
                    document=fid,
                    caption=confirm_text,
                    reply_markup=markup
                )
                return
            except Exception as e:
                logging.error(f"Failed to send mockup preview for delete: {e}")

    # Fallback: no mockup or send failed, just text
    await update.message.reply_text(confirm_text, reply_markup=markup)


# ===========================================================================
# CONFIRM DELETE CALLBACK (with deduplication)
# ===========================================================================

def _delete_key(update, context) -> str:
    """Lock key for delete confirmation — prevents double-tap"""
    data: str = update.callback_query.data
    if data == "cancel_delete":
        return "cancel_delete"
    # callback_data format: confirm_delete_{code}
    code: str = data.split('_', 2)[2]
    return f"delete_{code}"


@deduplicate_callback(_delete_key)
async def confirm_delete_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """
    ✅ SAFE — Handle confirm/cancel delete buttons.
    - Uses Design.delete_completely() as single source of truth.
    - No duplicate deletion logic.
    - Deduplication prevents race conditions.
    """
    query = update.callback_query
    await query.answer()

    # Fresh privilege check
    user: Optional[User] = User.get_by_id(query.from_user.id)
    if not user or not is_privileged(user.user_id):
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        await safe_edit_message(query, "🚫 شما مجاز به حذف طرح نیستید.")
        return

    if query.data == "cancel_delete":
        await safe_edit_message(query, "👌 عملیات حذف لغو شد.")
        return

    # Extract code — callback_data format: confirm_delete_{code}
    code: str = query.data.split('_', 2)[2]

    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await safe_edit_message(query, "❌ طرح یافت نشد.")
        return

    if design.status != DesignStatus.APPROVED:
        await safe_edit_message(
            query,
            "❌ فقط طرح‌های تایید شده قابل حذف هستند."
        )
        return

    await safe_edit_message(query, f"⏳ در حال حذف طرح {code} از گروه‌ها...")

    # ✅ Single centralized deletion — handles everything
    result = await Design.delete_completely(code, context.bot)

    if not result['database_deleted']:
        error_text = (
            f"❌ خطا در حذف طرح {code}.\n"
            f"لطفاً با Sudo تماس بگیرید."
        )
        if result['errors']:
            error_text += f"\n\nخطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5])

        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=error_text
        )

        # Notify sudo about the failure
        from config.settings import SUDO_USER_ID
        if query.from_user.id != SUDO_USER_ID:
            try:
                await context.bot.send_message(
                    chat_id=SUDO_USER_ID,
                    text=(
                        f"❌ خطا در حذف طرح {code}\n"
                        f"درخواست‌کننده: {user.first_name} ({user.user_id})\n\n"
                        + (f"خطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5]) if result['errors'] else "جزئیات خطا موجود نیست.")
                    )
                )
            except Exception:
                pass
        return

    # ✅ Build result message
    result_lines = [
        f"✅ طرح {code} با موفقیت حذف شد.",
        "━━━━━━━━━━━━━━━━━━",
        f"🗑 پیام‌های حذف شده از گروه‌ها: {result['group_messages_deleted']}",
        f"🔓 کد {code} آزاد شد و قابل استفاده مجدد است.",
    ]

    # Show non-fatal errors with details (e.g. messages already deleted)
    if result['errors']:
        result_lines.append(
            f"\n⚠️ {len(result['errors'])} خطا رخ داد:\n"
            + "\n".join(f"• {e}" for e in result['errors'][:5])
        )

    await context.bot.send_message(
        chat_id=query.from_user.id,
        text='\n'.join(result_lines)
    )

    # Notify sudo about errors (non-fatal)
    if result['errors']:
        from config.settings import SUDO_USER_ID
        if query.from_user.id != SUDO_USER_ID:
            try:
                await context.bot.send_message(
                    chat_id=SUDO_USER_ID,
                    text=(
                        f"⚠️ حذف طرح {code} با خطا انجام شد\n"
                        f"درخواست‌کننده: {user.first_name} ({user.user_id})\n"
                        f"پیام‌های حذف شده: {result['group_messages_deleted']}\n\n"
                        f"خطاها:\n" + "\n".join(f"• {e}" for e in result['errors'][:5])
                    )
                )
            except Exception:
                pass



# ===========================================================================
# PENDING DESIGNS LIST — with inline code buttons
# ===========================================================================

async def pending_designs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show pending designs as inline code buttons.
    Accessible to privileged users AND all reviewers.
    """
    user_id: int = update.effective_user.id
    user: Optional[User] = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    # Sudo, Nazi, or any reviewer
    is_reviewer: bool = (
        user.role == 'reviewer'
        or user.is_sudo
        or User.is_privileged_user(user_id)
    )
    if not is_reviewer:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    pending: list[Design] = Design.get_all_pending()
    if not pending:
        await update.message.reply_text("✨ هیچ طرحی در انتظار بررسی نیست.")
        return

    # Group by product line
    by_line: dict = {}
    for d in pending:
        key: str = f"{d.product_icon} {d.product_name}"
        by_line.setdefault(key, []).append(d)

    lines = [f"📋 طرح‌های در انتظار ({len(pending)} طرح)\n━━━━━━━━━━━━━━━━━━"]
    keyboard = []

    for pl_label, designs in by_line.items():
        lines.append(f"\n{pl_label}:")
        row = []
        for d in designs:
            row.append(
                InlineKeyboardButton(
                    d.code,
                    callback_data=f"pending_view_{d.code}"
                )
            )
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    await update.message.reply_text(
        '\n'.join(lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


# ===========================================================================
# PENDING VIEW CALLBACK — send files when code button tapped
# ===========================================================================

async def pending_view_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Send mockup + print files when a code button is tapped in pending list.

    ✅ FIXED: Mockup message IDs are now stored in DB per reviewer.
    This allows cleanup of reviewer PV messages after approve/reject.
    Print file message IDs are NOT stored — only mockups need cleanup.
    """
    query = update.callback_query
    await query.answer()

    user_id: int = query.from_user.id
    user: Optional[User] = User.get_by_id(user_id)

    if not user or not user.is_active:
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        return

    is_reviewer: bool = (
        user.role == 'reviewer'
        or user.is_sudo
        or User.is_privileged_user(user_id)
    )
    if not is_reviewer:
        await query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
        return

    code: str = query.data.split('_', 2)[2]
    design: Optional[Design] = Design.get_by_code(code)

    if not design:
        await query.answer("❌ این طرح یافت نشد یا حذف شده.", show_alert=True)
        return

    if design.status != DesignStatus.PENDING:
        await query.answer("⚠️ این طرح دیگر در انتظار نیست.", show_alert=True)
        return

    product_line: Optional[ProductLine] = ProductLine.get_by_id(design.product_line_id)
    pl_name: str = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    # Warn if too many files
    total_files: int = len(design.mockup_file_ids) + len(set(design.print_file_ids))
    if total_files > 10:
        await query.answer(
            f"⚠️ این طرح {total_files} فایل دارد. ارسال ممکن است کمی طول بکشد...",
            show_alert=True
        )

    # Send info header
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"📋 {pl_name} | کد: {code}\n"
            f"👤 طراح: {design.editor_name or 'نامشخص'}\n"
            f"📅 ثبت: {format_datetime_persian(design.created_at)}\n"
            f"🖼 موکاپ: {len(design.mockup_file_ids)} | "
            f"🖨 چاپی: {len(design.print_file_ids)}\n\n"
            f"⏳ در حال ارسال فایل‌ها..."
        )
    )

    # ===========================================================
    # ✅ Send mockups — collect message IDs for later cleanup
    # ===========================================================
    mockup_count: int = len(design.mockup_file_ids)
    sent_mockup_msg_ids: list[int] = []

    for i, fid in enumerate(design.mockup_file_ids):
        cap: str = f"🖼 موکاپ {i+1}/{mockup_count} — {code}"
        msg = None
        try:
            msg = await context.bot.send_photo(
                chat_id=user_id,
                photo=fid,
                caption=cap
            )
        except Exception:
            try:
                msg = await context.bot.send_document(
                    chat_id=user_id,
                    document=fid,
                    caption=cap
                )
            except Exception as e:
                logging.error(
                    f"Failed to send mockup {i+1} of {code} "
                    f"to reviewer {user_id}: {e}"
                )

        # ✅ Collect message ID if send succeeded
        if msg:
            sent_mockup_msg_ids.append(msg.message_id)

        await asyncio.sleep(0.3)

    # ===========================================================
    # ✅ Save mockup message IDs to DB
    # So _delete_my_messages() can clean up after review decision
    # ✅ This ADDS to existing reviewer entries (does not overwrite others)
    # ✅ If reviewer views the design again, IDs are refreshed
    # ===========================================================
    if sent_mockup_msg_ids:
        design.set_reviewer_messages(user_id, sent_mockup_msg_ids)
        try:
            design.save_reviewer_messages()
        except Exception as e:
            logging.error(
                f"Could not save reviewer message IDs for {code}: {e}"
            )

    # ===========================================================
    # Send approve / reject buttons
    # ✅ Buttons sent AFTER files so reviewer sees files first
    # ===========================================================
    review_markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ تایید",
                callback_data=f"approve_{code}"
            ),
            InlineKeyboardButton(
                "❌ رد",
                callback_data=f"reject_{code}"
            ),
        ]
    ])

    btn_msg = await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"🔖 کد: {code}\n"
            f"👤 طراح: {design.editor_name}\n\n"
            f"تصمیم خود را انتخاب کنید:"
        ),
        reply_markup=review_markup
    )

    # ✅ Also track the button message ID so it gets cleaned up too
    if btn_msg:
        existing = design.get_reviewer_messages(user_id)
        updated = existing + [btn_msg.message_id]
        design.set_reviewer_messages(user_id, updated)
        try:
            design.save_reviewer_messages()
        except Exception as e:
            logging.error(
                f"Could not save button message ID for {code}: {e}"
            )

    # ===========================================================
    # Send print files
    # ✅ Print file IDs are NOT tracked — no cleanup needed
    # ===========================================================
    unique_prints: list = list(dict.fromkeys(design.print_file_ids))
    print_count: int = len(unique_prints)

    for i, fid in enumerate(unique_prints):
        cap: str = f"🖨 فایل چاپی {i+1}/{print_count} — {code}"
        try:
            await context.bot.send_document(
                chat_id=user_id,
                document=fid,
                caption=cap
            )
            await asyncio.sleep(0.3)
        except Exception as e:
            logging.error(
                f"Failed to send print {i+1} of {code} "
                f"to reviewer {user_id}: {e}"
            )

    # Completion message
    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ تمام فایل‌های طرح {code} ارسال شد."
    )