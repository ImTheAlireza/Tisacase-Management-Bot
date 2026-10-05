import asyncio
import html
from telegram import (
    Update,
    InputFile,
    InputMediaPhoto,
    InputMediaDocument,
)
from telegram.ext import ContextTypes
from utils.decorators import require_role
from models.design import Design
from models.design_group_message import DesignGroupMessage
from models.user import User
from models.product_line import ProductLine
from utils.helpers import safe_edit_message, delete_messages, send_with_retry, safe_answer_callback
from config.settings import (
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
        logging.error(f"{LOG_TAG} Failed to send log to LOG_GROUP_ID: {e}")


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


def _get_reviewer_mockup_message_ids(design: Design, reviewer_user_id: int) -> list[int]:
    """
    Return only the reviewer PV mockup message ids for this design.

    mockup_message_ids_reviewer also stores the action-button message id for
    multi-file submissions and pending-list views; the mockups are always saved
    first, so slicing by mockup count excludes that button message.
    """
    msg_ids = design.get_reviewer_messages(reviewer_user_id)
    mockup_msg_ids = []
    for msg_id in msg_ids[:len(design.mockup_file_ids)]:
        try:
            mockup_msg_ids.append(int(msg_id))
        except (TypeError, ValueError):
            continue
    return mockup_msg_ids


def _remember_reject_reason_targets(
    context: ContextTypes.DEFAULT_TYPE,
    code: str,
    message_ids: list[int]
) -> None:
    pending = context.user_data.get(REJECT_REASON_STATE_KEY)
    if not isinstance(pending, dict):
        pending = {}
        context.user_data[REJECT_REASON_STATE_KEY] = pending
    for msg_id in message_ids:
        pending[str(msg_id)] = code


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


async def _edit_reject_prompt_caption(bot, chat_id: int, message_id: int) -> bool:
    try:
        await bot.edit_message_caption(
            chat_id=chat_id,
            message_id=message_id,
            caption=REJECT_REASON_PROMPT,
            reply_markup=None
        )
        return True
    except Exception as e:
        logging.warning(
            f"{LOG_TAG} Could not edit reject prompt caption "
            f"chat={chat_id} msg={message_id}: {e}"
        )
        return False


async def _request_reject_reason(
    query,
    context: ContextTypes.DEFAULT_TYPE,
    design: Design,
    user: User,
    code: str
) -> None:
    """
    First step of rejection: ask the reviewer to reply with the reason.

    The design remains pending until a text reply arrives on the prompted mockup
    message. This keeps the code/process unchanged until a reason is captured.
    """
    chat_id = query.message.chat_id
    target_msg_id: int | None = None

    # Prefer the media message that actually owns the pressed inline button.
    if query.message and (query.message.photo or query.message.document or query.message.caption is not None):
        target_msg_id = query.message.message_id
    # For multi-mockup submissions, the button message replies to the last mockup.
    elif query.message and query.message.reply_to_message:
        replied = query.message.reply_to_message
        if replied.photo or replied.document or replied.caption is not None:
            target_msg_id = replied.message_id

    # Pending-list views send a separate button message without reply_to. In that
    # case use the last mockup message recorded for this reviewer.
    mockup_msg_ids = _get_reviewer_mockup_message_ids(design, user.user_id)
    if target_msg_id is None and mockup_msg_ids:
        target_msg_id = mockup_msg_ids[-1]

    prompted_msg_ids: list[int] = []
    if target_msg_id is not None:
        if await _edit_reject_prompt_caption(context.bot, chat_id, target_msg_id):
            prompted_msg_ids.append(target_msg_id)

    # If caption editing failed for the preferred target, try the recorded mockups.
    if not prompted_msg_ids:
        for msg_id in reversed(mockup_msg_ids):
            if await _edit_reject_prompt_caption(context.bot, chat_id, msg_id):
                prompted_msg_ids.append(msg_id)
                break

    # Remove the buttons from the clicked message and make the next step clear.
    try:
        if query.message.text:
            await query.edit_message_text(
                "❌ درخواست رد ثبت شد.\n\n"
                "دلیل رد را روی موکاپی که کپشنش تغییر کرد ریپلای کنید.",
                reply_markup=None
            )
        else:
            await query.edit_message_reply_markup(reply_markup=None)
    except Exception as e:
        logging.warning(f"{LOG_TAG} Could not clear reject buttons for {code}: {e}")

    # Absolute fallback: if no media caption could be changed, ask on the button
    # message itself and accept a reply to that message.
    if not prompted_msg_ids:
        try:
            if query.message.text:
                await query.edit_message_text(REJECT_REASON_PROMPT, reply_markup=None)
                prompted_msg_ids.append(query.message.message_id)
            else:
                prompt_msg = await query.message.reply_text(REJECT_REASON_PROMPT)
                prompted_msg_ids.append(prompt_msg.message_id)
        except Exception as e:
            logging.error(f"{LOG_TAG} Could not request reject reason for {code}: {e}")
            try:
                await query.message.reply_text("❌ خطا در ثبت درخواست رد")
            except Exception:
                pass
            return

    _remember_reject_reason_targets(context, code, prompted_msg_ids)
    logging.info(
        f"{LOG_TAG} Reject reason requested | code={code} | "
        f"reviewer={user.user_id} | targets={prompted_msg_ids}"
    )


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
            await context.bot.send_message(chat_id=uid, text=notification_text)
        except Exception:
            logging.exception(f"{LOG_TAG} Notification FAILED for {uid}")


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
    user: User
) -> None:
    lock_key = f"review_{code}"
    acquired = await callback_lock.acquire(lock_key)
    if not acquired:
        await update.message.reply_text("⏳ این طرح در حال پردازش است. لطفاً چند لحظه صبر کنید.")
        return

    try:
        design = Design.get_by_code(code)
        if not design:
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
    """
    Complete a rejection when a reviewer replies to the prompted mockup caption.

    Returns True when this message was consumed by the reject-reason flow.
    """
    message = update.message
    if not message or not message.text:
        return False

    if message.chat.type != "private":
        return False

    pending = context.user_data.get(REJECT_REASON_STATE_KEY)
    has_pending_state = isinstance(pending, dict) and bool(pending)

    if not message.reply_to_message:
        if has_pending_state:
            await message.reply_text(
                "برای ثبت دلیل رد، لطفاً روی پیام موکاپی که کپشنش تغییر کرده ریپلای کنید."
            )
            return True
        return False

    reply_msg = message.reply_to_message
    reply_msg_id = reply_msg.message_id
    code = pending.get(str(reply_msg_id)) if isinstance(pending, dict) else None

    prompt_matches = (
        (reply_msg.caption == REJECT_REASON_PROMPT)
        or (reply_msg.text == REJECT_REASON_PROMPT)
    )

    if code is None and prompt_matches:
        design = Design.get_pending_by_reviewer_message(
            update.effective_user.id,
            reply_msg_id
        )
        code = design.code if design else None

    if code is None:
        if has_pending_state:
            await message.reply_text(
                "این پیام، پیامِ درخواست دلیل رد نیست. لطفاً روی همان موکاپ ریپلای کنید."
            )
            return True
        return False

    user = User.get_by_id(update.effective_user.id)
    if not user or not user.is_active:
        await message.reply_text("🚫 شما مجاز به استفاده از این ربات نیستید.")
        return True

    effective_role = user.get_effective_role()
    if not user.is_sudo and effective_role not in ('reviewer', 'sudo'):
        await message.reply_text("🚫 فقط ناظر می‌تواند دلیل رد را ثبت کند.")
        return True

    reason = message.text.strip()
    if not reason:
        await message.reply_text("❌ دلیل رد نمی‌تواند خالی باشد.")
        return True

    await _finalize_rejection_with_reason(update, context, code, reason, user)
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

    user = User.get_by_id(query.from_user.id)
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

    # ── REJECT STEP 1 ──────────────────────────────────────────
    # Do not mark the design as rejected yet. First ask for the reason, then
    # handle_reject_reason_reply() will finalize the rejection on the reply.
    if action == "reject":
        await _request_reject_reason(query, context, design, user, code)
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
        logging.warning(f"{LOG_TAG} mockup type lookup failed for {str(fid)[:30]}: {e}")
        return 'unknown'
    path = file.file_path or ''
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else ''
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
                logging.error(f"{LOG_TAG} {label} ALBUM FAILED: {code} | {e}")
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
                logging.exception(f"{LOG_TAG} {file_label} FAILED: {code} | fid={str(fid)[:30]}")
                results.append((fid, False, str(e)[:100]))

    return results


