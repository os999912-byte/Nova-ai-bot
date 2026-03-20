import os
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
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
from telegram.constants import ParseMode, ChatAction

# ── Logging ────────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("NOVA")

# ── Config ─────────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
GROQ_API_KEY   = os.environ["GROQ_API_KEY"]
PORT           = int(os.environ.get("PORT", 10000))
DB_PATH        = Path("nova_bot.db")
MAX_HISTORY    = 30
MAX_DB_MSGS    = 1000
MODEL_CHAT     = "llama-3.3-70b-versatile"
MODEL_VISION   = "llama-3.2-90b-vision-preview"

SYSTEM_PROMPT = (
    "Ты NOVA, умный AI-ассистент в Telegram на базе LLaMA 3.3 70B.\n"
    "Умеешь: отвечать на вопросы, писать и дебажить код, переводить, "
    "решать задачи, помогать с творчеством.\n"
    "Форматирование Telegram Markdown: *жирный*, _курсив_, `код`, ```блок кода```.\n"
    "Отвечай на языке пользователя. Будь дружелюбным и точным."
)

# ── Web server (needed for Render Web Service free plan) ───────────────────────
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-type", "text/plain")
        self.end_headers()
        self.wfile.write(b"NOVA AI Bot is running")

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    logger.info("Web server started on port %d", PORT)
    server.serve_forever()


# ── Database ───────────────────────────────────────────────────────────────────
def init_db():
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            user_id    INTEGER PRIMARY KEY,
            username   TEXT    DEFAULT '',
            first_name TEXT    DEFAULT '',
            joined_at  TEXT    DEFAULT (datetime('now')),
            last_seen  TEXT    DEFAULT (datetime('now')),
            msg_count  INTEGER DEFAULT 0,
            img_count  INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            role       TEXT    NOT NULL,
            content    TEXT    NOT NULL,
            msg_type   TEXT    DEFAULT 'text',
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS images (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL,
            prompt     TEXT    NOT NULL,
            created_at TEXT    DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_msg ON messages(user_id);
        CREATE INDEX IF NOT EXISTS idx_img ON images(user_id);
    """)
    con.commit()
    con.close()
    logger.info("Database ready: %s", DB_PATH)


def db_upsert_user(user):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        INSERT INTO users (user_id, username, first_name)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id) DO UPDATE SET
            username   = excluded.username,
            first_name = excluded.first_name,
            last_seen  = datetime('now')
        """,
        (user.id, user.username or "", user.first_name or ""),
    )
    con.commit()
    con.close()


def db_save_msg(user_id, role, content, msg_type="text"):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO messages (user_id, role, content, msg_type) VALUES (?, ?, ?, ?)",
        (user_id, role, content, msg_type),
    )
    cur.execute(
        """
        DELETE FROM messages
        WHERE user_id = ?
          AND id NOT IN (
              SELECT id FROM messages
              WHERE user_id = ?
              ORDER BY id DESC
              LIMIT ?
          )
        """,
        (user_id, user_id, MAX_DB_MSGS),
    )
    if role == "user":
        col = "img_count" if msg_type == "image" else "msg_count"
        cur.execute(f"UPDATE users SET {col} = {col} + 1 WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()


def db_get_history(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        """
        SELECT role, content FROM messages
        WHERE user_id = ? AND msg_type = 'text'
        ORDER BY id DESC
        LIMIT ?
        """,
        (user_id, MAX_HISTORY),
    )
    rows = cur.fetchall()
    con.close()
    return [{"role": r[0], "content": r[1]} for r in reversed(rows)]


def db_clear(user_id):
    con = sqlite3.connect(DB_PATH)
    con.execute("DELETE FROM messages WHERE user_id = ?", (user_id,))
    con.commit()
    con.close()


def db_get_stats(user_id):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT first_name, username, joined_at, last_seen, msg_count, img_count "
        "FROM users WHERE user_id = ?",
        (user_id,),
    )
    row = cur.fetchone()
    con.close()
    if not row:
        return {}
    keys = ["first_name", "username", "joined_at", "last_seen", "msg_count", "img_count"]
    return dict(zip(keys, row))


