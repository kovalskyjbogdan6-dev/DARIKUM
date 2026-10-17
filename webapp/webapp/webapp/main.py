import os
import json
import random
import logging
from datetime import datetime, timedelta

import aiosqlite
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1040670594"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")


def now_iso():
    return datetime.utcnow().isoformat()


def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        if user_id != ADMIN_ID:
            await update.message.reply_text("У вас нет прав администратора.")
            return
        return await func(update, context)
    return wrapper


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                last_claim TEXT,
                created_at TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                mode TEXT DEFAULT 'spin',
                wheel_json TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT,
                ends_at TEXT,
                result TEXT,
                winner_user_id INTEGER,
                winner_name TEXT
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER,
                user_id INTEGER,
                username TEXT,
                joined_at TEXT,
                UNIQUE(game_id, user_id)
            )
            """
        )
        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_stats (
                user_id INTEGER PRIMARY KEY,
                wins INTEGER DEFAULT 0,
                spins INTEGER DEFAULT 0
            )
            """
        )
        await db.commit()


async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """
            INSERT OR IGNORE INTO users (user_id, username, points, streak, created_at)
            VALUES (?, ?, 0, 0, ?)
            """,
            (user.id, user.username or user.full_name, now_iso()),
        )
        await db.execute(
            """
            INSERT OR IGNORE INTO user_stats (user_id, wins, spins)
            VALUES (?, 0, 0)
            """,
            (user.id,),
        )
        await db.commit()


def weighted_pick(items):
    total = sum(item["weight"] for item in items)
    r = random.randint(1, total)
    acc = 0
    for item in items:
        acc += item["weight"]
        if r <= acc:
            return item["label"]
    return items[0]["label"]


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    text = (
        "🎉 Добро пожаловать в DARIKUM!\n\n"
        "Это новое мини-приложение для Telegram-розыгрышей.\n"
        "Здесь используется уникальная механика Spin-the-Wheel.\n\n"
        "Команды:\n"
        "/list — посмотреть активные розыгрыши\n"
        "/join <id> — участвовать в розыгрыше\n"
        "/my — профиль и очки\n"
        "/help — помощь\n"
    )
    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎡 Открыть розыгрыши", callback_data="list_games")],
            [InlineKeyboardButton("👤 Мой профиль", callback_data="profile")],
        ]
    )
    await update.message.reply_text(text, reply_markup=keyboard)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Список команд DARIKUM:\n\n"
        "/start — старт\n"
        "/list — активные розыгрыши\n"
        "/join <id> — присоединиться к розыгрышу\n"
        "/my — профиль\n"
        "/create_spin <title>|<minutes>|<label:weight,...> — создать колесо\n"
        "/end_spin <id> — завершить розыгрыш \n"
        "/stats — статистика\n"
        "/admin — админ-команды\n"
    )
    await update.message.reply_text(text)


async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Доступ только для администратора.")
        return

    text = (
        "Админ-команды:\n"
        "/create_spin <title>|<minutes>|<label:weight,...>\n"
        "/end_spin <id>\n"
        "/stats\n"
    )
    await update.message.reply_text(text)


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute(
            "SELECT id, title, mode, status, ends_at FROM games WHERE status='open' ORDER BY id DESC"
        ).fetchall()

    if not rows:
        await update.message.reply_text("Сейчас нет активных розыгрышей.")
        return

    lines = ["🎡 Активные розыгрыши:"]
    for game in rows:
        gid, title, mode, status, ends_at = game
        lines.append(f"#{gid} — {title} ({mode}) — до {ends_at}")
    await update.message.reply_text("\n".join(lines))


