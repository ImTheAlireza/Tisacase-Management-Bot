import logging
from typing import Optional
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes

from services.search_service import SearchService
from models.product_line import ProductLine
from models.user import User
from utils.helpers import format_datetime_persian
from utils.enums import DesignStatus


# ===========================================================================
# SEARCH COMMAND - Entry Point
# ===========================================================================

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Entry point for /search command or "🔍 جستجوی پیشرفته" button.
    Shows filter selection menu.
    Only accessible to reviewers, sudo, and privileged users (Nazi).
    """
    user_id = update.effective_user.id
    user = User.get_by_id(user_id)

    if not user or not user.is_active:
        await update.message.reply_text("🚫 دسترسی غیرمجاز.")
        return

    # Check if user has permission (reviewer, sudo, or privileged)
    role = user.get_effective_role()
    is_privileged = User.is_privileged_user(user_id)

    if role not in ['sudo', 'reviewer'] and not is_privileged:
        await update.message.reply_text("🚫 این بخش فقط برای ناظران و مدیران است.")
        return

    # Initialize search state
    context.user_data['search_filters'] = {
        'code_pattern': None,
        'status': None,
        'date_range': 'all',
        'product_line_id': None,
        'page': 0
    }

    await _show_filter_menu(update, context)


# ===========================================================================
# FILTER MENU
# ===========================================================================

async def _show_filter_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show filter selection menu with current filters."""
    filters = context.user_data.get('search_filters', {})

    # Build filter status text
    filter_text = "🔍 جستجوی طرح\n━━━━━━━━━━━━━━━━━━\n\n"
    filter_text += "📋 فیلترهای فعال:\n\n"

    code_status = filters.get('code_pattern') or "همه"
    filter_text += f"🔖 کد: {code_status}\n"

    status_map = {
        'pending': 'در انتظار',
        'approved': 'تایید شده',
        'rejected': 'رد شده',
        'deleted': 'حذف شده',
        None: 'همه'
    }
    status_display = status_map.get(filters.get('status'), 'همه')
    filter_text += f"📊 وضعیت: {status_display}\n"

    date_map = {
        'today': 'امروز',
        'week': 'این هفته',
        'month': 'این ماه',
        'all': 'همه'
    }
    date_display = date_map.get(filters.get('date_range', 'all'), 'همه')
    filter_text += f"📅 زمان: {date_display}\n"

    if filters.get('product_line_id'):
        pl = ProductLine.get_by_id(filters['product_line_id'])
        filter_text += f"📦 خط تولید: {pl.icon} {pl.name_fa}\n" if pl else "📦 خط تولید: نامشخص\n"
    else:
        filter_text += f"📦 خط تولید: همه\n"

    filter_text += "\n💡 فیلتر مورد نظر را انتخاب کنید یا جستجو کنید:"

    keyboard = [
        [
            InlineKeyboardButton("🔖 کد", callback_data="search_filter_code"),
            InlineKeyboardButton("📊 وضعیت", callback_data="search_filter_status")
        ],
        [
            InlineKeyboardButton("📅 زمان", callback_data="search_filter_date"),
            InlineKeyboardButton("📦 خط تولید", callback_data="search_filter_line")
        ],
        [
            InlineKeyboardButton("🔍 جستجو", callback_data="search_execute"),
            InlineKeyboardButton("🗑 پاک کردن فیلترها", callback_data="search_clear")
        ],
        [InlineKeyboardButton("❌ لغو", callback_data="search_cancel")]
    ]

    markup = InlineKeyboardMarkup(keyboard)

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                text=filter_text,
                reply_markup=markup
            )
        except Exception:
            await update.callback_query.message.reply_text(
                text=filter_text,
                reply_markup=markup
            )
    else:
        await update.message.reply_text(
            text=filter_text,
            reply_markup=markup
        )


# ===========================================================================
# FILTER CALLBACKS
# ===========================================================================

