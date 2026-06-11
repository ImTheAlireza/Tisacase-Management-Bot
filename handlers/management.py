import logging
from telegram import Update
from telegram.ext import ContextTypes
from utils.decorators import require_sudo
from models.user import User
from models.product_line import ProductLine


@require_sudo
async def add_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /adduser {user_id} {role} {name}
    Example: /adduser 123456789 editor علی
    """
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ فرمت نادرست\n"
            "استفاده: /adduser {user_id} {role} {name}\n"
            "مثال: /adduser 123456789 editor علی"
        )
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return

    role = args[1].lower()
    if role not in ('editor', 'reviewer'):
        await update.message.reply_text("❌ نقش باید editor یا reviewer باشد.")
        return

    first_name = ' '.join(args[2:])
    sudo_user = context.user_data['db_user']

    existing = User.get_by_id(user_id)
    if existing:
        if existing.is_active:
            await update.message.reply_text(
                f"⚠️ کاربر {existing.first_name} ({user_id}) قبلاً ثبت شده است.\n"
                f"نقش فعلی: {existing.role}"
            )
            return
        else:
            # Reactivate
            conn = __import__('config.database', fromlist=['get_db_connection']).get_db_connection()
            cursor = conn.cursor()
            try:
                cursor.execute("""
                    UPDATE users SET is_active = TRUE, role = %s, first_name = %s
                    WHERE user_id = %s
                """, (role, first_name, user_id))
                conn.commit()
            finally:
                cursor.close()
                conn.close()
            await update.message.reply_text(
                f"✅ کاربر {first_name} ({user_id}) دوباره فعال شد.\n"
                f"نقش: {role}"
            )
            return

    try:
        User.create(
            user_id=user_id,
            first_name=first_name,
            role=role,
            added_by=sudo_user.user_id
        )
        await update.message.reply_text(
            f"✅ کاربر جدید اضافه شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"👤 نام: {first_name}\n"
            f"🆔 ID: {user_id}\n"
            f"🎭 نقش: {role}"
        )
    except Exception as e:
        logging.error(f"Failed to add user {user_id}: {e}")
        await update.message.reply_text(f"❌ خطا در افزودن کاربر: {e}")


@require_sudo
async def remove_user_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /removeuser {user_id}
    Deactivates user — data is preserved.
    """
    args = context.args
    if not args:
        await update.message.reply_text(
            "❌ فرمت نادرست\n"
            "استفاده: /removeuser {user_id}\n"
            "مثال: /removeuser 123456789"
        )
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return

    sudo_user = context.user_data['db_user']
    if user_id == sudo_user.user_id:
        await update.message.reply_text("❌ نمی‌توانید خودتان را غیرفعال کنید.")
        return

    user = User.get_by_id(user_id)
    if not user:
        await update.message.reply_text(f"❌ کاربری با ID {user_id} یافت نشد.")
        return

    if not user.is_active:
        await update.message.reply_text(f"⚠️ کاربر {user.first_name} قبلاً غیرفعال شده است.")
        return

    user.deactivate()
    await update.message.reply_text(
        f"✅ کاربر غیرفعال شد\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 نام: {user.first_name}\n"
        f"🆔 ID: {user_id}\n"
        f"🎭 نقش قبلی: {user.role}\n\n"
        f"داده‌های تاریخی حفظ شده‌اند."
    )


