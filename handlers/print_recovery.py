"""Sudo-only, explicitly confirmed re-delivery of one approved print file."""
import logging
import re
import time
from copy import copy

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from models.design import Design
from models.product_line import ProductLine
from models.user import User
from utils.callback_lock import callback_lock, deduplicate_callback
from utils.decorators import require_sudo
from utils.enums import DesignStatus
from utils.helpers import safe_answer_callback, safe_edit_message
from handlers.reviewer import PRINT_WARNING_PREFIX, _send_prints_to_print_group, _safe_telegram_error


PENDING_KEY = 'pending_print_resend'
FORWARDED_FILE_KEY = 'awaiting_print_repair_file'
CONFIRM_TTL_SECONDS = 15 * 60
USAGE = (
    "روش استفاده:\n"
    "/resendprint TS350\n"
    "/resendprint TS350 2\n"
    "/resendprint TS350 2 ai\n\n"
    "برای طرح چندفایلی شماره فایل را مشخص کنید. پسوند اختیاری است و "
    "فقط وقتی فرمت اصلی را با اطمینان می‌دانید وارد شود."
)


def _print_ids(design: Design) -> list[str]:
    return list(dict.fromkeys(design.print_file_ids or []))


@require_sudo
async def resend_print_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Prepare a confirmation, but never send a print file from the command."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text('🚫 این دستور فقط در گفت‌وگوی خصوصی با ربات قابل استفاده است.')
        return

    context.user_data.pop(FORWARDED_FILE_KEY, None)
    args = list(context.args or [])
    if not 1 <= len(args) <= 3:
        await update.message.reply_text(USAGE)
        return
    code = args[0].strip().upper()
    # Approved codes also occur in the 20-character locked-code/group tables.
    # Do not put arbitrary text into a confirmation, logs, or a group caption.
    if not code or len(code) > 20 or not all(ch.isalnum() or ch in '_-' for ch in code):
        await update.message.reply_text('❌ کد طرح معتبر نیست.\n\n' + USAGE)
        return

    design = Design.get_by_code(code)
    if not design or design.id is None or design.status != DesignStatus.APPROVED:
        await update.message.reply_text('❌ طرح تاییدشده‌ای با این کد پیدا نشد.')
        return
    prints = _print_ids(design)
    if not prints:
        await update.message.reply_text('❌ شناسه فایل چاپی برای این طرح ذخیره نشده است؛ نسخه اصلی لازم است.')
        return
    if len(args) < 2 and len(prints) != 1:
        await update.message.reply_text(
            f'این طرح {len(prints)} فایل چاپی دارد. برای جلوگیری از چاپ تکراری، '
            f'شماره یک فایل را مشخص کنید؛ مثلاً /resendprint {code} 1'
        )
        return
    try:
        index = int(args[1]) - 1 if len(args) >= 2 else 0
    except ValueError:
        await update.message.reply_text('❌ شماره فایل باید عدد باشد.\n\n' + USAGE)
        return
    if index < 0 or index >= len(prints):
        await update.message.reply_text(f'❌ شماره فایل باید بین ۱ و {len(prints)} باشد.')
        return

    extension = args[2].lower() if len(args) == 3 else None
    if extension is not None and not re.fullmatch(r'[a-z0-9]{1,16}', extension):
        await update.message.reply_text('❌ پسوند را بدون نقطه و فقط با حروف انگلیسی یا عدد وارد کنید.')
        return
    product = ProductLine.get_by_id(design.product_line_id)
    if not product or not product.group_print:
        await update.message.reply_text('❌ گروه چاپ برای خط تولید این طرح تنظیم نشده است.')
        return

    text = (
        f'⚠️ ارسال مجدد فایل چاپی {index + 1}/{len(prints)} طرح {code}\n\n'
        'پس از تایید، یک نسخه جدید به گروه چاپ ارسال می‌شود. '
        'پیام‌های قبلی حذف نمی‌شوند؛ ابتدا مطمئن شوید فایل دوباره چاپ نمی‌شود.'
    )
    if extension:
        text += (f'\n\n⚠️ پسوند .{extension} را شما تعیین کرده‌اید؛ '
                 'اشتباه بودن آن ممکن است فایل را غیرقابل باز کردن کند.')
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ ارسال مجدد', callback_data='resendprint_confirm'),
        InlineKeyboardButton('انصراف', callback_data='resendprint_cancel'),
    ]])
    message = await update.message.reply_text(text, reply_markup=keyboard)
    context.user_data[PENDING_KEY] = {
        'code': code,
        'design_id': design.id,
        'product_line_id': design.product_line_id,
        'chat_id': update.effective_chat.id,
        'message_id': message.message_id,
        'group_print': product.group_print,
        'prints': prints,
        'design_prints': prints,
        'source': 'stored',
        'index': index,
        'extension': extension,
        'expires': time.monotonic() + CONFIRM_TTL_SECONDS,
    }


