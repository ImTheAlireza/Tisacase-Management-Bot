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

    user = User.get_by_id(query.from_user.id)
    action, code = query.data.split('_', 1)

    design = Design.get_by_code(code)

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------
    if not design:
        await safe_edit_message(query, f"❌ طرح با کد {code} در سیستم یافت نشد.")
        return

    if design.id is None:
        logging.error(f"Design {code} has id=None in review_callback")
        await safe_edit_message(
            query,
            "❌ خطای داخلی: طرح بدون شناسه (ID) است. به Sudo اطلاع دهید."
        )
        return

    if design.status != DesignStatus.PENDING:
        status_map = {
            DesignStatus.APPROVED: 'تایید شده',
            DesignStatus.REJECTED: 'رد شده',
            DesignStatus.DELETED: 'حذف شده'
        }
        status_fa = status_map.get(design.status, design.status)
        reviewer_name = design.reviewer_name or "ناظر دیگر"

        await safe_edit_message(
            query,
            f"⚠️ این طرح قبلاً پردازش شده است.\n"
            f"وضعیت: {status_fa}\n"
            f"توسط: {reviewer_name}"
        )
        return

    await safe_edit_message(query, "⏳ در حال پردازش...")

    product_line = ProductLine.get_by_id(design.product_line_id)

    # ============================================================
    # APPROVE
    # ============================================================
    if action == "approve":

        won = design.approve(user.user_id, user.first_name)
        if not won:
            fresh = Design.get_by_code(code)
            other = fresh.reviewer_name if fresh else "ناظر دیگر"
            await safe_edit_message(
                query,
                f"⚠️ این طرح همین الان توسط {other} پردازش شد.\n"
                f"شما کمی دیر رسیدید."
            )
            return

        # ---------------- Send files to groups ----------------
        if product_line and product_line.is_fully_configured():

            # Mockups → products group
            for i, fid in enumerate(design.mockup_file_ids):
                cap = f"کد: {code} ({i+1}/{len(design.mockup_file_ids)})"
                try:
                    if fid.startswith(('AgAC', 'AQA')):
                        m = await context.bot.send_photo(
                            product_line.group_products,
                            photo=fid,
                            caption=cap
                        )
                    else:
                        m = await context.bot.send_document(
                            product_line.group_products,
                            document=fid,
                            caption=cap
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
                    logging.error(f"Error sending mockup {i}: {e}")

            # Print files → print group
            unique_prints = list(dict.fromkeys(design.print_file_ids))
            print_count = len(unique_prints)

            for i, fid in enumerate(unique_prints):
                try:
                    # ✅ Get file info to extract extension
                    file = await context.bot.get_file(fid)

                    # Extract extension from file path
                    if file.file_path and '.' in file.file_path:
                        ext = file.file_path.split('.')[-1].lower()
                    else:
                        ext = 'png'

                    # ✅ Build filename
                    # Single print file → TS001.ext
                    # Multiple print files → TS001_1.ext, TS001_2.ext ...
                    if print_count == 1:
                        new_filename = f"{code}.{ext}"
                    else:
                        new_filename = f"{code}_{i+1}.{ext}"

                    # ✅ Check file size — Telegram limits download to 20MB
                    if file.file_size and file.file_size > 20 * 1024 * 1024:
                        # Too large to download and rename
                        # Send as-is with code as caption
                        logging.warning(
                            f"Print file {i+1} for {code} is too large "
                            f"({file.file_size} bytes) — sending without rename"
                        )
                        m = await context.bot.send_document(
                            chat_id=product_line.group_print,
                            document=fid,
                            caption=f"⚠️ {new_filename} (فایل بزرگ — نام تغییر نکرد)"
                        )
                    else:
                        # ✅ Download, rename and re-upload
                        file_bytes = await file.download_as_bytearray()
                        m = await context.bot.send_document(
                            chat_id=product_line.group_print,
                            document=InputFile(
                                BytesIO(file_bytes),
                                filename=new_filename
                            )
                        )

                    # ✅ Always record the sent message
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
                    logging.error(
                        f"Error sending print file {i+1}/{print_count} "
                        f"for {code}: {e}"
                    )

        else:
            logging.error(f"Groups not configured for line {design.product_line_id}")

        await safe_edit_message(query, f"✅ تایید شد: {code}")

    # ============================================================
    # REJECT
    # ============================================================
    elif action == "reject":

        won = design.reject(user.user_id, user.first_name)
        if not won:
            await safe_edit_message(
                query,
                "⚠️ این طرح قبلاً توسط ناظر دیگری پردازش شده است."
            )
            return

        await safe_edit_message(query, f"❌ رد شد: {code}")

    else:
        return

    # ============================================================
    # ✅ ✅ ✅ UNIFIED NOTIFICATION SYSTEM (NO DUPLICATES)
    # ============================================================

    from config.settings import SUDO_USER_ID

    submitter_id = design.editor_user_id
    reviewer_id = user.user_id

    recipients = set()

    # Always notify submitter
    if submitter_id:
        recipients.add(submitter_id)

    # Always notify sudo
    recipients.add(SUDO_USER_ID)

    # Build message
    status_emoji = "🟢" if action == "approve" else "🔴"
    status_text = "تایید شد" if action == "approve" else "رد شد"

    notification_text = (
        f"{status_emoji} طرح {code} {status_text}!\n"
        "━━━━━━━━━━━━━━━━━━\n"
        f"🔖 کد: {code}\n"
        f"📦 خط تولید: {product_line.name_fa if product_line else '-'}\n"
        f"👤 طراح: {design.editor_name}\n"
        f"✅ ناظر: {user.first_name}"
    )

    for uid in recipients:
        try:
            await context.bot.send_message(chat_id=uid, text=notification_text)
        except Exception as e:
            logging.warning(f"Notification failed for {uid}: {e}")

    # ------------------------------------------------------------
    # Cleanup reviewer messages
    # ------------------------------------------------------------
    await _delete_other_reviewer_messages(context.bot, design, user.user_id)
    await _delete_my_messages(context.bot, user.user_id, design)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_my_messages(bot, reviewer_user_id: int, design: Design) -> None:
    msg_ids = design.get_reviewer_messages(reviewer_user_id)
    if msg_ids:
        await delete_messages(bot, reviewer_user_id, msg_ids)


async def _delete_other_reviewer_messages(bot, design: Design, acting_reviewer_id: int) -> None:
    for reviewer_user_id, msg_ids in design.all_reviewer_message_pairs():
        if reviewer_user_id != acting_reviewer_id and msg_ids:
            await delete_messages(bot, reviewer_user_id, msg_ids)

