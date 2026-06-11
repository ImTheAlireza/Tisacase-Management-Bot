from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.decorators import require_sudo


# ---------------------------------------------------------------------------
# Help content — each category as a dict with text and optional back button
# ---------------------------------------------------------------------------

HELP_CATEGORIES = {
    'main': {
        'text': (
            "📖 راهنمای Sudo\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "یک دسته را انتخاب کنید:"
        ),
        'buttons': [
            [InlineKeyboardButton("👥 مدیریت کاربران", callback_data="help_users")],
            [InlineKeyboardButton("📦 مدیریت خطوط تولید", callback_data="help_lines")],
            [InlineKeyboardButton("⚙️ تنظیم گروه‌ها", callback_data="help_groups")],
            [InlineKeyboardButton("🔒 مدیریت کدها", callback_data="help_codes")],
            [InlineKeyboardButton("🛠 سیستم", callback_data="help_system")],
        ]
    },
    'users': {
        'text': (
            "👥 مدیریت کاربران\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "➕ افزودن کاربر جدید:\n"
            "دستور: /adduser\n"
            "فرمت: /adduser {user_id} {role} {name}\n"
            "نقش‌ها: editor یا reviewer\n"
            "مثال: /adduser 123456789 editor علی\n\n"
            "➖ غیرفعال کردن کاربر:\n"
            "دستور: /removeuser {user_id}\n"
            "مثال: /removeuser 123456789\n\n"
            "📋 مشاهده همه کاربران:\n"
            "دستور: /listusers\n"
            "لیست همه کاربران فعال با نقش آن‌ها\n\n"
            "🔄 تغییر نقش کاربر:\n"
            "دستور: /setrole {user_id} {role}\n"
            "مثال: /setrole 123456789 reviewer\n\n"
            "⚠️ نکته: غیرفعال کردن کاربر باعث حذف او نمی‌شود.\n"
            "داده‌های تاریخی (طرح‌های ثبت شده) حفظ می‌شوند."
        ),
        'back': 'main'
    },
    'lines': {
        'text': (
            "📦 مدیریت خطوط تولید\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "📋 مشاهده همه خطوط:\n"
            "دستور: /listlines\n"
            "نمایش همه خطوط با وضعیت و گروه‌های تنظیم شده\n\n"
            "➕ افزودن خط تولید جدید:\n"
            "دستور: /addline\n"
            "فرمت: /addline {prefix} {name_fa} {icon}\n"
            "مثال: /addline MG ماگ ☕\n"
            "پیشوند باید منحصربه‌فرد باشد (حروف لاتین بزرگ)\n\n"
            "🔴 غیرفعال کردن خط:\n"
            "دستور: /disableline {prefix}\n"
            "مثال: /disableline MG\n"
            "طرح‌های موجود حفظ می‌شوند ولی ثبت جدید امکان‌پذیر نیست\n\n"
            "🟢 فعال کردن خط:\n"
            "دستور: /enableline {prefix}\n"
            "مثال: /enableline MG\n\n"
            "⚙️ تنظیم گروه‌های هر خط:\n"
            "از دکمه ⚙️ تنظیم گروه‌ها در منوی اصلی استفاده کنید"
        ),
        'back': 'main'
    },
    'groups': {
        'text': (
            "⚙️ تنظیم گروه‌ها\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "هر خط تولید به دو گروه جداگانه نیاز دارد:\n"
            "📦 گروه محصولات: موکاپ‌های تایید شده اینجا ارسال می‌شوند\n"
            "🖨 گروه چاپ: فایل‌های چاپی اینجا ارسال می‌شوند\n\n"
            "روش تنظیم:\n"
            "۱. دکمه ⚙️ تنظیم گروه‌ها را بزنید\n"
            "۲. خط تولید مورد نظر را انتخاب کنید\n"
            "۳. نوع گروه را انتخاب کنید (محصولات یا چاپ)\n"
            "۴. Chat ID گروه را ارسال کنید\n\n"
            "روش پیدا کردن Chat ID:\n"
            "ربات را به گروه اضافه کنید، سپس یک پیام بفرستید.\n"
            "Chat ID معمولاً با -100 شروع می‌شود.\n"
            "مثال: -1001234567890\n\n"
            "⚠️ تا زمانی که گروه‌های یک خط تنظیم نشده باشند،\n"
            "هیچ طراحی نمی‌تواند برای آن خط ثبت کند."
        ),
        'back': 'main'
    },
    'codes': {
        'text': (
            "🔒 مدیریت کدها\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "🔒 قفل دستی یک کد (رزرو):\n"
            "دستور: /lockcode {code}\n"
            "مثال: /lockcode TS050\n"
            "کد قفل شده در کد‌گذاری بعدی رد می‌شود\n\n"
            "🔓 آزاد کردن کد قفل شده:\n"
            "دستور: /unlockcode {code}\n"
            "مثال: /unlockcode TS050\n"
            "فقط کدهای قفل دستی قابل آزادسازی هستند\n"
            "(کدهای تایید شده قابل آزادسازی نیستند)\n\n"
            "📋 مشاهده کدهای قفل شده:\n"
            "دستور: /lockedcodes\n"
            "دستور: /lockedcodes {prefix} (فیلتر بر اساس خط)\n"
            "مثال: /lockedcodes TS\n\n"
            "ℹ️ توضیح وضعیت کدها:\n"
            "• pending: ثبت شده، منتظر تایید\n"
            "• approved: تایید شده، کد قفل شده\n"
            "• rejected: رد شده، کد همچنان بلاک است\n"
            "• locked (manual): رزرو دستی توسط Sudo"
        ),
        'back': 'main'
    },
    'system': {
        'text': (
            "🛠 سیستم\n"
            "━━━━━━━━━━━━━━━━━━\n\n"
            "👑 تغییر نقش فعال:\n"
            "دکمه: 👑 تغییر نقش\n"
            "می‌توانید به عنوان Editor یا Reviewer عمل کنید\n"
            "دسترسی Sudo همیشه حفظ می‌شود\n\n"
            "💾 بکاپ دستی:\n"
            "دکمه: 💾 بکاپ\n"
            "یک فایل ZIP شامل dump کامل دیتابیس برای شما ارسال می‌شود\n\n"
            "📋 لاگ روزانه:\n"
            "هر شب ساعت ۲۳:۵۹ لاگ تیم + بکاپ به صورت خودکار ارسال می‌شود\n"
            "می‌توانید لاگ را با دکمه به ناظرها فوروارد کنید\n\n"
            "🔄 ریستارت ربات:\n"
            "دکمه: 🔄 ریستارت\n"
            "نیاز به تایید دارد. ربات از طریق supervisorctl ریستارت می‌شود\n\n"
            "🚨 خطاهای سیستمی:\n"
            "هر خطای سطح ERROR به گروه لاگ ارسال می‌شود"
        ),
        'back': 'main'
    }
}


def _build_markup(category_key):
    """Build inline keyboard for a help category"""
    category = HELP_CATEGORIES[category_key]
    buttons = list(category.get('buttons', []))

    if 'back' in category:
        buttons.append([InlineKeyboardButton("↩️ بازگشت", callback_data=f"help_{category['back']}")])

    return InlineKeyboardMarkup(buttons)


@require_sudo
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send the main help menu"""
    cat = HELP_CATEGORIES['main']
    await update.message.reply_text(
        cat['text'],
        reply_markup=_build_markup('main')
    )


@require_sudo
async def help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle navigation within the help menu"""
    query = update.callback_query
    await query.answer()

    # callback_data format: help_{category}
    category_key = query.data.split('_', 1)[1]

    if category_key not in HELP_CATEGORIES:
        await query.answer("❌ دسته‌بندی یافت نشد", show_alert=True)
        return

    cat = HELP_CATEGORIES[category_key]
    await query.edit_message_text(
        cat['text'],
        reply_markup=_build_markup(category_key)
    )