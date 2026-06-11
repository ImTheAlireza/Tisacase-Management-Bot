from telegram import Update
from telegram.ext import ContextTypes
from models.user import User
from ui.keyboards import Keyboards
from utils.decorators import require_role
from utils.helpers import get_tehran_time

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start"""
    user_id = update.effective_user.id
    user = User.get_by_id(user_id)
    
    if not user or not user.is_active:
        await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
        return
        
    user.update_last_active()
    role_name = user.get_effective_role().upper()
    now_str = get_tehran_time().strftime('%H:%M')
    
    reply_markup = Keyboards.get_main_menu(user)
    
    await update.message.reply_text(
        f"سلام {user.first_name} 👋\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 نقش فعال شما: {role_name}\n"
        f"🕐 زمان: {now_str}\n\n"
        f"💡 از دکمه‌های زیر برای مدیریت استفاده کنید.",
        reply_markup=reply_markup
    )

async def cancel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel current operation"""
    context.user_data.clear()
    await update.message.reply_text("✅ عملیات لغو شد. به منوی اصلی بازگشتید.")