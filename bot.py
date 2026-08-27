#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I.N.D.Y Leader v2.0 — адаптивный гид по IndyCar
Архитектура: FastAPI + Webhook + ООП
Автор: P4/9 · Gabriella Projects
"""

import os
import sys
import logging
import sqlite3
import asyncio
import aiohttp
import feedparser
import requests
import random
import re
import time
import threading
from datetime import datetime, timezone
from collections import Counter
from typing import Optional, Dict, List, Any

import telebot
from fastapi import FastAPI, Request, Response
from telebot.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB,
    InputMediaPhoto
)

# ===== ПЕРЕВОДЧИК (googletrans-modified) =====
from googletrans import Translator as GoogleTranslator

# ============================================
# ИМПОРТ ДАННЫХ
# ============================================

from data.drivers import DRIVERS
from data.winners import WINNERS

# ============================================
# НАСТРОЙКА ЛОГИРОВАНИЯ
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ============================================

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8000))
GROQ_API_KEY = os.getenv("GROQ_API_KEY")

if not TOKEN:
    logger.error("❌ BOT_TOKEN не задан")
    sys.exit(1)

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "7025868617,7946032603").split(",") if x.strip()]
logger.info(f"✅ Админы: {ADMIN_IDS}")

# ============================================
# БАЗА ДАННЫХ
# ============================================

class Database:
    def __init__(self, path: str = 'indyleader.db'):
        self.path = path
        self._init()

    def _init(self):
        conn = sqlite3.connect(self.path, check_same_thread=False)
        c = conn.cursor()

        c.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                first_seen TEXT,
                last_seen TEXT,
                total_commands INTEGER DEFAULT 0,
                level TEXT DEFAULT 'novice'
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

    def add_user(self, uid: int, username: str, first_name: str):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        now = datetime.now().isoformat()

        c.execute('SELECT user_id FROM users WHERE user_id = ?', (uid,))
        if c.fetchone():
            c.execute('''
                UPDATE users SET username=?, first_name=?, last_seen=?, total_commands=total_commands+1
                WHERE user_id=?
            ''', (username, first_name, now, uid))
        else:
            c.execute('''
                INSERT INTO users (user_id, username, first_name, first_seen, last_seen, total_commands)
                VALUES (?, ?, ?, ?, ?, 1)
            ''', (uid, username, first_name, now, now))

        conn.commit()
        conn.close()

    def log_command(self, uid: int, cmd: str):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('INSERT INTO stats (command, user_id, timestamp) VALUES (?, ?, ?)',
                  (cmd, uid, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_user_level(self, uid: int) -> str:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT level FROM users WHERE user_id = ?', (uid,))
        row = c.fetchone()
        conn.close()
        return row[0] if row else 'novice'

    def set_user_level(self, uid: int, level: str):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, uid))
        conn.commit()
        conn.close()

    def get_all_users(self) -> list:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT user_id FROM users')
        users = [row[0] for row in c.fetchall()]
        conn.close()
        return users

    def get_active_users(self, days: int = 7) -> list:
        cutoff = (datetime.now() - timezone.timedelta(days=days)).isoformat()
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('''
            SELECT DISTINCT user_id FROM stats
            WHERE timestamp > ?
            GROUP BY user_id
            HAVING COUNT(*) > 1
        ''', (cutoff,))
        users = [row[0] for row in c.fetchall()]
        conn.close()
        return users

    def get_stats(self) -> Dict:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM users')
        users = c.fetchone()[0]
        c.execute('SELECT COUNT(*) FROM stats')
        commands = c.fetchone()[0]
        conn.close()
        return {'users': users, 'commands': commands}

    def get_command_stats(self) -> list:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT command, COUNT(*) FROM stats GROUP BY command ORDER BY COUNT(*) DESC')
        rows = c.fetchall()
        conn.close()
        return rows

    def get_users_list(self) -> list:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT user_id, username, first_name, level, total_commands, last_seen FROM users ORDER BY last_seen DESC')
        rows = c.fetchall()
        conn.close()
        return rows

    def set_state(self, uid: int, state: str, data: str = None):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO user_states (user_id, state, data, updated_at)
            VALUES (?, ?, ?, ?)
        ''', (uid, state, data, datetime.now().isoformat()))
        conn.commit()
        conn.close()

    def get_state(self, uid: int) -> tuple:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('SELECT state, data FROM user_states WHERE user_id = ?', (uid,))
        row = c.fetchone()
        conn.close()
        return row if row else (None, None)

    def clear_state(self, uid: int):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('DELETE FROM user_states WHERE user_id = ?', (uid,))
        conn.commit()
        conn.close()

    def create_ticket(self, uid: int, issue: str, contact: str) -> int:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('''
            INSERT INTO tickets (user_id, issue, contact, created_at)
            VALUES (?, ?, ?, ?)
        ''', (uid, issue, contact, datetime.now().isoformat()))
        conn.commit()
        ticket_id = c.lastrowid
        conn.close()
        return ticket_id

    def get_open_tickets(self) -> list:
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('''
            SELECT id, user_id, issue, contact, created_at
            FROM tickets WHERE status = 'open'
            ORDER BY created_at DESC
        ''')
        rows = c.fetchall()
        conn.close()
        return rows

    def close_ticket(self, ticket_id: int):
        conn = sqlite3.connect(self.path)
        c = conn.cursor()
        c.execute('UPDATE tickets SET status = "closed" WHERE id = ?', (ticket_id,))
        conn.commit()
        conn.close()


# ============================================
# ПЕРЕВОДЧИК
# ============================================

class Translator:
    def __init__(self):
        self.translator = GoogleTranslator()
        logger.info("✅ Переводчик Google инициализирован")

    def translate(self, text: str, dest: str = 'ru') -> str:
        if not text:
            return text
        try:
            result = self.translator.translate(text, dest=dest)
            return result.text
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return text


# ============================================
# AI (НИКО)
# ============================================

class NicoAI:
    def __init__(self, api_key: str = None):
        self.api_key = api_key or GROQ_API_KEY

    def ask(self, question: str) -> str:
        if not self.api_key:
            return "⚠️ Нико не настроен (нет API-ключа)"

        knowledge = ""
        try:
            with open("data/knowledge.txt", "r", encoding="utf-8") as f:
                knowledge = f.read()[:2000]
        except:
            pass

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
{knowledge}
"""

        try:
            resp = requests.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
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

            if resp.status_code != 200:
                error = resp.json().get('error', {}).get('message', 'Неизвестная ошибка')
                return f"⚠️ Groq API ошибка {resp.status_code}: {error}"

            data = resp.json()
            return data["choices"][0]["message"]["content"]

        except requests.exceptions.Timeout:
            return "⏰ Нико слишком долго думал. Попробуй еще раз."
        except Exception as e:
            logger.error(f"AI error: {e}")
            return f"⚠️ Ошибка: {e}"


