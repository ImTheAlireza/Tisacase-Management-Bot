import traceback
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, InputFile
from telegram.ext import ContextTypes
from utils.decorators import require_role
from models.design import Design
from models.design_group_message import DesignGroupMessage
from models.user import User
from models.product_line import ProductLine
from utils.helpers import safe_edit_message, delete_messages
from config.settings import SUDO_USER_ID, MAX_FILE_SIZE_DOWNLOAD_MB, LOG_GROUP_ID
import logging
from io import BytesIO
from utils.enums import DesignStatus
from utils.callback_lock import deduplicate_callback

LOG_TAG = "[REVIEW]"


async def _log_to_group(context, text: str) -> None:
    """Send a log message to the LOG_GROUP_ID channel (sudo-only)."""
    try:
        await context.bot.send_message(
            chat_id=LOG_GROUP_ID,
            text=text,
            parse_mode="HTML"
        )
    except Exception as e:
        logging.error(f"{LOG_TAG} Failed to send log to LOG_GROUP_ID: {e}")


def _review_key(update, context) -> str:
    """Lock key: action + code e.g. 'approve_TS001'"""
    code = update.callback_query.data.split('_', 1)[1]
    return f"review_{code}"


@require_role('reviewer', 'sudo')
@deduplicate_callback(_review_key)
async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    query = update.callback_query
    await query.answer()

    user = User.get_by_id(query.from_user.id)
    action, code = query.data.split('_', 1)

    logging.info(f"{LOG_TAG} START | action={action} | code={code} | user={user.first_name}({user.user_id})")

    design = Design.get_by_code(code)

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------
    if not design:
        logging.warning(f"{LOG_TAG} Design {code} not found")
        await _log_to_group(context, f"❌ <b>{code}</b> NOT FOUND")
        await safe_edit_message(query, f"❌ طرح با کد {code} در سیستم یافت نشد.")
        return

    if design.id is None:
        logging.error(f"{LOG_TAG} Design {code} has id=None")
        await _log_to_group(context, f"❌ <b>{code}</b> id=None — CRITICAL")
        await safe_edit_message(query, "❌ خطای داخلی: طرح بدون شناسه (ID) است.")
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
            f"⚠️ این طرح قبلاً پردازش شده است.\nوضعیت: {status_fa}\nتوسط: {reviewer_name}"
        )
        return

    await safe_edit_message(query, "⏳ در حال پردازش...")

    product_line = ProductLine.get_by_id(design.product_line_id)
    is_configured = product_line and product_line.is_fully_configured()

    # ── APPROVE ────────────────────────────────────────────────
    if action == "approve":

        won = design.approve(user.user_id, user.first_name)
        if not won:
            fresh = Design.get_by_code(code)
            other = fresh.reviewer_name if fresh else "ناظر دیگر"
            await _log_to_group(context, f"⚠️ Approve LOST: <b>{code}</b> → {other}")
            await safe_edit_message(query, f"⚠️ این طرح همین الان توسط {other} پردازش شد.\nشما کمی دیر رسیدید.")
            return

        # ── Send files to groups ──────────────────────────────
        mockup_results = []  # (file_id, success, detail)
        print_results = []   # (file_id, success, detail)

        if is_configured:

            # ── MOCKUPS → PRODUCTS GROUP ──────────────────────
            for i, fid in enumerate(design.mockup_file_ids):
                cap = f"کد: {code} ({i+1}/{len(design.mockup_file_ids)})"
                has_type = fid in design.file_types
                is_photo = design.file_types.get(fid) == 'photo'

                try:
                    # ── Fallback: if file_types is missing, try photo then document
                    if not has_type:
                        try:
                            m = await context.bot.send_photo(
                                product_line.group_products, photo=fid, caption=cap
                            )
                        except Exception:
                            m = await context.bot.send_document(
                                product_line.group_products, document=fid, caption=cap
                            )
                    elif is_photo:
                        m = await context.bot.send_photo(
                            product_line.group_products, photo=fid, caption=cap
                        )
                    else:
                        m = await context.bot.send_document(
                            product_line.group_products, document=fid, caption=cap
                        )

                    # Record in DB
                    try:
                        DesignGroupMessage.record(
                            design_id=design.id, code=code, group_type='products',
                            chat_id=product_line.group_products, message_id=m.message_id,
                            file_id=fid, file_index=i
                        )
                    except Exception as record_err:
                        logging.exception(f"{LOG_TAG} Mockup {i+1} RECORDBAD: {code}")

                    mockup_results.append((fid, True, f"msg={m.message_id}"))
                    logging.info(f"{LOG_TAG} Mockup {i+1}/{len(design.mockup_file_ids)} OK → {product_line.group_products}")

                except Exception as e:
                    logging.exception(f"{LOG_TAG} Mockup {i+1} FAILED: {code} | fid={fid[:30]}")
                    mockup_results.append((fid, False, str(e)[:100]))

            # ── PRINT FILES → PRINT GROUP ─────────────────────
            unique_prints = list(dict.fromkeys(design.print_file_ids))
            print_count = len(unique_prints)

            for i, fid in enumerate(unique_prints):
                try:
                    file = await context.bot.get_file(fid)

                    if file.file_path and '.' in file.file_path:
                        ext = file.file_path.split('.')[-1].lower()
                    else:
                        ext = 'png'

                    new_filename = f"{code}.{ext}" if print_count == 1 else f"{code}_{i+1}.{ext}"

                    max_size_bytes = MAX_FILE_SIZE_DOWNLOAD_MB * 1024 * 1024
                    if file.file_size and file.file_size > max_size_bytes:
                        m = await context.bot.send_document(
                            chat_id=product_line.group_print, document=fid,
                            caption=f"⚠️ {new_filename} (فایل بزرگ — نام تغییر نکرد)"
                        )
                    else:
                        file_bytes = await file.download_as_bytearray()
                        m = await context.bot.send_document(
                            chat_id=product_line.group_print,
                            document=InputFile(BytesIO(file_bytes), filename=new_filename)
                        )

                    try:
                        DesignGroupMessage.record(
                            design_id=design.id, code=code, group_type='print',
                            chat_id=product_line.group_print, message_id=m.message_id,
                            file_id=fid, file_index=i
                        )
                    except Exception as record_err:
                        logging.exception(f"{LOG_TAG} Print {i+1} RECORDBAD: {code}")

                    print_results.append((fid, True, f"fn={new_filename} msg={m.message_id}"))
                    logging.info(f"{LOG_TAG} Print {i+1}/{print_count} OK → {product_line.group_print}")

                except Exception as e:
                    logging.exception(f"{LOG_TAG} Print {i+1} FAILED: {code} | fid={fid[:30]}")
                    print_results.append((fid, False, str(e)[:100]))

        else:
            reason = "product_line is None" if not product_line else f"missing: {product_line.missing_groups()}"
            logging.error(f"{LOG_TAG} FILES SKIPPED: {code} — {reason}")

        # ── Final summary to reviewer ─────────────────────────
        total_mockups = len(design.mockup_file_ids)
        total_prints = len(unique_prints) if is_configured else 0
        mockup_ok = sum(1 for _, ok, _ in mockup_results if ok) if mockup_results else 0
        mockup_fail = sum(1 for _, ok, _ in mockup_results if not ok) if mockup_results else 0
        print_ok = sum(1 for _, ok, _ in print_results if ok) if print_results else 0
        print_fail = sum(1 for _, ok, _ in print_results if not ok) if print_results else 0
        total_ok = mockup_ok + print_ok
        total_fail = mockup_fail + print_fail
        total_expected = total_mockups + total_prints

        if total_fail > 0:
            status_msg = f"✅ تایید شد: {code}\n\n📊 نتیجه ارسال:"
            status_msg += f"\n• موکاپ: {mockup_ok}/{total_mockups}"
            status_msg += f"\n• چاپی: {print_ok}/{total_prints}"
            failed = []
            for fid, ok, detail in mockup_results:
                if not ok:
                    failed.append(f"• موکاپ: {detail[:80]}")
            for fid, ok, detail in print_results:
                if not ok:
                    failed.append(f"• چاپی: {detail[:80]}")
            if failed:
                status_msg += f"\n\n❌ خطاها:\n" + "\n".join(failed[:5])
            status_msg += "\n\n⚠️ لطفاً به Sudo اطلاع دهید."
            await safe_edit_message(query, status_msg)
        elif not is_configured:
            await safe_edit_message(
                query,
                f"✅ تایید شد: {code}\n\n⚠️ خط تولید تنظیم نشده — فایل‌ها ارسال نشدند.\nلطفاً به Sudo اطلاع دهید."
            )
        else:
            await safe_edit_message(query, f"✅ تایید شد: {code}\n\n📊 {total_ok}/{total_expected} فایل ارسال شد.")

        # ── Compact log to group: ONE message with everything ─
        log_lines = [f"✅ <b>APPROVE {code}</b> by {user.first_name}"]
        if is_configured:
            log_lines.append(f"📦 {product_line.name_fa} | PL ID: {product_line.id}")
            log_lines.append(f"📤 Products: {product_line.group_products} | Print: {product_line.group_print}")
        else:
            log_lines.append(f"⚠️ <b>PRODUCT LINE NOT CONFIGURED</b>")
            if product_line:
                log_lines.append(f"Missing: {product_line.missing_groups()}")

        if mockup_results:
            log_lines.append(f"🖼 Mockups: {mockup_ok}/{len(mockup_results)} OK" + (f", {mockup_fail} FAILED" if mockup_fail else ""))
        else:
            log_lines.append(f"🖼 Mockups: 0 files")

        if print_results:
            log_lines.append(f"🖨 Prints: {print_ok}/{len(print_results)} OK" + (f", {print_fail} FAILED" if print_fail else ""))
        else:
            log_lines.append(f"🖨 Prints: 0 files")

        # Log failures with details
        failures = [(fid, d) for fid, ok, d in mockup_results + print_results if not ok]
        if failures:
            log_lines.append(f"\n❌ <b>FAILURES:</b>")
            for fid, detail in failures:
                log_lines.append(f"• {fid[:20]}... — <pre>{detail[:100]}</pre>")

        await _log_to_group(context, "\n".join(log_lines))
        logging.info(f"{LOG_TAG} DONE | {code} | ok={total_ok}/{total_expected} fail={total_fail}")

    # ── REJECT ─────────────────────────────────────────────────
    elif action == "reject":

        won = design.reject(user.user_id, user.first_name)
        if not won:
            await safe_edit_message(query, "⚠️ این طرح قبلاً توسط ناظر دیگری پردازش شده است.")
            return

        await _log_to_group(context, f"❌ <b>REJECT {code}</b> by {user.first_name}")
        await safe_edit_message(query, f"❌ رد شد: {code}")

    else:
        return

    # ── NOTIFICATIONS ──────────────────────────────────────────
    submitter_id = design.editor_user_id
    recipients = set()
    if submitter_id:
        recipients.add(submitter_id)
    recipients.add(SUDO_USER_ID)

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
            logging.exception(f"{LOG_TAG} Notification FAILED for {uid}")

    # ── CLEANUP ────────────────────────────────────────────────
    try:
        await _delete_other_reviewer_messages(context.bot, design, user.user_id)
    except Exception as e:
        logging.exception(f"{LOG_TAG} Cleanup other reviewers FAILED: {code}")

    try:
        await _delete_my_messages(context.bot, user.user_id, design)
    except Exception as e:
        logging.exception(f"{LOG_TAG} Cleanup my messages FAILED: {code}")

    logging.info(f"{LOG_TAG} END | {code} | {action}")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _delete_my_messages(bot, reviewer_user_id: int, design: Design) -> None:
    msg_ids = design.get_reviewer_messages(reviewer_user_id)
    if msg_ids:
        logging.info(f"{LOG_TAG} Deleting {len(msg_ids)} msgs from reviewer {reviewer_user_id} for {design.code}")
        await delete_messages(bot, reviewer_user_id, msg_ids)


async def _delete_other_reviewer_messages(bot, design: Design, acting_reviewer_id: int) -> None:
    for reviewer_user_id, msg_ids in design.all_reviewer_message_pairs():
        if reviewer_user_id != acting_reviewer_id and msg_ids:
            logging.info(f"{LOG_TAG} Deleting {len(msg_ids)} msgs from reviewer {reviewer_user_id} for {design.code}")
            await delete_messages(bot, reviewer_user_id, msg_ids)
