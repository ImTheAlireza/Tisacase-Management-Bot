from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes
from utils.decorators import require_role
from models.design import Design
from models.design_group_message import DesignGroupMessage
from models.user import User
from models.product_line import ProductLine
from utils.helpers import safe_edit_message, delete_messages
from config.settings import SUDO_USER_ID
import logging
from io import BytesIO
from utils.enums import DesignStatus
from utils.callback_lock import deduplicate_callback


def _review_key(update, context) -> str:
    """Lock key: action + code e.g. 'approve_TS001'"""
    # Locks per code regardless of approve/reject
    # so two reviewers can't both approve simultaneously
    code = update.callback_query.data.split('_', 1)[1]
    return f"review_{code}"


@require_role('reviewer', 'sudo')
@deduplicate_callback(_review_key)
async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    query = update.callback_query
    await query.answer()

    user = User.get_by_id(query.from_user.id)  # fresh fetch for sensitive action
    action, code = query.data.split('_', 1)

    design = Design.get_by_code(code)
    
    # FIX: Explicit validation with clear error messages
    if not design:
        await _delete_my_messages(context.bot, user.user_id, design) if design else None
        await safe_edit_message(query, f"❌ طرح با کد {code} در سیستم یافت نشد.")
        return
    
    if design.id is None:
        await _delete_my_messages(context.bot, user.user_id, design)
        await safe_edit_message(query, "❌ خطای داخلی: طرح بدون شناسه (ID) است. به Sudo اطلاع دهید.")
        logging.error(f"Design {code} has id=None in review_callback")
        return

    if design.status != DesignStatus.PENDING:
        status_map = {
            DesignStatus.APPROVED: 'تایید شده',
            DesignStatus.REJECTED: 'رد شده',
            DesignStatus.DELETED: 'حذف شده'
        }
        status_fa = status_map.get(design.status, design.status)
        reviewer_name = design.reviewer_name or "ناظر دیگر"
        
        await _delete_my_messages(context.bot, user.user_id, design)
        await safe_edit_message(
            query,
            f"⚠️ این طرح قبلاً پردازش شده است.\n"
            f"وضعیت: {status_fa}\n"
            f"توسط: {reviewer_name}"
        )
        return

    await safe_edit_message(query, "⏳ در حال پردازش...")

    product_line = ProductLine.get_by_id(design.product_line_id)

    if action == "approve":
        won = design.approve(user.user_id, user.first_name)

        if not won:
            # FIX: More specific error - fetch fresh design to see who won
            fresh_design = Design.get_by_code(code)
            other_reviewer = fresh_design.reviewer_name if fresh_design else "ناظر دیگر"
            
            await _delete_my_messages(context.bot, user.user_id, design)
            await safe_edit_message(
                query,
                f"⚠️ این طرح همین الان توسط {other_reviewer} پردازش شد.\n"
                f"شما کمی دیر رسیدید."
            )
            return

        if not product_line or not product_line.is_fully_configured():
            logging.error(f"Groups not configured for product line {design.product_line_id}")
            await safe_edit_message(
                query,
                "❌ تایید ثبت شد اما گروه‌های ارسال تنظیم نیستند. با Sudo تماس بگیرید."
            )
        else:
            # Send mockups to Products Group and record each message
            for i, fid in enumerate(design.mockup_file_ids):
                cap = f"کد: {code} ({i+1}/{len(design.mockup_file_ids)})"
                try:
                    if fid.startswith(('AgAC', 'AQA')):
                        m = await context.bot.send_photo(
                            product_line.group_products, photo=fid, caption=cap
                        )
                    else:
                        m = await context.bot.send_document(
                            product_line.group_products, document=fid, caption=cap
                        )
                    DesignGroupMessage.record(
                        design_id=design.id,
                        code=code,
                        group_type='products',
                        chat_id=product_line.group_products,
                        message_id=m.message_id,
                        file_id=fid,
                        file_index=i
                    )
                except Exception as e:
                    logging.error(f"Error sending mockup {i} to products group: {e}")

            # Send renamed prints to Print Group and record each message
            unique_prints = list(dict.fromkeys(design.print_file_ids))
            for i, fid in enumerate(unique_prints):
                try:
                    # FIX: Try to use copy_message for efficiency (requires message_id)
                    # Since we don't have the original message_id, we must download and re-upload
                    # But we can at least optimize the filename generation
                    
                    file = await context.bot.get_file(fid)
                    
                    # Check file size - if over 20MB, Telegram API can't download it
                    if file.file_size and file.file_size > 20 * 1024 * 1024:
                        logging.warning(f"Print file {i} for {code} is too large ({file.file_size} bytes), skipping download")
                        # Send with original filename
                        m = await context.bot.send_document(
                            product_line.group_print,
                            document=fid,
                            caption=f"⚠️ {code} - فایل بزرگ (نام تغییر نکرد)"
                        )
                    else:
                        # Download and rename for files under 20MB
                        file_bytes = await file.download_as_bytearray()
                        ext = (file.file_path.split('.')[-1].lower()
                               if file.file_path and '.' in file.file_path else 'png')
                        new_filename = (f"{code}.{ext}" if len(unique_prints) == 1
                                       else f"{code}_{i+1}.{ext}")
                        m = await context.bot.send_document(
                            product_line.group_print,
                            document=InputFile(BytesIO(file_bytes), filename=new_filename)
                        )
                    
                    DesignGroupMessage.record(
                        design_id=design.id,
                        code=code,
                        group_type='print',
                        chat_id=product_line.group_print,
                        message_id=m.message_id,
                        file_id=fid,
                        file_index=i
                    )
                except Exception as e:
                    logging.error(f"Error sending print file {i}: {e}")

        # Notify editor
        try:
            await context.bot.send_message(
                design.editor_user_id,
                f"🟢 طرح {code} توسط {user.first_name} تایید و ارسال شد!"
            )
        except Exception as e:
            logging.error(f"Failed to notify editor: {e}")

        # Notify sudo
        await _notify_sudo(context.bot, design, product_line, user, action=DesignStatus.APPROVED)

        # Delete other reviewers' messages, then edit acting reviewer's, then clean up
        await _delete_other_reviewer_messages(context.bot, design, user.user_id)
        await safe_edit_message(query, f"✅ تایید شد: {code}")
        await _delete_my_messages(context.bot, user.user_id, design)

    elif action == "reject":
        won = design.reject(user.user_id, user.first_name)

        if not won:
            await _delete_my_messages(context.bot, user.user_id, design)
            await safe_edit_message(query, "⚠️ این طرح قبلاً توسط ناظر دیگری پردازش شده است.")
            return

        # Notify editor
        try:
            await context.bot.send_message(
                design.editor_user_id,
                f"🔴 طرح {code} توسط {user.first_name} رد شد."
            )
        except Exception as e:
            logging.error(f"Failed to notify editor: {e}")

        # Notify sudo
        await _notify_sudo(context.bot, design, product_line, user, action=DesignStatus.REJECTED)

        await _delete_other_reviewer_messages(context.bot, design, user.user_id)
        await safe_edit_message(query, f"❌ رد شد: {code}")
        await _delete_my_messages(context.bot, user.user_id, design)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_my_messages(bot, reviewer_user_id: int, design: Design) -> None:
    """Delete only this reviewer's messages"""
    msg_ids = design.get_reviewer_messages(reviewer_user_id)
    if msg_ids:
        await delete_messages(bot, reviewer_user_id, msg_ids)


async def _delete_other_reviewer_messages(bot, design: Design, acting_reviewer_id: int) -> None:
    """Delete messages from all reviewers EXCEPT the one who acted"""
    for reviewer_user_id, msg_ids in design.all_reviewer_message_pairs():
        if reviewer_user_id != acting_reviewer_id and msg_ids:
            await delete_messages(bot, reviewer_user_id, msg_ids)


async def _notify_sudo(bot, design: Design, product_line, acting_reviewer, action: DesignStatus) -> None:
    """Send approval/rejection detail to sudo"""
    action_text = "✅ تایید" if action == DesignStatus.APPROVED else "❌ رد"
    product_name = product_line.name_fa if product_line else f"ID:{design.product_line_id}"

    try:
        await bot.send_message(
            SUDO_USER_ID,
            f"{action_text} شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔖 کد: {design.code}\n"
            f"📦 خط تولید: {product_name}\n"
            f"👤 طراح: {design.editor_name}\n"
            f"✅ ناظر: {acting_reviewer.first_name}"
        )
    except Exception as e:
        logging.error(f"Failed to notify sudo of review action: {e}")