import os
import telebot
import requests
import random
import uvicorn
import logging
import sqlite3
import re
import time
from datetime import datetime, timezone
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from fastapi import FastAPI, Request, Response
from data.winners import winners
from data.drivers import DRIVERS

# ===== НАСТРОЙКА ЛОГГИРОВАНИЯ =====
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ===== ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ =====
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://turbo-train-2b9d.onrender.com/webhook")

# ===== АДМИНЫ ИЗ ПЕРЕМЕННОЙ ОКРУЖЕНИЯ =====
ADMIN_IDS = []
if os.getenv("ADMIN_IDS"):
    try:
        ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS").split(',')]
        print(f"✅ Загружено {len(ADMIN_IDS)} админов")
    except:
        ADMIN_IDS = []
        print("⚠️ Ошибка парсинга ADMIN_IDS, админ-панель недоступна")
else:
    print("⚠️ ADMIN_IDS не задан, админ-панель недоступна")

# ===== ИНИЦИАЛИЗАЦИЯ БОТА =====
bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# ===== БАЗА ДАННЫХ =====
def init_db():
    conn = sqlite3.connect('indyleader.db', check_same_thread=False)
    c = conn.cursor()
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            first_name TEXT,
            last_name TEXT,
            first_seen TEXT,
            last_seen TEXT,
            total_commands INTEGER DEFAULT 0
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            command TEXT,
            user_id INTEGER,
            timestamp TEXT
        )
    ''')
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS user_states (
            user_id INTEGER PRIMARY KEY,
            state TEXT,
            data TEXT,
            updated_at TEXT
        )
    ''')
    
    c.execute('PRAGMA journal_mode=WAL')
    conn.commit()
    conn.close()

init_db()

# ===== ФУНКЦИИ РАБОТЫ С БД =====
def log_user(user):
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    now = datetime.now().isoformat()
    c.execute('SELECT user_id FROM users WHERE user_id = ?', (user.id,))
    if c.fetchone():
        c.execute('''
            UPDATE users SET last_seen = ?, total_commands = total_commands + 1
            WHERE user_id = ?
        ''', (now, user.id))
    else:
        c.execute('''
            INSERT INTO users (user_id, username, first_name, last_name, first_seen, last_seen, total_commands)
            VALUES (?, ?, ?, ?, ?, ?, 1)
        ''', (user.id, user.username, user.first_name, user.last_name, now, now))
    conn.commit()
    conn.close()