@require_sudo
async def repair_print_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ask Sudo for an existing document when the original file ID is missing."""
    if update.effective_chat.type != 'private':
        await update.message.reply_text('🚫 این دستور فقط در گفت‌وگوی خصوصی با ربات قابل استفاده است.')
        return
    if context.user_data.get('code'):
        await update.message.reply_text('⚠️ ابتدا عملیات ویرایش طرح را تمام کنید؛ سپس /repairprint را بزنید.')
        return
    if context.user_data.get('awaiting_restore_file') or context.user_data.get('restore_pending'):
        await update.message.reply_text('⚠️ ابتدا عملیات ریستور را تمام یا لغو کنید؛ سپس /repairprint را بزنید.')
        return
    context.user_data.pop(PENDING_KEY, None)
    context.user_data.pop(FORWARDED_FILE_KEY, None)
    args = list(context.args or [])
    if not 1 <= len(args) <= 2:
        await update.message.reply_text(
            'روش استفاده: /repairprint TS350 یا /repairprint TS350 psd\n'
            'پسوند اختیاری است؛ فقط در صورت اطلاع قطعی از فرمت وارد کنید.'
        )
        return
    code = args[0].strip().upper()
    if not code or len(code) > 20 or not all(ch.isalnum() or ch in '_-' for ch in code):
        await update.message.reply_text('❌ کد طرح معتبر نیست.')
        return
    extension = args[1].lower() if len(args) == 2 else None
    if extension is not None and not re.fullmatch(r'[a-z0-9]{1,16}', extension):
        await update.message.reply_text('❌ پسوند را بدون نقطه و فقط با حروف انگلیسی یا عدد وارد کنید.')
        return
    design = Design.get_by_code(code)
    if not design or design.id is None or design.status != DesignStatus.APPROVED:
        await update.message.reply_text('❌ طرح تاییدشده‌ای با این کد پیدا نشد.')
        return
    product = ProductLine.get_by_id(design.product_line_id)
    if not product or not product.group_print:
        await update.message.reply_text('❌ گروه چاپ برای خط تولید این طرح تنظیم نشده است.')
        return

    text = (
        f'📎 یک فایل چاپی مربوط به {code} را به‌صورت سند در همین گفت‌وگو ارسال یا فوروارد کنید.\n'
        'پیش از ارسال به گروه چاپ، دوباره از شما تایید گرفته می‌شود. '
        'درستی تعلق فایل به طرح را خودتان بررسی کنید.'
    )
    if extension:
        text += f'\n⚠️ پسوند انتخابی شما: .{extension}؛ صحیح بودن آن را بررسی کنید.'
    await update.message.reply_text(text)
    context.user_data[FORWARDED_FILE_KEY] = {
        'code': code,
        'design_id': design.id,
        'product_line_id': design.product_line_id,
        'group_print': product.group_print,
        'chat_id': update.effective_chat.id,
        'design_prints': _print_ids(design),
        'extension': extension,
        'expires': time.monotonic() + CONFIRM_TTL_SECONDS,
    }


async def handle_print_repair_file(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Consume a privately forwarded document only during Sudo's repair flow."""
    waiting = context.user_data.get(FORWARDED_FILE_KEY)
    if not isinstance(waiting, dict):
        return False
    if (context.user_data.get('code') or context.user_data.get('awaiting_restore_file')
            or context.user_data.get('restore_pending')):
        # An editor or backup-restore workflow started after the command;
        # never capture its upload, especially a sensitive backup archive.
        context.user_data.pop(FORWARDED_FILE_KEY, None)
        return False
    message = update.message
    if not message or update.effective_chat.type != 'private' or update.effective_chat.id != waiting['chat_id']:
        return False
    user = User.get_by_id(update.effective_user.id)
    if not user or not user.is_active or not user.is_sudo:
        context.user_data.pop(FORWARDED_FILE_KEY, None)
        await message.reply_text('🚫 این بخش فقط برای Sudo است.')
        return True
    if time.monotonic() > waiting['expires']:
        context.user_data.pop(FORWARDED_FILE_KEY, None)
        await message.reply_text('⚠️ درخواست منقضی شده است؛ /repairprint را دوباره بزنید.')
        return True
    document = message.document
    if not document or not isinstance(document.file_id, str) or not document.file_id:
        await message.reply_text('⚠️ فایل را به‌صورت سند ارسال کنید، نه عکس فشرده.')
        return True

    design = Design.get_by_code(waiting['code'])
    product = ProductLine.get_by_id(design.product_line_id) if design else None
    if (not design or design.id != waiting['design_id'] or design.status != DesignStatus.APPROVED
            or design.product_line_id != waiting['product_line_id']
            or _print_ids(design) != waiting['design_prints']
            or not product or product.group_print != waiting['group_print']):
        context.user_data.pop(FORWARDED_FILE_KEY, None)
        await message.reply_text('⚠️ طرح یا گروه چاپ تغییر کرده است؛ دستور را دوباره بزنید.')
        return True

    # Do not expose untrusted file paths or formatting from the forwarded name.
    original_name = document.file_name if isinstance(document.file_name, str) else None
    mime_type = document.mime_type if isinstance(document.mime_type, str) else None
    fid = document.file_id
    code = waiting['code']
    extension = waiting['extension']
    text = (
        f'⚠️ فایل ارسالی شما برای طرح {code} آماده بررسی است.\n'
        'با تایید، یک نسخه جدید به گروه چاپ ارسال می‌شود؛ '
        'پیام قبلی حذف نمی‌شود. تعلق فایل و نبود خطر چاپ تکراری را بررسی کنید.'
    )
    if extension:
        text += f'\n\n⚠️ پسوند .{extension} را شما تعیین کرده‌اید؛ از صحت آن مطمئن شوید.'
    keyboard = InlineKeyboardMarkup([[
        InlineKeyboardButton('✅ ارسال مجدد', callback_data='resendprint_confirm'),
        InlineKeyboardButton('انصراف', callback_data='resendprint_cancel'),
    ]])
    context.user_data.pop(FORWARDED_FILE_KEY, None)
    confirmation = await message.reply_text(text, reply_markup=keyboard)
    context.user_data[PENDING_KEY] = {
        **waiting,
        'prints': [fid],
        'source': 'forwarded',
        'forward_metadata': {'file_name': original_name, 'mime_type': mime_type},
        'index': 0,
        'message_id': confirmation.message_id,
        'expires': time.monotonic() + CONFIRM_TTL_SECONDS,
    }
    return True


