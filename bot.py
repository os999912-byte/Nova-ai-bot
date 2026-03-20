"""
╔══════════════════════════════════════════════════════════╗
║          🤖  NOVA AI — Telegram Bot                      ║
║  Brain: Groq (llama-3.3-70b)  — БЕСПЛАТНО              ║
║  Images: Pollinations.ai       — БЕСПЛАТНО              ║
║  Storage: SQLite               — БЕСПЛАТНО              ║
║  Host: Render.com              — БЕСПЛАТНО              ║
╚══════════════════════════════════════════════════════════╝
"""

import os
import re
import logging
import base64
import sqlite3
import threading
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from io import BytesIO
from pathlib import Path

import httpx
from groq import Groq
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    CallbackQueryHandler, ContextTypes, filters,
)
from telegram.constants import ParseMode, ChatAction

# ══════════════════════════════════════════════════════════
#  LOGGING
# ══════════════════════════════════════════════════════════
logging.basicConfig(
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("NOVA")

# ══════════════════════════════════════════════════════════
#  CONFIG
# ══════════════════════════════════════════════════════════
TELEGRAM_TOKEN    = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY      = os.environ["GROQ_API_KEY"]
PORT              = int(os.environ.get("PORT", 10000))
DB_PATH           = Path("nova_bot.db")
MAX_HISTORY       = 30
MAX_DB_HISTORY    = 1000
GROQ_MODEL        = "llama-3.3-70b-versatile"
GROQ_VISION_MODEL = "llama-3.2-90b-vision-preview"

SYSTEM_PROMPT = """Ты — NOVA, умный AI-ассистент в Telegram на базе LLaMA 3.3 70B.

Умеешь:
• Отвечать на любые вопросы на любом языке
• Писать, дебажить и объяснять код
• Переводить и редактировать тексты
• Решать математические задачи
• Помогать с творчеством и бизнесом

Форматирование для Telegram:
- *жирный* для заголовков
- `код` для фрагментов кода
- ```язык\nкод\n``` для блоков кода
- _курсив_ для примечаний

Отвечай на языке пользователя. Будь дружелюбным и чётким."""

# ══════════════════════════════════════════════════════════
#  ВЕБ-СЕРВЕР (нужен Render чтобы считать это Web Service)
# ══════════════════════════════════════════════════════════

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write("<h1>🤖 NOVA AI Bot is running!</h1>".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def run_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("🌐 Веб-сервер запущен на порту %d", PORT)
    server.serve_forever()


# ══════════════════════════════════════════════════════════
#  DATABASE
# ══════════════════════════════════════════════════════════

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS users (
        user_id    INTEGER PRIMARY KEY,
        username   TEXT, first_name TEXT, last_name TEXT,
        joined_at  TEXT DEFAULT (datetime('now')),
        last_seen  TEXT DEFAULT (datetime('now')),
        msg_count  INTEGER DEFAULT 0,
        img_count  INTEGER DEFAULT 0
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS messages (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        role       TEXT NOT NULL,
        content    TEXT NOT NULL,
        msg_type   TEXT DEFAULT 'text',
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS images (
        id         INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id    INTEGER NOT NULL,
        prompt     TEXT NOT NULL,
        image_url  TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_msg_user ON messages(user_id)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_img_user ON images(user_id)")
    conn.commit()
    conn.close()
    logger.info("✅ База данных готова: %s", DB_PATH)


def db_upsert_user(user):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""INSERT INTO users (user_id, username, first_name, last_name)
        VALUES (?,?,?,?) ON CONFLICT(user_id) DO UPDATE SET
        username=excluded.username, first_name=excluded.first_name,
        last_name=excluded.last_name, last_seen=datetime('now')""",
        (user.id, user.username or "", user.first_name or "", user.last_name or ""))
    conn.commit()
    conn.close()


def db_save_message(user_id, role, content, msg_type="text"):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO messages (user_id,role,content,msg_type) VALUES (?,?,?,?)",
              (user_id, role, content, msg_type))
    c.execute("""DELETE FROM messages WHERE user_id=? AND id NOT IN (
        SELECT id FROM messages WHERE user_id=? ORDER BY id DESC LIMIT ?)""",
        (user_id, user_id, MAX_DB_HISTORY))
    if role == "user":
        col = "img_count" if msg_type == "image" else "msg_count"
        c.execute(f"UPDATE users SET {col}={col}+1 WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def db_get_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT role,content FROM messages
        WHERE user_id=? AND msg_type='text' ORDER BY id DESC LIMIT ?""",
        (user_id, MAX_HISTORY))
    rows = c.fetchall()
    conn.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def db_clear_history(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("DELETE FROM messages WHERE user_id=?", (user_id,))
    conn.commit()
    conn.close()


def db_get_stats(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("""SELECT first_name,username,joined_at,last_seen,msg_count,img_count
        FROM users WHERE user_id=?""", (user_id,))
    row = c.fetchone()
    conn.close()
    if not row:
        return {}
    return dict(zip(["first_name","username","joined_at","last_seen","msg_count","img_count"], row))


def db_save_image(user_id, prompt, url):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("INSERT INTO images (user_id,prompt,image_url) VALUES (?,?,?)",
              (user_id, prompt, url))
    conn.commit()
    conn.close()


def db_get_image_history(user_id, limit=8):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT prompt,created_at FROM images WHERE user_id=? ORDER BY id DESC LIMIT ?",
              (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return [{"prompt": r[0], "created_at": r[1]} for r in rows]


# ══════════════════════════════════════════════════════════
#  AI
# ══════════════════════════════════════════════════════════

groq_client = Groq(api_key=GROQ_API_KEY)


async def ask_ai(user_id, user_text):
    db_save_message(user_id, "user", user_text)
    history = db_get_history(user_id)
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            max_tokens=2048, temperature=0.7,
        )
        reply = resp.choices[0].message.content
        db_save_message(user_id, "assistant", reply)
        return reply
    except Exception as e:
        logger.error("Groq error: %s", e)
        return f"⚠️ *Ошибка AI:* `{e}`\n\nПопробуй снова через минуту."


async def generate_image(prompt, user_id):
    encoded = urllib.parse.quote(prompt)
    url = (f"https://image.pollinations.ai/prompt/{encoded}"
           f"?width=1024&height=1024&nologo=true&seed={user_id}")
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            r = await client.get(url)
            ct = r.headers.get("content-type", "")
            if r.status_code == 200 and "image" in ct:
                db_save_image(user_id, prompt, url)
                return r.content, url
    except Exception as e:
        logger.error("Image error: %s", e)
    return None, ""


# ══════════════════════════════════════════════════════════
#  KEYBOARDS
# ══════════════════════════════════════════════════════════

def kb_main():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🎨 Нарисовать", callback_data="hint_image"),
         InlineKeyboardButton("💻 Код",        callback_data="hint_code")],
        [InlineKeyboardButton("📊 Статистика", callback_data="my_stats"),
         InlineKeyboardButton("🖼 Картинки",   callback_data="img_history")],
        [InlineKeyboardButton("🗑 Очистить историю", callback_data="ask_clear"),
         InlineKeyboardButton("❓ Помощь",          callback_data="show_help")],
    ])

def kb_after_reply():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Перефразируй", callback_data="rephrase"),
        InlineKeyboardButton("📋 Меню",         callback_data="main_menu"),
    ]])