async def search_filter_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle filter selection callbacks."""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == "search_filter_code":
        await _show_code_filter(update, context)

    elif data == "search_filter_status":
        await _show_status_filter(update, context)

    elif data == "search_filter_date":
        await _show_date_filter(update, context)

    elif data == "search_filter_line":
        await _show_line_filter(update, context)

    elif data == "search_execute":
        await _execute_search(update, context)

    elif data == "search_clear":
        context.user_data['search_filters'] = {
            'code_pattern': None,
            'status': None,
            'date_range': 'all',
            'product_line_id': None,
            'page': 0
        }
        await _show_filter_menu(update, context)

    elif data == "search_cancel":
        context.user_data.pop('search_filters', None)
        context.user_data.pop('awaiting_search_code', None)
        await query.edit_message_text("❌ جستجو لغو شد.")

    elif data.startswith("search_set_status_"):
        status = data.replace("search_set_status_", "")
        if status == "all":
            context.user_data['search_filters']['status'] = None
        else:
            context.user_data['search_filters']['status'] = status
        await _show_filter_menu(update, context)

    elif data.startswith("search_set_date_"):
        date_range = data.replace("search_set_date_", "")
        context.user_data['search_filters']['date_range'] = date_range
        await _show_filter_menu(update, context)

    elif data.startswith("search_set_line_"):
        if data == "search_set_line_all":
            context.user_data['search_filters']['product_line_id'] = None
        else:
            line_id = int(data.replace("search_set_line_", ""))
            context.user_data['search_filters']['product_line_id'] = line_id
        await _show_filter_menu(update, context)

    elif data.startswith("search_page_"):
        page = int(data.replace("search_page_", ""))
        context.user_data['search_filters']['page'] = page
        await _execute_search(update, context)

    elif data.startswith("search_view_"):
        code = data.replace("search_view_", "")
        await _show_design_details(update, context, code)


async def _show_code_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask user to input code pattern."""
    context.user_data['awaiting_search_code'] = True
    await update.callback_query.edit_message_text(
        "🔖 کد طرح را وارد کنید:\n\n"
        "مثال:\n"
        "• TS001 - جستجوی دقیق\n"
        "• TS - همه کدهای TS\n\n"
        "برای پاک کردن فیلتر: -\n"
        "برای لغو: /cancel"
    )


async def _show_status_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show status selection menu."""
    keyboard = [
        [InlineKeyboardButton("⏳ در انتظار", callback_data="search_set_status_pending")],
        [InlineKeyboardButton("✅ تایید شده", callback_data="search_set_status_approved")],
        [InlineKeyboardButton("❌ رد شده", callback_data="search_set_status_rejected")],
        [InlineKeyboardButton("🗑 حذف شده", callback_data="search_set_status_deleted")],
        [InlineKeyboardButton("📋 همه", callback_data="search_set_status_all")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="search_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "📊 وضعیت مورد نظر را انتخاب کنید:",
        reply_markup=markup
    )


async def _show_date_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show date range selection menu."""
    keyboard = [
        [InlineKeyboardButton("📅 امروز", callback_data="search_set_date_today")],
        [InlineKeyboardButton("📅 این هفته", callback_data="search_set_date_week")],
        [InlineKeyboardButton("📅 این ماه", callback_data="search_set_date_month")],
        [InlineKeyboardButton("📅 همه", callback_data="search_set_date_all")],
        [InlineKeyboardButton("🔙 بازگشت", callback_data="search_back")]
    ]
    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "📅 محدوده زمانی را انتخاب کنید:",
        reply_markup=markup
    )


