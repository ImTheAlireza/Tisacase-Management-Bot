import asyncio
import html
import re
from urllib.parse import quote, unquote, urlsplit
from telegram import (
    Update,
    InputFile,
    InputMediaPhoto,
    InputMediaDocument,
)
from telegram.ext import ContextTypes
from telegram.error import BadRequest, RetryAfter
from utils.decorators import require_role
from models.design import Design
from models.design_group_message import DesignGroupMessage
from models.user import User
from models.product_line import ProductLine
from utils.helpers import safe_edit_message, delete_messages, send_with_retry, safe_answer_callback
from config.settings import (
    BOT_TOKEN,
    SUDO_USER_ID,
    MAX_FILE_SIZE_DOWNLOAD_MB,
    MEDIA_GROUP_MAX_UPLOAD_BYTES,
    LOG_GROUP_ID,
)
import logging
from io import BytesIO
from utils.enums import DesignStatus
from utils.callback_lock import callback_lock, deduplicate_callback

LOG_TAG = "[REVIEW]"
REJECT_REASON_PROMPT = "دلیل رد این طرح رو روی همین پیام ریپلای کنید."
REJECT_REASON_STATE_KEY = "awaiting_reject_reasons"
MAX_REJECTION_REASON_LENGTH = 3000


def _safe_telegram_error(error: Exception) -> str:
    """Never copy a bot token from a network exception into logs or messages."""
    detail = str(error)
    if BOT_TOKEN:
        for token in (BOT_TOKEN, quote(BOT_TOKEN, safe='')):
            detail = detail.replace(token, '[REDACTED]')
    return detail[:100]


__all__ = [
    "review_callback",
    "handle_reject_reason_reply",
    "_send_media_with_retry",
]


async def _send_media_with_retry(send, label: str, max_retries: int = 3):
    """Backward-compatible wrapper for older imports/tests.

    The shared implementation lives in utils.helpers.send_with_retry; keep this
    symbol exported so main/tests/deployments that import it do not fail.
    """
    return await send_with_retry(send, label, max_retries=max_retries)


async def _send_log_to_group(context, text: str) -> None:
    """Deliver one log message to LOG_GROUP_ID (best-effort)."""
    try:
        await send_with_retry(
            lambda: context.bot.send_message(
                chat_id=LOG_GROUP_ID,
                text=text,
                parse_mode="HTML"
            ),
            f"{LOG_TAG} send log to LOG_GROUP_ID"
        )
    except Exception as e:
        logging.error(f"{LOG_TAG} Failed to send log to LOG_GROUP_ID: {_safe_telegram_error(e)}")


def _log_to_group(context, text: str) -> None:
    """Queue a log message for LOG_GROUP_ID without stalling the handler.

    Telegram flood control on the log chat can force multi-second waits (with
    retries, up to ~90s). Awaiting that inside review_callback would hold the
    review lock and delay the user-visible completion of the approve/reject,
    so delivery runs as a background task instead. Logs are best-effort and
    must never block (or crash) the review flow.
    """
    try:
        asyncio.get_running_loop().create_task(
            _send_log_to_group(context, text)
        )
    except RuntimeError:
        # No running event loop (unusual in a handler); just drop the log.
        logging.error(f"{LOG_TAG} No event loop — log not sent: {text[:120]}")


def _review_key(update, context) -> str:
    """Lock key: action + code e.g. 'approve_TS001'"""
    code = update.callback_query.data.split('_', 1)[1]
    return f"review_{code}"


def _truncate_rejection_reason(reason: str) -> str:
    if len(reason) <= MAX_REJECTION_REASON_LENGTH:
        return reason
    return reason[:MAX_REJECTION_REASON_LENGTH] + "\n…"


def _clear_reject_reason_state(
    context: ContextTypes.DEFAULT_TYPE,
    code: str | None = None
) -> None:
    pending = context.user_data.get(REJECT_REASON_STATE_KEY)
    if not isinstance(pending, dict):
        context.user_data.pop(REJECT_REASON_STATE_KEY, None)
        return

    if code is None:
        context.user_data.pop(REJECT_REASON_STATE_KEY, None)
        return

    for key, value in list(pending.items()):
        if value == code:
            pending.pop(key, None)

    if not pending:
        context.user_data.pop(REJECT_REASON_STATE_KEY, None)


async def _send_decision_notifications(
    context: ContextTypes.DEFAULT_TYPE,
    action: str,
    code: str,
    design: Design,
    reviewer: User,
    product_line: ProductLine | None,
    rejection_reason: str | None = None
) -> None:
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
        f"✅ ناظر: {reviewer.first_name}"
    )

    if action == "reject" and rejection_reason:
        notification_text += f"\n\n📝 دلیل رد:\n{_truncate_rejection_reason(rejection_reason)}"

    for uid in recipients:
        try:
            await send_with_retry(
                lambda uid=uid: context.bot.send_message(chat_id=uid, text=notification_text),
                f"{LOG_TAG} decision notification to {uid}"
            )
        except Exception as e:
            logging.error(f"{LOG_TAG} Notification FAILED for {uid}: {_safe_telegram_error(e)}")