def db_save_image(user_id, prompt):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "INSERT INTO images (user_id, prompt) VALUES (?, ?)",
        (user_id, prompt),
    )
    cur.execute(
        "UPDATE users SET img_count = img_count + 1 WHERE user_id = ?",
        (user_id,),
    )
    con.commit()
    con.close()


def db_get_images(user_id, limit=8):
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute(
        "SELECT prompt, created_at FROM images WHERE user_id = ? ORDER BY id DESC LIMIT ?",
        (user_id, limit),
    )
    rows = cur.fetchall()
    con.close()
    return [{"prompt": r[0], "created_at": r[1]} for r in rows]


# ── AI client ──────────────────────────────────────────────────────────────────
groq_client = Groq(api_key=GROQ_API_KEY)


async def ask_ai(user_id, text):
    db_save_msg(user_id, "user", text)
    history = db_get_history(user_id)
    try:
        resp = groq_client.chat.completions.create(
            model=MODEL_CHAT,
            messages=[{"role": "system", "content": SYSTEM_PROMPT}] + history,
            max_tokens=2048,
            temperature=0.7,
        )
        reply = resp.choices[0].message.content
        db_save_msg(user_id, "assistant", reply)
        return reply
    except Exception as exc:
        logger.error("Groq error: %s", exc)
        return f"Ошибка AI: {exc}\n\nПопробуй снова через минуту."


async def gen_image(prompt, user_id):
    encoded = urllib.parse.quote(prompt)
    url = (
        f"https://image.pollinations.ai/prompt/{encoded}"
        f"?width=1024&height=1024&nologo=true&seed={user_id}"
    )
    try:
        async with httpx.AsyncClient(timeout=90, follow_redirects=True) as client:
            resp = await client.get(url)
            ct = resp.headers.get("content-type", "")
            if resp.status_code == 200 and "image" in ct:
                db_save_image(user_id, prompt)
                return resp.content
    except Exception as exc:
        logger.error("Image error: %s", exc)
    return None


# ── Keyboards ──────────────────────────────────────────────────────────────────
def kb_main():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Нарисовать", callback_data="hint_img"),
            InlineKeyboardButton("Помощь с кодом", callback_data="hint_code"),
        ],
        [
            InlineKeyboardButton("Статистика", callback_data="my_stats"),
            InlineKeyboardButton("Мои картинки", callback_data="img_hist"),
        ],
        [
            InlineKeyboardButton("Очистить историю", callback_data="ask_clear"),
            InlineKeyboardButton("Помощь", callback_data="show_help"),
        ],
    ])


def kb_reply():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Перефразируй", callback_data="rephrase"),
            InlineKeyboardButton("Меню", callback_data="main_menu"),
        ],
    ])


def kb_confirm():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Да, удалить", callback_data="clear_ok"),
            InlineKeyboardButton("Отмена", callback_data="main_menu"),
        ],
    ])


def kb_img(prompt):
    safe = prompt[:190]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Ещё вариант", callback_data="regen:" + safe),
            InlineKeyboardButton("Меню", callback_data="main_menu"),
        ],
    ])


# ── Helpers ────────────────────────────────────────────────────────────────────
def split_text(text, limit=4000):
    if len(text) <= limit:
        return [text]
    parts = []
    current = ""
    in_code = False
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


async def reply_text(update_or_msg, text, kb=None):
    msg = getattr(update_or_msg, "message", update_or_msg)
    for part in split_text(text):
        try:
            await msg.reply_text(part, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)
        except Exception:
            await msg.reply_text(part, reply_markup=kb)
        kb = None


async def send_image(msg, user_id, prompt):
    status = await msg.reply_text("Рисую: " + prompt + "\n\nПодожди 15-30 секунд...")
    await msg.chat.send_action(ChatAction.UPLOAD_PHOTO)
    data = await gen_image(prompt, user_id)
    try:
        await status.delete()
    except Exception:
        pass
    if data:
        try:
            await msg.reply_photo(
                photo=BytesIO(data),
                caption=prompt[:200],
                reply_markup=kb_img(prompt),
            )
        except Exception:
            await msg.reply_text("Картинка готова, но не удалось отправить. Попробуй снова.")
    else:
        await msg.reply_text("Не удалось сгенерировать картинку. Попробуй другое описание.")