# ============================================
# ПАРСЕР НОВОСТЕЙ
# ============================================

class NewsParser:
    SOURCES = {
        'espn': 'https://site.api.espn.com/apis/site/v2/sports/racing/irl/news',
        'therace': 'https://www.the-race.com/category/indycar/rss',
        'motorsport': 'https://www.motorsport.com/indycar/rss/',
    }

    def __init__(self):
        self.translator = Translator()

    async def fetch_all(self) -> list:
        all_news = []

        for name, url in self.SOURCES.items():
            if name == 'espn':
                continue
            try:
                feed = feedparser.parse(url)
                for entry in feed.entries[:3]:
                    all_news.append({
                        'title': entry.get('title', ''),
                        'summary': entry.get('summary', '')[:300],
                        'link': entry.get('link', '#'),
                        'source': name
                    })
            except:
                continue

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.SOURCES['espn'], timeout=10) as resp:
                    data = await resp.json()
                    for article in data.get('articles', [])[:3]:
                        all_news.append({
                            'title': article.get('headline', ''),
                            'summary': article.get('description', '')[:300],
                            'link': article.get('links', {}).get('web', {}).get('href', '#'),
                            'source': 'espn'
                        })
        except:
            pass

        return all_news

    def fetch_sync(self) -> list:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(self.fetch_all())

    def translate_news(self, articles: list) -> list:
        translated = []
        for article in articles:
            try:
                translated.append({
                    'title': self.translator.translate(article['title']),
                    'summary': self.translator.translate(article['summary']),
                    'link': article['link'],
                    'source': article['source']
                })
            except:
                translated.append(article)
        return translated


# ============================================
# ТОП-5 ЧЕМПИОНАТА
# ============================================

class StandingsFetcher:
    @staticmethod
    def fetch() -> list:
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings"
            resp = requests.get(url, timeout=10)
            data = resp.json()

            top5 = []
            for entry in data.get('standings', [{}])[0].get('entries', [])[:5]:
                top5.append({
                    'name': entry.get('athlete', {}).get('displayName', 'Неизвестно'),
                    'points': entry.get('points', 0)
                })
            return top5
        except:
            return []


# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def escape_markdown(text: str) -> str:
    if not text:
        return text
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    return re.sub(f'([{re.escape(escape_chars)}])', r'\\\1', text)