async def _cleanup_after_decision(
    context: ContextTypes.DEFAULT_TYPE,
    design: Design,
    acting_reviewer_id: int,
    code: str
) -> None:
    try:
        await _delete_other_reviewer_messages(context.bot, design, acting_reviewer_id)
    except Exception:
        logging.exception(f"{LOG_TAG} Cleanup other reviewers FAILED: {code}")

    try:
        await _delete_my_messages(context.bot, acting_reviewer_id, design)
    except Exception:
        logging.exception(f"{LOG_TAG} Cleanup my messages FAILED: {code}")


async def _finalize_rejection_with_reason(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    code: str,
    reason: str,
    user: User,
    expected_design_id: int
) -> None:
    lock_key = f"review_{code}"
    acquired = await callback_lock.acquire(lock_key)
    if not acquired:
        await update.message.reply_text("⏳ این طرح در حال پردازش است. لطفاً چند لحظه صبر کنید.")
        return

    try:
        design = Design.get_by_code(code)
        if not design or design.id != expected_design_id:
            _clear_reject_reason_state(context, code)
            await update.message.reply_text("⚠️ این طرح قبلاً پردازش یا حذف شده است.")
            return

        if design.status != DesignStatus.PENDING:
            _clear_reject_reason_state(context, code)
            reviewer_name = design.reviewer_name or "ناظر دیگر"
            await update.message.reply_text(
                f"⚠️ این طرح قبلاً توسط {reviewer_name} پردازش شده است."
            )
            return

        product_line = ProductLine.get_by_id(design.product_line_id)
        won = design.reject(user.user_id, user.first_name)
        if not won:
            _clear_reject_reason_state(context, code)
            await update.message.reply_text("⚠️ این طرح قبلاً توسط ناظر دیگری پردازش شده است.")
            return

        escaped_code = html.escape(code)
        escaped_name = html.escape(user.first_name or str(user.user_id))
        safe_reason = _truncate_rejection_reason(reason)
        escaped_reason = html.escape(safe_reason)
        _log_to_group(
            context,
            f"❌ <b>REJECT {escaped_code}</b> by {escaped_name}\n"
            f"📝 <b>Reason:</b>\n<pre>{escaped_reason}</pre>"
        )

        await _send_decision_notifications(
            context=context,
            action="reject",
            code=code,
            design=design,
            reviewer=user,
            product_line=product_line,
            rejection_reason=reason
        )

        await update.message.reply_text(
            f"❌ رد شد: {code}\n\n📝 دلیل ثبت شد:\n{safe_reason}"
        )
        _clear_reject_reason_state(context, code)
        await _cleanup_after_decision(context, design, user.user_id, code)
        logging.info(f"{LOG_TAG} END | {code} | reject with reason")

    finally:
        await callback_lock.release(lock_key)


async def handle_reject_reason_reply(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
) -> bool:
    """Accept replies to reason prompts sent by versions before one-tap reject.

    New rejections never ask for a reason. Unrelated messages must not be
    intercepted, and a stale prompt must not reject a new design that reused
    the old code.
    """
    message = update.message
    if not message or not message.text or message.chat.type != "private":
        return False
    reply_msg = message.reply_to_message
    if not reply_msg:
        return False

    pending = context.user_data.get(REJECT_REASON_STATE_KEY)
    code = pending.get(str(reply_msg.message_id)) if isinstance(pending, dict) else None
    prompt_matches = (
        reply_msg.caption == REJECT_REASON_PROMPT or reply_msg.text == REJECT_REASON_PROMPT
    )
    if not code and not prompt_matches:
        return False

    user = User.get_by_id(update.effective_user.id)
    if not user or not user.is_active:
        await message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
        return True
    if not user.is_sudo and user.get_effective_role() not in ('reviewer', 'sudo'):
        await message.reply_text("🚫 فقط ناظر می‌تواند طرح را رد کند.")
        return True

    if code:
        design = Design.get_by_code(code)
        valid_ids = design.get_reviewer_messages(user.user_id) if design else []
        if not design or design.status != DesignStatus.PENDING or str(reply_msg.message_id) not in {
            str(mid) for mid in valid_ids
        }:
            _clear_reject_reason_state(context, code)
            await message.reply_text("⚠️ این درخواست قدیمی است؛ طرح را از فهرست در انتظار باز کنید.")
            return True
    else:
        design = Design.get_pending_by_reviewer_message(user.user_id, reply_msg.message_id)
        if not design:
            await message.reply_text("⚠️ این درخواست قدیمی است؛ طرح را از فهرست در انتظار باز کنید.")
            return True
        code = design.code

    reason = message.text.strip()
    if not reason:
        return False
    await _finalize_rejection_with_reason(update, context, code, reason, user, design.id)
    return True


