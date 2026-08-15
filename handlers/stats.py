import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from config.settings import SUDO_USER_ID, NAZI_CHAT_ID
from services.stats_service import StatsService
from models.design import Design
from models.user import User
from utils.enums import DesignStatus
from utils.helpers import safe_answer_callback


def is_privileged(user_id):
    """Only sudo and Nazi see the full stats system"""
    # FIX: Use centralized method
    return User.is_privileged_user(user_id)


def _main_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📦 خطوط تولید", callback_data="stats_lines")],
        [InlineKeyboardButton("👥 کاربران",     callback_data="stats_users")],
        [InlineKeyboardButton("🏆 برترین‌ها",   callback_data="stats_top")],
        [InlineKeyboardButton("🤖 سیستم",       callback_data="stats_system")],
    ])


def _back_markup():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("↩️ بازگشت", callback_data="stats_main")]
    ])


def _as_int(value) -> int:
    return int(value or 0)


def _trend(current, previous) -> str:
    """Format current-vs-previous trend with premium up/down emojis."""
    current = _as_int(current)
    previous = _as_int(previous)

    if previous == 0:
        if current == 0:
            return "➖"
        return "🔼 جدید"

    change = ((current - previous) / previous) * 100
    if change > 0:
        return "🔼"
    if change < 0:
        return "🔽"
    return "➖"


def _metric(value, previous) -> str:
    return f"{_as_int(value)} ({_trend(value, previous)})"


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if not is_privileged(user_id):
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    await update.message.reply_text(
        "📊 آمار کلی\n━━━━━━━━━━━━━━━━━━\nیک بخش را انتخاب کنید:",
        reply_markup=_main_markup()
    )


async def stats_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await safe_answer_callback(query)

    if not is_privileged(query.from_user.id):
        await safe_answer_callback(query, "🚫 دسترسی غیرمجاز", show_alert=True)
        return

    action = query.data

    if action == "stats_main":
        await query.edit_message_text(
            "📊 آمار کلی\n━━━━━━━━━━━━━━━━━━\nیک بخش را انتخاب کنید:",
            reply_markup=_main_markup()
        )

    elif action == "stats_lines":
        await query.edit_message_text("⏳ در حال بارگذاری...")
        text = _build_lines_text()
        await query.edit_message_text(text, reply_markup=_back_markup())

    elif action == "stats_users":
        await query.edit_message_text("⏳ در حال بارگذاری...")
        text = _build_users_text()
        await query.edit_message_text(text, reply_markup=_back_markup())

    elif action == "stats_top":
        await query.edit_message_text("⏳ در حال بارگذاری...")
        text = _build_top_text()
        await query.edit_message_text(text, reply_markup=_back_markup())

    elif action == "stats_system":
        await query.edit_message_text("⏳ در حال بارگذاری...")
        text = _build_system_text()
        await query.edit_message_text(text, reply_markup=_back_markup())




