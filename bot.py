import os
import telebot
import requests
import random
import uvicorn
import logging
import sqlite3
import re
import time
import threading
import asyncio
from datetime import datetime, timezone
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton, InputMediaPhoto
from fastapi import FastAPI, Request, Response

# Импорты из модулей
from data.drivers import DRIVERS
from data.winners import winners
from admin_tools import (
    get_all_users, get_active_users, get_user_stats, get_global_stats,
    send_broadcast, update_knowledge_base, start_auto_update,
    create_ticket, get_open_tickets, close_ticket
)

# ===== НАСТРОЙКА =====
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден")

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://turbo-train-2b9d.onrender.com/webhook")

# ===== АДМИНЫ ХАРДКОД =====
ADMIN_IDS = [7025868617, 7946032603]
print(f"✅ Загружено {len(ADMIN_IDS)} админов")

# ===== ИНИЦИАЛИЗАЦИЯ =====
bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# ===== БД (инициализация с таблицей для заявок) =====
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
    
    c.execute('''
        CREATE TABLE IF NOT EXISTS tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            issue TEXT,
            contact TEXT,
            status TEXT DEFAULT 'open',
            created_at TEXT
        )
    ''')
    
    c.execute('PRAGMA journal_mode=WAL')
    conn.commit()
    conn.close()

init_db()

# ===== БАЗОВЫЕ ФУНКЦИИ =====
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

def escape_markdown(text):
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)

# ===== AI (Нико) =====
def ask_nico(question: str) -> str:
    if not GROQ_API_KEY:
        return "⚠️ Нико не настроен (нет API-ключа)"

    # Сначала ищем в базе знаний
    try:
        with open("data/knowledge.txt", "r", encoding="utf-8") as f:
            knowledge = f.read()
    except:
        knowledge = ""
    
    # Формируем промпт с контекстом
    system_prompt = f"""
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

ВАЖНО: Используй информацию из базы знаний, если она есть:
{knowledge[:2000]}
"""

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
                    {"role": "system", "content": system_prompt},
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

# ===== КЛАВИАТУРЫ =====
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
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("📊 Статистика", callback_data="admin_stats"),
        InlineKeyboardButton("👥 Пользователи", callback_data="admin_users")
    )
    markup.add(
        InlineKeyboardButton("📈 Команды", callback_data="admin_commands"),
        InlineKeyboardButton("👤 Юзер-стат", callback_data="admin_user_stats")
    )
    markup.add(
        InlineKeyboardButton("📨 Рассылка", callback_data="admin_broadcast"),
        InlineKeyboardButton("🔄 Обновить базу", callback_data="admin_update_db")
    )
    markup.add(
        InlineKeyboardButton("🎫 Заявки в ТП", callback_data="admin_tickets"),
        InlineKeyboardButton("🔙 Выйти", callback_data="menu")
    )
    return markup

# ===== КОМАНДЫ =====
@bot.message_handler(commands=['start'])
def start(message):
    log_user(message.from_user)
    log_command(message.from_user.id, 'start')
    clear_state(message.from_user.id)
    bot.send_message(
        message.chat.id,
        "🏁 **I.N.D.Y Leader**\n\nБот для настоящих фанатов IndyCar.\nВыбирай кнопку и погнали!",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['admin'])
def admin_command(message):
    if message.from_user.id not in ADMIN_IDS:
        bot.reply_to(message, "⛔ У вас нет доступа")
        return
    log_command(message.from_user.id, 'admin')
    bot.send_message(
        message.chat.id,
        "🔐 **Админ-панель**\n\nВыберите действие:",
        reply_markup=admin_panel(),
        parse_mode="Markdown"
    )

@bot.message_handler(commands=['ticket'])
def ticket_command(message):
    """Команда для создания заявки в ТП"""
    bot.send_message(
        message.chat.id,
        "🎫 **Техническая поддержка**\n\n"
        "Опиши свою проблему в одном сообщении.\n"
        "Я передам её админам.\n\n"
        "Формат: проблема | контакт (например: @username или почта)"
    )
    set_state(message.from_user.id, "waiting_ticket")

# ===== ТЕКСТОВЫЕ СООБЩЕНИЯ =====
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
    elif state == "waiting_ticket":
        handle_ticket_input(message)
        clear_state(user_id)
    elif state == "waiting_broadcast":
        handle_broadcast_input(message, data)
        clear_state(user_id)