async def _show_line_filter(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Show product line selection menu."""
    lines = ProductLine.get_all_active()
    keyboard = []

    for line in lines:
        keyboard.append([
            InlineKeyboardButton(
                f"{line.icon} {line.name_fa}",
                callback_data=f"search_set_line_{line.id}"
            )
        ])

    keyboard.append([InlineKeyboardButton("📋 همه", callback_data="search_set_line_all")])
    keyboard.append([InlineKeyboardButton("🔙 بازگشت", callback_data="search_back")])

    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        "📦 خط تولید را انتخاب کنید:",
        reply_markup=markup
    )


async def search_back_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle back button."""
    await update.callback_query.answer()
    await _show_filter_menu(update, context)


# ===========================================================================
# TEXT INPUT HANDLER
# ===========================================================================

async def handle_search_code_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Handle text input for code filter.
    Returns True if consumed, False otherwise.
    """
    if not context.user_data.get('awaiting_search_code'):
        return False

    context.user_data.pop('awaiting_search_code', None)
    text = update.message.text.strip().upper()

    if text == "-":
        # Clear code filter
        context.user_data['search_filters']['code_pattern'] = None
        await update.message.reply_text("✅ فیلتر کد پاک شد.")
    else:
        context.user_data['search_filters']['code_pattern'] = text
        await update.message.reply_text(f"✅ فیلتر کد: {text}")

    # Show filter menu again
    await _show_filter_menu(update, context)
    return True


# ===========================================================================
# EXECUTE SEARCH
# ===========================================================================

async def _execute_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Execute search and show results."""
    filters = context.user_data.get('search_filters', {})
    page = filters.get('page', 0)
    limit = 10

    await update.callback_query.edit_message_text("⏳ در حال جستجو...")

    try:
        # Get date range
        date_from, date_to = None, None
        date_range = filters.get('date_range', 'all')
        if date_range != 'all':
            date_from, date_to = SearchService.get_quick_date_range(date_range)

        # Execute search
        results, total_count = SearchService.search_designs(
            code_pattern=filters.get('code_pattern'),
            status=filters.get('status'),
            date_from=date_from,
            date_to=date_to,
            product_line_id=filters.get('product_line_id'),
            offset=page * limit,
            limit=limit
        )

        if not results:
            await update.callback_query.edit_message_text(
                "❌ نتیجه‌ای یافت نشد.\n\n"
                "💡 فیلترها را تغییر دهید و دوباره جستجو کنید.",
                reply_markup=InlineKeyboardMarkup([[
                    InlineKeyboardButton("🔙 بازگشت به فیلترها", callback_data="search_back")
                ]])
            )
            return

        await _show_results(update, context, results, total_count, page, limit)

    except Exception as e:
        logging.error(f"Search execution failed: {e}")
        await update.callback_query.edit_message_text(
            f"❌ خطا در جستجو: {e}",
            reply_markup=InlineKeyboardMarkup([[
                InlineKeyboardButton("🔙 بازگشت", callback_data="search_back")
            ]])
        )


async def _show_results(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    results: list,
    total_count: int,
    page: int,
    limit: int
) -> None:
    """Show search results with pagination."""
    start = page * limit + 1
    end = min((page + 1) * limit, total_count)

    text = f"🔍 نتایج جستجو\n━━━━━━━━━━━━━━━━━━\n\n"
    text += f"📊 {start}-{end} از {total_count} نتیجه\n\n"

    status_emoji = {
        'pending': '⏳',
        'approved': '✅',
        'rejected': '❌',
        'deleted': '🗑'
    }

    for r in results:
        emoji = status_emoji.get(r['status'], '•')
        # Clean rejected codes for display
        display_code = r['code'].split('_REJ_')[0] if r['status'] == 'rejected' else r['code']
        status_suffix = " (Rejected)" if r['status'] == 'rejected' else ""
        text += f"{emoji} {display_code}{status_suffix} | {r['product_icon']} {r['product_name']}\n"
        text += f"   👤 {r['editor_name']}"
        if r['status'] in ['approved', 'rejected'] and r['reviewer_name']:
            text += f" | ✓ {r['reviewer_name']}"
        text += f"\n   🕐 {format_datetime_persian(r['created_at'])}\n\n"

    # Build keyboard with result codes
    keyboard = []
    row = []
    for r in results:
        # Clean rejected codes for button display
        display_code = r['code'].split('_REJ_')[0] if r['status'] == 'rejected' else r['code']
        row.append(InlineKeyboardButton(display_code, callback_data=f"search_view_{r['code']}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)

    # Pagination buttons
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("◀️ قبلی", callback_data=f"search_page_{page-1}"))
    if end < total_count:
        nav_row.append(InlineKeyboardButton("بعدی ▶️", callback_data=f"search_page_{page+1}"))
    if nav_row:
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton("🔙 بازگشت به فیلترها", callback_data="search_back")])

    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=markup
    )


async def _show_design_details(update: Update, context: ContextTypes.DEFAULT_TYPE, code: str) -> None:
    """Show brief design details from search results."""
    from models.design import Design

    design = Design.get_by_code(code)
    if not design:
        await update.callback_query.answer("❌ طرح یافت نشد", show_alert=True)
        return

    from models.product_line import ProductLine
    # ✅ product line may be missing/deactivated — guard before dereferencing
    product_line = ProductLine.get_by_id(design.product_line_id)
    pl_name = f"{product_line.icon} {product_line.name_fa}" if product_line else "نامشخص"

    status_map = {
        DesignStatus.PENDING: '⏳ در انتظار',
        DesignStatus.APPROVED: '✅ تایید شده',
        DesignStatus.REJECTED: '❌ رد شده',
        DesignStatus.DELETED: '🗑 حذف شده'
    }

    text = f"🔍 جزئیات طرح\n━━━━━━━━━━━━━━━━━━\n\n"
    # Clean rejected code for display
    display_code = code.split('_REJ_')[0] if design.status == DesignStatus.REJECTED else code
    text += f"🔖 کد: {display_code}\n"
    text += f"📦 خط تولید: {pl_name}\n"
    text += f"📊 وضعیت: {status_map.get(design.status, design.status)}\n\n"
    text += f"👤 طراح: {design.editor_name}\n"
    text += f"🕐 ثبت: {format_datetime_persian(design.created_at)}\n"

    if design.status in [DesignStatus.APPROVED, DesignStatus.REJECTED]:
        text += f"\n✓ ناظر: {design.reviewer_name}\n"
        text += f"🕐 بررسی: {format_datetime_persian(design.reviewed_at)}\n"

    text += f"\n📎 موکاپ: {len(design.mockup_file_ids)} فایل\n"
    text += f"🖨 چاپ: {len(design.print_file_ids)} فایل\n"

    keyboard = [[InlineKeyboardButton("🔙 بازگشت", callback_data=f"search_page_{context.user_data['search_filters']['page']}")]]
    markup = InlineKeyboardMarkup(keyboard)

    await update.callback_query.edit_message_text(
        text=text,
        reply_markup=markup
    )