def _build_lines_text() -> str:
    try:
        rows = StatsService.get_product_line_stats()
    except Exception as e:
        logging.error(f"Stats lines error: {e}")
        return "❌ خطا در بارگذاری آمار خطوط تولید."

    if not rows:
        return "هیچ خط تولیدی وجود ندارد."

    lines = ["📦 آمار خطوط تولید\n━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        active = "✅" if r['is_active'] else "🔴"

        pending_str = ", ".join(r['pending_codes']) if r['pending_codes'] else "هیچ"
        appr_str = ", ".join(r['recent_approved']) if r['recent_approved'] else "هیچ"
        rej_str = ", ".join(r['recent_rejected']) if r['recent_rejected'] else "هیچ"

        lines.append(
            f"\n{active} {r['icon']} {r['name_fa']} ({r['code_prefix']})\n"
            f"  ⏳ کدهای در انتظار: {pending_str}\n"
            f"  ✅ ۱۰ تایید اخیر: {appr_str}\n"
            f"  ❌ ۱۰ رد اخیر: {rej_str}\n"
            f"  امروز:  ثبت {_metric(r['submitted_today'], r['submitted_yesterday'])} | "
            f"تایید {_metric(r['approved_today'], r['approved_yesterday'])} | "
            f"رد {_metric(r['rejected_today'], r['rejected_yesterday'])}\n"
            f"  هفته:   ثبت {_metric(r['submitted_week'], r['submitted_prev_week'])} | "
            f"تایید {_metric(r['approved_week'], r['approved_prev_week'])} | "
            f"رد {_metric(r['rejected_week'], r['rejected_prev_week'])}\n"
            f"  ماه:    ثبت {_metric(r['submitted_month'], r['submitted_prev_month'])} | "
            f"تایید {_metric(r['approved_month'], r['approved_prev_month'])} | "
            f"رد {_metric(r['rejected_month'], r['rejected_prev_month'])}\n"
            f"  کل:     ثبت {r['total_all']} | تایید {r['approved_all']} | رد {r['rejected_all']} | انتظار {r['pending_all']}\n"
        )
    return '\n'.join(lines)


def _build_users_text() -> str:
    try:
        editors = StatsService.get_editor_stats()
        reviewers = StatsService.get_reviewer_stats()
    except Exception as e:
        logging.error(f"Stats users error: {e}")
        return "❌ خطا در بارگذاری آمار کاربران."

    lines = ["👥 آمار کاربران\n━━━━━━━━━━━━━━━━━━"]

    lines.append("\n🎨 طراحان:")
    if not editors:
        lines.append("  هیچ طرحی ثبت نشده.")
    else:
        for e in editors:
            name = e['editor_name'] or "نامشخص"
            lines.append(
                f"\n  👤 {name}\n"
                f"    امروز:  ثبت {_metric(e['submitted_today'], e['submitted_yesterday'])} | "
                f"تایید {_metric(e['approved_today'], e['approved_yesterday'])} | "
                f"رد {_metric(e['rejected_today'], e['rejected_yesterday'])}\n"
                f"    هفته:   ثبت {_metric(e['submitted_week'], e['submitted_prev_week'])} | "
                f"تایید {_metric(e['approved_week'], e['approved_prev_week'])} | "
                f"رد {_metric(e['rejected_week'], e['rejected_prev_week'])}\n"
                f"    ماه:    ثبت {_metric(e['submitted_month'], e['submitted_prev_month'])} | "
                f"تایید {_metric(e['approved_month'], e['approved_prev_month'])} | "
                f"رد {_metric(e['rejected_month'], e['rejected_prev_month'])}\n"
                f"    کل:     ثبت {e['submitted_all']} | تایید {e['approved_all']} | رد {e['rejected_all']} | انتظار {e['pending_all']}"
            )

    lines.append("\n\n✅ ناظران:")
    if not reviewers:
        lines.append("  هیچ طرحی بررسی نشده.")
    else:
        for r in reviewers:
            name = r['reviewer_name'] or "نامشخص"
            lines.append(
                f"\n  👤 {name}\n"
                f"    امروز:  بررسی {_metric(r['reviewed_today'], r['reviewed_yesterday'])} | "
                f"تایید {_metric(r['approved_today'], r['approved_yesterday'])} | "
                f"رد {_metric(r['rejected_today'], r['rejected_yesterday'])}\n"
                f"    هفته:   بررسی {_metric(r['reviewed_week'], r['reviewed_prev_week'])} | "
                f"تایید {_metric(r['approved_week'], r['approved_prev_week'])} | "
                f"رد {_metric(r['rejected_week'], r['rejected_prev_week'])}\n"
                f"    ماه:    بررسی {_metric(r['reviewed_month'], r['reviewed_prev_month'])} | "
                f"تایید {_metric(r['approved_month'], r['approved_prev_month'])} | "
                f"رد {_metric(r['rejected_month'], r['rejected_prev_month'])}\n"
                f"    کل:     بررسی {r['reviewed_all']} | تایید {r['approved_all']} | رد {r['rejected_all']}"
            )

    return '\n'.join(lines)


def _build_top_text() -> str:
    try:
        top = StatsService.get_top_performers()
    except Exception as e:
        logging.error(f"Stats top error: {e}")
        return "❌ خطا در بارگذاری برترین‌ها."

    lines = ["🏆 برترین‌ها\n━━━━━━━━━━━━━━━━━━"]

    lines.append("\n🎨 پرکارترین طراح:")
    if top['top_editor_all']:
        e = top['top_editor_all']
        lines.append(f"  کل زمان:  {e['editor_name']} — {e['count']} طرح")
    else:
        lines.append("  —")

    if top['top_editor_today']:
        e = top['top_editor_today']
        lines.append(f"  امروز:    {e['editor_name']} — {e['count']} طرح")
    else:
        lines.append("  امروز: هنوز کسی ثبت نکرده")

    lines.append("\n✅ پرکارترین ناظر:")
    if top['top_reviewer_all']:
        r = top['top_reviewer_all']
        lines.append(f"  کل زمان:  {r['reviewer_name']} — {r['count']} بررسی")
    else:
        lines.append("  —")

    if top['top_reviewer_today']:
        r = top['top_reviewer_today']
        lines.append(f"  امروز:    {r['reviewer_name']} — {r['count']} بررسی")
    else:
        lines.append("  امروز: هنوز کسی بررسی نکرده")

    return '\n'.join(lines)


def _build_system_text() -> str:
    try:
        data = StatsService.get_system_stats()
    except Exception as e:
        logging.error(f"Stats system error: {e}")
        return "❌ خطا در بارگذاری اطلاعات سیستم."

    t = data['totals']
    u = data['users']
    uptime = data['uptime']

    lines = [
        "🤖 وضعیت سیستم\n━━━━━━━━━━━━━━━━━━",
        "",
        "📊 کل طرح‌ها:",
        f"  امروز ثبت شده:  {_metric(t['submitted_today'], t['submitted_yesterday'])}",
        f"  ماه جاری:       ثبت {_metric(t['submitted_month'], t['submitted_prev_month'])} | "
        f"تایید {_metric(t['approved_month'], t['approved_prev_month'])} | "
        f"رد {_metric(t['rejected_month'], t['rejected_prev_month'])}",
        f"  در انتظار:       {t[DesignStatus.PENDING]}",
        f"  تایید شده:       {t[DesignStatus.APPROVED]}",
        f"  رد شده:          {t[DesignStatus.REJECTED]}",
        f"  مجموع کل:        {t['total']}",
        "",
        "👥 کاربران فعال:",
        f"  👑 Sudo:      {u.get('sudo', 0)}",
        f"  ✅ ناظر:      {u.get('reviewer', 0)}",
        f"  🎨 طراح:      {u.get('editor', 0)}",
        "",
        f"⏱ آپتایم ربات:  {uptime}",
    ]

    return '\n'.join(lines)