@require_role('reviewer', 'sudo')
@deduplicate_callback(_review_key)
async def review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:

    query = update.callback_query
    # Answer first, but never let a stale/duplicate query kill the review.
    # Telegram invalidates the query after a short window (delayed processing,
    # double-taps), and losing the cosmetic answer must not lose the actual
    # approve/reject work that follows.
    await safe_answer_callback(query)

    # require_role has already loaded and authorized this user.
    user = context.user_data['db_user']
    action, code = query.data.split('_', 1)

    logging.info(f"{LOG_TAG} START | action={action} | code={code} | user={user.first_name}({user.user_id})")

    design = Design.get_by_code(code)

    # -------------------------------------------------
    # Validation
    # -------------------------------------------------
    if not design:
        logging.warning(f"{LOG_TAG} Design {code} not found")
        _log_to_group(context, f"❌ <b>{code}</b> NOT FOUND")
        await safe_edit_message(query, f"❌ طرح با کد {code} در سیستم یافت نشد.")
        return

    if design.id is None:
        logging.error(f"{LOG_TAG} Design {code} has id=None")
        _log_to_group(context, f"❌ <b>{code}</b> id=None — CRITICAL")
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

    # Reject immediately; the DB's conditional update arbitrates reviewer races.
    if action == "reject":
        product_line = ProductLine.get_by_id(design.product_line_id)
        if not design.reject(user.user_id, user.first_name):
            _clear_reject_reason_state(context, code)
            await safe_edit_message(query, "⚠️ این طرح قبلاً توسط ناظر دیگری پردازش شده است.")
            return

        _clear_reject_reason_state(context, code)
        _log_to_group(
            context,
            f"❌ <b>REJECT {html.escape(code)}</b> by "
            f"{html.escape(user.first_name or str(user.user_id))}"
        )
        await safe_edit_message(query, f"❌ رد شد: {code}")
        await _send_decision_notifications(
            context=context, action="reject", code=code, design=design,
            reviewer=user, product_line=product_line
        )
        await _cleanup_after_decision(context, design, user.user_id, code)
        # The clicked message may have been deleted by cleanup; leave the
        # reviewer a separate, persistent confirmation.
        try:
            await context.bot.send_message(chat_id=user.user_id, text=f"❌ رد شد: {code}")
        except Exception as e:
            logging.error(f"{LOG_TAG} Reviewer confirmation FAILED for {user.user_id}: {_safe_telegram_error(e)}")
        logging.info(f"{LOG_TAG} END | {code} | reject")
        return

    if action != "approve":
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
            _log_to_group(context, f"⚠️ Approve LOST: <b>{code}</b> → {other}")
            await safe_edit_message(query, f"⚠️ این طرح همین الان توسط {other} پردازش شد.\nشما کمی دیر رسیدید.")
            return

        # ── Send files to groups ──────────────────────────────
        # One mockup/print file is sent as a regular message; two or more are
        # sent as Telegram albums (media groups) of up to 10 items each.
        mockup_results = []  # (file_id, success, detail)
        print_results = []   # (file_id, success, detail)
        unique_prints = list(dict.fromkeys(design.print_file_ids))

        if is_configured:

            # ── MOCKUPS → PRODUCTS GROUP ──────────────────────
            mockup_results = await _send_mockups_to_products_group(
                context.bot, design, product_line, code
            )

            # ── PRINT FILES → PRINT GROUP ─────────────────────
            print_results = await _send_prints_to_print_group(
                context.bot, design, unique_prints,
                product_line.group_print, code, product_line.name_fa
            )

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
        print_warnings = [detail for _, ok, detail in print_results
                          if ok and detail.startswith(PRINT_WARNING_PREFIX)]
        warning_text = (
            f"⚠️ {len(print_warnings)} فایل چاپی با هشدار ارسال شد؛ "
            "نام، پسوند و نسخه اصلی را بررسی کنید."
        ) if print_warnings else ""
        if total_fail:
            delivery_alert = f"❌ ارسال {total_fail} فایل ناموفق یا نامشخص بود؛ لطفاً به Sudo اطلاع دهید."
            if print_fail:
                failed_prints = [detail for _, ok, detail in print_results if not ok]
                delivery_alert += "\n" + "\n".join(f"• {detail[:160]}" for detail in failed_prints[:5])
                delivery_alert += (f"\n🔧 بازیابی چاپی توسط Sudo: /resendprint {code}"
                                   "\nپیش از ارسال مجدد، گروه چاپ را برای جلوگیری از چاپ تکراری بررسی کنید.")
        elif not is_configured:
            delivery_alert = "❌ خط تولید تنظیم نشده و فایل‌ها ارسال نشدند؛ لطفاً به Sudo اطلاع دهید."
        else:
            delivery_alert = ""

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
            if warning_text:
                status_msg += f"\n\n{warning_text}"
            status_msg += "\n\n⚠️ لطفاً به Sudo اطلاع دهید."
            await safe_edit_message(query, status_msg)
        elif not is_configured:
            await safe_edit_message(
                query,
                f"✅ تایید شد: {code}\n\n⚠️ خط تولید تنظیم نشده — فایل‌ها ارسال نشدند.\nلطفاً به Sudo اطلاع دهید."
            )
        else:
            status_msg = f"✅ تایید شد: {code}\n\n📊 {total_ok}/{total_expected} فایل ارسال شد."
            if warning_text:
                status_msg += f"\n\n{warning_text}"
            await safe_edit_message(query, status_msg)

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

        if print_warnings:
            log_lines.append(f"⚠️ Prints with unchanged/unverified names: {len(print_warnings)}")

        # Log failures with details
        failures = [(fid, d) for fid, ok, d in mockup_results + print_results if not ok]
        if failures:
            log_lines.append(f"\n❌ <b>FAILURES:</b>")
            for fid, detail in failures:
                log_lines.append(f"• {fid[:20]}... — <pre>{detail[:100]}</pre>")

        _log_to_group(context, "\n".join(log_lines))
        logging.info(f"{LOG_TAG} DONE | {code} | ok={total_ok}/{total_expected} fail={total_fail}")

    await _send_decision_notifications(
        context=context,
        action="approve",
        code=code,
        design=design,
        reviewer=user,
        product_line=product_line
    )

    await _cleanup_after_decision(context, design, user.user_id, code)
    alerts = [text for text in (delivery_alert, warning_text) if text]
    if alerts:
        # Cleanup may delete the edited button message, so persist the alerts.
        for alert_user_id in {user.user_id, SUDO_USER_ID}:
            try:
                await context.bot.send_message(chat_id=alert_user_id, text=f"{code}\n" + "\n".join(alerts))
            except Exception as e:
                logging.error(f"{LOG_TAG} Delivery alert FAILED for {alert_user_id}: {_safe_telegram_error(e)}")

    logging.info(f"{LOG_TAG} END | {code} | {action}")


