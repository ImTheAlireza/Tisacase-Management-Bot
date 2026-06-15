from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from typing import Optional
from utils.decorators import require_role
from services.code_service import CodeService
from models.product_line import ProductLine
from models.design import Design
from models.user import User
from ui.keyboards import Keyboards
from utils.helpers import safe_edit_message, delete_messages
from utils.enums import DesignStatus
from telegram import InputMediaPhoto, InputMediaDocument
import logging
from utils.callback_lock import deduplicate_callback


@require_role('editor', 'sudo')
async def start_new_design(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    prefix: str
) -> None:
    user = context.user_data['db_user']
    product_line: Optional[ProductLine] = ProductLine.get_by_prefix(prefix)

    if not product_line:
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد.")
        return

    if not product_line.is_fully_configured():
        missing: str = ', '.join(product_line.missing_groups())
        await update.message.reply_text(
            f"⚠️ گروه‌های این خط تولید تنظیم نشده‌اند:\n{missing}\n\n"
            f"لطفاً ابتدا از طریق منوی Sudo → تنظیم گروه‌ها اقدام کنید."
        )
        return

    for key in ['mockup_files', 'print_files', 'awaiting_input', 'code',
                'current_menu_id', 'product_id']:
        context.user_data.pop(key, None)

    try:
        code, design = CodeService.generate_code(prefix, user.user_id, user.first_name)
        product = ProductLine.get_by_id(design.product_line_id)

        context.user_data['code'] = code
        context.user_data['product_id'] = product.id
        context.user_data['product_name'] = product.name_fa
        context.user_data['mockup_files'] = []
        context.user_data['print_files'] = []
        context.user_data['awaiting_input'] = None

        text, markup = Keyboards.get_design_menu(code, product.name_fa, 0, 0)
        msg = await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
        context.user_data['current_menu_id'] = msg.message_id

    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {str(e)}")


@require_role('editor', 'sudo')
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state: Optional[str] = context.user_data.get('awaiting_input')
    if state not in ['mockup', 'print']:
        return

    code: Optional[str] = context.user_data.get('code')
    if not code:
        await update.message.reply_text(
            "⚠️ جلسه طراحی یافت نشد. لطفاً دوباره شروع کنید."
        )
        context.user_data.clear()
        return

    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await update.message.reply_text(
            f"❌ طرح {code} در سیستم یافت نشد. جلسه منقضی شده است.\n"
            f"لطفاً دوباره ثبت طرح را شروع کنید."
        )
        context.user_data.clear()
        return

    if design.status != DesignStatus.PENDING:
        await update.message.reply_text(
            f"⚠️ طرح {code} قبلاً پردازش شده است.\n"
            f"جلسه شما منقضی شده است."
        )
        context.user_data.clear()
        return

    if design.editor_user_id != update.effective_user.id:
        await update.message.reply_text("❌ شما مالک این طرح نیستید.")
        context.user_data.clear()
        return

    if update.message.photo:
        file_id: str = update.message.photo[-1].file_id
    elif update.message.document:
        file_id = update.message.document.file_id
    else:
        await update.message.reply_text("⚠️ لطفاً فقط عکس یا فایل ارسال کنید.")
        return

    if state == 'mockup':
        context.user_data.setdefault('mockup_files', []).append(file_id)
        await update.message.reply_text(
            f"✅ موکاپ اضافه شد. کل: {len(context.user_data['mockup_files'])}"
        )
    else:
        context.user_data.setdefault('print_files', []).append(file_id)
        await update.message.reply_text(
            f"✅ فایل چاپی اضافه شد. کل: {len(context.user_data['print_files'])}"
        )

    await refresh_menu(update, context)