def log_command(user_id, command):
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    c.execute('''
        INSERT INTO stats (command, user_id, timestamp)
        VALUES (?, ?, ?)
    ''', (command, user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_stats():
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM stats')
    total_commands = c.fetchone()[0]
    c.execute('''
        SELECT username, total_commands, last_seen
        FROM users ORDER BY total_commands DESC LIMIT 10
    ''')
    top_users = c.fetchall()
    conn.close()
    return total_users, total_commands, top_users

# ===== ФУНКЦИИ СОСТОЯНИЙ =====
def set_state(user_id, state, data=None):
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    c.execute('''
        INSERT OR REPLACE INTO user_states (user_id, state, data, updated_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, state, data, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def get_state(user_id):
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    c.execute('SELECT state, data FROM user_states WHERE user_id = ?', (user_id,))
    result = c.fetchone()
    conn.close()
    return result if result else (None, None)

def clear_state(user_id):
    conn = sqlite3.connect('indyleader.db')
    c = conn.cursor()
    c.execute('DELETE FROM user_states WHERE user_id = ?', (user_id,))
    conn.commit()
    conn.close()

# ===== ЭКРАНИРОВАНИЕ MARKDOWN =====
def escape_markdown(text):
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# ===== ФУНКЦИЯ ПОЛУЧЕНИЯ ТОП-5 ИЗ ESPN =====
def get_top5_from_espn():
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        entries = data.get('standings', [{}])[0].get('entries', [])[:5]
        top5 = []
        for entry in entries:
            athlete = entry.get('athlete', {})
            name = athlete.get('displayName', 'Неизвестно')
            points = entry.get('points', 0)
            top5.append(f"{name} — {points} очков")
        
        return top5
    except Exception as e:
        logger.error(f"ESPN standings error: {e}")
        return []

# ===== ФУНКЦИЯ ПЕРЕВОДА НОВОСТЕЙ (через Нико) =====
def translate_news_to_russian(headline, description):
    """Переводит новость на русский через Groq"""
    if not GROQ_API_KEY:
        return headline, description
    
    try:
        prompt = f"Переведи на русский язык эту новость IndyCar:\nЗаголовок: {headline}\nОписание: {description}\n\nОтвет дай в формате:\nЗаголовок: [перевод]\nОписание: [перевод]"
        
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": "Ты переводчик. Переводи новости IndyCar с английского на русский. Сохраняй стиль и терминологию."},
                    {"role": "user", "content": prompt}
                ],
                "temperature": 0.3,
                "max_tokens": 300
            },
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            translated = data["choices"][0]["message"]["content"]
            
            # Парсим ответ
            import re
            title_match = re.search(r'Заголовок:\s*(.+?)(?:\n|$)', translated)
            desc_match = re.search(r'Описание:\s*(.+?)(?:\n|$)', translated, re.DOTALL)
            
            new_title = title_match.group(1).strip() if title_match else headline
            new_desc = desc_match.group(1).strip() if desc_match else description
            
            return new_title, new_desc
    except Exception as e:
        logger.error(f"Translation error: {e}")
    
    return headline, description

# ===== AI-ФУНКЦИЯ (НИКО) =====
def ask_nico(question: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ Нико не настроен (нет API-ключа)"

    try:
        response = requests.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {GROQ_API_KEY}",
                "Content-Type": "application/json"
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": """
Ты — Нико, живой эксперт по IndyCar.
Ты фанат гонок, знаешь всё о пилотах, командах, трассах и истории.
Твоя задача — отвечать на вопросы про IndyCar как человек, а не как робот.

Правила:
1. Отвечай на русском языке.
2. Будь дерзким, но по делу.
3. Если не знаешь — скажи честно.
4. Используй факты, когда они есть.
5. Говори как реальный фанат, с эмоциями.

Ты знаешь:
- Всех действующих пилотов IndyCar и их команды
- Победителей Indy 500 с 1911 года
- Основные трассы календаря
- Историю серии
"""},
                    {"role": "user", "content": question}
                ],
                "temperature": 0.7,
                "max_tokens": 500
            },
            timeout=30
        )
        
        if response.status_code != 200:
            error_detail = response.json().get('error', {}).get('message', 'Неизвестная ошибка')
            return f"⚠️ Groq API ошибка {response.status_code}: {error_detail}"
        
        data = response.json()
        return data["choices"][0]["message"]["content"]
    except requests.exceptions.Timeout:
        return "⏰ Нико слишком долго думал. Попробуй еще раз."
    except Exception as e:
        logger.error(f"AI error: {e}")
        return f"⚠️ Ошибка: {e}"

# ===== КЛАВИАТУРЫ (без капса) =====
def main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    
    markup.add(
        InlineKeyboardButton("🏁 Календарь и топ", callback_data="schedule_top"),
        InlineKeyboardButton("🏎️ Пилоты", callback_data="drivers_list")
    )
    
    markup.add(
        InlineKeyboardButton("🏆 Indy 500", callback_data="indy500_menu"),
        InlineKeyboardButton("🎲 Случайный пилот", callback_data="random_driver")
    )
    
    markup.add(
        InlineKeyboardButton("🧠 Спросить Нико", callback_data="ask_nico"),
        InlineKeyboardButton("📰 Новости", callback_data="news")
    )
    
    markup.add(
        InlineKeyboardButton("❤️ Поддержать", callback_data="donate"),
        InlineKeyboardButton("ℹ️ О проекте", callback_data="about")
    )
    
    return markup

def back_to_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад в меню", callback_data="menu"))
    return markup

def drivers_list_buttons():
    markup = InlineKeyboardMarkup(row_width=2)
    
    teams = {}
    for code, d in DRIVERS.items():
        team = d['team']
        if team not in teams:
            teams[team] = []
        teams[team].append((code, d))
    
    for team, drivers in sorted(teams.items()):
        markup.add(InlineKeyboardButton(f"━━ {team} ━━", callback_data="noop"))
        
        row = []
        for code, d in drivers:
            surname = d['name'].split()[-1]
            row.append(InlineKeyboardButton(
                f"{surname} #{d['number']}",
                callback_data=f"driver_{code}"
            ))
            if len(row) == 2:
                markup.add(*row)
                row = []
        if row:
            markup.add(*row)
    
    markup.add(InlineKeyboardButton("🔙 Назад", callback_data="menu"))
    return markup

def indy500_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📅 По году", callback_data="winner_prompt"),
        InlineKeyboardButton("🏆 Топ-10 победителей", callback_data="top_winners"),
        InlineKeyboardButton("🔙 Назад", callback_data="menu")
    )
    return markup

def admin_panel():
    markup = InlineKeyboardMarkup(row_width=1)
    markup.add(
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_users"),
        InlineKeyboardButton("📈 Команды", callback_data="admin_commands"),
        InlineKeyboardButton("🔙 Выйти из админки", callback_data="menu")
    )
    return markup

# ===== ОБРАБОТЧИК КОМАНД =====
@bot.message_handler(commands=['start'])
def start(message):
    log_user(message.from_user)
    log_command(message.from_user.id, 'start')
    clear_state(message.from_user.id)
    
    bot.send_message(
        message.chat.id,
        "🏁 **I.N.D.Y Leader**\n\n"
        "Бот для настоящих фанатов IndyCar.\n"
        "Выбирай кнопку и погнали!",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if not ADMIN_IDS:
        bot.reply_to(message, "⛔ Админ-панель отключена")
        return
    
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ У вас нет доступа")
        return
    
    log_command(message.from_user.id, 'admin')
    bot.send_message(
        message.chat.id,
        "🔐 **Админ-панель**",
        reply_markup=admin_panel(),
        parse_mode="Markdown"
    )

# ===== ОБРАБОТЧИК ВСЕХ ТЕКСТОВЫХ СООБЩЕНИЙ =====
@bot.message_handler(func=lambda message: True)
def handle_text_messages(message):
    user_id = message.from_user.id
    state, data = get_state(user_id)
    
    if not state:
        bot.send_message(
            message.chat.id,
            "Используй кнопки в меню 👇",
            reply_markup=main_menu()
        )
        return
    
    if state == "waiting_year":
        handle_winner_year_input(message, data)
        clear_state(user_id)
    elif state == "waiting_nico":
        handle_nico_input(message, data)
        clear_state(user_id)

# ===== ОБРАБОТЧИК КНОПОК =====
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.message is None:
        bot.answer_callback_query(call.id, "Сообщение удалено")
        return
    
    user_id = call.from_user.id
    
    if call.data == "menu":
        try:
            bot.edit_message_text(
                "🏁 **Главное меню**",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "🏁 **Главное меню**",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data == "schedule_top":
        log_command(user_id, 'schedule_top')
        show_schedule_and_top(call)
        return
    
    if call.data == "drivers_list":
        log_command(user_id, 'drivers_list')
        try:
            bot.edit_message_text(
                "Выбери пилота:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=drivers_list_buttons()
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "Выбери пилота:",
                reply_markup=drivers_list_buttons()
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data.startswith("driver_"):
        log_command(user_id, 'driver_info')
        show_driver_info(call)
        return
    
    if call.data == "indy500_menu":
        log_command(user_id, 'indy500_menu')
        try:
            bot.edit_message_text(
                "🏆 **Indy 500**\n\nЧто хочешь узнать?",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=indy500_menu(),
                parse_mode="Markdown"
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "🏆 **Indy 500**\n\nЧто хочешь узнать?",
                reply_markup=indy500_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data == "top_winners":
        log_command(user_id, 'top_winners')
        show_top_winners(call)
        return
    
    if call.data == "winner_prompt":
        log_command(user_id, 'winner_prompt')
        set_state(user_id, "waiting_year")
        try:
            bot.edit_message_text(
                "📅 **Введи год** (например, 2023):\n\n"
                "Или напиши *назад*, чтобы вернуться",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
                parse_mode="Markdown"
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "📅 **Введи год** (например, 2023):\n\n"
                "Или напиши *назад*, чтобы вернуться",
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data == "random_driver":
        log_command(user_id, 'random_driver')
        show_random_driver(call)
        return
    
    if call.data == "ask_nico":
        log_command(user_id, 'ask_nico')
        set_state(user_id, "waiting_nico")
        try:
            bot.edit_message_text(
                "🧠 **Нико**\n\nНапиши свой вопрос про IndyCar:",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "🧠 **Нико**\n\nНапиши свой вопрос про IndyCar:"
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data == "news":
        log_command(user_id, 'news')
        show_news(call)
        return
    
    if call.data == "donate":
        log_command(user_id, 'donate')
        try:
            bot.edit_message_text(
                "❤️ **Поддержать проект**\n\n"
                "💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "❤️ **Поддержать проект**\n\n"
                "💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data == "about":
        log_command(user_id, 'about')
        try:
            bot.edit_message_text(
                "📘 **О проекте**\n\n"
                "Неофициальный бот для фанатов IndyCar.\n"
                "Не связан с IndyCar Series, LLC.\n\n"
                "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                "🧑‍💻 @RedRaceF1, @Gabriella1488",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "📘 **О проекте**\n\n"
                "Неофициальный бот для фанатов IndyCar.\n"
                "Не связан с IndyCar Series, LLC.\n\n"
                "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                "🧑‍💻 @RedRaceF1, @Gabriella1488",
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        bot.answer_callback_query(call.id)
        return
    
    if call.data in ["admin_stats", "admin_users", "admin_commands"]:
        if not ADMIN_IDS:
            bot.answer_callback_query(call.id, "Админ-панель отключена")
            return
        if user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Нет доступа")
            return
        handle_admin(call)
        return
    
    if call.data == "noop":
        bot.answer_callback_query(call.id)
        return
    
    bot.answer_callback_query(call.id, "Неизвестная команда")

# ===== ОТДЕЛЬНЫЕ ФУНКЦИИ ДЛЯ ОБРАБОТЧИКОВ =====

def show_schedule_and_top(call):
    try:
        try:
            bot.edit_message_text(
                "⏳ Загружаю календарь...",
                call.message.chat.id,
                call.message.message_id
            )
        except:
            pass
        
        url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard?seasontype=2&level=3"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        calendar = data.get('leagues', [{}])[0].get('calendar', [])
        now = datetime.now(timezone.utc)
        future_races = []
        
        for event in calendar:
            start_date = event.get('startDate', '')
            if not start_date:
                continue
            try:
                clean_date = start_date.replace('Z', '+00:00')
                event_date = datetime.fromisoformat(clean_date)
                if event_date > now:
                    label = event.get('label', 'Неизвестная гонка')
                    future_races.append({
                        'label': label,
                        'date': event_date.strftime('%d.%m.%Y'),
                        'timestamp': event_date
                    })
            except:
                continue
        
        future_races.sort(key=lambda x: x['timestamp'])
        future_races = future_races[:10]
        
        lines = ["🏁 **Ближайшие гонки 2026**", ""]
        if not future_races:
            lines.append("🏁 Сезон завершен или календарь не загружен")
        else:
            for race in future_races:
                lines.append(f"📅 {race['date']} — **{race['label']}**")
        
        top5 = get_top5_from_espn()
        if top5:
            lines.extend(["", "🏆 **Топ-5 чемпионата**", ""])
            for i, line in enumerate(top5, 1):
                lines.append(f"{i}. {line}")
        
        response_text = "\n".join(lines)
        if len(response_text) > 4000:
            response_text = response_text[:3997] + "..."
        
        bot.edit_message_text(
            response_text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Schedule error: {e}")
        bot.edit_message_text(
            "⚠️ Ошибка загрузки календаря",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu()
        )
    
    bot.answer_callback_query(call.id)

def show_driver_info(call):
    code = call.data.replace("driver_", "")
    d = DRIVERS.get(code)
    if not d:
        bot.answer_callback_query(call.id, "Гонщик не найден")
        return
    
    text = f"🏎️ **{d['name']}**\n🏁 {d['team']}\n🔢 #{d['number']}"
    
    if d.get('image'):
        try:
            bot.edit_message_media(
                media=InputMediaPhoto(d['image'], caption=text, parse_mode="Markdown"),
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                reply_markup=back_to_menu()
            )
        except Exception as e:
            logger.error(f"Media edit error: {e}")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            bot.send_photo(
                call.message.chat.id,
                d['image'],
                caption=text,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
    else:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown"
        )
    
    bot.answer_callback_query(call.id)

def show_top_winners(call):
    from collections import Counter
    
    wins = Counter()
    for w in winners:
        if w["year"] >= 1911 and "не проводилась" not in w["driver"]:
            wins[w["driver"]] += 1
    
    top = wins.most_common(10)
    medals = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
    
    text = "🏆 **10 величайших победителей**\n\n"
    for i, (driver, count) in enumerate(top):
        text += f"{medals[i]} {driver} — **{count}** побед\n"
    
    bot.edit_message_text(
        text,
        call.message.chat.id,
        call.message.message_id,
        reply_markup=back_to_menu(),
        parse_mode="Markdown"
    )
    bot.answer_callback_query(call.id)

def show_random_driver(call):
    if not DRIVERS:
        bot.send_message(
            call.message.chat.id,
            "⚠️ Нет данных о гонщиках",
            reply_markup=back_to_menu()
        )
        bot.answer_callback_query(call.id)
        return
    
    code, d = random.choice(list(DRIVERS.items()))
    text = f"🎲 **{d['name']}**\n🏁 {d['team']}\n🔢 #{d['number']}"
    
    if d.get('image'):
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except:
            pass
        bot.send_photo(
            call.message.chat.id,
            d['image'],
            caption=text,
            reply_markup=back_to_menu(),
            parse_mode="Markdown"
        )
    else:
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown"
        )
    
    bot.answer_callback_query(call.id)

def show_news(call):
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/news"
        resp = requests.get(url, timeout=10)
        data = resp.json()
        articles = data.get('articles', [])[:5]
        
        if not articles:
            bot.edit_message_text(
                "📰 Новостей пока нет",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu()
            )
            bot.answer_callback_query(call.id)
            return
        
        text = "📰 **Новости IndyCar**\n\n"
        
        for article in articles:
            headline = article.get('headline', 'Без заголовка')
            description = article.get('description', '')[:200]
            link = article.get('links', {}).get('web', {}).get('href', '#')
            
            # Переводим на русский
            ru_headline, ru_description = translate_news_to_russian(headline, description)
            
            text += f"**{ru_headline}**\n{ru_description}...\n[Читать]({link})\n\n"
        
        if len(text) > 4000:
            text = text[:3997] + "..."
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    except Exception as e:
        logger.error(f"News error: {e}")
        bot.edit_message_text(
            "⚠️ Новости временно недоступны",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu()
        )
    
    bot.answer_callback_query(call.id)

def handle_winner_year_input(message, data):
    if message.text.lower() in ["назад", "меню"]:
        start(message)
        return
    
    try:
        year = int(message.text.strip())
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Это не год. Попробуй ещё раз или напиши *назад*",
            parse_mode="Markdown",
            reply_markup=back_to_menu()
        )
        return
    
    for entry in winners:
        if entry.get("year") == year:
            driver = entry.get("driver", "Неизвестно")
            bot.send_message(
                message.chat.id,
                f"🏆 **Indy 500 {year}**\n🏁 {driver}",
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
            return
    
    bot.send_message(
        message.chat.id,
        f"❌ Нет данных за {year}.\n\nПопробуй другой год.",
        reply_markup=back_to_menu()
    )

def handle_nico_input(message, data):
    if message.text.lower() in ["назад", "меню"]:
        start(message)
        return
    
    bot.send_chat_action(message.chat.id, 'typing')
    thinking = bot.send_message(message.chat.id, "🧠 Нико думает...")
    
    response = ask_nico(message.text)
    safe_response = escape_markdown(response)
    
    bot.edit_message_text(
        f"🧠 **Нико:**\n\n{safe_response}",
        thinking.chat.id,
        thinking.message_id,
        reply_markup=back_to_menu(),
        parse_mode="Markdown"
    )

def handle_admin(call):
    if call.data == "admin_stats":
        total_users, total_commands, _ = get_stats()
        text = f"📊 **Статистика**\n\n"
        text += f"👤 Всего пользователей: {total_users}\n"
        text += f"📝 Всего команд: {total_commands}"
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    elif call.data == "admin_users":
        conn = sqlite3.connect('indyleader.db')
        c = conn.cursor()
        c.execute('SELECT username, first_name, last_seen, total_commands FROM users ORDER BY last_seen DESC LIMIT 20')
        users = c.fetchall()
        conn.close()
        
        text = "👥 **Последние пользователи:**\n\n"
        for u in users:
            name = u[1] or u[0] or "Аноним"
            text += f"• {name} — {u[3]} команд, последний раз: {u[2][:16]}\n"
        
        bot.edit_message_text(
            text[:4000],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    elif call.data == "admin_commands":
        conn = sqlite3.connect('indyleader.db')
        c = conn.cursor()
        c.execute('SELECT command, COUNT(*) FROM stats GROUP BY command ORDER BY COUNT(*) DESC')
        commands = c.fetchall()
        conn.close()
        
        text = "📈 **Статистика команд:**\n\n"
        for cmd, count in commands:
            text += f"• {cmd} — {count} раз\n"
        
        bot.edit_message_text(
            text[:4000],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    bot.answer_callback_query(call.id)

# ===== ВЕБХУК =====
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = telebot.types.Update.de_json(data)
    bot.process_new_updates([update])
    return Response(content="OK", status_code=200)

@app.get("/")
def root():
    return {"status": "INDY Leader is running", "webhook_url": WEBHOOK_URL}

def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Webhook установлен")

if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