# ===== ОБРАБОТЧИК КНОПОК =====
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    if call.message is None:
        bot.answer_callback_query(call.id, "Сообщение удалено")
        return
    
    user_id = call.from_user.id
    
    # === ГЛАВНОЕ МЕНЮ ===
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
    
    # === КАЛЕНДАРЬ ===
    if call.data == "schedule_top":
        log_command(user_id, 'schedule_top')
        show_schedule_and_top(call)
        return
    
    # === ПИЛОТЫ ===
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
    
    # === INDY 500 ===
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
                "📅 **Введи год** (например, 2023):\n\nИли напиши *назад*, чтобы вернуться",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=None,
                parse_mode="Markdown"
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "📅 **Введи год** (например, 2023):\n\nИли напиши *назад*, чтобы вернуться",
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return
    
    # === РАНДОМ ===
    if call.data == "random_driver":
        log_command(user_id, 'random_driver')
        show_random_driver(call)
        return
    
    # === НИКО ===
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
    
    # === НОВОСТИ ===
    if call.data == "news":
        log_command(user_id, 'news')
        show_news(call)
        return
    
    # === ДОНАТ ===
    if call.data == "donate":
        log_command(user_id, 'donate')
        try:
            bot.edit_message_text(
                "❤️ **Поддержать проект**\n\n💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "❤️ **Поддержать проект**\n\n💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        bot.answer_callback_query(call.id)
        return
    
    # === О ПРОЕКТЕ ===
    if call.data == "about":
        log_command(user_id, 'about')
        try:
            bot.edit_message_text(
                "📘 **О проекте**\n\nНеофициальный бот для фанатов IndyCar.\nНе связан с IndyCar Series, LLC.\n\n🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n🧑‍💻 @RedRaceF1, @Gabriella1488",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        except:
            bot.send_message(
                call.message.chat.id,
                "📘 **О проекте**\n\nНеофициальный бот для фанатов IndyCar.\nНе связан с IndyCar Series, LLC.\n\n🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n🧑‍💻 @RedRaceF1, @Gabriella1488",
                reply_markup=back_to_menu(),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        bot.answer_callback_query(call.id)
        return
    
    # === АДМИН-ПАНЕЛЬ ===
    if call.data in ["admin_stats", "admin_users", "admin_commands", "admin_user_stats", 
                     "admin_broadcast", "admin_update_db", "admin_tickets"]:
        if user_id not in ADMIN_IDS:
            bot.answer_callback_query(call.id, "Нет доступа")
            return
        handle_admin(call)
        return
    
    if call.data == "noop":
        bot.answer_callback_query(call.id)
        return
    
    bot.answer_callback_query(call.id, "Неизвестная команда")

# ===== ФУНКЦИИ ДЛЯ ОБРАБОТЧИКОВ =====

def show_schedule_and_top(call):
    try:
        try:
            bot.edit_message_text("⏳ Загружаю календарь...", call.message.chat.id, call.message.message_id)
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
        
        # Топ-5 из ESPN
        try:
            standings_url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings"
            s_resp = requests.get(standings_url, timeout=10)
            s_data = s_resp.json()
            entries = s_data.get('standings', [{}])[0].get('entries', [])[:5]
            if entries:
                lines.extend(["", "🏆 **Топ-5 чемпионата**", ""])
                for i, entry in enumerate(entries, 1):
                    name = entry.get('athlete', {}).get('displayName', 'Неизвестно')
                    points = entry.get('points', 0)
                    lines.append(f"{i}. {name} — {points} очков")
        except:
            pass
        
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
            
            # Пробуем перевести через Нико
            try:
                translated = ask_nico(f"Переведи на русский эту новость: {headline}. {description}")
                ru_headline, ru_description = translated.split('\n', 1) if '\n' in translated else (headline, description)
            except:
                ru_headline, ru_description = headline, description
            
            text += f"**{ru_headline}**\n{ru_description}...\n[Читать]({link})\n\n_Переведено с помощью Nico AI_\n\n"
        
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

def handle_ticket_input(message):
    """Обработка заявки в ТП"""
    try:
        parts = message.text.split('|')
        issue = parts[0].strip()
        contact = parts[1].strip() if len(parts) > 1 else message.from_user.username or "Не указан"
    except:
        issue = message.text
        contact = message.from_user.username or "Не указан"
    
    ticket_id = create_ticket(message.from_user.id, issue, contact)
    
    bot.send_message(
        message.chat.id,
        f"✅ **Заявка #{ticket_id}** создана!\n\n"
        f"Админы скоро ответят.\n"
        f"Можешь отслеживать статус через /ticket"
    )
    
    # Отправляем уведомление админам
    for admin_id in ADMIN_IDS:
        try:
            bot.send_message(
                admin_id,
                f"🎫 **Новая заявка #{ticket_id}**\n"
                f"От: @{contact}\n"
                f"ID: {message.from_user.id}\n"
                f"Проблема: {issue[:200]}"
            )
        except:
            pass

# ===== АДМИН-ОБРАБОТЧИКИ =====
def handle_admin(call):
    if call.data == "admin_stats":
        stats = get_global_stats()
        text = "📊 **Глобальная статистика**\n\n"
        text += f"👤 Пользователей: {stats['total_users']}\n"
        text += f"📝 Команд: {stats['total_commands']}\n\n"
        text += "**Топ-10 команд:**\n"
        for cmd, count in stats['top_commands'][:5]:
            text += f"• {cmd} — {count}\n"
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    elif call.data == "admin_users":
        users = get_all_users()
        text = f"👥 **Все пользователи ({len(users)})**\n\n"
        text += f"Активных за 7 дней: {len(get_active_users(7))}\n"
        text += f"Активных за 30 дней: {len(get_active_users(30))}\n\n"
        text += "Последние 10 ID:\n"
        for uid in users[-10:]:
            text += f"• `{uid}`\n"
        
        bot.edit_message_text(
            text,
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
        
        text = "📈 **Статистика команд**\n\n"
        for cmd, count in commands:
            text += f"• {cmd} — {count} раз\n"
        
        bot.edit_message_text(
            text[:4000],
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    elif call.data == "admin_user_stats":
        bot.send_message(
            call.message.chat.id,
            "🔍 **Поиск пользователя**\n\nВведите ID пользователя:",
            reply_markup=back_to_menu()
        )
        set_state(call.from_user.id, "waiting_user_search")
    
    elif call.data == "admin_broadcast":
        bot.send_message(
            call.message.chat.id,
            "📨 **Рассылка**\n\n"
            "Введите текст рассылки.\n"
            "Опции:\n"
            "• `all` — всем пользователям\n"
            "• `active` — активным за 7 дней\n"
            "• `ID,ID` — конкретным пользователям\n\n"
            "Пример: `all | Привет!`"
        )
        set_state(call.from_user.id, "waiting_broadcast")
    
    elif call.data == "admin_update_db":
        msg = bot.send_message(
            call.message.chat.id,
            "🔄 Обновляю базу знаний..."
        )
        
        asyncio.run(update_knowledge_base())
        
        bot.edit_message_text(
            "✅ База знаний обновлена!\n"
            "Нико теперь знает актуальные данные.",
            msg.chat.id,
            msg.message_id,
            reply_markup=admin_panel()
        )
    
    elif call.data == "admin_tickets":
        tickets = get_open_tickets()
        
        if not tickets:
            text = "🎫 **Открытых заявок нет**"
        else:
            text = f"🎫 **Открытые заявки ({len(tickets)})**\n\n"
            for ticket in tickets[:10]:
                text += f"#{ticket[0]} | от @{ticket[3]} | {ticket[4][:16]}\n"
                text += f"  {ticket[2][:80]}...\n\n"
            text += f"\nИспользуй /close_ticket [id] чтобы закрыть"
        
        bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.message_id,
            reply_markup=admin_panel(),
            parse_mode="Markdown"
        )
    
    bot.answer_callback_query(call.id)

def handle_broadcast_input(message, data):
    """Обрабатывает рассылку"""
    try:
        parts = message.text.split('|', 1)
        target = parts[0].strip().lower()
        text = parts[1].strip()
    except:
        bot.send_message(
            message.chat.id,
            "❌ Неверный формат. Используй: `all | текст`"
        )
        return
    
    # Определяем получателей
    if target == "all":
        users = get_all_users()
    elif target == "active":
        users = get_active_users(7)
    else:
        try:
            users = [int(x.strip()) for x in target.split(',')]
        except:
            bot.send_message(
                message.chat.id,
                "❌ Неверный формат ID. Используй: `12345,67890,11111 | текст`"
            )
            return
    
    if not users:
        bot.send_message(
            message.chat.id,
            "⚠️ Нет пользователей для рассылки"
        )
        return
    
    # Подтверждение
    confirm = InlineKeyboardMarkup()
    confirm.add(
        InlineKeyboardButton("✅ Отправить", callback_data=f"broadcast_confirm_{len(users)}"),
        InlineKeyboardButton("❌ Отмена", callback_data="menu")
    )
    
    bot.send_message(
        message.chat.id,
        f"📨 **Подтверждение рассылки**\n\n"
        f"Получателей: {len(users)}\n"
        f"Текст:\n{text[:200]}\n\n"
        f"Нажми 'Отправить' для запуска.",
        reply_markup=confirm
    )
    
    # Сохраняем данные для отправки
    set_state(message.from_user.id, "broadcast_confirm", f"{users}|{text}")

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

# ===== ЗАПУСК =====
if __name__ == "__main__":
    # Запускаем автопарсинг в отдельном потоке
    threading.Thread(target=start_auto_update, daemon=True).start()
    
    # Первое обновление базы
    print("🔄 Первоначальное обновление базы знаний...")
    asyncio.run(update_knowledge_base())
    
    # Установка вебхука
    set_webhook()
    
    # Запуск сервера
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