async def refresh_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    code: Optional[str] = context.user_data.get('code')
    pname: Optional[str] = context.user_data.get('product_name')
    mockups: int = len(context.user_data.get('mockup_files', []))
    prints: int = len(context.user_data.get('print_files', []))

    text, markup = Keyboards.get_design_menu(code, pname, mockups, prints)

    if context.user_data.get('awaiting_input'):
        kind: str = 'موکاپ' if context.user_data['awaiting_input'] == 'mockup' else 'فایل چاپی'
        text = f"طرح {pname} — کد: {code}\n\n📥 منتظر دریافت {kind}..."
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_to_menu")]]
        )

    menu_id: Optional[int] = context.user_data.get('current_menu_id')
    if not menu_id:
        try:
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            context.user_data['current_menu_id'] = msg.message_id
        except Exception as e:
            logging.error(f"Failed to send new menu: {e}")
        return

    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=menu_id,
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception as e:
        logging.warning(f"Failed to edit menu {menu_id}: {e}")
        try:
            msg = await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text=text,
                reply_markup=markup,
                parse_mode="Markdown"
            )
            context.user_data['current_menu_id'] = msg.message_id
        except Exception as e2:
            logging.error(f"Failed to send replacement menu: {e2}")


def _submit_key(update, context) -> str:
    """Lock key for submit"""
    code = context.user_data.get('code', 'unknown')
    return f"submit_{code}"


def _undo_key(update, context) -> str:
    """Lock key for undo"""
    code = update.callback_query.data.split('_', 1)[1]
    return f"undo_{code}"


@require_role('editor', 'sudo')
@deduplicate_callback(_submit_key)
async def editor_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data: str = query.data

    if data == "add_mockup":
        context.user_data.setdefault('mockup_files', [])
        context.user_data['awaiting_input'] = 'mockup'
        await refresh_menu(update, context)

    elif data == "add_print":
        context.user_data.setdefault('print_files', [])
        context.user_data['awaiting_input'] = 'print'
        await refresh_menu(update, context)

    elif data == "back_to_menu":
        context.user_data['awaiting_input'] = None
        await refresh_menu(update, context)

    elif data == "cancel_submission":
        code: Optional[str] = context.user_data.get('code')
        if code:
            design: Optional[Design] = Design.get_by_code(code)
            if design:
                design.delete()
        await safe_edit_message(query, f"🗑 لغو شد. کد {code} آزاد شد.")
        context.user_data.clear()

    elif data == "confirm_submit":
        await submit_to_reviewer(update, context)


