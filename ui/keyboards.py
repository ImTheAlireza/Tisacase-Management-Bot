from telegram import InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton
from models.product_line import ProductLine
from models.user import User
from utils.enums import DesignStatus


class Keyboards:

    @staticmethod
    def get_main_menu(user):
        role = user.get_effective_role()
        product_lines = ProductLine.get_all_active()
        keyboard = []
        # FIX: Use centralized method
        privileged = User.is_privileged_user(user.user_id)

        # Design submission buttons — editors and sudo
        if role in ['sudo', 'editor']:
            row = []
            for pl in product_lines:
                row.append(KeyboardButton(f"➕ {pl.icon} ثبت {pl.name_fa}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

        # Per-line stats — everyone
        row = []
        for pl in product_lines:
            row.append(KeyboardButton(f"📊 آمار {pl.name_fa}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Pending + overall stats — reviewers and sudo
        if role in ['sudo', 'reviewer']:
            if privileged:
                keyboard.append([
                    KeyboardButton("📋 طرح‌های در انتظار"),
                    KeyboardButton("📊 آمار کلی")
                ])
            else:
                keyboard.append([KeyboardButton("📋 طرح‌های در انتظار")])

        # Design management — privileged only (sudo + nazi)
        if privileged:
            keyboard.append([
                KeyboardButton("🔍 اطلاعات طرح"),
                KeyboardButton("🗑 حذف طرح")
            ])

        # Sudo control panel
        if user.is_sudo:
            keyboard.append([
                KeyboardButton("👑 تغییر نقش"),
                KeyboardButton("💾 بکاپ")
            ])
            keyboard.append([
                KeyboardButton("🔄 ریستارت"),
                KeyboardButton("⚙️ تنظیم گروه‌ها")
            ])
            keyboard.append([
                KeyboardButton("📊 وضعیت"),
                KeyboardButton("📖 راهنما")
            ])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    @staticmethod
    def get_design_menu(code, product_name, mockup_count, print_count):
        text = (
            f"طرح {product_name} — کد: *{code}*\n\n"
            f"📊 وضعیت فعلی:\n"
            f"🖼 موکاپ: {mockup_count} {'✅' if mockup_count > 0 else '❌'}\n"
            f"🖨 فایل چاپی: {print_count} {'✅' if print_count > 0 else '❌'}"
        )

        buttons = [
            [
                InlineKeyboardButton(
                    f"🖼 ویرایش موکاپ ({mockup_count})" if mockup_count > 0 else "🖼 افزودن موکاپ",
                    callback_data="add_mockup"
                ),
                InlineKeyboardButton(
                    f"🖨 ویرایش فایل چاپی ({print_count})" if print_count > 0 else "🖨 افزودن فایل چاپی",
                    callback_data="add_print"
                )
            ]
        ]

        if mockup_count > 0 and print_count > 0:
            buttons.append([
                InlineKeyboardButton("✅ ثبت نهایی و ارسال", callback_data="confirm_submit")
            ])

        buttons.append([InlineKeyboardButton("🗑 لغو و حذف", callback_data="cancel_submission")])

        return text, InlineKeyboardMarkup(buttons)