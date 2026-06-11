import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from utils.decorators import require_role
from services.code_service import CodeService
from models.product_line import ProductLine
from models.design import Design
from models.user import User
from ui.keyboards import Keyboards
from utils.helpers import safe_edit_message, delete_messages
from telegram import InputMediaPhoto, InputMediaDocument
import logging

@require_role('editor', 'sudo')
async def start_new_design(update: Update, context: ContextTypes.DEFAULT_TYPE, prefix: str):
    user = context.user_data['db_user']

    product_line = ProductLine.get_by_prefix(prefix)
    if not product_line:
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد.")
        return

    if not product_line.is_fully_configured():
        missing = ', '.join(product_line.missing_groups())
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
async def handle_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('awaiting_input')
    if state not in ['mockup', 'print']:
        return

    if update.message.photo:
        file_id = update.message.photo[-1].file_id
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


async def refresh_menu(update, context):
    code = context.user_data.get('code')
    pname = context.user_data.get('product_name')
    mockups = len(context.user_data.get('mockup_files', []))
    prints = len(context.user_data.get('print_files', []))

    text, markup = Keyboards.get_design_menu(code, pname, mockups, prints)

    if context.user_data.get('awaiting_input'):
        kind = 'موکاپ' if context.user_data['awaiting_input'] == 'mockup' else 'فایل چاپی'
        text = f"طرح {pname} — کد: {code}\n\n📥 منتظر دریافت {kind}..."
        markup = InlineKeyboardMarkup(
            [[InlineKeyboardButton("↩️ بازگشت به منو", callback_data="back_to_menu")]]
        )

    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=context.user_data['current_menu_id'],
            text=text,
            reply_markup=markup,
            parse_mode="Markdown"
        )
    except Exception:
        pass


@require_role('editor', 'sudo')
async def editor_callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "add_mockup":
        context.user_data['mockup_files'] = []
        context.user_data['awaiting_input'] = 'mockup'
        await refresh_menu(update, context)

    elif data == "add_print":
        context.user_data['print_files'] = []
        context.user_data['awaiting_input'] = 'print'
        await refresh_menu(update, context)

    elif data == "back_to_menu":
        context.user_data['awaiting_input'] = None
        await refresh_menu(update, context)

    elif data == "cancel_submission":
        code = context.user_data.get('code')
        if code:
            design = Design.get_by_code(code)
            if design:
                design.delete()
        await safe_edit_message(query, f"🗑 لغو شد. کد {code} آزاد شد.")
        context.user_data.clear()

    elif data == "confirm_submit":
        await submit_to_reviewer(update, context)


async def submit_to_reviewer(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    code = context.user_data['code']
    mockups = context.user_data.get('mockup_files', [])
    prints = context.user_data.get('print_files', [])
    pname = context.user_data['product_name']
    editor_name = context.user_data['db_user'].first_name

    if not mockups or not prints:
        await query.answer(
            "⚠️ لطفاً حداقل یک موکاپ و یک فایل چاپی بفرستید.", show_alert=True
        )
        return

    await query.edit_message_text("⏳ در حال ارسال برای تایید...")

    design = Design.get_by_code(code)
    design.mockup_file_ids = mockups
    design.print_file_ids = prints

    reviewers = User.get_by_role('reviewer')
    if not reviewers:
        await query.edit_message_text("❌ هیچ ناظری در سیستم ثبت نشده است.")
        design.delete()
        context.user_data.clear()
        return

    total = len(mockups)
    cb_approve = f"approve_{code}"
    cb_reject = f"reject_{code}"
    markup = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ تایید", callback_data=cb_approve),
            InlineKeyboardButton("❌ رد", callback_data=cb_reject)
        ]
    ])

    for reviewer in reviewers:
        msg_ids = []
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

        except Exception as e:
            logging.error(f"Failed to send design {code} to reviewer {reviewer.user_id}: {e}")

    design.save()
    
    # Adding the Undo button!
    undo_markup = InlineKeyboardMarkup([[InlineKeyboardButton("↩️ Undo (لغو ثبت)", callback_data=f"undo_{code}")]])
    await query.edit_message_text(f"✅ {pname} کد {code} ثبت و برای تایید ارسال شد.", reply_markup=undo_markup)
    context.user_data.clear()

@require_role('editor', 'sudo')
async def handle_undo_submission(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    code = query.data.split('_', 1)[1]
    design = Design.get_by_code(code)
    
    if not design:
        await safe_edit_message(query, "❌ این طرح یافت نشد یا از قبل پردازش شده است.")
        return
        
    if design.status != 'pending':
        await safe_edit_message(query, "⚠️ این طرح قبلاً توسط ناظر بررسی شده و قابل لغو نیست.")
        return
        
    # Delete messages sent to reviewers
    for reviewer_id, msg_ids in design.all_reviewer_message_pairs():
        if msg_ids:
            await delete_messages(context.bot, reviewer_id, msg_ids)
            
    # Delete from DB to free the code entirely
    design.delete()
    
    await safe_edit_message(query, f"↩️ طرح {code} با موفقیت لغو شد و کد مجدداً آزاد گردید.")