async def _describe_print_file(bot, fid: str, code: str, index: int, total: int,
                               max_size_bytes: int) -> dict:
    """Resolve the renamed filename, size and file handle for one print file.

    Files above the download limit keep their original name and are sent by
    file_id; smaller ones are re-uploaded as ``{code}_{n}.{ext}``. The payload
    itself is downloaded later, per album, to keep memory usage bounded.
    """
    file = await bot.get_file(fid)
    path = file.file_path or ''
    ext = path.rsplit('.', 1)[-1].lower() if '.' in path else 'png'
    filename = f"{code}.{ext}" if total == 1 else f"{code}_{index + 1}.{ext}"
    large = bool(file.file_size and file.file_size > max_size_bytes)
    return {
        'file_id': fid,
        'filename': filename,
        'large': large,
        'size': 0 if large else int(file.file_size or 0),
        'index': index,
        'file': file,
        'data': None,
    }


async def _load_print_payload(item: dict) -> None:
    """Download the bytes needed to re-upload one print file (idempotent)."""
    if item['large'] or item['data'] is not None:
        return
    item['data'] = await item['file'].download_as_bytearray()


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
    if item['large']:
        return InputMediaDocument(media=item['file_id'], caption=caption)
    return InputMediaDocument(
        media=InputFile(BytesIO(item['data']), filename=item['filename']),
        caption=caption
    )