def find_driver(query: str) -> Optional[Dict]:
    query = query.lower().strip()
    for code, driver in DRIVERS.items():
        name = driver['name'].lower()
        if query in name:
            return driver
        for part in name.split():
            if query == part or query in part:
                return driver
    return None


# ============================================
# ГЛАВНЫЙ КЛАСС БОТА
# ============================================

class IndyBot:
    def __init__(self, token: str):
        self.bot = telebot.TeleBot(token, threaded=False)
        self.db = Database()
        self.ai = NicoAI()
        self.news_parser = NewsParser()
        self.admin_ids = ADMIN_IDS

        self._register_handlers()
        logger.info("✅ INDY Leader v2.0 готов к работе!")

    def _register_handlers(self):
        self.bot.message_handler(commands=['start'])(self.cmd_start)
        self.bot.message_handler(commands=['help'])(self.cmd_help)
        self.bot.message_handler(commands=['switch'])(self.cmd_switch)
        self.bot.message_handler(commands=['admin'])(self.cmd_admin)
        self.bot.message_handler(commands=['ticket'])(self.cmd_ticket)

        self.bot.callback_query_handler(func=lambda c: True)(self._handle_callback)
        self.bot.message_handler(func=lambda m: True)(self._handle_text)

    # ============================================
    # КОМАНДЫ
    # ============================================

    def cmd_start(self, m: Message):
        uid = m.from_user.id
        name = m.from_user.first_name or 'Пользователь'
        username = m.from_user.username or 'без_юзернейма'

        self.db.add_user(uid, username, name)
        level = self.db.get_user_level(uid)

        if level in ['novice', 'pro']:
            level_name = '🟢 Новичок' if level == 'novice' else '🔴 Продвинутый'
            markup = IKM(row_width=1)
            markup.add(IKB("🔄 Сменить уровень", callback_data="switch_level"))
            markup.add(IKB("🔙 В меню", callback_data="menu"))

            self.bot.send_message(
                m.chat.id,
                f"🏁 **С возвращением, {name}!**\n\nТвой уровень: **{level_name}**\n\n/switch — сменить уровень",
                reply_markup=markup,
                parse_mode="Markdown"
            )
            return

        markup = IKM(row_width=2)
        markup.add(
            IKB("🟢 Новичок", callback_data="level_novice"),
            IKB("🔴 Продвинутый", callback_data="level_pro")
        )

        self.bot.send_message(
            m.chat.id,
            f"👋 **Привет, {name}!**\n\n"
            f"Я — INDY Leader, гид по IndyCar.\n\n"
            f"**Кто ты?**\n"
            f"🟢 **Новичок** — объясню всё с нуля\n"
            f"🔴 **Продвинутый** — дам максимум фактов\n\n"
            f"Уровень можно сменить командой /switch в любой момент",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def cmd_help(self, m: Message):
        self.bot.send_message(
            m.chat.id,
            "🤖 **INDY Leader — справка**\n\n"
            "/start — главное меню\n"
            "/switch — сменить уровень\n"
            "/admin — админ-панель\n"
            "/ticket — заявка в техподдержку\n\n"
            "Все остальные функции доступны через кнопки в меню.",
            parse_mode="Markdown"
        )

    def cmd_switch(self, m: Message):
        uid = m.from_user.id
        current = self.db.get_user_level(uid)
        cur_name = '🟢 Новичок' if current == 'novice' else '🔴 Продвинутый'

        markup = IKM(row_width=2)
        markup.add(
            IKB("🟢 Новичок", callback_data="level_novice"),
            IKB("🔴 Продвинутый", callback_data="level_pro")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))

        self.bot.send_message(
            m.chat.id,
            f"⚙️ **Смена уровня**\n\nТекущий уровень: **{cur_name}**\n\nВыбери новый:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def cmd_admin(self, m: Message):
        if m.from_user.id not in self.admin_ids:
            self.bot.reply_to(m, "⛔ У вас нет доступа к админ-панели")
            return

        markup = IKM(row_width=2)
        markup.add(
            IKB("📊 Статистика", callback_data="admin_stats"),
            IKB("👥 Пользователи", callback_data="admin_users")
        )
        markup.add(
            IKB("📈 Команды", callback_data="admin_commands"),
            IKB("🎫 Заявки", callback_data="admin_tickets")
        )
        markup.add(
            IKB("📨 Рассылка", callback_data="admin_broadcast"),
            IKB("🔄 Обновить базу", callback_data="admin_update_db")
        )
        markup.add(IKB("🔙 Выйти", callback_data="menu"))

        self.bot.send_message(
            m.chat.id,
            "🔐 **Админ-панель**\n\nВыберите действие:",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def cmd_ticket(self, m: Message):
        self.bot.send_message(
            m.chat.id,
            "🎫 **Техническая поддержка**\n\n"
            "Опиши свою проблему в одном сообщении.\n"
            "Формат: проблема | контакт (например: @username или почта)"
        )
        self.db.set_state(m.from_user.id, "waiting_ticket")

    # ============================================
    # ОБРАБОТЧИК КНОПОК (ФИКС: answer_callback_query)
    # ============================================

    def _handle_callback(self, call: CallbackQuery):
        # ВСЕГДА отвечаем на callback
        self.bot.answer_callback_query(call.id)
        
        data = call.data
        uid = call.from_user.id

        if data == "level_novice":
            self.db.set_user_level(uid, 'novice')
            self.bot.edit_message_text(
                "🟢 **Уровень: Новичок**",
                call.message.chat.id,
                call.message.id,
                reply_markup=self._main_menu(uid),
                parse_mode="Markdown"
            )
            return

        if data == "level_pro":
            self.db.set_user_level(uid, 'pro')
            self.bot.edit_message_text(
                "🔴 **Уровень: Продвинутый**",
                call.message.chat.id,
                call.message.id,
                reply_markup=self._main_menu(uid),
                parse_mode="Markdown"
            )
            return

        if data == "switch_level":
            self.cmd_switch(call.message)
            return

        if data == "menu":
            self.bot.edit_message_text(
                "🏁 **Главное меню**",
                call.message.chat.id,
                call.message.id,
                reply_markup=self._main_menu(uid),
                parse_mode="Markdown"
            )
            return

        if data.startswith("admin_"):
            if uid not in self.admin_ids:
                self.bot.answer_callback_query(call.id, "Нет доступа")
                return
            self._handle_admin(call)
            return

        if data == "drivers_list":
            self._show_drivers_list(call)
            return

        if data == "driver_random":
            code, driver = random.choice(list(DRIVERS.items()))
            self._send_driver(call.message.chat.id, driver)
            return

        if data.startswith("driver_"):
            code = data.replace("driver_", "")
            driver = DRIVERS.get(code)
            if driver:
                self._send_driver(call.message.chat.id, driver)
            else:
                self.bot.answer_callback_query(call.id, "Пилот не найден")
            return

        if data == "schedule_top":
            self._show_schedule_and_top(call)
            return

        if data == "indy500_menu":
            self._show_indy500_menu(call)
            return

        if data == "top_winners":
            self._show_top_winners(call)
            return

        if data == "winner_prompt":
            self.db.set_state(uid, "waiting_year")
            self.bot.edit_message_text(
                "📅 **Введи год** (например, 2023):",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
            )
            return

        if data == "news":
            self._show_news(call)
            return

        if data == "ask_nico":
            self.db.set_state(uid, "waiting_nico")
            self.bot.edit_message_text(
                "🧠 **Нико**\n\nНапиши свой вопрос про IndyCar:",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
            )
            return

        if data == "guide_intro":
            self._show_guide(call)
            return

        if data == "guide_rules":
            self._show_rules(call)
            return

        if data == "guide_tracks":
            self._show_tracks(call)
            return

        if data == "donate":
            self.bot.edit_message_text(
                "❤️ **Поддержать проект**\n\n"
                "💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu")),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            return

        if data == "about":
            self.bot.edit_message_text(
                "📘 **О проекте**\n\n"
                "Неофициальный бот для фанатов IndyCar.\n"
                "Не связан с IndyCar Series, LLC.\n\n"
                "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                "🧑‍💻 @Gabriella1488, @Scanialove",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu")),
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
            return

        self.bot.answer_callback_query(call.id, "Неизвестная команда")

    # ============================================
    # ОБРАБОТЧИК ТЕКСТА
    # ============================================

    def _handle_text(self, m: Message):
        uid = m.from_user.id
        state, data = self.db.get_state(uid)

        if not state:
            self.bot.send_message(
                m.chat.id,
                "Используй кнопки в меню 👇",
                reply_markup=self._main_menu(uid)
            )
            return

        if state == "waiting_year":
            self._handle_year_input(m)
            self.db.clear_state(uid)
        elif state == "waiting_nico":
            self._handle_nico_input(m)
            self.db.clear_state(uid)
        elif state == "waiting_ticket":
            self._handle_ticket_input(m)
            self.db.clear_state(uid)
        elif state == "waiting_broadcast":
            self._handle_broadcast_input(m)
            self.db.clear_state(uid)
        else:
            self.bot.send_message(
                m.chat.id,
                "Используй кнопки в меню 👇",
                reply_markup=self._main_menu(uid)
            )

    # ============================================
    # МЕНЮ
    # ============================================

    def _main_menu(self, uid: int) -> IKM:
        level = self.db.get_user_level(uid)
        return self._menu_pro() if level == 'pro' else self._menu_novice()

    def _menu_novice(self) -> IKM:
        markup = IKM(row_width=2)
        markup.add(
            IKB("📖 Гайд по IndyCar", callback_data="guide_intro"),
            IKB("🏁 Календарь и топ", callback_data="schedule_top")
        )
        markup.add(
            IKB("🏎️ Пилоты", callback_data="drivers_list"),
            IKB("🎲 Случайный пилот", callback_data="driver_random")
        )
        markup.add(
            IKB("🏆 Indy 500", callback_data="indy500_menu"),
            IKB("📰 Новости", callback_data="news")
        )
        markup.add(
            IKB("🧠 Спросить Нико", callback_data="ask_nico"),
            IKB("❤️ Поддержать", callback_data="donate")
        )
        markup.add(
            IKB("ℹ️ О проекте", callback_data="about"),
            IKB("🔄 Сменить уровень", callback_data="switch_level")
        )
        return markup

    def _menu_pro(self) -> IKM:
        markup = IKM(row_width=2)
        markup.add(
            IKB("🏁 Календарь и топ", callback_data="schedule_top"),
            IKB("🏎️ Пилоты", callback_data="drivers_list")
        )
        markup.add(
            IKB("🎲 Случайный пилот", callback_data="driver_random"),
            IKB("🏆 Indy 500", callback_data="indy500_menu")
        )
        markup.add(
            IKB("📰 Новости", callback_data="news"),
            IKB("🧠 Спросить Нико", callback_data="ask_nico")
        )
        markup.add(
            IKB("❤️ Поддержать", callback_data="donate"),
            IKB("ℹ️ О проекте", callback_data="about")
        )
        markup.add(
            IKB("🔄 Сменить уровень", callback_data="switch_level")
        )
        return markup

    # ============================================
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ (ФИКС: photo без edit)
    # ============================================

    def _send_driver(self, chat_id: int, driver: Dict):
        text = f"🏎️ **{driver['name']}**\n🏁 {driver['team']}\n🔢 #{driver['number']}"
        if driver.get('pos'):
            text += f"\n📊 Позиция: {driver['pos']}"

        markup = IKM().add(IKB("🔙 Назад", callback_data="drivers_list"))

        if driver.get('image'):
            try:
                # Отправляем НОВОЕ сообщение с фото
                self.bot.send_photo(
                    chat_id,
                    driver['image'],
                    caption=text,
                    parse_mode="Markdown",
                    reply_markup=markup
                )
                return
            except Exception as e:
                logger.error(f"Photo send error: {e}")

        # Если фото нет или ошибка — текстом
        self.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

    def _show_drivers_list(self, call: CallbackQuery):
        markup = IKM(row_width=2)
        teams = {}
        for code, d in DRIVERS.items():
            if d['team'] not in teams:
                teams[d['team']] = []
            teams[d['team']].append((code, d))

        for team, drivers in sorted(teams.items())[:8]:
            markup.add(IKB(f"━━ {team} ━━", callback_data="noop"))
            row = []
            for code, d in drivers[:4]:
                surname = d['name'].split()[-1]
                row.append(IKB(f"{surname} #{d['number']}", callback_data=f"driver_{code}"))
                if len(row) == 2:
                    markup.add(*row)
                    row = []
            if row:
                markup.add(*row)

        markup.add(IKB("🔙 Назад", callback_data="menu"))
        self.bot.edit_message_text(
            "🏎️ **Выбери пилота**",
            call.message.chat.id,
            call.message.id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def _show_schedule_and_top(self, call: CallbackQuery):
        self.bot.edit_message_text(
            "⏳ Загружаю календарь...",
            call.message.chat.id,
            call.message.id
        )

        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard?seasontype=2&level=3"
            resp = requests.get(url, timeout=10)
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
                        future_races.append({
                            'label': event.get('label', 'Неизвестная гонка'),
                            'date': event_date.strftime('%d.%m.%Y')
                        })
                except:
                    continue

            future_races = future_races[:10]

            lines = ["🏁 **Ближайшие гонки 2026**", ""]
            if not future_races:
                lines.append("🏁 Сезон завершен или календарь не загружен")
            else:
                for race in future_races:
                    lines.append(f"📅 {race['date']} — **{race['label']}**")

            top5 = StandingsFetcher.fetch()
            if top5:
                lines.extend(["", "🏆 **Топ-5 чемпионата**", ""])
                for i, d in enumerate(top5, 1):
                    lines.append(f"{i}. {d['name']} — {d['points']} очков")

            self.bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu")),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Schedule error: {e}")
            self.bot.edit_message_text(
                "⚠️ Ошибка загрузки календаря",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
            )

    def _show_indy500_menu(self, call: CallbackQuery):
        markup = IKM(row_width=2)
        markup.add(
            IKB("📅 По году", callback_data="winner_prompt"),
            IKB("🏆 Топ-10 победителей", callback_data="top_winners")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))

        self.bot.edit_message_text(
            "🏆 **Indy 500**\n\nЧто хочешь узнать?",
            call.message.chat.id,
            call.message.id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def _show_top_winners(self, call: CallbackQuery):
        wins = Counter()
        for w in WINNERS:
            if w['year'] >= 1911 and 'не проводилась' not in w['driver']:
                wins[w['driver']] += 1

        top = wins.most_common(10)
        medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

        text = "🏆 **10 величайших победителей**\n\n"
        for i, (driver, count) in enumerate(top):
            text += f"{medals[i]} {driver} — **{count}** побед\n"

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="indy500_menu")),
            parse_mode="Markdown"
        )

    def _show_news(self, call: CallbackQuery):
        self.bot.edit_message_text(
            "📰 Собираю новости...",
            call.message.chat.id,
            call.message.id
        )

        articles = self.news_parser.fetch_sync()
        if not articles:
            self.bot.edit_message_text(
                "📰 Новостей пока нет",
                call.message.chat.id,
                call.message.id,
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
            )
            return

        translated = self.news_parser.translate_news(articles)
        article = translated[0]
        text = f"📰 **{article['title']}**\n\n{article['summary']}...\n\n[Читать]({article['link']})"
        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            reply_markup=IKM().add(IKB("➡️ Следующая", callback_data="news_next")),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

        self.db.set_state(call.from_user.id, "news_view", str(translated))

    def _handle_year_input(self, m: Message):
        try:
            year = int(m.text.strip())
        except:
            self.bot.send_message(
                m.chat.id,
                "❌ Введи год цифрами",
                reply_markup=IKM().add(IKB("🔙 Назад", callback_data="indy500_menu"))
            )
            return

        for w in WINNERS:
            if w.get('year') == year:
                self.bot.send_message(
                    m.chat.id,
                    f"🏆 **Indy 500 {year}**\n🏁 {w.get('driver', 'Неизвестно')}",
                    parse_mode="Markdown",
                    reply_markup=IKM().add(IKB("🔙 Назад", callback_data="indy500_menu"))
                )
                return

        self.bot.send_message(
            m.chat.id,
            f"❌ Нет данных за {year}",
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="indy500_menu"))
        )

    def _handle_nico_input(self, m: Message):
        self.bot.send_message(m.chat.id, "🧠 Нико думает...")
        response = self.ai.ask(m.text)
        safe_response = escape_markdown(response)

        self.bot.send_message(
            m.chat.id,
            f"🧠 **Нико:**\n\n{safe_response}",
            parse_mode="Markdown",
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
        )

    def _handle_ticket_input(self, m: Message):
        try:
            parts = m.text.split('|')
            issue = parts[0].strip()
            contact = parts[1].strip() if len(parts) > 1 else m.from_user.username or "Не указан"
        except:
            issue = m.text
            contact = m.from_user.username or "Не указан"

        ticket_id = self.db.create_ticket(m.from_user.id, issue, contact)

        self.bot.send_message(
            m.chat.id,
            f"✅ **Заявка #{ticket_id}** создана!\n\nАдмины скоро ответят.",
            reply_markup=IKM().add(IKB("🔙 В меню", callback_data="menu"))
        )

        for admin_id in self.admin_ids:
            try:
                self.bot.send_message(
                    admin_id,
                    f"🎫 **Новая заявка #{ticket_id}**\n"
                    f"От: @{contact}\n"
                    f"ID: {m.from_user.id}\n"
                    f"Проблема: {issue[:200]}"
                )
            except:
                pass

    def _handle_broadcast_input(self, m: Message):
        try:
            parts = m.text.split('|', 1)
            target = parts[0].strip().lower()
            text = parts[1].strip()
        except:
            self.bot.send_message(
                m.chat.id,
                "❌ Неверный формат. Используй: `all | текст`"
            )
            return

        if target == "all":
            users = self.db.get_all_users()
        elif target == "active":
            users = self.db.get_active_users(7)
        else:
            try:
                users = [int(x.strip()) for x in target.split(',')]
            except:
                self.bot.send_message(
                    m.chat.id,
                    "❌ Неверный формат ID. Используй: `12345,67890,11111 | текст`"
                )
                return

        if not users:
            self.bot.send_message(m.chat.id, "⚠️ Нет пользователей для рассылки")
            return

        success = 0
        failed = 0
        for uid in users:
            try:
                self.bot.send_message(uid, text, parse_mode="Markdown")
                success += 1
                time.sleep(0.05)
            except:
                failed += 1

        self.bot.send_message(
            m.chat.id,
            f"📨 **Рассылка завершена**\n\n✅ Успешно: {success}\n❌ Ошибок: {failed}",
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="menu"))
        )

    def _show_guide(self, call: CallbackQuery):
        text = (
            "📖 **Что такое IndyCar?**\n\n"
            "IndyCar — американская серия гонок на открытых колесах.\n\n"
            "**Особенности:**\n"
            "🏁 Овальные трассы (США)\n"
            "🚗 Болиды до 700 л.с.\n"
            "🏆 Indy 500 — главная гонка\n"
            "🌍 Пилоты из 10+ стран\n\n"
            "**Как устроен чемпионат:**\n"
            "• 17 этапов\n"
            "• 7 типов трасс\n"
            "• Очки топ-10\n"
            "• Победитель по итогам сезона"
        )

        markup = IKM(row_width=2)
        markup.add(
            IKB("📋 Правила", callback_data="guide_rules"),
            IKB("🏁 Трассы", callback_data="guide_tracks")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def _show_rules(self, call: CallbackQuery):
        text = (
            "📋 **Правила IndyCar**\n\n"
            "**Очки:**\n"
            "1 место — 50\n"
            "2 место — 40\n"
            "3 место — 35\n"
            "...\n"
            "10 место — 10\n"
            "+1 за поул\n"
            "+1 за быстрый круг\n\n"
            "**Штрафы:**\n"
            "• Превышение на пит-лейн\n"
            "• Блокировка\n"
            "• Нарушение флагов"
        )

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="guide_intro")),
            parse_mode="Markdown"
        )

    def _show_tracks(self, call: CallbackQuery):
        text = (
            "🏁 **Трассы IndyCar**\n\n"
            "🏟️ **Овалы** (7 этапов)\n"
            "• Indianapolis (2.5 мили)\n"
            "• Texas (1.5 мили)\n\n"
            "🔄 **Шоссейные** (5 этапов)\n"
            "• Road America (4 мили)\n"
            "• Mid-Ohio (2.25 мили)\n\n"
            "🏙️ **Уличные** (5 этапов)\n"
            "• St. Petersburg (1.8 мили)\n"
            "• Long Beach (1.97 мили)\n\n"
            "🏆 **Indy 500** — главная гонка"
        )

        self.bot.edit_message_text(
            text,
            call.message.chat.id,
            call.message.id,
            reply_markup=IKM().add(IKB("🔙 Назад", callback_data="guide_intro")),
            parse_mode="Markdown"
        )

    # ============================================
    # АДМИН-ОБРАБОТЧИК
    # ============================================

    def _handle_admin(self, call: CallbackQuery):
        data = call.data

        if data == "admin_stats":
            stats = self.db.get_stats()
            commands = self.db.get_command_stats()
            text = f"📊 **Глобальная статистика**\n\n"
            text += f"👤 Пользователей: {stats['users']}\n"
            text += f"📝 Команд: {stats['commands']}\n\n"
            text += "**Топ-5 команд:**\n"
            for cmd, count in commands[:5]:
                text += f"• {cmd} — {count}\n"

            self.bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.id,
                reply_markup=self._admin_back(),
                parse_mode="Markdown"
            )
            return

        if data == "admin_users":
            users = self.db.get_all_users()
            text = f"👥 **Все пользователи ({len(users)})**\n\n"
            text += f"Активных за 7 дней: {len(self.db.get_active_users(7))}\n"
            text += f"Активных за 30 дней: {len(self.db.get_active_users(30))}\n\n"
            text += "Последние 10 ID:\n"
            for uid in users[-10:]:
                text += f"• `{uid}`\n"

            self.bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.id,
                reply_markup=self._admin_back(),
                parse_mode="Markdown"
            )
            return

        if data == "admin_commands":
            commands = self.db.get_command_stats()
            text = "📈 **Статистика команд**\n\n"
            for cmd, count in commands[:10]:
                text += f"• {cmd} — {count} раз\n"

            self.bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.id,
                reply_markup=self._admin_back(),
                parse_mode="Markdown"
            )
            return

        if data == "admin_tickets":
            tickets = self.db.get_open_tickets()
            if not tickets:
                text = "🎫 **Открытых заявок нет**"
            else:
                text = f"🎫 **Открытые заявки ({len(tickets)})**\n\n"
                for ticket in tickets[:10]:
                    text += f"#{ticket[0]} | от @{ticket[3]} | {ticket[4][:16]}\n"
                    text += f"  {ticket[2][:80]}...\n\n"

            self.bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.id,
                reply_markup=self._admin_back(),
                parse_mode="Markdown"
            )
            return

        if data == "admin_broadcast":
            self.db.set_state(call.from_user.id, "waiting_broadcast")
            self.bot.edit_message_text(
                "📨 **Рассылка**\n\n"
                "Введите текст рассылки.\n"
                "Опции:\n"
                "• `all` — всем пользователям\n"
                "• `active` — активным за 7 дней\n"
                "• `ID,ID` — конкретным пользователям\n\n"
                "Пример: `all | Привет!`",
                call.message.chat.id,
                call.message.id,
                reply_markup=self._admin_back()
            )
            return

        if data == "admin_update_db":
            self.bot.edit_message_text(
                "🔄 Обновляю базу знаний...",
                call.message.chat.id,
                call.message.id
            )
            try:
                import requests
                resp = requests.get(
                    "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings",
                    timeout=10
                )
                data = resp.json()
                standings = []
                for entry in data.get('standings', [{}])[0].get('entries', [])[:10]:
                    standings.append({
                        'name': entry.get('athlete', {}).get('displayName', 'Unknown'),
                        'points': entry.get('points', 0),
                        'team': entry.get('team', {}).get('displayName', '')
                    })

                os.makedirs("data", exist_ok=True)
                with open("data/knowledge.txt", "w", encoding="utf-8") as f:
                    f.write(f"# База знаний INDY Leader\n")
                    f.write(f"# Обновлено: {datetime.now().isoformat()}\n\n")
                    f.write("## ТУРНИРНАЯ ТАБЛИЦА (ТОП-10)\n")
                    for i, driver in enumerate(standings, 1):
                        f.write(f"{i}. {driver['name']} — {driver['points']} очков ({driver['team']})\n")

                self.bot.edit_message_text(
                    "✅ База знаний обновлена!",
                    call.message.chat.id,
                    call.message.id,
                    reply_markup=self._admin_back()
                )
            except Exception as e:
                self.bot.edit_message_text(
                    f"❌ Ошибка: {e}",
                    call.message.chat.id,
                    call.message.id,
                    reply_markup=self._admin_back()
                )
            return

    def _admin_back(self) -> IKM:
        markup = IKM()
        markup.add(IKB("🔙 Назад", callback_data="admin_panel"))
        return markup


