from functools import wraps
from typing import Callable
from models.user import User
from utils.rate_limiter import rate_limiter
import logging


def require_role(*allowed_roles: str, rate_limit_action: str = None) -> Callable:
    """
    Decorator to check if user has required role and apply rate limiting.
    Fetches a fresh user from DB on every call.
    Sudo always has access regardless of active role.

    Args:
        allowed_roles: Tuple of allowed role names
        rate_limit_action: Optional rate limit key (from rate_limiter)
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            user_id: int = update.effective_user.id
            user: User | None = User.get_by_id(user_id)

            if not user or not user.is_active:
                if update.message:
                    await update.message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
                elif update.callback_query:
                    await update.callback_query.answer("🚫 دسترسی غیرمجاز", show_alert=True)
                return

            effective_role: str = user.get_effective_role()

            if not user.is_sudo and effective_role not in allowed_roles:
                msg = "🚫 این بخش فقط برای نقش‌های زیر است:\n" + ", ".join(allowed_roles)
                if update.message:
                    await update.message.reply_text(msg)
                elif update.callback_query:
                    await update.callback_query.answer(msg, show_alert=True)
                return

            # Rate limiting (skip for sudo users)
            if rate_limit_action and not user.is_sudo:
                can_proceed, wait_time = rate_limiter.check_rate_limit(user_id, rate_limit_action)
                if not can_proceed:
                    msg = f"⏳ لطفاً {wait_time:.1f} ثانیه صبر کنید"
                    if update.message:
                        await update.message.reply_text(msg)
                    elif update.callback_query:
                        await update.callback_query.answer(msg, show_alert=True)
                    return

            context.user_data['db_user'] = user
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator


def require_sudo(func: Callable) -> Callable:
    """
    Decorator that requires the user to be a sudo user.
    Checks user.is_sudo directly — NOT effective_role.
    """
    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        user_id: int = update.effective_user.id
        user: User | None = User.get_by_id(user_id)

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