# ---------------------------------------------------------------------------
# Group delivery — albums (media groups)
# ---------------------------------------------------------------------------

# Telegram rejects albums with fewer than 2 or more than 10 items.
MAX_MEDIA_GROUP_ITEMS = 10
MOCKUP_PHOTO_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}


def _media_group_ranges(count: int, size: int = MAX_MEDIA_GROUP_ITEMS) -> list:
    """Split ``count`` items into [start, end) ranges of at most ``size``.

    Albums need 2-10 items, so a lone trailing item would have to be sent on
    its own; when that happens it is paired with the previous range instead
    (e.g. 11 files → 9 + 2) so everything still travels as an album.
    """
    ranges = [(start, min(start + size, count)) for start in range(0, count, size)]
    if len(ranges) > 1 and ranges[-1][1] - ranges[-1][0] == 1:
        prev_start, prev_end = ranges[-2]
        ranges[-2] = (prev_start, prev_end - 1)
        ranges[-1] = (ranges[-1][0] - 1, ranges[-1][1])
    return ranges


def _record_group_file(design: Design, code: str, group_type: str, chat_id: int,
                       message_id: int, file_id: str, file_index: int) -> None:
    """Best-effort DB record of one sent group message (never raises)."""
    try:
        DesignGroupMessage.record(
            design_id=design.id, code=code, group_type=group_type,
            chat_id=chat_id, message_id=message_id,
            file_id=file_id, file_index=file_index
        )
    except Exception:
        logging.exception(f"{LOG_TAG} {group_type} #{file_index + 1} RECORDBAD: {code}")


async def _resolve_mockup_kind(bot, fid: str, file_types: dict) -> str:
    """Return 'photo', 'document' or 'unknown' for one mockup file_id.

    ``file_types`` (written at upload time) is authoritative. Designs created
    before that field existed are resolved from the Telegram file path;
    'unknown' means the caller must send the file on its own (photo → document
    fallback), because albums need the type up front.
    """
    kind = (file_types or {}).get(fid)
    if kind in ('photo', 'document'):
        return kind
    try:
        file = await bot.get_file(fid)
    except Exception as e:
        logging.warning(f"{LOG_TAG} mockup type lookup failed for {str(fid)[:30]} ({type(e).__name__})")
        return 'unknown'
    ext = _telegram_path_extension(file.file_path)
    if ext in MOCKUP_PHOTO_EXTENSIONS:
        return 'photo'
    return 'document' if ext else 'unknown'


def _mockup_media(fid: str, kind: str, caption: str):
    """Build one album item for a mockup."""
    if kind == 'photo':
        return InputMediaPhoto(media=fid, caption=caption)
    return InputMediaDocument(media=fid, caption=caption)


async def _send_one_mockup(bot, chat_id: int, fid: str, kind: str, caption: str, label: str):
    """Send a single mockup; unknown legacy types try photo, then document."""
    if kind == 'document':
        return await send_with_retry(
            lambda: bot.send_document(chat_id, document=fid, caption=caption), label
        )
    try:
        return await send_with_retry(
            lambda: bot.send_photo(chat_id, photo=fid, caption=caption), label
        )
    except Exception:
        if kind == 'photo':
            raise
        return await send_with_retry(
            lambda: bot.send_document(chat_id, document=fid, caption=caption),
            f"{label} (document fallback)"
        )