@require_sudo
async def list_users_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /listusers
    Lists all active users grouped by role.
    """
    users = User.get_all_active()
    if not users:
        await update.message.reply_text("هیچ کاربر فعالی وجود ندارد.")
        return

    by_role = {}
    for u in users:
        by_role.setdefault(u.role, []).append(u)

    role_icons = {'sudo': '👑', 'editor': '🎨', 'reviewer': '✅'}
    lines = ["📋 کاربران فعال\n━━━━━━━━━━━━━━━━━━"]

    for role in ('sudo', 'reviewer', 'editor'):
        if role not in by_role:
            continue
        lines.append(f"\n{role_icons.get(role, '👤')} {role.upper()}")
        for u in by_role[role]:
            sudo_tag = " (Sudo)" if u.is_sudo else ""
            lines.append(f"  • {u.first_name}{sudo_tag} — {u.user_id}")

    await update.message.reply_text('\n'.join(lines))


@require_sudo
async def set_role_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /setrole {user_id} {role}
    Changes a user's role.
    """
    args = context.args
    if len(args) < 2:
        await update.message.reply_text(
            "❌ فرمت نادرست\n"
            "استفاده: /setrole {user_id} {role}\n"
            "نقش‌ها: editor, reviewer\n"
            "مثال: /setrole 123456789 reviewer"
        )
        return

    try:
        user_id = int(args[0])
    except ValueError:
        await update.message.reply_text("❌ user_id باید عدد باشد.")
        return

    new_role = args[1].lower()
    if new_role not in ('editor', 'reviewer'):
        await update.message.reply_text("❌ نقش باید editor یا reviewer باشد. (sudo قابل تغییر نیست)")
        return

    user = User.get_by_id(user_id)
    if not user:
        await update.message.reply_text(f"❌ کاربری با ID {user_id} یافت نشد.")
        return

    if user.is_sudo:
        await update.message.reply_text("❌ نقش Sudo قابل تغییر نیست.")
        return

    old_role = user.role

    conn = __import__('config.database', fromlist=['get_db_connection']).get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "UPDATE users SET role = %s WHERE user_id = %s",
            (new_role, user_id)
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()

    await update.message.reply_text(
        f"✅ نقش تغییر کرد\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 {user.first_name} ({user_id})\n"
        f"قبلی: {old_role} → جدید: {new_role}"
    )


# ---------------------------------------------------------------------------
# Product line management commands
# ---------------------------------------------------------------------------

@require_sudo
async def list_lines_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /listlines
    Shows all product lines with status and group configuration.
    """
    lines = ProductLine.get_all()
    if not lines:
        await update.message.reply_text("هیچ خط تولیدی وجود ندارد.")
        return

    text_lines = ["📦 خطوط تولید\n━━━━━━━━━━━━━━━━━━"]
    for pl in lines:
        active_tag = "✅ فعال" if pl.is_active else "🔴 غیرفعال"
        gp = f"✅ {pl.group_products}" if pl.group_products else "❌ تنظیم نشده"
        gpr = f"✅ {pl.group_print}" if pl.group_print else "❌ تنظیم نشده"
        text_lines.append(
            f"\n{pl.icon} {pl.name_fa} ({pl.code_prefix}) — {active_tag}\n"
            f"  📦 محصولات: {gp}\n"
            f"  🖨 چاپ: {gpr}"
        )

    await update.message.reply_text('\n'.join(text_lines))


@require_sudo
async def add_line_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /addline {prefix} {name_fa} {icon}
    Example: /addline MG ماگ ☕
    """
    args = context.args
    if len(args) < 3:
        await update.message.reply_text(
            "❌ فرمت نادرست\n"
            "استفاده: /addline {prefix} {name_fa} {icon}\n"
            "مثال: /addline MG ماگ ☕"
        )
        return

    prefix = args[0].upper()
    icon = args[-1]
    name_fa = ' '.join(args[1:-1])

    if not name_fa:
        await update.message.reply_text("❌ نام فارسی را وارد کنید.")
        return

    existing = ProductLine.get_by_prefix(prefix)
    if existing:
        await update.message.reply_text(f"❌ پیشوند {prefix} قبلاً ثبت شده است.")
        return

    try:
        pl = ProductLine.create(
            code_prefix=prefix,
            name_en=prefix.lower(),
            name_fa=name_fa,
            icon=icon
        )
        await update.message.reply_text(
            f"✅ خط تولید جدید اضافه شد\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"{pl.icon} {pl.name_fa} ({pl.code_prefix})\n\n"
            f"⚠️ فراموش نکنید گروه‌های این خط را از\n"
            f"⚙️ تنظیم گروه‌ها تنظیم کنید."
        )
    except Exception as e:
        logging.error(f"Failed to add product line {prefix}: {e}")
        await update.message.reply_text(f"❌ خطا: {e}")