# ── Command handlers ───────────────────────────────────────────────────────────
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    db_upsert_user(user)
    s = db_get_stats(user.id)
    greet = "С возвращением" if s.get("msg_count", 0) > 0 else "Привет"
    text = (
        greet + ", *" + user.first_name + "*!\n\n"
        "Я NOVA, AI-ассистент на базе LLaMA 3.3 70B.\n\n"
        "Умею:\n"
        "- Отвечать на любые вопросы\n"
        "- Писать и проверять код\n"
        "- Генерировать картинки\n"
        "- Анализировать фотографии\n"
        "- Переводить тексты\n\n"
        "Всё бесплатно! Напиши что-нибудь."
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_help(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    text = (
        "*Команды NOVA:*\n\n"
        "/start - главное меню\n"
        "/img текст - нарисовать картинку\n"
        "/stats - моя статистика\n"
        "/history - история картинок\n"
        "/clear - очистить диалог\n"
        "/about - о боте\n\n"
        "Или просто напиши вопрос!"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_about(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "*NOVA AI v2.0*\n\n"
        "Модель: LLaMA 3.3 70B\n"
        "Инференс: Groq Cloud\n"
        "Картинки: Pollinations.ai\n"
        "БД: SQLite\n"
        "Хостинг: Render.com\n\n"
        "Стоимость: бесплатно"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_stats(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    s = db_get_stats(update.effective_user.id)
    if not s:
        await update.message.reply_text("Нет данных.")
        return
    joined = (s.get("joined_at") or "")[:10]
    last   = (s.get("last_seen") or "")[:16].replace("T", " ")
    text = (
        "*Моя статистика:*\n\n"
        "Имя: " + s.get("first_name", "-") + "\n"
        "С нами с: " + joined + "\n"
        "Последний визит: " + last + "\n\n"
        "Сообщений: *" + str(s.get("msg_count", 0)) + "*\n"
        "Картинок: *" + str(s.get("img_count", 0)) + "*"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main())


async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    items = db_get_images(update.effective_user.id)
    if not items:
        await update.message.reply_text(
            "Картинок пока нет. Попробуй: /img котик в лесу",
            reply_markup=kb_main(),
        )
        return
    lines = ["*Твои последние картинки:*\n"]
    for i, item in enumerate(items, 1):
        dt = (item["created_at"] or "")[:16].replace("T", " ")
        p  = item["prompt"][:55]
        if len(item["prompt"]) > 55:
            p += "..."
        lines.append(str(i) + ". " + dt + " - " + p)
    await update.message.reply_text(
        "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=kb_main()
    )


async def cmd_clear(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Удалить всю историю диалога? Это нельзя отменить.",
        reply_markup=kb_confirm(),
    )


async def cmd_img(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    db_upsert_user(update.effective_user)
    prompt = " ".join(ctx.args).strip()
    if not prompt:
        await update.message.reply_text(
            "*Генерация картинок*\n\nИспользуй: /img описание\n\n"
            "Примеры:\n"
            "/img закат над океаном, масло\n"
            "/img кот-самурай, аниме, 4K",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    await send_image(update.message, update.effective_user.id, prompt)


# ── Message handlers ───────────────────────────────────────────────────────────
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text    = update.message.text.strip()
    db_upsert_user(update.effective_user)
    image_kw = [
        "нарисуй", "нарисовать", "сгенерируй картинку",
        "создай картинку", "создай изображение", "сделай картинку",
        "draw ", "generate image",
    ]
    if any(kw in text.lower() for kw in image_kw):
        await send_image(update.message, user_id, text)
        return
    await update.message.chat.send_action(ChatAction.TYPING)
    answer = await ask_ai(user_id, text)
    await reply_text(update, answer, kb=kb_reply())


async def handle_photo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db_upsert_user(update.effective_user)
    caption = update.message.caption or "Опиши что изображено на фото."
    await update.message.chat.send_action(ChatAction.TYPING)
    photo = update.message.photo[-1]
    tfile = await photo.get_file()
    buf   = BytesIO()
    await tfile.download_to_memory(buf)
    b64 = base64.standard_b64encode(buf.getvalue()).decode()
    try:
        resp = groq_client.chat.completions.create(
            model=MODEL_VISION,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64," + b64}},
                    {"type": "text", "text": caption},
                ],
            }],
            max_tokens=1024,
        )
        answer = resp.choices[0].message.content
    except Exception as exc:
        logger.warning("Vision failed: %s", exc)
        answer = await ask_ai(user_id, "Пользователь прислал фото с вопросом: " + caption)
    db_save_msg(user_id, "user", "[Фото] " + caption, "photo")
    db_save_msg(user_id, "assistant", answer)
    await reply_text(update, answer, kb=kb_reply())


# ── Callback handler ───────────────────────────────────────────────────────────
async def handle_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = update.effective_user.id
    data    = query.data
    await query.answer()

    if data == "main_menu":
        await query.message.reply_text(
            "Главное меню:", reply_markup=kb_main()
        )

    elif data == "show_help":
        await query.message.reply_text(
            "*Справка:*\n"
            "- Пиши вопрос - отвечу\n"
            "- /img текст - картинка\n"
            "- /stats - статистика\n"
            "- /clear - очистить историю",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "my_stats":
        db_upsert_user(update.effective_user)
        s = db_get_stats(user_id)
        joined = (s.get("joined_at") or "")[:10]
        last   = (s.get("last_seen") or "")[:16].replace("T", " ")
        text = (
            "*Моя статистика:*\n\n"
            "Имя: " + s.get("first_name", "-") + "\n"
            "С нами с: " + joined + "\n"
            "Последний визит: " + last + "\n\n"
            "Сообщений: *" + str(s.get("msg_count", 0)) + "*\n"
            "Картинок: *" + str(s.get("img_count", 0)) + "*"
        )
        await query.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)

    elif data == "img_hist":
        items = db_get_images(user_id)
        if not items:
            await query.message.reply_text("Картинок пока нет.")
        else:
            lines = ["*Твои картинки:*\n"]
            for i, item in enumerate(items, 1):
                dt = (item["created_at"] or "")[:16].replace("T", " ")
                p  = item["prompt"][:55]
                if len(item["prompt"]) > 55:
                    p += "..."
                lines.append(str(i) + ". " + dt + " - " + p)
            await query.message.reply_text(
                "\n".join(lines), parse_mode=ParseMode.MARKDOWN
            )

    elif data == "ask_clear":
        await query.message.reply_text(
            "Удалить всю историю? Это нельзя отменить.",
            reply_markup=kb_confirm(),
        )

    elif data == "clear_ok":
        db_clear(user_id)
        await query.message.reply_text(
            "История удалена. Начнём заново!", reply_markup=kb_main()
        )

    elif data == "hint_img":
        await query.message.reply_text(
            "*Генерация картинок*\n\n"
            "Команда: /img описание\n\n"
            "Примеры:\n"
            "/img лиса в лесу, акварель\n"
            "/img робот-самурай, киберпанк, 4K",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "hint_code":
        await query.message.reply_text(
            "*Помощь с кодом*\n\n"
            "Просто напиши задачу:\n"
            "- Напиши функцию на Python\n"
            "- Найди баг в этом коде: [код]\n"
            "- Объясни что делает этот код",
            parse_mode=ParseMode.MARKDOWN,
        )

    elif data == "rephrase":
        await query.message.chat.send_action(ChatAction.TYPING)
        answer = await ask_ai(
            user_id, "Перефразируй свой предыдущий ответ кратко и простым языком."
        )
        await reply_text(query.message, answer, kb=kb_reply())

    elif data.startswith("regen:"):
        prompt = data[len("regen:"):]
        await send_image(query.message, user_id, prompt)


# ── Main ───────────────────────────────────────────────────────────────────────
def main():
    init_db()

    t = threading.Thread(target=start_web_server, daemon=True)
    t.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",   cmd_start))
    app.add_handler(CommandHandler("help",    cmd_help))
    app.add_handler(CommandHandler("about",   cmd_about))
    app.add_handler(CommandHandler("stats",   cmd_stats))
    app.add_handler(CommandHandler("history", cmd_history))
    app.add_handler(CommandHandler("clear",   cmd_clear))
    app.add_handler(CommandHandler("img",     cmd_img))
    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    logger.info("NOVA AI Bot started!")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
