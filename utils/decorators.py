from functools import wraps
from models.user import User
import logging


def require_role(*allowed_roles):
    """
    Decorator to check if user has required role.
    Fetches a fresh user from DB on every call.
    Sudo always has access to everything regardless of active role.
    """
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            user_id = update.effective_user.id
            user = User.get_by_id(user_id)

            if not user or not user.is_active:
                if update.message:
                    await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
                elif update.callback_query:
                    await update.callback_query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
                return

            effective_role = user.get_effective_role()

            # Sudo always passes role checks regardless of active role
            if not user.is_sudo and effective_role not in allowed_roles:
                msg = "🚫 این بخش فقط برای نقش‌های زیر است:\n" + ", ".join(allowed_roles)
                if update.message:
                    await update.message.reply_text(msg)
                elif update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                return

            context.user_data['db_user'] = user
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def require_sudo(func):
    """
    Decorator that requires the user to be a sudo user.
    Checks user.is_sudo directly — NOT effective_role —
    so sudo can use sudo commands even when acting as editor or reviewer.
    """
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user_id = update.effective_user.id
        user = User.get_by_id(user_id)

        if not user or not user.is_active:
            if update.message:
                await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
            elif update.callback_query:
                await update.callback_query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
            return

        if not user.is_sudo:
            if update.message:
                await update.message.reply_text("🚫 این بخش فقط برای Sudo است.")
            elif update.callback_query:
                await update.callback_query.answer("🚫 فقط Sudo", show_alert=True)
            return

        context.user_data['db_user'] = user
        return await func(update, context, *args, **kwargs)
    return wrapper