@require_sudo
async def disable_line_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /disableline {prefix}
    """
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /disableline {prefix}\nمثال: /disableline MG")
        return

    prefix = args[0].upper()
    pl = ProductLine.get_by_prefix(prefix)
    if not pl:
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد یا قبلاً غیرفعال است.")
        return

    pl.deactivate()
    await update.message.reply_text(
        f"🔴 خط تولید غیرفعال شد: {pl.icon} {pl.name_fa} ({prefix})\n"
        f"طرح‌های موجود حفظ شده‌اند."
    )


@require_sudo
async def enable_line_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /enableline {prefix}
    """
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /enableline {prefix}\nمثال: /enableline MG")
        return

    prefix = args[0].upper()

    # Need to get inactive lines too
    lines = ProductLine.get_all()
    pl = next((l for l in lines if l.code_prefix == prefix), None)

    if not pl:
        await update.message.reply_text(f"❌ خط تولید '{prefix}' یافت نشد.")
        return

    if pl.is_active:
        await update.message.reply_text(f"⚠️ خط تولید {prefix} از قبل فعال است.")
        return

    pl.activate()
    configured = "✅ گروه‌ها تنظیم شده‌اند." if pl.is_fully_configured() else "⚠️ گروه‌ها هنوز تنظیم نشده‌اند."
    await update.message.reply_text(
        f"🟢 خط تولید فعال شد: {pl.icon} {pl.name_fa} ({prefix})\n"
        f"{configured}"
    )


# ---------------------------------------------------------------------------
# Code management commands
# ---------------------------------------------------------------------------

@require_sudo
async def lock_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lockcode {code}
    Example: /lockcode TS050
    """
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /lockcode {code}\nمثال: /lockcode TS050")
        return

    code = args[0].upper()
    notes = ' '.join(args[1:]) if len(args) > 1 else None
    sudo_user = context.user_data['db_user']

    from services.code_service import CodeService

    # Determine product line from code prefix
    lines = ProductLine.get_all()
    product_line = None
    for pl in lines:
        if code.startswith(pl.code_prefix):
            product_line = pl
            break

    if not product_line:
        await update.message.reply_text(f"❌ پیشوند کد '{code}' با هیچ خط تولیدی مطابقت ندارد.")
        return

    try:
        CodeService.lock_code_manual(code, product_line.code_prefix, sudo_user.user_id, notes)
        await update.message.reply_text(
            f"🔒 کد قفل شد: {code}\n"
            f"خط تولید: {product_line.icon} {product_line.name_fa}"
            + (f"\nیادداشت: {notes}" if notes else "")
        )
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")


@require_sudo
async def unlock_code_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /unlockcode {code}
    """
    args = context.args
    if not args:
        await update.message.reply_text("استفاده: /unlockcode {code}\nمثال: /unlockcode TS050")
        return

    code = args[0].upper()
    from services.code_service import CodeService

    try:
        CodeService.unlock_code(code)
        await update.message.reply_text(f"🔓 کد آزاد شد: {code}")
    except ValueError as e:
        await update.message.reply_text(f"❌ {e}")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا: {e}")


@require_sudo
async def locked_codes_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /lockedcodes [prefix]
    """
    args = context.args
    prefix = args[0].upper() if args else None

    from services.code_service import CodeService
    codes = CodeService.get_locked_codes(prefix)

    if not codes:
        scope = f"برای {prefix}" if prefix else ""
        await update.message.reply_text(f"هیچ کد قفل شده‌ای {scope} وجود ندارد.")
        return

    lines = [f"🔒 کدهای قفل شده{' — ' + prefix if prefix else ''}\n━━━━━━━━━━━━━━━━━━"]
    for row in codes:
        lock_type = "دستی 🔒" if row['is_manual'] else "اتوماتیک ✅"
        locked_by = row.get('locked_by_name') or "—"
        lines.append(
            f"\n{row['product_icon']} {row['code']} — {lock_type}\n"
            f"  توسط: {locked_by}"
            + (f"\n  یادداشت: {row['notes']}" if row.get('notes') else "")
        )

    # Telegram message limit — split if too long
    text = '\n'.join(lines)
    if len(text) > 4000:
        chunks = [text[i:i+4000] for i in range(0, len(text), 4000)]
        for chunk in chunks:
            await update.message.reply_text(chunk)
    else:
        await update.message.reply_text(text)