async def submit_to_reviewer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    code: str = context.user_data['code']
    mockups: list = context.user_data.get('mockup_files', [])
    prints: list = context.user_data.get('print_files', [])
    pname: str = context.user_data['product_name']
    editor_name: str = context.user_data['db_user'].first_name

    if not mockups or not prints:
        await query.answer(
            "⚠️ لطفاً حداقل یک موکاپ و یک فایل چاپی بفرستید.", show_alert=True
        )
        return

    await query.edit_message_text("⏳ در حال ارسال برای تایید...")

    design: Optional[Design] = Design.get_by_code(code)
    if not design:
        await query.edit_message_text("❌ خطا: طرح در دیتابیس یافت نشد.")
        context.user_data.clear()
        return

    design.mockup_file_ids = mockups
    design.print_file_ids = prints

    try:
        design.save()
    except Exception as e:
        logging.error(f"Failed to save design {code} before reviewer send: {e}")
        await query.edit_message_text(
            f"❌ خطا در ذخیره طرح. لطفاً دوباره تلاش کنید.\n"
            f"کد {code} همچنان برای شما رزرو است."
        )
        return

    reviewers: list[User] = User.get_by_role('reviewer')
    if not reviewers:
        await query.edit_message_text("❌ هیچ ناظری در سیستم ثبت نشده است.")
        design.delete()
        context.user_data.clear()
        return

    total: int = len(mockups)
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=f"approve_{code}"),
            InlineKeyboardButton("❌ رد", callback_data=f"reject_{code}")
        ]
    ])

    successful_sends: int = 0
    for reviewer in reviewers:
        msg_ids: list[int] = []
        try:
            if total == 1:
                caption = f"🖼 {pname} | کد: {code}\n👤 طراح: {editor_name}"
                fid = mockups[0]
                if isinstance(fid, str) and fid.startswith(('AgAC', 'AQA')):
                    m = await context.bot.send_photo(
                        reviewer.user_id, photo=fid,
                        caption=caption, reply_markup=markup
                    )
                else:
                    m = await context.bot.send_document(
                        reviewer.user_id, document=fid,
                        caption=caption, reply_markup=markup
                    )
                msg_ids.append(m.message_id)
            else:
                media_group = []
                for i, fid in enumerate(mockups):
                    cap = f"🖼 {pname} | کد: {code} ({i+1}/{total})\n👤 طراح: {editor_name}"
                    if isinstance(fid, str) and fid.startswith(('AgAC', 'AQA')):
                        media_group.append(InputMediaPhoto(media=fid, caption=cap))
                    else:
                        media_group.append(InputMediaDocument(media=fid, caption=cap))

                msgs = await context.bot.send_media_group(reviewer.user_id, media=media_group)
                msg_ids.extend([m.message_id for m in msgs])

                m = await context.bot.send_message(
                    reviewer.user_id,
                    f"👆 بررسی {pname} {code}",
                    reply_markup=markup,
                    reply_to_message_id=msg_ids[-1]
                )
                msg_ids.append(m.message_id)

            design.set_reviewer_messages(reviewer.user_id, msg_ids)
            successful_sends += 1

        except Exception as e:
            logging.error(f"Failed to send design {code} to reviewer {reviewer.user_id}: {e}")

    if successful_sends == 0:
        await query.edit_message_text(
            "❌ خطا در ارسال به ناظران. طرح ذخیره شده اما هیچ ناظری اطلاع‌رسانی نشد.\n"
            "لطفاً به Sudo اطلاع دهید."
        )
        context.user_data.clear()
        return

    try:
        design.save()
    except Exception as e:
        logging.error(f"Failed to save reviewer message IDs for {code}: {e}")
        try:
            from config.settings import SUDO_USER_ID
            await context.bot.send_message(
                SUDO_USER_ID,
                f"⚠️ خطا در ذخیره message IDs برای طرح {code}\n"
                f"طراح: {editor_name}\n"
                f"Error: {str(e)[:200]}"
            )
        except Exception:
            pass

    msg_text: str = f"✅ {pname} کد {code} ثبت و برای تایید ارسال شد."
    if successful_sends < len(reviewers):
        msg_text += f"\n⚠️ ارسال به {len(reviewers) - successful_sends} ناظر ناموفق بود."

    undo_markup = InlineKeyboardMarkup([[
        InlineKeyboardButton("↩️ Undo (لغو ثبت)", callback_data=f"undo_{code}")
    ]])
    await query.edit_message_text(msg_text, reply_markup=undo_markup)
    context.user_data.clear()


@require_role('editor', 'sudo')
@deduplicate_callback(_undo_key)
async def handle_undo_submission(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    code: str = query.data.split('_', 1)[1]
    design: Optional[Design] = Design.get_by_code(code)

    if not design:
        await safe_edit_message(query, "❌ این طرح یافت نشد یا از قبل پردازش شده است.")
        return

    if design.status != DesignStatus.PENDING:
        await safe_edit_message(query, "⚠️ این طرح قبلاً توسط ناظر بررسی شده و قابل لغو نیست.")
        return

    user: Optional[User] = context.user_data.get('db_user')
    if not user:
        user = User.get_by_id(query.from_user.id)

    is_owner: bool = design.editor_user_id == query.from_user.id
    is_sudo: bool = bool(user and user.is_sudo)

    if not is_owner and not is_sudo:
        await query.answer(
            "🚫 فقط طراح اصلی یا Sudo می‌توانند این طرح را لغو کنند.",
            show_alert=True
        )
        return

    for reviewer_id, msg_ids in design.all_reviewer_message_pairs():
        if msg_ids:
            await delete_messages(context.bot, reviewer_id, msg_ids)

    design.delete()
    await safe_edit_message(query, f"↩️ طرح {code} با موفقیت لغو شد و کد مجدداً آزاد گردید.")