async def _send_mockups_to_products_group(bot, design: Design, product_line, code: str) -> list:
    """Deliver approved mockups to the product line's products group.

    A single mockup is sent as a normal photo/document. Two or more are sent
    as albums of up to 10 items, with the caption on the first item of each
    album. If an album fails, its files are retried individually so one bad
    item cannot lose the rest.

    Returns the ``(file_id, ok, detail)`` results used by the approve summary.
    """
    mockups = list(design.mockup_file_ids)
    total = len(mockups)
    results: list = []
    if not mockups:
        return results

    chat_id = product_line.group_products
    kinds = {fid: await _resolve_mockup_kind(bot, fid, design.file_types) for fid in mockups}

    for start, end in _media_group_ranges(total):
        chunk = mockups[start:end]
        # Albums must hold 2-10 items; a single file, and any chunk with an
        # unresolved legacy type, go out one request per file.
        as_album = len(chunk) > 1 and all(kinds[fid] != 'unknown' for fid in chunk)
        label = f"Mockups {start + 1}-{end}/{total} → products group"

        if as_album:
            caption = f"کد: {code} ({start + 1}-{end}/{total})"
            media = [
                _mockup_media(fid, kinds[fid], caption if offset == 0 else "")
                for offset, fid in enumerate(chunk)
            ]
            try:
                msgs = await send_with_retry(
                    lambda: bot.send_media_group(chat_id, media=media), label
                )
            except Exception as e:
                logging.error(f"{LOG_TAG} {label} ALBUM FAILED: {code} | {_safe_telegram_error(e)}")
                msgs = None

            if msgs:
                for offset, (fid, msg) in enumerate(zip(chunk, msgs)):
                    index = start + offset
                    _record_group_file(design, code, 'products', chat_id,
                                       msg.message_id, fid, index)
                    results.append((fid, True, f"msg={msg.message_id}"))
                    logging.info(f"{LOG_TAG} Mockup {index + 1}/{total} OK → {chat_id}")
                continue

        for offset, fid in enumerate(chunk):
            index = start + offset
            file_label = f"Mockup {index + 1}/{total}"
            cap = f"کد: {code} ({index + 1}/{total})"
            try:
                msg = await _send_one_mockup(bot, chat_id, fid, kinds[fid], cap, file_label)
                _record_group_file(design, code, 'products', chat_id,
                                   msg.message_id, fid, index)
                results.append((fid, True, f"msg={msg.message_id}"))
                logging.info(f"{LOG_TAG} {file_label} OK → {chat_id}")
            except Exception as e:
                detail = _safe_telegram_error(e)
                logging.error(f"{LOG_TAG} {file_label} FAILED: {code} | fid={str(fid)[:30]} | {detail}")
                results.append((fid, False, detail))

    return results


PRINT_WARNING_PREFIX = 'warning: '


def _filename_extension(name: str | None) -> str | None:
    """Extract only a safe suffix from a basename, never a directory or URL."""
    if not isinstance(name, str):
        return None
    basename = name.replace('\\', '/').rsplit('/', 1)[-1]
    stem, separator, suffix = basename.rpartition('.')
    if not separator or not stem or not re.fullmatch(r'[A-Za-z0-9]{1,16}', suffix):
        return None
    return suffix.lower()


def _telegram_path_extension(path: str | None) -> str | None:
    """Look at the path's last component only (the URL host contains dots).

    In PTB this path can be a complete URL containing the bot token. It must
    never be used as a filename or included in a caption/log.
    """
    if not isinstance(path, str):
        return None
    try:
        return _filename_extension(unquote(urlsplit(path).path))
    except ValueError:
        return None


def _sniff_print_extension(data: bytes | bytearray) -> str | None:
    """Recover a few unambiguous formats for legacy files without a name.

    In particular, don't guess PDF: PDF-compatible Illustrator (.ai) files can
    have the same header. Unknown formats need an original document name.
    """
    if data.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'png'
    if data.startswith(b'\xff\xd8\xff'):
        return 'jpg'
    if len(data) >= 6 and data[:4] == b'8BPS':
        if data[4:6] == b'\x00\x01':
            return 'psd'
        if data[4:6] == b'\x00\x02':
            return 'psb'
    if len(data) >= 12 and data[:4] == b'RIFF' and data[8:12] == b'WEBP':
        return 'webp'
    # TIFF magic is also used by several camera RAW formats; recover the
    # document's actual name instead of guessing .tif from the shared header.
    if data.startswith((b'GIF87a', b'GIF89a')):
        return 'gif'
    if len(data) >= 26 and data.startswith(b'BM'):
        # The two letters alone are too weak; require a plausible BMP header.
        size = int.from_bytes(data[2:6], 'little')
        pixels = int.from_bytes(data[10:14], 'little')
        dib = int.from_bytes(data[14:18], 'little')
        if (size == len(data) and dib in {12, 40, 52, 56, 64, 108, 124}
                and 14 + dib <= pixels <= len(data)):
            return 'bmp'
    return None


def _compatible_print_extension(expected: str, detected: str) -> bool:
    """Accept equivalent suffixes while rejecting a known format mismatch."""
    aliases = {
        'jpg': {'jpg', 'jpeg', 'jpe', 'jfif'},
        'png': {'png', 'apng'},
    }
    return expected in aliases.get(detected, {detected})


def _use_print_file_id(item: dict, warning: str) -> None:
    item['send_by_id'] = True
    item['warning'] = warning
    item['data'] = None
    item['size'] = 0