@require_sudo
@deduplicate_callback(lambda update, context: f"resendprint_{update.effective_user.id}")
async def resend_print_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Consume a confirmation once, re-check its snapshot, then send one file."""
    query = update.callback_query
    pending = context.user_data.get(PENDING_KEY)
    if (not isinstance(pending, dict)
            or query.message.message_id != pending.get('message_id')
            or query.message.chat_id != pending.get('chat_id')):
        await safe_answer_callback(query, '⚠️ این درخواست منقضی شده است؛ دستور را دوباره بزنید.', show_alert=True)
        return

    # Consume before the first await so repeated or queued taps cannot send a
    # second copy. A restart invalidates the in-memory confirmation as well.
    context.user_data.pop(PENDING_KEY, None)
    await safe_answer_callback(query)
    if query.data == 'resendprint_cancel':
        await safe_edit_message(query, '❎ ارسال مجدد لغو شد.')
        return
    if query.data != 'resendprint_confirm' or time.monotonic() > pending['expires']:
        await safe_edit_message(query, '⚠️ درخواست منقضی شده است؛ دستور را دوباره بزنید.')
        return

    code = pending['code']
    lock_key = f"review_{code}"
    if not await callback_lock.acquire(lock_key):
        await safe_edit_message(query, '⏳ این طرح در حال پردازش است؛ بعداً دستور را دوباره بزنید.')
        return
    try:
        design = Design.get_by_code(code)
        product = ProductLine.get_by_id(design.product_line_id) if design else None
        if (not design or design.id != pending['design_id']
                or design.status != DesignStatus.APPROVED
                or design.product_line_id != pending['product_line_id']
                or _print_ids(design) != pending['design_prints']
                or not product or product.group_print != pending['group_print']):
            await safe_edit_message(query, '⚠️ طرح یا گروه چاپ تغییر کرده است؛ دستور را دوباره بزنید.')
            return

        index = pending['index']
        fid = pending['prints'][index]
        overrides = {fid: pending['extension']} if pending['extension'] else None
        if pending['source'] == 'forwarded':
            # Keep the persisted design untouched: the forwarded file may be a
            # repaired copy, and its original file_id is not proven to match.
            design = copy(design)
            design.print_file_metadata = {fid: pending['forward_metadata']}
        await safe_edit_message(query, f'⏳ در حال ارسال مجدد فایل چاپی {index + 1}: {code}')
        try:
            results = await _send_prints_to_print_group(
                context.bot, design, pending['prints'], product.group_print,
                code, product.name_fa, selected_indices={index}, extension_overrides=overrides,
            )
        except Exception as e:
            logging.error('[PRINT_RECOVERY] resend failed: %s', _safe_telegram_error(e))
            await safe_edit_message(query, '❌ ارسال کامل نشد؛ قبل از تلاش مجدد، گروه چاپ را بررسی کنید.')
            return

        if len(results) != 1 or not results[0][1]:
            detail = results[0][2] if results else 'نتیجه ارسال مشخص نیست.'
            await safe_edit_message(
                query, f'❌ نتیجه ارسال ناموفق یا نامشخص بود: {detail}\n\n'
                       'پیش از تلاش مجدد گروه چاپ را بررسی کنید؛ پیام‌های قبلی حذف نشده‌اند.'
            )
            return
        warning = results[0][2] if results[0][2].startswith(PRINT_WARNING_PREFIX) else ''
        text = (f'✅ فایل چاپی {index + 1}/{len(pending["prints"])} طرح {code} '
                'دوباره به گروه چاپ ارسال شد.\nپیام‌های قبلی حذف نشده‌اند.')
        if warning:
            text += '\n\n' + warning[len(PRINT_WARNING_PREFIX):]
        await safe_edit_message(query, text)
    finally:
        await callback_lock.release(lock_key)
