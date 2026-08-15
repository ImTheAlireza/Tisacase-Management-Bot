import logging
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from models.user import User
from models.product_line import ProductLine
from services.activity_service import ActivityService
from utils.helpers import format_datetime_persian, safe_answer_callback


def _is_privileged(user_id: int) -> bool:
    return User.is_privileged_user(user_id)


async def reset_stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show the reset stats menu"""
    user_id = update.effective_user.id
    user = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    is_priv = _is_privileged(user_id)

    # Build menu based on role
    sections = []

    # Editors section
    if is_priv:
        editors = User.get_by_role('editor')
        # Also include sudo users who might have editor designs
        editors += [u for u in User.get_all_active() if u.is_sudo and u.user_id not in [e.user_id for e in editors]]
    else:
        # Editors can only reset their own stats
        editors = [user] if user.role == 'editor' else []

    if editors:
        buttons = []
        for e in editors:
            reset_info = ""
            if e.stats_reset_at:
                reset_info = f" (از {format_datetime_persian(e.stats_reset_at)})"
            buttons.append([InlineKeyboardButton(
                f"🎨 {e.first_name}{reset_info}",
                callback_data=f"reset_editor_{e.user_id}"
            )])
        sections.append(("🎨 بازنشانی آمار طراح:", buttons))

    # Reviewers section
    if is_priv:
        reviewers = User.get_by_role('reviewer')
        reviewers += [u for u in User.get_all_active() if u.is_sudo and u.user_id not in [r.user_id for r in reviewers]]
    else:
        reviewers = [user] if user.role == 'reviewer' else []

    if reviewers:
        buttons = []
        for r in reviewers:
            reset_info = ""
            if r.stats_reset_at:
                reset_info = f" (از {format_datetime_persian(r.stats_reset_at)})"
            buttons.append([InlineKeyboardButton(
                f"✅ {r.first_name}{reset_info}",
                callback_data=f"reset_reviewer_{r.user_id}"
            )])
        sections.append(("✅ بازنشانی آمار ناظر:", buttons))

    # Product lines section (privileged only)
    if is_priv:
        lines = ProductLine.get_all()
        if lines:
            buttons = []
            for pl in lines:
                reset_info = ""
                if pl.stats_reset_at:
                    reset_info = f" (از {format_datetime_persian(pl.stats_reset_at)})"
                buttons.append([InlineKeyboardButton(
                    f"{pl.icon} {pl.name_fa}{reset_info}",
                    callback_data=f"reset_line_{pl.id}"
                )])
            sections.append(("📦 بازنشانی آمار خط تولید:", buttons))

    if not sections:
        await update.message.reply_text("هیچ بخشی برای بازنشانی وجود ندارد.")
        return

    # Build message
    text_lines = [
        "🔄 بازنشانی آمار",
        "━━━━━━━━━━━━━━━━━━",
        "",
        "⚠️ هیچ داده‌ای حذف نمی‌شود.",
        "فقط آمار از این لحظه به بعد نمایش داده می‌شود.",
    ]

    keyboard = []
    for section_title, buttons in sections:
        text_lines.append(f"\n{section_title}")
        keyboard.extend(buttons)

    # Back button
    keyboard.append([InlineKeyboardButton("↩️ بازگشت", callback_data="reset_stats_back")])

    await update.message.reply_text(
        '\n'.join(text_lines),
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def reset_stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle reset stats callbacks"""
    query = update.callback_query
    await safe_answer_callback(query)

    user_id = query.from_user.id
    data = query.data

    if data == "reset_stats_back":
        await query.edit_message_text(
            "🔄 بازنشانی آمار\n━━━━━━━━━━━━━━━━━━\nاز منوی اصلی دوباره شروع کنید."
        )
        return

    # Check permission
    is_priv = _is_privileged(user_id)

    if data.startswith("reset_editor_"):
        target_id = int(data.split("_")[2])
        if not is_priv and user_id != target_id:
            await safe_answer_callback(query, "🚫 فقط می‌توانید آمار خودتان را بازنشانی کنید", show_alert=True)
            return

        target = User.get_by_id(target_id)
        if not target:
            await safe_answer_callback(query, "❌ کاربر یافت نشد", show_alert=True)
            return

        # Show confirmation
        reset_info = ""
        if target.stats_reset_at:
            reset_info = f"\n\n📊 بازنشانی قبلی: {format_datetime_persian(target.stats_reset_at)}"

        await query.edit_message_text(
            f"⚠️ بازنشانی آمار طراح\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {target.first_name} ({target.user_id})\n"
            f"{reset_info}\n\n"
            f"آیا مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، بازنشانی کن", callback_data=f"confirm_reset_editor_{target_id}"),
                InlineKeyboardButton("❌ انصراف", callback_data="reset_stats_cancel"),
            ]])
        )

    elif data.startswith("reset_reviewer_"):
        target_id = int(data.split("_")[2])
        if not is_priv and user_id != target_id:
            await safe_answer_callback(query, "🚫 فقط می‌توانید آمار خودتان را بازنشانی کنید", show_alert=True)
            return

        target = User.get_by_id(target_id)
        if not target:
            await safe_answer_callback(query, "❌ کاربر یافت نشد", show_alert=True)
            return

        reset_info = ""
        if target.stats_reset_at:
            reset_info = f"\n\n📊 بازنشانی قبلی: {format_datetime_persian(target.stats_reset_at)}"

        await query.edit_message_text(
            f"⚠️ بازنشانی آمار ناظر\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {target.first_name} ({target.user_id})\n"
            f"{reset_info}\n\n"
            f"آیا مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، بازنشانی کن", callback_data=f"confirm_reset_reviewer_{target_id}"),
                InlineKeyboardButton("❌ انصراف", callback_data="reset_stats_cancel"),
            ]])
        )

    elif data.startswith("reset_line_"):
        if not is_priv:
            await safe_answer_callback(query, "🚫 فقط Sudo و Nazi", show_alert=True)
            return

        line_id = int(data.split("_")[2])
        pl = ProductLine.get_by_id(line_id)
        if not pl:
            await safe_answer_callback(query, "❌ خط تولید یافت نشد", show_alert=True)
            return

        reset_info = ""
        if pl.stats_reset_at:
            reset_info = f"\n\n📊 بازنشانی قبلی: {format_datetime_persian(pl.stats_reset_at)}"

        await query.edit_message_text(
            f"⚠️ بازنشانی آمار خط تولید\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
            f"{reset_info}\n\n"
            f"آیا مطمئن هستید؟",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("✅ بله، بازنشانی کن", callback_data=f"confirm_reset_line_{line_id}"),
                InlineKeyboardButton("❌ انصراف", callback_data="reset_stats_cancel"),
            ]])
        )

    elif data.startswith("confirm_reset_editor_"):
        target_id = int(data.split("_")[3])
        target = User.get_by_id(target_id)
        if not target:
            await query.edit_message_text("❌ کاربر یافت نشد.")
            return

        ActivityService.reset_user_stats(target_id)
        await query.edit_message_text(
            f"✅ آمار طراح بازنشانی شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {target.first_name} ({target.user_id})\n"
            f"🕐 زمان بازنشانی: {format_datetime_persian(datetime.now())}\n\n"
            f"📊 آمار قبلی هنوز در دیتابیس موجود است\n"
            f"فقط از نمایش آمار مخفی شده است."
        )

    elif data.startswith("confirm_reset_reviewer_"):
        target_id = int(data.split("_")[3])
        target = User.get_by_id(target_id)
        if not target:
            await query.edit_message_text("❌ کاربر یافت نشد.")
            return

        ActivityService.reset_user_stats(target_id)
        await query.edit_message_text(
            f"✅ آمار ناظر بازنشانی شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 {target.first_name} ({target.user_id})\n"
            f"🕐 زمان بازنشانی: {format_datetime_persian(datetime.now())}\n\n"
            f"📊 آمار قبلی هنوز در دیتابیس موجود است\n"
            f"فقط از نمایش آمار مخفی شده است."
        )

    elif data.startswith("confirm_reset_line_"):
        line_id = int(data.split("_")[3])
        pl = ProductLine.get_by_id(line_id)
        if not pl:
            await query.edit_message_text("❌ خط تولید یافت نشد.")
            return

        ActivityService.reset_line_stats(line_id)
        await query.edit_message_text(
            f"✅ آمار خط تولید بازنشانی شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{pl.icon} {pl.name_fa} ({pl.code_prefix})\n"
            f"🕐 زمان بازنشانی: {format_datetime_persian(datetime.now())}\n\n"
            f"📊 آمار قبلی هنوز در دیتابیس موجود است\n"
            f"فقط از نمایش آمار مخفی شده است."
        )

    elif data == "reset_stats_cancel":
        await query.edit_message_text("❌ عملیات لغو شد.")