def kb_confirm_clear():
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Да, удалить", callback_data="clear_confirm"),
        InlineKeyboardButton("❌ Отмена",      callback_data="main_menu"),
    ]])

def kb_after_image(prompt):
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("🔄 Ещё вариант", callback_data=f"regen:{prompt[:200]}"),
        InlineKeyboardButton("📋 Меню",        callback_data="main_menu"),
    ]])


# ══════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════

def split_message(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts, current, in_code = [], "", False
    for line in text.split("\n"):
        if line.startswith("```"):
            in_code = not in_code
        if len(current) + len(line) + 1 > limit:
            if in_code:
                current += "\n```"
            parts.append(current)
            current = ("```\n" if in_code else "") + line + "\n"
        else:
            current += line + "\n"
    if current.strip():
        parts.append(current)
    return parts


async def send_reply(update_or_msg, text, kb=None):
    msg = getattr(update_or_msg, "message", update_or_msg)
    for part in split_message(text):
        try:
            await msg.reply_text(part, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception:
            await msg.reply_text(part, reply_markup=kb)
        kb = None


async def do_generate(msg, user_id, prompt):
    status = await msg.reply_text(
        f"🎨 *Рисую:* _{prompt}_\n\n⏳ Подожди 15–30 секунд…",
        parse_mode=ParseMode.MARKDOWN)
    await msg.chat.send_action(ChatAction.UPLOAD_PHOTO)
    image_bytes, url = await generate_image(prompt, user_id)
    try:
        await status.delete()
    except Exception:
        pass
    if image_bytes:
        caption = f"🎨 *{prompt[:200]}*\n_✅ Pollinations.ai_"
        try:
            await msg.reply_photo(photo=BytesIO(image_bytes), caption=caption,
                                  parse_mode=ParseMode.MARKDOWN,
                                  reply_markup=kb_after_image(prompt))
        except Exception:
            await msg.reply_photo(photo=BytesIO(image_bytes), caption=prompt[:200],
                                  reply_markup=kb_after_image(prompt))
    else:
        await msg.reply_text("⚠️ Не удалось сгенерировать. Попробуй другое описание.")


# ══════════════════════════════════════════════════════════
#  HANDLERS
# ══════════════════════════════════════════════════════════

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_upsert_user(user)
    s = db_get_stats(user.id)
    greet = "👋 С возвращением" if s.get("msg_count", 0) > 0 else "👋 Привет"
    await update.message.reply_text(
        f"{greet}, *{user.first_name}*!\n\n"
        "Я — *NOVA*, AI-ассистент на базе *LLaMA 3.3 70B*.\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🧠 *Что я умею:*\n"
        "├ 💬 Отвечать на любые вопросы\n"
        "├ 💻 Писать и проверять код\n"
        "├ 🎨 Генерировать картинки\n"
        "├ 🖼 Анализировать фотографии\n"
        "├ 🌐 Переводить тексты\n"
        "└ ✍️ Помогать с творчеством\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "💚 *Всё бесплатно!*\n\n"
        "Напиши что-нибудь или выбери действие ниже ✨",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    await update.message.reply_text(
        "📖 *Справка NOVA AI*\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "/start — главное меню\n"
        "/img `<описание>` — картинка\n"
        "/stats — моя статистика\n"
        "/history — история картинок\n"
        "/clear — очистить диалог\n"
        "/about — о боте\n\n"
        "━━━━━━━━━━━━━━━━━━━━━\n"
        "🆓 *100% бесплатно*",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "╔══════════════════════════╗\n"
        "║     🤖  NOVA AI v2.0     ║\n"
        "╚══════════════════════════╝\n\n"
        "🧠 *Модель:* LLaMA 3.3 70B\n"
        "⚡ *Инференс:* Groq Cloud\n"
        "🎨 *Картинки:* Pollinations.ai\n"
        "🗄 *БД:* SQLite\n"
        "🔧 *Хостинг:* Render.com\n\n"
        "💚 *Стоимость:* абсолютно бесплатно",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    s = db_get_stats(update.effective_user.id)
    if not s:
        await update.message.reply_text("📊 Нет данных.")
        return
    joined = (s.get("joined_at") or "")[:10]
    last   = (s.get("last_seen") or "")[:16].replace("T", " ")
    await update.message.reply_text(
        "╔════════════════════════╗\n"
        "║   📊  МОЯ СТАТИСТИКА   ║\n"
        "╚════════════════════════╝\n\n"
        f"👤 *Имя:* {s.get('first_name','—')}\n"
        f"📅 *С нами с:* `{joined}`\n"
        f"🕐 *Последний визит:* `{last}`\n\n"
        f"💬 *Сообщений:* *{s.get('msg_count',0)}*\n"
        f"🎨 *Картинок:* *{s.get('img_count',0)}*",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items = db_get_image_history(update.effective_user.id)
    if not items:
        text = "🖼 Картинок пока нет. Попробуй: `/img котик в лесу`"
    else:
        lines = ["*🖼 Твои последние картинки:*\n"]
        for i, item in enumerate(items, 1):
            dt = (item["created_at"] or "")[:16].replace("T", " ")
            p  = item["prompt"][:55] + ("…" if len(item["prompt"]) > 55 else "")
            lines.append(f"{i}. `{dt}` — _{p}_")
        text = "\n".join(lines)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN,
                                    reply_markup=kb_main())


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "⚠️ *Удалить всю историю диалога?*\n\nЭто нельзя отменить.",
        parse_mode=ParseMode.MARKDOWN, reply_markup=kb_confirm_clear())


async def cmd_img(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        await update.message.reply_text(
            "🎨 *Генерация картинок*\n\nИспользуй: `/img <описание>`\n\n"
            "• `/img закат над океаном, масло`\n"
            "• `/img кот-самурай, аниме, 4K`",
            parse_mode=ParseMode.MARKDOWN)
        return
    await do_generate(update.message, update.effective_user.id, prompt)


async def handle_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    db_upsert_user(update.effective_user)
    image_kw = ["нарисуй", "нарисовать", "сгенерируй картинку", "создай картинку",
                "создай изображение", "сделай картинку", "draw ", "generate image"]
    if any(kw in text.lower() for kw in image_kw):
        await do_generate(update.message, user_id, text)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    reply = await ask_ai(user_id, text)
    await send_reply(update, reply, kb=kb_after_reply())


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_upsert_user(update.effective_user)
    caption = update.message.caption or "Опиши подробно что изображено на фото."
    await update.message.chat.send_action(ChatAction.TYPING)
    photo = update.message.photo[-1]
    file  = await photo.get_file()
    buf   = BytesIO()
    await file.download_to_memory(buf)
    image_b64 = base64.standard_b64encode(buf.getvalue()).decode()
    try:
        resp = groq_client.chat.completions.create(
            model=GROQ_VISION_MODEL,
            messages=[{"role": "user", "content": [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": caption},
            ]}], max_tokens=1024)
        reply = resp.choices[0].message.content
    except Exception as e:
        logger.warning("Vision failed: %s", e)
        reply = await ask_ai(user_id, f"Пользователь прислал фото с вопросом: «{caption}»")
    db_save_message(user_id, "user",      f"[Фото] {caption}", "photo")
    db_save_message(user_id, "assistant", reply)
    await send_reply(update, reply, kb=kb_after_reply())


async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = update.effective_user.id
    data    = query.data
    await query.answer()

    if data == "main_menu":
        await query.message.reply_text("📋 *Главное меню*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())
    elif data == "show_help":
        await query.message.reply_text(
            "📖 *Справка:*\n• Пиши вопрос — отвечу\n"
            "• `/img текст` — картинка\n• `/stats` — статистика\n• `/clear` — очистить",
            parse_mode=ParseMode.MARKDOWN)
    elif data == "my_stats":
        db_upsert_user(update.effective_user)
        s = db_get_stats(user_id)
        joined = (s.get("joined_at") or "")[:10]
        last   = (s.get("last_seen") or "")[:16].replace("T", " ")
        await query.message.reply_text(
            "╔════════════════════════╗\n"
            "║   📊  МОЯ СТАТИСТИКА   ║\n"
            "╚════════════════════════╝\n\n"
            f"👤 *Имя:* {s.get('first_name','—')}\n"
            f"📅 *С нами с:* `{joined}`\n"
            f"🕐 *Последний визит:* `{last}`\n\n"
            f"💬 *Сообщений:* *{s.get('msg_count',0)}*\n"
            f"🎨 *Картинок:* *{s.get('img_count',0)}*",
            parse_mode=ParseMode.MARKDOWN)
    elif data == "img_history":
        items = db_get_image_history(user_id)
        if not items:
            text = "🖼 Картинок пока нет."
        else:
            lines = ["*🖼 Твои картинки:*\n"]
            for i, item in enumerate(items, 1):
                dt = (item["created_at"] or "")[:16].replace("T", " ")
                p  = item["prompt"][:55] + ("…" if len(item["prompt"]) > 55 else "")
                lines.append(f"{i}. `{dt}` — _{p}_")
            text = "\n".join(lines)
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)
    elif data == "ask_clear":
        await query.message.reply_text("⚠️ *Удалить всю историю?*",
            parse_mode=ParseMode.MARKDOWN, reply_markup=kb_confirm_clear())
    elif data == "clear_confirm":
        db_clear_his
