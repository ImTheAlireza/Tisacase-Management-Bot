import os
import shutil
import asyncio
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.decorators import require_sudo
from models.user import User
from models.product_line import ProductLine
from services.backup_service import BackupService
from utils.helpers import get_tehran_time
from config.settings import SUDO_USER_ID, SUPERVISORD_CONF, SUPERVISOR_PROCESS
from utils.enums import DesignStatus


def _verify_sudo_for_group_input(user_id):
    """Helper to verify sudo access for group input flow"""
    from config.settings import SUDO_USER_ID
    from models.user import User
    
    if user_id != SUDO_USER_ID:
        return False
    
    user = User.get_by_id(user_id)
    return user and user.is_sudo


@require_sudo
async def switch_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = context.user_data['db_user']
    keyboard = [
        [InlineKeyboardButton("👑 Sudo (کامل)", callback_data="role_sudo")],
        [InlineKeyboardButton("🎨 Editor (طراح)", callback_data="role_editor")],
        [InlineKeyboardButton("✅ Reviewer (ناظر)", callback_data="role_reviewer")]
    ]
    await update.message.reply_text(
        f"👑 تنظیمات نقش (Sudo)\n\n"
        f"نقش فعلی: {user.active_role.upper()}\n"
        f"شما همیشه Sudo هستید، این فقط رابط کاربری را تغییر می‌دهد.",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@require_sudo
async def handle_role_switch(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query

    ALLOWED_ROLES = {'sudo', 'editor', 'reviewer'}
    new_role = query.data.split('_')[1]

    if new_role not in ALLOWED_ROLES:
        await query.answer("❌ نقش نامعتبر", show_alert=True)
        return

    await query.answer()

    user = User.get_by_id(query.from_user.id)
    user.update_active_role(new_role)

    from ui.keyboards import Keyboards
    
    # FIX: Try to delete message, but don't fail if it's already gone
    try:
        await query.message.delete()
    except Exception as e:
        logging.warning(f"Could not delete role switch message: {e}")
    
    # Send new message with updated keyboard
    try:
        await context.bot.send_message(
            chat_id=query.from_user.id,
            text=f"✅ نقش شما به {new_role.upper()} تغییر یافت.",
            reply_markup=Keyboards.get_main_menu(user)
        )
    except Exception as e:
        logging.error(f"Failed to send role switch confirmation: {e}")
        # Fallback: edit the original message if delete failed
        try:
            await query.edit_message_text(
                f"✅ نقش شما به {new_role.upper()} تغییر یافت.\n\n"
                f"برای دیدن منوی جدید /start بزنید."
            )
        except Exception as e2:
            logging.error(f"Role switch message update fallback also failed: {e2}")

@require_sudo
async def manual_backup_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    msg = await update.message.reply_text("⏳ در حال تهیه بکاپ کامل... لطفاً صبر کنید.")

    zip_path = await BackupService.create_daily_backup_zip()

    if not zip_path:
        await msg.edit_text("❌ خطایی در گرفتن بکاپ رخ داد.")
        return

    try:
        file_size = os.path.getsize(zip_path) / 1024
        with open(zip_path, 'rb') as f:
            await context.bot.send_document(
                chat_id=SUDO_USER_ID,
                document=f,
                filename=os.path.basename(zip_path),
                caption=f"💾 بکاپ دستی\nحجم: {file_size:.1f} KB"
            )
        await msg.delete()
    finally:
        shutil.rmtree(os.path.dirname(zip_path), ignore_errors=True)


@require_sudo
async def restart_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    keyboard = [[
        InlineKeyboardButton("✅ ریستارت کن", callback_data="confirm_restart"),
        InlineKeyboardButton("❌ انصراف", callback_data="cancel_restart")
    ]]
    await update.message.reply_text(
        "⚠️ مطمئنی می‌خوای ربات رو ریستارت کنی؟",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

@require_sudo
async def execute_restart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    if query.data == "cancel_restart":
        await query.edit_message_text("لغو شد.")
        return

    await query.edit_message_text("🔄 ربات در حال ریستارت...\nلطفاً 10 ثانیه صبر کنید.")

    try:
        proc = await asyncio.create_subprocess_exec(
            'supervisorctl', '-c', SUPERVISORD_CONF, 'restart', SUPERVISOR_PROCESS,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        await asyncio.wait_for(proc.communicate(), timeout=30)
    except Exception as e:
        logging.error(f"Restart failed: {e}")


@require_sudo
async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        import subprocess
        result = subprocess.run(
            ['supervisorctl', '-c', SUPERVISORD_CONF, 'status', SUPERVISOR_PROCESS],
            capture_output=True, text=True, check=True, timeout=10
        )
        await update.message.reply_text(
            f"📊 وضعیت ربات:\n\n{result.stdout.strip()}\n\n"
            f"🕐 زمان: {get_tehran_time().strftime('%Y-%m-%d %H:%M:%S')}"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در دریافت وضعیت: {e}")



@require_sudo
async def broadcast_update_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    
    if query.from_user.id != SUDO_USER_ID:
        await query.answer("🚫 فقط Sudo!", show_alert=True)
        return
        
    users = User.get_all_active()
    sent_count = 0
    
    for u in users:
        if u.user_id == SUDO_USER_ID:
            continue
        try:
            await context.bot.send_message(
                chat_id=u.user_id,
                text="⏏️ ربات آپدیت شد!\n\n"
                     "💢 برای دریافت تغییرات جدید منوی اصلی را چک کنید یا دستور /start بزنید."
            )
            sent_count += 1
        except Exception:
            pass
            
    await query.edit_message_text(f"✅ پیام آپدیت با موفقیت به {sent_count} کاربر فعال ارسال شد!")


@require_sudo
async def group_management_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = ProductLine.get_all()
    if not lines:
        await update.message.reply_text("هیچ خط تولیدی وجود ندارد.")
        return

    text_lines = ["⚙️ تنظیم گروه‌ها\n━━━━━━━━━━━━━━━━━━"]
    keyboard = []

    for pl in lines:
        gp = pl.group_products or "❌ تنظیم نشده"
        gpr = pl.group_print or "❌ تنظیم نشده"
        status = "✅" if pl.is_fully_configured() else "⚠️"

        text_lines.append(
            f"\n{status} {pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
            f"  📦 محصولات: {gp}\n"
            f"  🖨 چاپ: {gpr}"
        )
        keyboard.append([
            InlineKeyboardButton(
                f"✏️ {pl.icon} {pl.name_fa}",
                callback_data=f"setgroup_select_{pl.id}"
            )
        ])

    await update.message.reply_text(
        '\n'.join(text_lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


@require_sudo
async def group_management_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("setgroup_select_"):
        pl_id = int(data.split("_")[2])
        pl = ProductLine.get_by_id(pl_id)
        if not pl:
            await query.edit_message_text("❌ خط تولید یافت نشد.")
            return

        keyboard = [
            [InlineKeyboardButton(
                "📦 گروه محصولات", callback_data=f"setgroup_type_{pl_id}_products"
            )],
            [InlineKeyboardButton(
                "🖨 گروه چاپ", callback_data=f"setgroup_type_{pl_id}_print"
            )],
            [InlineKeyboardButton("↩️ بازگشت", callback_data="setgroup_back")]
        ]

        gp = pl.group_products or "تنظیم نشده"
        gpr = pl.group_print or "تنظیم نشده"

        await query.edit_message_text(
            f"{pl.icon} {pl.name_fa}\n\n"
            f"📦 گروه محصولات: {gp}\n"
            f"🖨 گروه چاپ: {gpr}\n\n"
            f"کدام گروه را می‌خواهید تنظیم کنید؟",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif data.startswith("setgroup_type_"):
        parts = data.split("_")
        pl_id = int(parts[2])
        group_type = parts[3]

        pl = ProductLine.get_by_id(pl_id)
        if not pl:
            await query.edit_message_text("❌ خط تولید یافت نشد.")
            return

        context.user_data['awaiting_group_input'] = {
            'pl_id': pl_id,
            'group_type': group_type,
            'pl_name': pl.name_fa,
            'menu_message_id': query.message.message_id
        }

        type_label = "محصولات" if group_type == "products" else "چاپ"
        await query.edit_message_text(
            f"📝 لطفاً Chat ID گروه {type_label} را برای {pl.icon} {pl.name_fa} ارسال کنید.\n\n"
            f"مثال: -1001234567890\n\n"
            f"برای لغو /cancel بزنید."
        )

    elif data == "setgroup_back":
        lines = ProductLine.get_all()
        text_lines = ["⚙️ تنظیم گروه‌ها\n━━━━━━━━━━━━━━━━━━"]
        keyboard = []

        for pl in lines:
            gp = pl.group_products or "❌ تنظیم نشده"
            gpr = pl.group_print or "❌ تنظیم نشده"
            status = "✅" if pl.is_fully_configured() else "⚠️"
            text_lines.append(
                f"\n{status} {pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
                f"  📦 محصولات: {gp}\n"
                f"  🖨 چاپ: {gpr}"
            )
            keyboard.append([
                InlineKeyboardButton(
                    f"✏️ {pl.icon} {pl.name_fa}",
                    callback_data=f"setgroup_select_{pl.id}"
                )
            ])

        await query.edit_message_text(
            '\n'.join(text_lines),
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def handle_group_id_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    pending = context.user_data.get('awaiting_group_input')
    if not pending:
        return False

    if not _verify_sudo_for_group_input(update.effective_user.id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        context.user_data.pop('awaiting_group_input', None)
        return True

    from utils.validators import Validators, ValidationError
    try:
        chat_id = Validators.validate_chat_id(update.message.text.strip())
    except ValidationError as e:
        await update.message.reply_text(
            f"{e}\nبرای لغو /cancel بزنید."
        )
        return True

    pl = ProductLine.get_by_id(pending['pl_id'])
    if not pl:
        await update.message.reply_text("❌ خط تولید یافت نشد.")
        context.user_data.pop('awaiting_group_input', None)
        return True

    group_type = pending['group_type']
    pl.set_group(group_type, chat_id)

    type_label = "محصولات" if group_type == "products" else "چاپ"
    context.user_data.pop('awaiting_group_input', None)

    await update.message.reply_text(
        f"✅ گروه {type_label} برای {pl.icon} {pl.name_fa} تنظیم شد.\n"
        f"Chat ID: {chat_id}"
    )
    return True