async def _describe_print_file(bot, fid: str, code: str, index: int, total: int,
                               max_size_bytes: int, metadata: dict | None = None,
                               extension_override: str | None = None) -> dict:
    """Resolve a safe filename and size before loading the print payload."""
    metadata = metadata if isinstance(metadata, dict) else {}
    # MIME types can be generic or wrong. If no original suffix was saved,
    # verify recognizable bytes instead of inventing a name from MIME alone.
    saved_ext = _filename_extension(metadata.get('file_name'))
    if extension_override is not None:
        # Overrides are an explicit, confirmed Sudo action, never a guess.
        ext = _filename_extension(f"x.{extension_override}")
        if ext is None:
            raise ValueError('invalid print extension override')
    else:
        ext = saved_ext

    # The code is normally alphanumeric, but do not allow path components even
    # if a corrupted/legacy database contains an unexpected value.
    safe_code = ''.join(ch if ch.isalnum() or ch in '_-' else '_' for ch in code)
    base = safe_code if total == 1 else f"{safe_code}_{index + 1}"
    item = {
        'file_id': fid,
        'base': base,
        'filename': f"{base}.{ext}" if ext else None,
        'size': 0,
        'index': index,
        'file': None,
        'data': None,
        'send_by_id': False,
        'warning': ('پسوند را مدیر به‌صورت دستی تعیین کرده است؛ فرمت را بررسی کنید'
                    if extension_override is not None else None),
        'expected_extension': ext,
        'error': None,
    }
    try:
        file = await bot.get_file(fid)
    except Exception as e:
        # getFile can fail for files above Telegram's download limit even when
        # the original file_id remains perfectly usable for sending.
        logging.warning(f"{LOG_TAG} Print {index + 1}/{total} getFile failed ({type(e).__name__}); checking original ID")
        _use_print_file_id(item, 'دریافت اطلاعات ناموفق بود؛ با نام تلگرام ارسال شد')
        return item

    item['file'] = file
    # getFile's generated path is not the original name and its suffix may be
    # based on a generic MIME type (e.g. a PDF-compatible Illustrator file).
    # For legacy records with no saved name, inspect bytes or recover the
    # original document name from Telegram before sending.
    size = file.file_size
    if isinstance(size, int) and size <= 0:
        # A known zero-length or invalid-size source is not a usable print.
        item['error'] = f"فایل {index + 1}: فایل خالی است یا حجم نامعتبر دارد؛ نسخه اصلی لازم است."
    elif not isinstance(size, int):
        _use_print_file_id(item, 'حجم فایل نامشخص بود؛ با نام تلگرام ارسال شد')
    elif size > max_size_bytes:
        _use_print_file_id(item, 'فایل بزرگ است؛ با نام تلگرام ارسال شد')
    else:
        item['size'] = size
    return item


async def _load_print_payload(item: dict) -> None:
    """Download once, verify length, and safely handle missing/unknown data."""
    if item['send_by_id'] or item['data'] is not None:
        return
    try:
        data = await item['file'].download_as_bytearray()
        if len(data) != item['size']:
            raise ValueError(f"incomplete download ({len(data)} of {item['size']} bytes)")
        item['data'] = data
        detected = _sniff_print_extension(data)
        expected = item['expected_extension']
        if expected and detected and not _compatible_print_extension(expected, detected):
            item['data'] = None
            item['error'] = (
                f"فایل {item['index'] + 1}: پسوند با فرمت شناسایی‌شده هماهنگ نیست؛ "
                "نسخه اصلی یا پسوند صحیح را بررسی کنید."
            )
            return
        if item['filename'] is None and detected:
            item['filename'] = f"{item['base']}.{detected}"
        # Otherwise retain the bytes: the original name may still be
        # recoverable from a Telegram document message (unlike getFile).
    except Exception as e:
        # Do not put the download URL (and bot token) into logs or captions.
        logging.warning(f"{LOG_TAG} Print {item['index'] + 1} download failed ({type(e).__name__}); checking original ID")
        _use_print_file_id(item, 'دانلود کامل نشد؛ با نام تلگرام ارسال شد')


async def _recover_original_print_extension(bot, item: dict) -> str | None:
    """Read Telegram's original document name using a temporary private copy.

    getFile has no original filename; a sent Message.document can have one.
    Only the configured Sudo's *private* chat may receive the temporary copy.
    It is silent and deleted immediately on a best-effort basis. An absent name,
    mismatched identity/size, or a failed probe is never treated as an answer.
    Telegram may no longer retain the source document, so this is not guaranteed.
    """
    if not isinstance(SUDO_USER_ID, int) or SUDO_USER_ID <= 0:
        logging.warning(f"{LOG_TAG} Cannot recover print name: invalid Sudo private chat")
        return None

    message = None
    try:
        message = await send_with_retry(
            lambda: bot.send_document(
                chat_id=SUDO_USER_ID,
                document=item['file_id'],
                caption=(f"🔎 بررسی خودکار نام فایل {item['base']}؛ "
                         "این پیام پس از بررسی حذف می‌شود."),
                disable_notification=True,
                disable_content_type_detection=True,
            ),
            f"{LOG_TAG} recover print filename"
        )
        document = getattr(message, 'document', None)
        name = getattr(document, 'file_name', None)
        extension = _filename_extension(name)
        # Generic container names are not evidence of the original print
        # format. A Sudo can still explicitly choose one for a downloaded file.
        if not extension or extension in {'bin', 'dat', 'tmp', 'unknown'}:
            return None

        original = item.get('file')
        expected_id = getattr(original, 'file_unique_id', None)
        actual_id = getattr(document, 'file_unique_id', None)
        if (isinstance(expected_id, str) and isinstance(actual_id, str)
                and expected_id and actual_id and expected_id != actual_id):
            logging.warning(f"{LOG_TAG} Refusing recovered print name: file identity differs")
            return None
        expected_size = getattr(original, 'file_size', None)
        actual_size = getattr(document, 'file_size', None)
        if (isinstance(expected_size, int) and expected_size > 0
                and isinstance(actual_size, int) and actual_size > 0
                and expected_size != actual_size):
            logging.warning(f"{LOG_TAG} Refusing recovered print name: file size differs")
            return None
        return extension
    except Exception as e:
        # Network exceptions may contain a getFile URL with the bot token.
        logging.warning(f"{LOG_TAG} Print filename recovery failed: {_safe_telegram_error(e)}")
        return None
    finally:
        message_id = getattr(message, 'message_id', None)
        if isinstance(message_id, int):
            try:
                await send_with_retry(
                    lambda: bot.delete_message(chat_id=SUDO_USER_ID, message_id=message_id),
                    f"{LOG_TAG} delete temporary print copy"
                )
            except Exception as e:
                # The private Sudo copy can remain if Telegram refuses deletion.
                logging.warning(f"{LOG_TAG} Temporary print copy was not deleted: {_safe_telegram_error(e)}")