def _large_file_note(item: dict) -> str:
    return f"⚠️ {item['filename']} (فایل بزرگ — نام تغییر نکرد)"


async def _send_one_print(bot, chat_id: int, item: dict, caption: str, label: str):
    """Send a single print document (single files, lone tail, fallbacks)."""
    await _load_print_payload(item)
    if item['large']:
        return await send_with_retry(
            lambda: bot.send_document(
                chat_id, document=item['file_id'],
                caption=f"{caption}\n{_large_file_note(item)}"
            ),
            label
        )
    return await send_with_retry(
        lambda: bot.send_document(
            chat_id,
            document=InputFile(BytesIO(item['data']), filename=item['filename']),
            caption=caption
        ),
        label
    )


async def _send_prints_to_print_group(bot, design: Design, prints: list, chat_id: int,
                                      code: str, product_name: str) -> list:
    """Deliver approved print files to the print group.

    A single file keeps the old single-document behaviour. Two or more are
    uploaded as albums of renamed documents (``{code}_{n}.{ext}``), split by
    both item count and upload size. Albums that fail fall back to per-file
    sends so one bad item cannot lose the rest.

    Returns the ``(file_id, ok, detail)`` results used by the approve summary.
    """
    total = len(prints)
    results: list = []
    if not prints:
        return results

    caption_base = f"{product_name} - {code}"
    max_size_bytes = MAX_FILE_SIZE_DOWNLOAD_MB * 1024 * 1024

    # Pass 1: resolve names/sizes so albums can be planned before any payload
    # is downloaded; failed lookups are reported without blocking the rest.
    items: list = []
    for index, fid in enumerate(prints):
        try:
            items.append(await _describe_print_file(
                bot, fid, code, index, total, max_size_bytes
            ))
        except Exception as e:
            logging.exception(
                f"{LOG_TAG} Print {index + 1}/{total} LOOKUP FAILED: {code} | fid={str(fid)[:30]}"
            )
            results.append((fid, False, str(e)[:100]))

    if not items:
        return results

    for group in _group_print_items(items, MAX_MEDIA_GROUP_ITEMS, MEDIA_GROUP_MAX_UPLOAD_BYTES):
        first, last = group[0]['index'] + 1, group[-1]['index'] + 1
        label = f"Prints {first}-{last}/{total} → print group"
        caption = caption_base if total == 1 else f"{caption_base} ({first}-{last}/{total})"
        large_notes = [_large_file_note(item) for item in group if item['large']]
        first_caption = caption + ("\n" + "\n".join(large_notes) if large_notes else "")

        if len(group) > 1:
            # Payloads live only as long as their album (bounded by the size cap).
            downloaded = True
            for item in group:
                try:
                    await _load_print_payload(item)
                except Exception as e:
                    downloaded = False
                    logging.exception(
                        f"{LOG_TAG} Print {item['index'] + 1}/{total} DOWNLOAD FAILED: {code} | {e}"
                    )

            if downloaded:
                def _build_media(group=group, first_caption=first_caption):
                    return [
                        _print_media(item, first_caption if position == 0 else "")
                        for position, item in enumerate(group)
                    ]

                try:
                    msgs = await send_with_retry(
                        lambda: bot.send_media_group(chat_id, media=_build_media()), label
                    )
                except Exception as e:
                    logging.error(f"{LOG_TAG} {label} ALBUM FAILED: {code} | {e}")
                    msgs = None

                if msgs:
                    for item, msg in zip(group, msgs):
                        _record_group_file(design, code, 'print', chat_id,
                                           msg.message_id, item['file_id'], item['index'])
                        results.append((item['file_id'], True,
                                        f"fn={item['filename']} msg={msg.message_id}"))
                        logging.info(f"{LOG_TAG} Print {item['index'] + 1}/{total} OK → {chat_id}")
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
                                f"fn={item['filename']} msg={msg.message_id}"))
                logging.info(f"{LOG_TAG} {item_label} OK → {chat_id}")
            except Exception as e:
                logging.exception(
                    f"{LOG_TAG} {item_label} FAILED: {code} | fid={str(item['file_id'])[:30]}"
                )
                results.append((item['file_id'], False, str(e)[:100]))

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
