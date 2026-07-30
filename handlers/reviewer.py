import html
from telegram import Update, InputFile
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
from utils.callback_lock import callback_lock, deduplicate_callback

LOG_TAG = "[REVIEW]"
REJECT_REASON_PROMPT = "دلیل رد این طرح رو روی همین پیام ریپلای کنید."
REJECT_REASON_STATE_KEY = "awaiting_reject_reasons"
MAX_REJECTION_REASON_LENGTH = 3000


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
        await _log_to_group(
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
