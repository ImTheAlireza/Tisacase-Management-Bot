import logging
from telegram import Update
from telegram.ext import ContextTypes
from models.user import User
from ui.keyboards import Keyboards
from utils.decorators import require_role
from utils.helpers import get_tehran_time


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start"""
    user_id: int = update.effective_user.id
    user: User | None = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
        return

    user.update_last_active()
    role_name: str = user.get_effective_role().upper()
    now_str: str = get_tehran_time().strftime('%H:%M')

    reply_markup = Keyboards.get_main_menu(user)

    await update.message.reply_text(
        f"سلام {user.first_name} 👋\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 نقش فعال شما: {role_name}\n"
        f"🕐 زمان: {now_str}\n\n"
        f"💡 از دکمه‌های زیر برای مدیریت استفاده کنید.",
        reply_markup=reply_markup
    )


async def cancel_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> None:
    """Cancel current operation and clean all state."""
    from handlers.editor import _clear_editor_state
    from models.design import Design

    # If editor session is active, delete the pending design
    code = context.user_data.get('code')
    if code:
        design = Design.get_by_code(code)
        if design:
            try:
                design.delete()
            except Exception as e:
                logging.warning(f"Could not delete design {code} on cancel: {e}")

    _clear_editor_state(context)
    context.user_data.clear()

    await update.message.reply_text(
        "❌ عملیات لغو شد. به منوی اصلی بازگشتید."
    )