async def _prepare_print_for_delivery(bot, item: dict) -> None:
    """Refuse unknown original formats rather than silently sending file_630."""
    if item['error']:
        return
    await _load_print_payload(item)
    if item['error']:
        return
    if item['filename'] is not None and not item['send_by_id']:
        return

    recovered = await _recover_original_print_extension(bot, item)
    expected = item['expected_extension']
    if not recovered:
        item['error'] = (
            f"فایل {item['index'] + 1}: پسوند اصلی بازیابی نشد؛ "
            "فایل اصلی را دوباره ارسال کنید یا Sudo پسوند صحیح را مشخص کند."
        )
    elif item['send_by_id'] and expected and recovered != expected:
        # Sending by file_id cannot rename the document. A mismatch means even
        # a saved or manually supplied extension cannot be enforced safely.
        item['error'] = (
            f"فایل {item['index'] + 1}: پسوند اصلی با نام ثبت‌شده متفاوت است؛ "
            "نسخه اصلی را دوباره ارسال کنید."
        )
    elif item['filename'] is None:
        item['filename'] = f"{item['base']}.{recovered}"

    if item['error']:
        item['data'] = None


def _group_print_items(items: list, max_count: int, max_bytes: int) -> list:
    """Group consecutive print files into album-sized batches.

    Each batch holds at most ``max_count`` items and at most ``max_bytes`` of
    upload payload — files sent by file_id are not uploaded and do not count.
    A single-item tail is merged into the previous batch when it fits, because
    albums need at least two items.
    """
    groups: list = []
    current: list = []
    current_bytes = 0

    for item in items:
        if current and (len(current) >= max_count or current_bytes + item['size'] > max_bytes):
            groups.append(current)
            current, current_bytes = [], 0
        current.append(item)
        current_bytes += item['size']
    if current:
        groups.append(current)

    if len(groups) > 1 and len(groups[-1]) == 1 and len(groups[-2]) >= 3:
        moved = groups[-2][-1]
        if groups[-1][0]['size'] + moved['size'] <= max_bytes:
            groups[-1].insert(0, groups[-2].pop())
    return groups


def _print_media(item: dict, caption: str):
    """Album item for one print file (InputFile is rebuilt on every attempt)."""
    if item['send_by_id']:
        return InputMediaDocument(media=item['file_id'], caption=caption)
    return InputMediaDocument(
        # Album uploads must have a unique attach:// reference; without this
        # Telegram gets missing media fields and only one multipart payload.
        media=InputFile(BytesIO(item['data']), filename=item['filename'], attach=True),
        caption=caption
    )


def _print_file_note(item: dict) -> str:
    # A file_id send keeps Telegram's original name, not the code-based name.
    label = item['base'] if item['send_by_id'] else (item['filename'] or item['base'])
    return f"⚠️ {label}: {item['warning']}"


def _print_result_detail(item: dict, message_id: int) -> str:
    if item['warning']:
        return f"{PRINT_WARNING_PREFIX}{_print_file_note(item)} (msg={message_id})"
    return f"fn={item['filename']} msg={message_id}"


async def _send_one_print(bot, chat_id: int, item: dict, caption: str, label: str):
    """Send an already prepared print document (also used for album fallbacks)."""
    caption_with_warning = (
        f"{caption}\n{_print_file_note(item)}" if item['warning'] else caption
    )
    if item['send_by_id']:
        return await send_with_retry(
            lambda: bot.send_document(
                chat_id, document=item['file_id'], caption=caption_with_warning
            ),
            label
        )
    return await send_with_retry(
        lambda: bot.send_document(
            chat_id,
            document=InputFile(BytesIO(item['data']), filename=item['filename']),
            caption=caption_with_warning
        ),
        label
    )


