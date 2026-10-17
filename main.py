import os
import json
import base64
import random
import logging
from datetime import datetime, timedelta

from pathlib import Path
from fastapi.responses import FileResponse  

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID = int(os.getenv("ADMIN_ID", "1040670594"))
DB_PATH = os.getenv("DB_PATH", "darikum.db")
WEBAPP_BASE_URL = os.getenv("WEBAPP_BASE_URL", "").rstrip("/")

# Создаем FastAPI приложение, которое ожидает Uvicorn
app = FastAPI()
WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
ASSETS_DIR = WEBAPP_DIR / "webapp"

app.mount(
    "/static",
    StaticFiles(directory=str(ASSETS_DIR)),
    name="static",
)

# Если папка с веб-приложением называется webapp, подключаем статику (при необходимости)
# app.mount("/static", StaticFiles(directory="webapp"), name="static")

def now_iso():
    return datetime.utcnow().isoformat()

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS games (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                mode TEXT,
                payload_json TEXT,
                status TEXT DEFAULT 'open',
                created_at TEXT,
                ends_at TEXT,
                result TEXT,
                winner_user_id INTEGER,
                winner_name TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id INTEGER,
                user_id INTEGER,
                username TEXT,
                joined_at TEXT,
                UNIQUE(game_id, user_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                points INTEGER DEFAULT 0,
                streak INTEGER DEFAULT 0,
                created_at TEXT
            )
        """)
        await db.commit()

async def ensure_user(user):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO users (user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
            (user.id, user.username or user.full_name, now_iso()),
        )
        await db.commit()

def weighted_pick(items):
    total = sum(int(item.get("weight", 1)) for item in items)
    r = random.randint(1, total)
    acc = 0
    for item in items:
        acc += int(item.get("weight", 1))
        if r <= acc:
            return item.get("label")
    return items[0].get("label")

def encode_payload(payload: dict) -> str:
    s = json.dumps(payload)
    return base64.b64encode(s.encode("utf-8")).decode("utf-8")

def make_webapp_url(payload: dict) -> str:
    if not WEBAPP_BASE_URL:
        return "https://example.com/invalid"
    enc = encode_payload(payload)
    return f"{WEBAPP_BASE_URL}/?data={enc}"

# Инициализация Telegram бота
telegram_app = None

@app.on_event("startup")
async def startup_event():
    global telegram_app
    await init_db()
    if not BOT_TOKEN:
        raise RuntimeError("Добавьте BOT_TOKEN в окружение (.env)")
    
    telegram_app = ApplicationBuilder().token(BOT_TOKEN).build()
    
    telegram_app.add_handler(CommandHandler("start", cmd_start))
    telegram_app.add_handler(CommandHandler("list", cmd_list))
    telegram_app.add_handler(CommandHandler("create_spin", cmd_create_spin))
    telegram_app.add_handler(CommandHandler("create_vault", cmd_create_vault))
    telegram_app.add_handler(CommandHandler("stats", cmd_stats))
    telegram_app.add_handler(MessageHandler(filters.ALL, handle_webapp_message))
    
    await telegram_app.initialize()
    await telegram_app.start()
    # Запуск поллинга в фоновом режиме, чтобы бот работал параллельно с FastAPI
    await telegram_app.updater.start_polling(drop_pending_updates=True)
    logger.info("Telegram bot started via polling alongside FastAPI.")

@app.on_event("shutdown")
async def shutdown_event():
    if telegram_app:
        await telegram_app.updater.stop()
        await telegram_app.stop()
        await telegram_app.shutdown()

# --- Хендлеры Telegram бота ---

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_user(user)
    await update.message.reply_text(
        "Привет! DARIKUM готов. Открой мини-приложение через кнопку или используй /list."
    )

async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute("SELECT id, title, mode, status, ends_at FROM games WHERE status='open'")
        rows = await cursor.fetchall()
    if not rows:
        await update.message.reply_text("Нет активных игр.")
        return
    lines = []
    kb = []
    for gid, title, mode, status, ends_at in rows:
        lines.append(f"#{gid} — {title} ({mode}) — до {ends_at}")
        kb.append([InlineKeyboardButton(f"Открыть #{gid}", web_app=WebAppInfo(make_webapp_url({"mode": mode, "game_id": gid})))])
    await update.message.reply_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(kb))

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        uid = update.effective_user.id
        if uid != ADMIN_ID:
            await update.message.reply_text("Только админ.")
            return
        return await func(update, context)
    return wrapper

@admin_only
async def cmd_create_spin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args) if context.args else ""
    if not raw:
        await update.message.reply_text("Использование: /create_spin Title|minutes|label:weight,label:weight,...")
        return
    try:
        title, minutes_s, wheel_raw = [p.strip() for p in raw.split("|", 2)]
        minutes = int(minutes_s)
    except Exception:
        await update.message.reply_text("Неверный формат. Пример:\n/create_spin Лунный шанс|30|Стикер:30,Бонус:25")
        return
    slices = []
    try:
        for it in wheel_raw.split(","):
            lbl, wt = [x.strip() for x in it.split(":", 1)]
            slices.append({"label": lbl, "weight": int(wt)})
    except Exception:
        await update.message.reply_text("Ошибка разбора секторов.")
        return

    ends_at = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    payload = {"mode":"spin","title": title, "slices": slices}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO games(title, mode, payload_json, status, created_at, ends_at) VALUES (?, ?, ?, 'open', ?, ?)",
            (title, "spin", json.dumps(payload), now_iso(), ends_at),
        )
        await db.commit()
        gid = cur.lastrowid

    weburl = make_webapp_url({"mode":"spin", "game_id": gid, "title": title, "slices": slices})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть мини-приложение", web_app=WebAppInfo(weburl))]])
    await update.message.reply_text(f"Создан Spin #{gid}\nДо: {ends_at}", reply_markup=kb)

@admin_only
async def cmd_create_vault(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args) if context.args else ""
    if not raw:
        await update.message.reply_text("Использование: /create_vault Title|minutes|label:weight,label:weight,...")
        return
    try:
        title, minutes_s, prizes_raw = [p.strip() for p in raw.split("|", 2)]
        minutes = int(minutes_s)
    except Exception:
        await update.message.reply_text("Неверный формат. Пример:\n/create_vault Тайный сундук|45|Промокод:40,Стикер:30")
        return
    prizes = []
    try:
        for it in prizes_raw.split(","):
            lbl, wt = [x.strip() for x in it.split(":", 1)]
            prizes.append({"label": lbl, "weight": int(wt)})
    except Exception:
        await update.message.reply_text("Ошибка разбора призов.")
        return

    ends_at = (datetime.utcnow() + timedelta(minutes=minutes)).isoformat()
    payload = {"mode":"vault","title": title, "prizes": prizes}
    async with aiosqlite.connect(DB_PATH) as db:
        cur = await db.execute(
            "INSERT INTO games(title, mode, payload_json, status, created_at, ends_at) VALUES (?, ?, ?, 'open', ?, ?)",
            (title, "vault", json.dumps(payload), now_iso(), ends_at),
        )
        await db.commit()
        gid = cur.lastrowid

    weburl = make_webapp_url({"mode":"vault", "game_id": gid, "title": title, "prizes": prizes})
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("Открыть мини-приложение", web_app=WebAppInfo(weburl))]])
    await update.message.reply_text(f"Создан Mystery Vault #{gid}\nДо: {ends_at}", reply_markup=kb)

async def handle_webapp_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not getattr(msg, "web_app_data", None):
        return
    try:
        data_raw = msg.web_app_data.data
        data = json.loads(data_raw)
    except Exception:
        await msg.reply_text("Ошибка данных от мини-приложения.")
        logger.exception("Invalid web_app_data")
        return

    action = data.get("action")
    game_id = int(data.get("game_id")) if data.get("game_id") else None
    user = update.effective_user

    if action == "request_spin":
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, title, mode, payload_json, status FROM games WHERE id=?", (game_id,))
            game = await cursor.fetchone()
            if not game:
                await msg.reply_text("Розыгрыш не найден.")
                return
            if game[4] != "open":
                await msg.reply_text("Розыгрыш уже закрыт.")
                return
            payload = json.loads(game[3])
            slices = payload.get("slices", [])
            result = weighted_pick(slices)
            await db.execute("INSERT OR IGNORE INTO participants(game_id, user_id, username, joined_at) VALUES (?, ?, ?, ?)",
                             (game_id, user.id, user.username or user.full_name, now_iso()))
            await db.execute("UPDATE games SET status='closed', result=?, winner_user_id=?, winner_name=?, ends_at=? WHERE id=?",
                             (result, user.id, user.username or user.full_name, now_iso(), game_id))
            await db.execute("INSERT OR IGNORE INTO users (user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
                             (user.id, user.username or user.full_name, now_iso()))
            await db.execute("UPDATE users SET points = points + 10 WHERE user_id=?", (user.id,))
            await db.commit()
        await msg.reply_text(f"🎡 Вы попросили крутнуть колесо: результат — {result}. Поздравляю!")
        try:
            await context.bot.send_message(msg.chat_id, f"🎉 @{user.username or user.full_name} выиграл(а) в розыгрыше #{game_id}: {result}")
        except Exception:
            pass
        return

    if action == "request_vault_open":
        async with aiosqlite.connect(DB_PATH) as db:
            cursor = await db.execute("SELECT id, title, mode, payload_json, status FROM games WHERE id=?", (game_id,))
            game = await cursor.fetchone()
            if not game:
                await msg.reply_text("Розыгрыш не найден.")
                return
            if game[4] != "open":
                await msg.reply_text("Сундук уже открыт/закрыт.")
                return
            payload = json.loads(game[3])
            prizes = payload.get("prizes", [])
            result = weighted_pick(prizes)
            await db.execute("INSERT OR IGNORE INTO participants(game_id, user_id, username, joined_at) VALUES (?, ?, ?, ?)",
                             (game_id, user.id, user.username or user.full_name, now_iso()))
            await db.execute("UPDATE games SET status='closed', result=?, winner_user_id=?, winner_name=?, ends_at=? WHERE id=?",
                             (result, user.id, user.username or user.full_name, now_iso(), game_id))
            await db.execute("INSERT OR IGNORE INTO users (user_id, username, points, streak, created_at) VALUES (?, ?, 0, 0, ?)",
                             (user.id, user.username or user.full_name, now_iso()))
            await db.execute("UPDATE users SET points = points + 15 WHERE user_id=?", (user.id,))
            await db.commit()
        await msg.reply_text(f"🔓 Сундук открыт! Вы получили: {result}. Поздравляем!")
        try:
            await context.bot.send_message(msg.chat_id, f"🗝️ @{user.username or user.full_name} открыл(а) Mystery Vault #{game_id} и получил(а): {result}")
        except Exception:
            pass
        return

    await msg.reply_text("Неизвестное действие от мини-приложения.")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("Только админ.")
        return
    async with aiosqlite.connect(DB_PATH) as db:
        total_games = await (await db.execute("SELECT COUNT(*) FROM games")).fetchone()
        open_games = await (await db.execute("SELECT COUNT(*) FROM games WHERE status='open'")).fetchone()
        total_users = await (await db.execute("SELECT COUNT(*) FROM users")).fetchone()
    await update.message.reply_text(
        f"Всего игр: {total_games[0]}\nАктивных: {open_games[0]}\nПользователей: {total_users[0]}"
    )

# Базовый роут FastAPI (чтобы при открытии корневой страницы не было ошибок 404)
@app.get("/")
async def root():
    return FileResponse(WEBAPP_DIR / "index.html")