async def cmd_join(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /join <id>")
        return

    game_id = int(context.args[0])
    user = update.effective_user
    await ensure_user(user)

    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute(
            "SELECT id, status FROM games WHERE id=?",
            (game_id,),
        ).fetchone()

        if not row:
            await update.message.reply_text("Розыгрыш не найден.")
            return

        if row[1] != "open":
            await update.message.reply_text("Этот розыгрыш уже закрыт.")
            return

        await db.execute(
            """
            INSERT OR IGNORE INTO participants(game_id, user_id, username, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (game_id, user.id, user.username or user.full_name, now_iso()),
        )
        await db.commit()

    await update.message.reply_text(f"Вы участвуете в розыгрыше #{game_id}.")


async def cmd_my(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)

    async with aiosqlite.connect(DB_PATH) as db:
        user_row = await db.execute(
            "SELECT points, streak FROM users WHERE user_id=?",
            (user.id,),
        ).fetchone()
        stats_row = await db.execute(
            "SELECT wins, spins FROM user_stats WHERE user_id=?",
            (user.id,),
        ).fetchone()

    if not user_row:
        await update.message.reply_text("Профиль пока пустой.")
        return

    points, streak = user_row
    wins, spins = stats_row if stats_row else (0, 0)

    text = (
        f"👤 Профиль: @{user.username or user.full_name}\n"
        f"💎 Очки: {points}\n"
        f"🔥 Стрик: {streak}\n"
        f"🏆 Победы: {wins}\n"
        f"🎡 Круток: {spins}\n"
    )
    await update.message.reply_text(text)


@admin_only
async def cmd_create_spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "Использование: /create_spin <title>|<minutes>|<label:weight,label:weight,...>"
        )
        return

    raw = " ".join(context.args).strip()
    parts = [p.strip() for p in raw.split("|")]

    if len(parts) != 3:
        await update.message.reply_text(
            "Неверный формат. Пример:\n"
            "/create_spin Лунный шанс|30|Стикер:30,Бонус:25,Промокод:20,Ключ:15,Подарок:10"
        )
        return

    title, minutes_raw, wheel_raw = parts
    try:
        minutes = int(minutes_raw)
    except ValueError:
        await update.message.reply_text("Минуты должны быть числом.")
        return

    try:
        slices = []
        for item in wheel_raw.split(","):
            label, weight = [p.strip() for p in item.split(":")]
            slices.append({"label": label, "weight": int(weight)})
    except Exception:
        await update.message.reply_text("Формат секторов неверный. Пример: label:weight,label:weight")
        return

    if not slices:
        await update.message.reply_text("Нужно хотя бы 1 поле колеса.")
        return

    ends_at = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()

    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            """
            INSERT INTO games(title, mode, wheel_json, status, created_at, ends_at)
            VALUES (?, 'spin', ?, 'open', ?, ?)
            """,
            (title, json.dumps(slices), now_iso(), ends_at),
        )
        await db.commit()
        game_id = cur.lastrowid

    text = (
        f"🎡 Создан розыгрыш #{game_id}\n"
        f"Название: {title}\n"
        f"Время: {minutes} минут\n"
        f"Формат: Spin-the-Wheel\n"
        f"До: {ends_at}\n"
    )

    keyboard = InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🎡 Крутить колесо", callback_data=f"spin:{game_id}")]
        ]
    )

    await update.message.reply_text(text, reply_markup=keyboard)
    logger.info("Created spin %s", game_id)


@admin_only
async def cmd_end_spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Использование: /end_spin <id>")
        return

    game_id = int(context.args[0])

    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute(
            "SELECT id, title, wheel_json, status, winner_user_id FROM games WHERE id=?",
            (game_id,),
        ).fetchone()

        if not game:
            await update.message.reply_text("Розыгрыш не найден.")
            return

        if game[3] == "closed":
            await update.message.reply_text("Розыгрыш уже закрыт.")
            return

        wheel = json.loads(game[2])
        result = weighted_pick(wheel)
        winner_user_id = game[4]

        await db.execute(
            """
            UPDATE games
            SET status='closed', result=?, closed_at=?, winner_name='manual'
            WHERE id=?
            """,
            (result, now_iso(), game_id),
        )
        await db.commit()

    await update.message.reply_text(
        f"🎯 Розыгрыш #{game_id} завершён вручную.\n"
        f"Результат: {result}"
    )


async def callback_list_games(update: Update, context: CallbackContext):
    query = update.callback_query
    await query.answer()

    async with aiosqlite.connect(DB_PATH) as db:
        rows = await db.execute(
            "SELECT id, title, ends_at FROM games WHERE status='open' ORDER BY id DESC"
        ).fetchall()

    if not rows:
        await query.edit_message_text("Сейчас нет активных розыгрышей.")
        return

    lines = ["🎡 Активные розыгрыши:"]
    buttons = []

    for gid, title, ends_at in rows:
        lines.append(f"#{gid} — {title} — до {ends_at}")
        buttons.append([InlineKeyboardButton(f"🎡 #{gid}", callback_data=f"spin:{gid}")])

    text = "\n".join(lines)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(buttons))


async def callback_profile(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    await ensure_user(user)

    async with aiosqlite.connect(DB_PATH) as db:
        row = await db.execute(
            "SELECT points, streak FROM users WHERE user_id=?",
            (user.id,),
        ).fetchone()
        stats = await db.execute(
            "SELECT wins, spins FROM user_stats WHERE user_id=?",
            (user.id,),
        ).fetchone()

    points, streak = row if row else (0, 0)
    wins, spins = stats if stats else (0, 0)

    text = (
        f"👤 Профиль: @{user.username or user.full_name}\n"
        f"💎 Очки: {points}\n"
        f"🔥 Стрик: {streak}\n"
        f"🏆 Победы: {wins}\n"
        f"🎡 Круток: {spins}\n"
    )
    await query.answer()
    await query.edit_message_text(text)


async def callback_spin(update: Update, context: CallbackContext):
    query = update.callback_query
    user = query.from_user
    await ensure_user(user)

    payload = query.data.split(":")
    if len(payload) != 2:
        await query.answer("Некорректный запрос.", show_alert=True)
        return

    game_id = int(payload[1])

    async with aiosqlite.connect(DB_PATH) as db:
        game = await db.execute(
            "SELECT id, title, wheel_json, status, winner_user_id, winner_name FROM games WHERE id=?",
            (game_id,),
        ).fetchone()

        if not game:
            await query.answer("Розыгрыш не найден.", show_alert=True)
            return

        gid, title, wheel_json, status, winner_user_id, winner_name = game

        if status != "open":
            await query.answer("Этот розыгрыш уже закрыт.", show_alert=True)
            return

        # add user to participants
        await db.execute(
            """
            INSERT OR IGNORE INTO participants(game_id, user_id, username, joined_at)
            VALUES (?, ?, ?, ?)
            """,
            (gid, user.id, user.username or user.full_name, now_iso()),
        )

        wheel = json.loads(wheel_json)
        result = weighted_pick(wheel)

        # award user points a bit
        await db.execute(
            """
            UPDATE users
            SET points = points + 10, streak = streak + 1
            WHERE user_id = ?
            """,
            (user.id,),
        )

        await db.execute(
            """
            UPDATE user_stats
            SET spins = spins + 1
            WHERE user_id = ?
            """,
            (user.id,),
        )

        await db.execute(
            """
            UPDATE games
            SET status='closed', result=?, winner_user_id=?, winner_name=?, closed_at=?
            WHERE id=?
            """,
            (result, user.id, user.username or user.full_name, now_iso(), gid),
        )
        await db.commit()

    # send a message to chat with final result
    await query.answer(f"🎡 Результат: {result}", show_alert=True)
    await query.edit_message_text(
        f"🎡 Колесо фортуны в розыгрыше #{gid}\n"
        f"Название: {title}\n"
        f"Результат: {result}\n"
        f"Победитель: @{user.username or user.full_name}\n"
        f"Награда: {result}"
    )

    try:
        await context.bot.send_message(
            user.id,
            f"🎉 Поздравляем! Вы выиграли в розыгрыше #{gid}.\n"
            f"Приз: {result}\n"
            "Ваши очки увеличены на +10.",
        )
    except Exception as exc:
        logger.warning("Could not DM user %s: %s", user.id, exc)


async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только администратор может просматривать статистику.")
        return

    async with aiosqlite.connect(DB_PATH) as db:
        total_games = await db.execute("SELECT COUNT(*) FROM games").fetchone()
        open_games = await db.execute("SELECT COUNT(*) FROM games WHERE status='open'").fetchone()
        total_users = await db.execute("SELECT COUNT(*) FROM users").fetchone()
        total_participants = await db.execute("SELECT COUNT(*) FROM participants").fetchone()

    text = (
        "📊 Статистика DARIKUM\n\n"
        f"Всего розыгрышей: {total_games[0]}\n"
        f"Активных: {open_games[0]}\n"
        f"Пользователей: {total_users[0]}\n"
        f"Участий: {total_participants[0]}\n"
    )
    await update.message.reply_text(text)


async def callback_router(update: Update, context: CallbackContext):
    data = update.callback_query.data

    if data == "list_games":
        await callback_list_games(update, context)
    elif data == "profile":
        await callback_profile(update, context)
    elif data.startswith("spin:"):
        await callback_spin(update, context)
    else:
        await update.callback_query.answer("Неизвестная команда.", show_alert=True)


async def main():
    await init_db()
    if not BOT_TOKEN:
        raise RuntimeError("BOT_TOKEN is missing. Add it to .env or environment variables.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("join", cmd_join))
    app.add_handler(CommandHandler("my", cmd_my))
    app.add_handler(CommandHandler("create_spin", cmd_create_spin))
    app.add_handler(CommandHandler("end_spin", cmd_end_spin))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("admin", cmd_admin))

    app.add_handler(CallbackQueryHandler(callback_router))

    logger.info("Starting DARIKUM bot...")
    await app.run_polling()


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