async def _send_prints_to_print_group(bot, design: Design, prints: list, chat_id: int,
                                      code: str, product_name: str,
                                      selected_indices: set[int] | None = None,
                                      extension_overrides: dict | None = None) -> list:
    """Deliver approved print files to the print group.

    A single file keeps the old single-document behaviour. Two or more are
    uploaded as albums of renamed documents (``{code}_{n}.{ext}``), split by
    both item count and upload size. Albums explicitly rejected by Telegram
    fall back to individual sends; ambiguous network errors are not retried.

    Returns the ``(file_id, ok, detail)`` results used by the approve summary.
    """
    total = len(prints)
    results: list = []
    if not prints:
        return results

    caption_base = f"{product_name} - {code}"
    max_size_bytes = MAX_FILE_SIZE_DOWNLOAD_MB * 1024 * 1024

    # Plan by size first; unresolved original names are checked privately
    # before anything is sent to the printing group.
    items: list = []
    metadata = design.print_file_metadata if isinstance(design.print_file_metadata, dict) else {}
    overrides = extension_overrides if isinstance(extension_overrides, dict) else {}
    for index, fid in enumerate(prints):
        if selected_indices is not None and index not in selected_indices:
            continue
        try:
            items.append(await _describe_print_file(
                bot, fid, code, index, total, max_size_bytes, metadata.get(fid),
                overrides.get(fid)
            ))
        except Exception as e:
            detail = _safe_telegram_error(e)
            logging.error(
                f"{LOG_TAG} Print {index + 1}/{total} LOOKUP FAILED: {code} | "
                f"fid={str(fid)[:30]} | {detail}"
            )
            results.append((fid, False, f'فایل {index + 1}: {detail}'))

    if not items:
        return results

    for group in _group_print_items(items, MAX_MEDIA_GROUP_ITEMS, MEDIA_GROUP_MAX_UPLOAD_BYTES):
        # Payloads live only as long as their album (bounded by the size cap).
        # A failed format lookup must not prevent the other files from going out.
        for item in group:
            await _prepare_print_for_delivery(bot, item)
            if item['error']:
                results.append((item['file_id'], False, item['error']))
        group = [item for item in group if not item['error']]
        if not group:
            continue
        first, last = group[0]['index'] + 1, group[-1]['index'] + 1
        label = f"Prints {first}-{last}/{total} → print group"
        caption = caption_base if total == 1 else f"{caption_base} ({first}-{last}/{total})"

        notes = [_print_file_note(item) for item in group if item['warning']]
        if len(notes) > 5:
            # Telegram captions are limited to 1024 characters. Individual
            # fallback sends still carry their own warning if the album fails.
            notes = notes[:5] + [f"⚠️ {len(notes) - 5} فایل دیگر نیز با هشدار ارسال شد."]
        first_caption = caption + ("\n" + "\n".join(notes) if notes else "")

        if len(group) > 1:
            def _build_media(group=group, first_caption=first_caption):
                return [
                    _print_media(item, first_caption if position == 0 else "")
                    for position, item in enumerate(group)
                ]

            try:
                msgs = await send_with_retry(
                    lambda: bot.send_media_group(chat_id, media=_build_media()), label
                )
            except (BadRequest, RetryAfter) as e:
                # Telegram explicitly rejected the request, so no album was
                # accepted and individual retries cannot duplicate a print job.
                logging.error(f"{LOG_TAG} {label} ALBUM REJECTED: {code} | {_safe_telegram_error(e)}")
                msgs = None
            except Exception as e:
                # A timeout/network failure can happen *after* Telegram accepted
                # the album. Retrying automatically could print the same job twice.
                logging.error(f"{LOG_TAG} {label} ALBUM UNKNOWN: {code} | {_safe_telegram_error(e)}")
                for item in group:
                    results.append((item['file_id'], False,
                                    f"فایل {item['index'] + 1}: نتیجه ارسال آلبوم نامشخص است؛ پیش از تلاش مجدد گروه چاپ را بررسی کنید."))
                    item['data'] = None
                continue

            if msgs is not None:
                for item, msg in zip(group, msgs):
                    _record_group_file(design, code, 'print', chat_id,
                                       msg.message_id, item['file_id'], item['index'])
                    results.append((item['file_id'], True,
                                    _print_result_detail(item, msg.message_id)))
                    logging.info(f"{LOG_TAG} Print {item['index'] + 1}/{total} OK → {chat_id}")
                # An incomplete success response is also ambiguous: do not
                # resend the missing entries automatically.
                for item in group[len(msgs):]:
                    results.append((item['file_id'], False,
                                    f"فایل {item['index'] + 1}: پاسخ آلبوم ناقص است؛ پیش از تلاش مجدد گروه چاپ را بررسی کنید."))
                for item in group:
                    item['data'] = None
                continue

        for item in group:
            index = item['index']
            item_label = f"Print {index + 1}/{total}"
            item_caption = caption_base if total == 1 else (
                f"{caption_base} ({index + 1}/{total})"
            )
            try:
                msg = await _send_one_print(bot, chat_id, item, item_caption, item_label)
                _record_group_file(design, code, 'print', chat_id,
                                   msg.message_id, item['file_id'], index)
                results.append((item['file_id'], True,
                                _print_result_detail(item, msg.message_id)))
                logging.info(f"{LOG_TAG} {item_label} OK → {chat_id}")
            except Exception as e:
                detail = _safe_telegram_error(e)
                logging.error(
                    f"{LOG_TAG} {item_label} FAILED: {code} | "
                    f"fid={str(item['file_id'])[:30]} | {detail}"
                )
                results.append((item['file_id'], False, f'فایل {index + 1}: {detail}'))

        for item in group:
            item['data'] = None

    return results


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
