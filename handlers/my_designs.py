import logging
import asyncio
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto, InputMediaDocument
from telegram.ext import ContextTypes

from models.design import Design
from models.user import User
from models.product_line import ProductLine
from utils.helpers import format_datetime_persian
from utils.enums import DesignStatus
from config.settings import TELEGRAM_SEND_DELAY


# ===========================================================================
# MY DESIGNS COMMAND - Show editor's own submissions
# ===========================================================================

async def my_designs_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Show editor's own design submissions.
    Accessible to editors only.
    """
    user_id = update.effective_user.id
    user = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    role = user.get_effective_role()
    if role != 'editor':
        await update.message.reply_text("🚫 این بخش فقط برای طراحان است.")
        return

    await _show_my_designs(update, context, user_id)


async def _show_my_designs(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int) -> None:
    """Fetch and display user's designs grouped by status."""

    # Get all designs by this editor
    from config.database import get_db_connection
    import pymysql

    conn = get_db_connection()
    cursor = conn.cursor(pymysql.cursors.DictCursor)

    try:
        cursor.execute("""
            SELECT d.*, pl.name_fa as product_name, pl.icon as product_icon
            FROM designs d
            JOIN product_lines pl ON d.product_line_id = pl.id
            WHERE d.editor_user_id = %s
            ORDER BY d.created_at DESC
            LIMIT 50
        """, (user_id,))

        designs = cursor.fetchall()
    finally:
        cursor.close()
        conn.close()

    if not designs:
        await update.message.reply_text(
            "📋 شما هنوز طرحی ثبت نکرده‌اید.\n\n"
            "💡 برای ثبت طرح جدید، از دکمه‌های خط تولید استفاده کنید."
        )
        return

    # Group by status
    pending = []
    approved = []
    rejected = []
    deleted = []

    for d in designs:
        if d['status'] == 'pending':
            pending.append(d)
        elif d['status'] == 'approved':
            approved.append(d)
        elif d['status'] == 'rejected':
            rejected.append(d)
        elif d['status'] == 'deleted':
            deleted.append(d)

    # Build message
    text = "📋 طرح‌های من\n━━━━━━━━━━━━━━━━━━\n\n"

    if pending:
        text += f"⏳ در انتظار ({len(pending)}):\n"
        for d in pending[:5]:
            text += f"  • {d['code']} | {d['product_icon']} {d['product_name']}\n"
        if len(pending) > 5:
            text += f"  ... و {len(pending) - 5} مورد دیگر\n"
        text += "\n"

    if approved:
        text += f"✅ تایید شده ({len(approved)}):\n"
        for d in approved[:5]:
            text += f"  • {d['code']} | {d['product_icon']} {d['product_name']}\n"
        if len(approved) > 5:
            text += f"  ... و {len(approved) - 5} مورد دیگر\n"
        text += "\n"

    if rejected:
        text += f"❌ رد شده ({len(rejected)}):\n"
        for d in rejected[:5]:
            # Clean rejected code for display
            display_code = d['code'].split('_REJ_')[0]
            text += f"  • {display_code} | {d['product_icon']} {d['product_name']}\n"
        if len(rejected) > 5:
            text += f"  ... و {len(rejected) - 5} مورد دیگر\n"
        text += "\n"

    text += "💡 برای مشاهده جزئیات هر طرح، روی کد آن کلیک کنید:"

    # Build keyboard with codes
    keyboard = []

    # Pending designs (editable)
    if pending:
        keyboard.append([InlineKeyboardButton("⏳ در انتظار", callback_data="mydesigns_category_pending")])
        row = []
        for d in pending:
            row.append(InlineKeyboardButton(d['code'], callback_data=f"mydesigns_view_{d['code']}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    # Approved designs
    if approved:
        keyboard.append([InlineKeyboardButton("✅ تایید شده", callback_data="mydesigns_category_approved")])
        row = []
        for d in approved[:6]:
            row.append(InlineKeyboardButton(d['code'], callback_data=f"mydesigns_view_{d['code']}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    # Rejected designs (resubmittable)
    if rejected:
        keyboard.append([InlineKeyboardButton("❌ رد شده", callback_data="mydesigns_category_rejected")])
        row = []
        for d in rejected[:6]:
            display_code = d['code'].split('_REJ_')[0]
            row.append(InlineKeyboardButton(display_code, callback_data=f"mydesigns_view_{d['code']}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("🔄 بروزرسانی", callback_data="mydesigns_refresh")])

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        await update.callback_query.edit_message_text(
            text=text,
            reply_markup=markup
        )
    else:
        await update.message.reply_text(
            text=text,
            reply_markup=markup
        )


async def my_designs_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle my designs callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "mydesigns_refresh":
        await _show_my_designs(update, context, query.from_user.id)

    elif data.startswith("mydesigns_view_"):
        code = data[len("mydesigns_view_"):]
        await _show_design_detail(update, context, code)

    elif data.startswith("mydesigns_edit_"):
        code = data[len("mydesigns_edit_"):]
        await _start_edit_design(update, context, code)

    # ⚠️ ORDER MATTERS: "mydesigns_delete_confirm_" must be checked BEFORE
    # "mydesigns_delete_", otherwise the shorter prefix swallows the confirm
    # callback and the delete button silently does nothing.
    elif data.startswith("mydesigns_delete_confirm_"):
        code = data[len("mydesigns_delete_confirm_"):]
        await _execute_delete_design(update, context, code)

    elif data.startswith("mydesigns_delete_"):
        code = data[len("mydesigns_delete_"):]
        await _confirm_delete_design(update, context, code)

    elif data.startswith("mydesigns_files_"):
        code = data[len("mydesigns_files_"):]
        await _send_design_files(update, context, code)

    elif data.startswith("mydesigns_resubmit_"):
        code = data[len("mydesigns_resubmit_"):]
        await _handle_resubmit(update, context, code)

    elif data == "mydesigns_back":
        await _show_my_designs(update, context, query.from_user.id)


async def _show_design_detail(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Show detailed view of a design with action buttons."""
    design = Design.get_by_code(code)

    if not design:
        await update.callback_query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    # Verify ownership
    if design.editor_user_id != update.callback_query.from_user.id:
        await update.callback_query.answer("🚫 این طرح متعلق به شما نیست", show_alert=True)
        return

    # ✅ product line may be missing/deactivated — never dereference blindly,
    # otherwise the whole detail panel raises and the buttons look broken.
    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    status_map = {
        DesignStatus.PENDING: '⏳ در انتظار بررسی',
        DesignStatus.APPROVED: '✅ تایید شده',
        DesignStatus.REJECTED: '❌ رد شده',
        DesignStatus.DELETED: '🗑 حذف شده'
    }

    # For rejected designs, show original code
    display_code = code.split('_REJ_')[0] if design.status == DesignStatus.REJECTED else code

    text = f"🔍 جزئیات طرح\n━━━━━━━━━━━━━━━━━━\n\n"
    text += f"🔖 کد: {display_code}\n"
    text += f"📦 خط تولید: {pl_name}\n"
    text += f"📊 وضعیت: {status_map.get(design.status, design.status)}\n\n"
    text += f"🕐 ثبت: {format_datetime_persian(design.created_at)}\n"

    if design.status in [DesignStatus.APPROVED, DesignStatus.REJECTED]:
        text += f"✓ ناظر: {design.reviewer_name}\n"
        text += f"🕐 بررسی: {format_datetime_persian(design.reviewed_at)}\n"

    text += f"\n📎 موکاپ: {len(design.mockup_file_ids)} فایل\n"
    text += f"🖨 چاپ: {len(design.print_file_ids)} فایل\n"

    # Build action buttons based on status
    keyboard = []

    if design.status == DesignStatus.PENDING:
        keyboard.append([
            InlineKeyboardButton("✏️ ویرایش طرح", callback_data=f"mydesigns_edit_{code}"),
            InlineKeyboardButton("🗑 حذف طرح", callback_data=f"mydesigns_delete_{code}")
        ])
        keyboard.append([InlineKeyboardButton("👁 مشاهده فایل‌ها", callback_data=f"mydesigns_files_{code}")])
        text += "\n💡 می‌توانید این طرح را ویرایش یا حذف کنید."

    elif design.status == DesignStatus.APPROVED:
        keyboard.append([InlineKeyboardButton("👁 مشاهده فایل‌ها", callback_data=f"mydesigns_files_{code}")])

    elif design.status == DesignStatus.REJECTED:
        keyboard.append([InlineKeyboardButton("🔄 ثبت مجدد", callback_data=f"mydesigns_resubmit_{code}")])
        keyboard.append([InlineKeyboardButton("👁 مشاهده فایل‌ها", callback_data=f"mydesigns_files_{code}")])
        text += "\n💡 می‌توانید این طرح را اصلاح و مجدداً ثبت کنید."

    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="mydesigns_back")])

    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=markup
    )


async def _start_edit_design(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Start editing a pending design."""
    from handlers.editor import load_design_for_edit

    design = Design.get_by_code(code)

    if not design:
        await update.callback_query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    if not design.can_be_edited_by(update.callback_query.from_user.id):
        await update.callback_query.answer(
            "⚠️ فقط طرح‌های در انتظار را می‌توان ویرایش کرد",
            show_alert=True
        )
        return

    # Load design into editor
    await load_design_for_edit(update, context, design)


async def _confirm_delete_design(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Show confirmation for deleting a pending design."""
    design = Design.get_by_code(code)

    if not design:
        await update.callback_query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    if design.editor_user_id != update.callback_query.from_user.id:
        await update.callback_query.answer("🚫 این طرح متعلق به شما نیست", show_alert=True)
        return

    if design.status != DesignStatus.PENDING:
        await update.callback_query.answer("⚠️ فقط طرح‌های در انتظار قابل حذف هستند", show_alert=True)
        return

    # ✅ A missing product line must not block deletion — otherwise a design
    # whose product line was removed could never be deleted.
    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"
    display_code = code

    text = (
        f"⚠️ تایید حذف طرح\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"🔖 کد: {display_code}\n"
        f"📦 خط تولید: {pl_name}\n\n"
        f"🚨 آیا مطمئن هستید؟\n"
        f"کد آزاد شده و قابل استفاده مجدد خواهد بود."
    )

    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ بله، حذف کن", callback_data=f"mydesigns_delete_confirm_{code}"),
            InlineKeyboardButton("❌ انصراف", callback_data=f"mydesigns_view_{code}")
        ]
    ])

    await update.callback_query.edit_message_text(text=text, reply_markup=markup)


async def _execute_delete_design(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Execute design deletion."""
    query = update.callback_query
    design = Design.get_by_code(code)

    if not design:
        await query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    if design.editor_user_id != query.from_user.id:
        await query.answer("🚫 این طرح متعلق به شما نیست", show_alert=True)
        return

    if design.status != DesignStatus.PENDING:
        await query.answer("⚠️ فقط طرح‌های در انتظار قابل حذف هستند", show_alert=True)
        return

    # Delete the design completely
    result = await Design.delete_completely(code, context.bot)

    if result['database_deleted']:
        await query.edit_message_text(
            f"✅ طرح {code} حذف شد.\n"
            f"🔓 کد آزاد شد."
        )
    else:
        error_text = f"❌ خطا در حذف طرح {code}."
        if result['errors']:
            error_text += f"\n\n" + "\n".join(f"• {e}" for e in result['errors'][:3])
        await query.edit_message_text(error_text)


async def _send_design_files(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Send mockup and print files to the user's chat."""
    query = update.callback_query
    design = Design.get_by_code(code)

    if not design:
        await query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    if design.editor_user_id != query.from_user.id:
        await query.answer("🚫 این طرح متعلق به شما نیست", show_alert=True)
        return

    user_id = query.from_user.id
    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else ""

    # Clean rejected code for display
    display_code = code.split('_REJ_')[0] if design.status == DesignStatus.REJECTED else code

    # Send info header
    await context.bot.send_message(
        chat_id=user_id,
        text=(
            f"📋 {pl_name} | کد: {display_code}\n"
            f"🖼 موکاپ: {len(design.mockup_file_ids)} | "
            f"🖨 چاپی: {len(design.print_file_ids)}\n\n"
            f"⏳ در حال ارسال فایل‌ها..."
        )
    )

    # Send mockups
    mockup_count = len(design.mockup_file_ids)
    for i, fid in enumerate(design.mockup_file_ids):
        cap = f"🖼 موکاپ {i+1}/{mockup_count} — {display_code}"
        try:
            await context.bot.send_photo(chat_id=user_id, photo=fid, caption=cap)
        except Exception:
            try:
                await context.bot.send_document(chat_id=user_id, document=fid, caption=cap)
            except Exception as e:
                logging.error(f"Failed to send mockup {i+1} of {code}: {e}")
        await asyncio.sleep(0.3)

    # Send print files
    unique_prints = list(dict.fromkeys(design.print_file_ids))
    print_count = len(unique_prints)
    for i, fid in enumerate(unique_prints):
        cap = f"🖨 فایل چاپی {i+1}/{print_count} — {display_code}"
        try:
            await context.bot.send_document(chat_id=user_id, document=fid, caption=cap)
        except Exception as e:
            logging.error(f"Failed to send print {i+1} of {code}: {e}")
        await asyncio.sleep(0.3)

    await context.bot.send_message(
        chat_id=user_id,
        text=f"✅ تمام فایل‌های طرح {display_code} ارسال شد."
    )


async def _handle_resubmit(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Handle resubmitting a rejected design with error handling (toast notifications)."""
    from handlers.editor import load_design_for_edit
    from services.code_service import CodeService

    query = update.callback_query
    design = Design.get_by_code(code)

    if not design:
        await query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    if design.editor_user_id != query.from_user.id:
        await query.answer("🚫 این طرح متعلق به شما نیست", show_alert=True)
        return

    if design.status != DesignStatus.REJECTED:
        await query.answer("⚠️ فقط طرح‌های رد شده قابل ثبت مجدد هستند", show_alert=True)
        return

    # Try to generate a new code for the same product line
    product_line = ProductLine.get_by_id(design.product_line_id)
    if not product_line:
        await query.answer("❌ خط تولید یافت نشد", show_alert=True)
        return

    user = User.get_by_id(query.from_user.id)
    editor_name = user.first_name if user else "نامشخص"

    try:
        new_code, new_design = CodeService.generate_code(
            product_line.code_prefix,
            query.from_user.id,
            editor_name
        )
    except Exception as e:
        logging.error(f"Resubmit code generation failed for {code}: {e}")
        await query.answer(
            f"❌ خطا: کد جدید قابل تولید نیست.\n{str(e)[:100]}",
            show_alert=True
        )
        return

    # Copy files from old design to new design
    new_design.mockup_file_ids = design.mockup_file_ids.copy()
    new_design.print_file_ids = design.print_file_ids.copy()
    new_design.file_types = design.file_types.copy() if design.file_types else {}
    new_design.save()

    # Load into editor
    await load_design_for_edit(update, context, new_design)
