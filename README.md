# 🤖 NOVA AI — Telegram Bot

**Умный AI-бот для Telegram на базе LLaMA 3.3 70B**  
💚 Полностью бесплатно • 🗄 История в SQLite • 🎨 Генерация картинок

---

## ✨ Возможности

| Функция | Описание |
|---|---|
| 💬 Умный чат | LLaMA 3.3 70B — отвечает на любые вопросы |
| 🗄 База данных | История диалогов сохраняется в SQLite |
| 🎨 Картинки | Pollinations.ai — бесплатная генерация без ключа |
| 🖼 Анализ фото | Описывает и отвечает на вопросы по изображениям |
| 📊 Статистика | Счётчик сообщений и картинок на пользователя |
| 💻 Помощь с кодом | Python, JS, Go, Rust, C++, Java и другие |

---

## 🆓 Почему всё бесплатно?

| Сервис | Что делает | Лимиты бесплатного |
|---|---|---|
| **Groq** | AI-мозг (LLaMA 70B) | 14 400 запросов/день |
| **Pollinations.ai** | Генерация картинок | Без лимитов |
| **SQLite** | База данных | Без лимитов (локально) |
| **python-telegram-bot** | Фреймворк | Открытый код |

---

## 🚀 Пошаговая установка

### Шаг 1 — Создать бота в Telegram

1. Открой Telegram, найди **@BotFather**
2. Напиши `/newbot`
3. Введи имя бота (например: `NOVA AI`)
4. Введи username (например: `my_nova_ai_bot`) — должен заканчиваться на `bot`
5. Скопируй **токен** вида: `7123456789:AAHxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`

📌 *Сохрани этот токен — он понадобится на шаге 4*

---

### Шаг 2 — Получить бесплатный Groq API ключ

1. Перейди на сайт: **https://console.groq.com**
2. Нажми **"Sign Up"** и зарегистрируйся (можно через Google)
3. После входа нажми **"API Keys"** в боковом меню
4. Нажми **"Create API Key"**, дай имя ключу
5. Скопируй ключ вида: `gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx`

📌 *Ключ показывается только один раз — сохрани его!*

---

### Шаг 3 — Установить Python

**Windows:**
1. Зайди на **https://python.org/downloads**
2. Скачай Python 3.11 или выше
3. При установке ✅ поставь галочку **"Add Python to PATH"**
4. Нажми **Install Now**

**Mac:**
```bash
brew install python3
```

**Linux (Ubuntu/Debian):**
```bash
sudo apt update && sudo apt install python3 python3-pip -y
```

Проверь установку:
```bash
python --version
# Должно показать: Python 3.11.x или выше
```

---

### Шаг 4 — Скачать и настроить бота

1. Создай папку для бота:
```bash
mkdir nova_bot
cd nova_bot
```

2. Скопируй в эту папку файлы `bot.py` и `requirements.txt`

3. Установи зависимости:
```bash
pip install -r requirements.txt
```

---

### Шаг 5 — Задать переменные окружения

#### Windows (Командная строка cmd):
```cmd
set TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxx
set GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

#### Windows (PowerShell):
```powershell
$env:TELEGRAM_BOT_TOKEN="7123456789:AAHxxxxxxxxxxxxxxx"
$env:GROQ_API_KEY="gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

#### Mac / Linux:
```bash
export TELEGRAM_BOT_TOKEN="7123456789:AAHxxxxxxxxxxxxxxx"
export GROQ_API_KEY="gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx"
```

> 💡 **Совет:** Чтобы не вводить каждый раз, создай файл `.env`:

Создай файл `.env` в папке бота:
```
TELEGRAM_BOT_TOKEN=7123456789:AAHxxxxxxxxxxxxxxx
GROQ_API_KEY=gsk_xxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Затем установи `python-dotenv` и добавь в начало `bot.py`:
```python
from dotenv import load_dotenv
load_dotenv()
```

---

### Шаг 6 — Запустить бота!

```bash
python bot.py
```

Ты увидишь:
```
2024-01-15 12:00:00 │ INFO     │ NOVA │ ✅ База данных готова: nova_bot.db
2024-01-15 12:00:00 │ INFO     │ NOVA │ 🚀 NOVA AI Bot запущен!
```

Открой Telegram, найди своего бота и напиши `/start` 🎉

---

## 📋 Команды бота

| Команда | Действие |
|---|---|
| `/start` | Главное меню с кнопками |
| `/help` | Справка по командам |
| `/img <описание>` | Сгенерировать картинку |
| `/stats` | Моя статистика |
| `/history` | История последних картинок |
| `/clear` | Очистить историю диалога |
| `/about` | Информация о боте |

---

## 🐳 Запуск через Docker (опционально)

Если хочешь запустить в контейнере:

```bash
docker build -t nova-ai-bot .

docker run -d \
  -e TELEGRAM_BOT_TOKEN="твой_токен" \
  -e GROQ_API_KEY="твой_ключ_groq" \
  -v $(pwd)/data:/app/data \
  --name nova-bot \
  nova-ai-bot
```

---

## 🖥 Запуск 24/7 на сервере

Чтобы бот работал постоянно, используй **systemd** (Linux):

Создай файл `/etc/systemd/system/nova-bot.service`:
```ini
[Unit]
Description=NOVA AI Telegram Bot
After=network.target

[Service]
User=ubuntu
WorkingDirectory=/home/ubuntu/nova_bot
Environment=TELEGRAM_BOT_TOKEN=твой_токен
Environment=GROQ_API_KEY=твой_ключ
ExecStart=/usr/bin/python3 bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Активируй:
```bash
sudo systemctl enable nova-bot
sudo systemctl start nova-bot
sudo systemctl status nova-bot
```

---

## 🗄 База данных

База данных `nova_bot.db` создаётся автоматически в папке с ботом.

**Структура:**
- `users` — все пользователи и их статистика
- `messages` — история всех диалогов
- `images` — история сгенерированных картинок

**Просмотр данных:**
```bash
# Установи DB Browser: https://sqlitebrowser.org
# Или используй командную строку:
sqlite3 nova_bot.db
.tables
SELECT * FROM users;
SELECT count(*) FROM messages;
.quit
```

---

## ❓ Частые вопросы

**Q: Бот не запускается, ошибка ModuleNotFoundError**  
A: Выполни `pip install -r requirements.txt`

**Q: Ошибка "TELEGRAM_BOT_TOKEN not set"**  
A: Убедись что ты задал переменные окружения перед запуском `python bot.py`

**Q: Картинка не генерируется**  
A: Pollinations.ai иногда недоступен. Подожди минуту и попробуй снова.

**Q: Groq вернул ошибку о лимите**  
A: Бесплатный план: 14 400 запросов/день. Подожди несколько часов.

**Q: Как обновить бота?**  
A: Замени `bot.py` новой версией и перезапусти командой `python bot.py`
