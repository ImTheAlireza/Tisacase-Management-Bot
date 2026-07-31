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
        privileged = User.is_privileged_user(user.user_id)

        # Design submission buttons — editors only
        if role == 'editor':
            row = []
            for pl in product_lines:
                row.append(KeyboardButton(f"➕ {pl.icon} ثبت {pl.name_fa}"))
                if len(row) == 2:
                    keyboard.append(row)
                    row = []
            if row:
                keyboard.append(row)

            # My designs button for editors
            keyboard.append([KeyboardButton("📋 طرح‌های من")])

            # Reset stats for editors
            keyboard.append([KeyboardButton("🔄 بازنشانی آمار")])

        # Per-line stats — everyone
        row = []
        for pl in product_lines:
            row.append(KeyboardButton(f"📊 آمار {pl.name_fa}"))
            if len(row) == 2:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)

        # Pending + overall stats — reviewers only
        if role == 'reviewer':
            keyboard.append([KeyboardButton("📋 طرح‌های در انتظار")])
        if privileged:
            keyboard.append([KeyboardButton("📊 آمار کلی")])

        # Advanced search — reviewers, sudo and Nazi only
        if role in ['sudo', 'reviewer'] or privileged:
            keyboard.append([KeyboardButton("🔍 جستجوی پیشرفته")])

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
                KeyboardButton("💾 بکاپ"),
                KeyboardButton("🔧 ریستور")
            ])
            keyboard.append([
                KeyboardButton("🔄 ریستارت"),
                KeyboardButton("⚙️ تنظیم گروه‌ها")
            ])
            keyboard.append([
                KeyboardButton("📊 وضعیت"),
                KeyboardButton("🔄 بازنشانی آمار"),
                KeyboardButton("📖 راهنما")
            ])

        return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)


    @staticmethod
    def get_mockup_stage(
        code: str,
        product_name: str,
        mockup_count: int,
        is_edit: bool = False
    ) -> tuple[str, InlineKeyboardMarkup]:
        """Stage 1: Mockup upload screen"""

        count_line = (
            f"📊 تعداد فعلی: {mockup_count} فایل"
            if mockup_count > 0
            else "📊 هنوز فایلی ارسال نشده"
        )

        text = (
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 *{code}* | {product_name}\n"
            f"🎨 آپلود موکاپ\n\n"
            f"{count_line}\n\n"
            f"⬇️ فایل موکاپ رو بفرستید یا یکی از گزینه‌ها رو انتخاب کنید\n"
            f"━━━━━━━━━━━━━━━━"
        )

        buttons = [
            [InlineKeyboardButton(
                "✅ اتمام ثبت موکاپ",
                callback_data="stage_mockup_done"
            )],
            [InlineKeyboardButton(
                "🗑 پاکسازی لیست و ارسال دوباره",
                callback_data="stage_mockup_clear"
            )],
            [InlineKeyboardButton(
                "❌ لغو ویرایش" if is_edit else "❌ لغو طرح",
                callback_data="cancel_editing" if is_edit else "cancel_submission"
            )],
        ]

        return text, InlineKeyboardMarkup(buttons)


    @staticmethod
    def get_print_stage(
        code: str,
        product_name: str,
        mockup_count: int,
        print_count: int,
        is_edit: bool = False
    ) -> tuple[str, InlineKeyboardMarkup]:
        """Stage 2: Print file upload screen"""

        count_line = (
            f"📊 تعداد فعلی: {print_count} فایل"
            if print_count > 0
            else "📊 هنوز فایلی ارسال نشده"
        )

        text = (
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 *{code}* | {product_name}\n"
            f"🖨 آپلود فایل چاپی\n\n"
            f"🎨 موکاپ ثبت شده: {mockup_count} فایل\n"
            f"{count_line}\n\n"
            f"⬇️ فایل چاپی رو بفرستید یا یکی از گزینه‌ها رو انتخاب کنید\n"
            f"━━━━━━━━━━━━━━━━"
        )

        buttons = [
            [InlineKeyboardButton(
                "✅ اتمام ثبت فایل چاپی",
                callback_data="stage_print_done"
            )],
            [InlineKeyboardButton(
                "🗑 پاکسازی لیست و ارسال دوباره",
                callback_data="stage_print_clear"
            )],
            [InlineKeyboardButton(
                "❌ لغو ویرایش" if is_edit else "❌ لغو طرح",
                callback_data="cancel_editing" if is_edit else "cancel_submission"
            )],
        ]

        return text, InlineKeyboardMarkup(buttons)


    @staticmethod
    def get_workspace_stage(
        code: str,
        product_name: str,
        mockup_count: int,
        print_count: int,
        is_edit: bool = False
    ) -> tuple[str, InlineKeyboardMarkup]:
        """Workspace: editing hub between stages"""

        mockup_line = (
            f"🎨 موکاپ: {mockup_count} فایل"
            if mockup_count > 0
            else "🎨 موکاپ: ندارد"
        )
        print_line = (
            f"🖨 فایل چاپی: {print_count} فایل"
            if print_count > 0
            else "🖨 فایل چاپی: ندارد"
        )

        text = (
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 *{code}* | {product_name}\n\n"
            f"{mockup_line}\n"
            f"{print_line}\n"
            f"━━━━━━━━━━━━━━━━"
        )

        buttons = [
            [
                InlineKeyboardButton(
                    "🎨 افزودن موکاپ +",
                    callback_data="stage_goto_mockup"
                ),
                InlineKeyboardButton(
                    "🖨 افزودن فایل چاپی +",
                    callback_data="stage_goto_print"
                ),
            ],
        ]

        # Management buttons — only show if files exist
        manage_row = []
        if mockup_count > 0:
            manage_row.append(InlineKeyboardButton(
                "📁 مدیریت موکاپ‌ها",
                callback_data="manage_mockups"
            ))
        if print_count > 0:
            manage_row.append(InlineKeyboardButton(
                "📁 مدیریت فایل چاپی",
                callback_data="manage_prints"
            ))
        if manage_row:
            buttons.append(manage_row)

        buttons.append([
            InlineKeyboardButton(
                "✅ ثبت نهایی",
                callback_data="confirm_submit"
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                "❌ لغو ویرایش" if is_edit else "❌ لغو طرح",
                callback_data="cancel_editing" if is_edit else "cancel_submission"
            ),
        ])

        return text, InlineKeyboardMarkup(buttons)


    @staticmethod
    def get_confirm_stage(
        code: str,
        product_name: str,
        mockup_count: int,
        print_count: int,
        is_edit: bool = False
    ) -> tuple[str, InlineKeyboardMarkup]:
        """Stage 3: Final confirmation before sending to reviewer"""

        text = (
            f"━━━━━━━━━━━━━━━━\n"
            f"📦 تایید ارسال\n\n"
            f"🔢 کد: *{code}*\n"
            f"📁 محصول: {product_name}\n"
            f"🎨 موکاپ: {mockup_count} فایل\n"
            f"🖨 چاپی: {print_count} فایل\n\n"
            f"آیا مطمئن هستید؟\n"
            f"━━━━━━━━━━━━━━━━"
        )

        buttons = [
            [InlineKeyboardButton(
                "✅ ارسال برای تایید",
                callback_data="submit_to_reviewer"
            )],
            [InlineKeyboardButton(
                "👁 مشاهده فایل‌ها",
                callback_data="preview_files"
            )],
            [InlineKeyboardButton(
                "✏️ بازگشت برای ویرایش",
                callback_data="back_to_workspace"
            )],
            [InlineKeyboardButton(
                "❌ لغو ویرایش" if is_edit else "❌ لغو کامل",
                callback_data="cancel_editing" if is_edit else "cancel_submission"
            )],
        ]

        return text, InlineKeyboardMarkup(buttons)


    @staticmethod
    def get_clear_confirmation(stage: str) -> tuple[str, InlineKeyboardMarkup]:
        """Confirmation dialog before clearing file list"""

        stage_label = "موکاپ‌ها" if stage == "mockup" else "فایل‌های چاپی"

        text = (
            f"⚠️ آیا مطمئن هستید؟\n"
            f"تمام {stage_label} پاک خواهند شد."
        )

        buttons = [[
            InlineKeyboardButton(
                "✅ بله، پاک شود",
                callback_data=f"clear_confirmed_{stage}"
            ),
            InlineKeyboardButton(
                "❌ انصراف",
                callback_data=f"clear_cancelled_{stage}"
            ),
        ]]

        return text, InlineKeyboardMarkup(buttons)


    @staticmethod
    def get_manage_files_stage(
        stage: str,
        files: list,
        code: str,
        product_name: str
    ) -> tuple[str, InlineKeyboardMarkup]:
        """Management view for individual file removal"""

        stage_label = "موکاپ‌ها" if stage == "mockup" else "فایل‌های چاپی"
        stage_emoji = "🎨" if stage == "mockup" else "🖨"

        text = (
            f"📁 مدیریت {stage_label} — *{code}*\n"
            f"━━━━━━━━━━━━━━━━\n"
        )

        if not files:
            text += f"\nهیچ {stage_label[:-1]}ی ثبت نشده.\n"
        else:
            for i in range(len(files)):
                text += f"{i+1}️⃣ {stage_emoji} {stage_label[:-1]} {i+1}\n"

        text += f"\n━━━━━━━━━━━━━━━━"

        buttons = []

        # Individual remove buttons (2 per row)
        if files:
            row = []
            for i in range(len(files)):
                row.append(InlineKeyboardButton(
                    f"❌ {i+1}",
                    callback_data=f"remove_{stage}_{i}"
                ))
                if len(row) == 2:
                    buttons.append(row)
                    row = []
            if row:
                buttons.append(row)

            # Clear all button
            buttons.append([InlineKeyboardButton(
                f"🗑 حذف همه {stage_label}",
                callback_data=f"manage_clear_{stage}"
            )])

        # Back button
        buttons.append([InlineKeyboardButton(
            "🔙 بازگشت",
            callback_data="manage_back"
        )])

        return text, InlineKeyboardMarkup(buttons)