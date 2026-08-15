import json
import logging
from datetime import datetime

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from config.settings import SUDO_USER_ID
from utils.decorators import require_sudo
from utils.callback_lock import deduplicate_callback
from utils.helpers import safe_answer_callback


def _server_bill_key(update, context) -> str:
    return "server_bill"


@require_sudo
@deduplicate_callback(_server_bill_key)
async def server_bill_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)

    if query.data == "server_bill_paid":
        # Mark as paid in bot_data (in-memory for immediate effect)
        context.bot_data['server_bill_active'] = False

        # Persist to database
        try:
            from models.user import User
            user = User.get_by_id(SUDO_USER_ID)
            if user:
                metadata = json.loads(user.metadata) if user.metadata else {}
                metadata['server_bill_paid'] = True
                metadata['server_bill_paid_at'] = datetime.now().isoformat()
                user.metadata = json.dumps(metadata)
                user.save()
        except Exception as e:
            logging.error(f"Failed to save server bill paid status: {e}")

        # Edit message to show paid status
        await query.edit_message_text("✅ صورتحساب سرور پرداخت شد!")


async def send_monthly_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send initial reminder on 12th of month"""
    # Check database for existing paid status this month
    try:
        from models.user import User
        user = User.get_by_id(SUDO_USER_ID)
        if user and user.metadata:
            metadata = json.loads(user.metadata)
            paid_at = metadata.get('server_bill_paid_at')
            if paid_at:
                paid_date = datetime.fromisoformat(paid_at)
                now = datetime.now()
                # If paid this month, don't send reminder
                if paid_date.year == now.year and paid_date.month == now.month:
                    return
    except Exception as e:
        logging.error(f"Failed to check server bill status: {e}")

    # Set reminder as active
    context.bot_data['server_bill_active'] = True

    # Reset paid status for new month
    try:
        from models.user import User
        user = User.get_by_id(SUDO_USER_ID)
        if user:
            metadata = json.loads(user.metadata) if user.metadata else {}
            metadata['server_bill_paid'] = False
            user.metadata = json.dumps(metadata)
            user.save()
    except Exception as e:
        logging.error(f"Failed to reset server bill status: {e}")

    # Build inline keyboard with "paid" button
    keyboard = [[InlineKeyboardButton("✅ پرداخت شد", callback_data="server_bill_paid")]]
    markup = InlineKeyboardMarkup(keyboard)

    # Send to SUDO_USER_ID
    await context.bot.send_message(
        chat_id=SUDO_USER_ID,
        text="💰 یادآوری صورتحساب سرور!\n\nلطفا صورتحساب سرور را پرداخت کنید.",
        reply_markup=markup
    )


async def send_daily_reminder(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send daily reminder if not yet paid"""
    # Check if reminder is active
    if not context.bot_data.get('server_bill_active'):
        return

    # Check database for paid status
    try:
        from models.user import User
        user = User.get_by_id(SUDO_USER_ID)
        if user and user.metadata:
            metadata = json.loads(user.metadata)
            if metadata.get('server_bill_paid'):
                context.bot_data['server_bill_active'] = False
                return
    except Exception as e:
        logging.error(f"Failed to check server bill status: {e}")

    # Build inline keyboard with "paid" button
    keyboard = [[InlineKeyboardButton("✅ پرداخت شد", callback_data="server_bill_paid")]]
    markup = InlineKeyboardMarkup(keyboard)

    # Send reminder
    await context.bot.send_message(
        chat_id=SUDO_USER_ID,
        text="💰 یادآوری صورتحساب سرور!\n\nلطفا صورتحساب سرور را پرداخت کنید.",
        reply_markup=markup
    )