# ============================================
# СОЗДАНИЕ БОТА
# ============================================

indy_bot = IndyBot(TOKEN)
bot = indy_bot.bot
db = indy_bot.db

# ============================================
# FASTAPI — ВЕБХУК
# ============================================

app = FastAPI(title="INDY Leader", version="2.0.0")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    try:
        data = await request.json()
        update = telebot.types.Update.de_json(data)
        asyncio.create_task(_process_update(update))
        return Response(content="OK", status_code=200)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
        return Response(content="OK", status_code=200)

async def _process_update(update):
    try:
        bot.process_new_updates([update])
    except Exception as e:
        logger.error(f"Update error: {e}")

@app.get("/health")
async def health_check():
    try:
        info = bot.get_webhook_info()
        return {
            "status": "ok",
            "bot": bot.get_me().username,
            "webhook": info.url,
            "pending": info.pending_update_count,
            "last_error": info.last_error_message
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 503

@app.get("/")
async def root():
    return {"status": "INDY Leader is running", "webhook_url": WEBHOOK_URL + WEBHOOK_PATH}

def set_webhook():
    try:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(
            url=WEBHOOK_URL + WEBHOOK_PATH,
            max_connections=40,
            drop_pending_updates=True
        )
        logger.info(f"✅ Webhook: {WEBHOOK_URL + WEBHOOK_PATH}")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")

# ============================================
# ЗАПУСК
# ============================================

if __name__ == "__main__":
    import uvicorn
    set_webhook()
    uvicorn.run(app, host="0.0.0.0", port=PORT)
