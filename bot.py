#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I.N.D.Y Leader v2.5.0 — адаптивный гид по IndyCar
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
from datetime import datetime, timezone
from collections import Counter
from typing import Optional, Dict, List, Any

import telebot
from fastapi import FastAPI, Request, Response
from telebot.types import (
    Message, CallbackQuery,
    InlineKeyboardMarkup as IKM,
    InlineKeyboardButton as IKB
)

from google import genai
from googletrans import Translator as GoogleTranslator

from data.drivers import DRIVERS
from data.winners import WINNERS

# ============================================
# ИМПОРТ ИЗ ADMIN_TOOLS
# ============================================

from admin_tools import (
    get_all_users,
    get_active_users,
    get_user_stats,
    get_global_stats,
    send_broadcast,
    update_knowledge_base,
    start_auto_update,
    create_ticket,
    get_open_tickets,
    close_ticket,
    block_user,
    unblock_user,
    is_user_blocked,
    get_blocked_users,
    backup_database,
    cleanup_old_stats,
    db_check
)

# ============================================
# НАСТРОЙКА
# ============================================

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8000))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

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
# НИКО (GEMINI 3.6 FLASH)
# ============================================

class NicoAI:
    def __init__(self):
        self.client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None
        if self.client:
            logger.info("✅ Gemini 3.6 Flash инициализирован")
        else:
            logger.warning("⚠️ Gemini API ключ не задан")

    def ask(self, question: str) -> str:
        if not self.client:
            return "⚠️ Нико не настроен (нет API-ключа Gemini)"

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

ВАЖНО: Используй информацию из базы знаний, если она есть:
{knowledge}
"""

        try:
            response = self.client.models.generate_content(
                model="gemini-3.6-flash",
                contents=question,
                config={
                    "system_instruction": system_prompt,
                    "temperature": 0.7,
                    "max_output_tokens": 500,
                }
            )
            return response.text if response and response.text else "⚠️ Gemini вернул пустой ответ"
        except Exception as e:
            logger.error(f"Gemini API ошибка: {e}")
            return f"⚠️ Ошибка Gemini: {str(e)}"


# ============================================
# КЛАСС ДЛЯ ПОСТРОЕНИЯ МЕНЮ
# ============================================

class MenuBuilder:
    @staticmethod
    def main_menu(level: str) -> IKM:
        if level == 'novice':
            return MenuBuilder._menu_novice()
        return MenuBuilder._menu_pro()
    
    @staticmethod
    def _menu_novice() -> IKM:
        markup = IKM(row_width=2)
        buttons = [
            ("📖 Гайд", "guide_intro"),
            ("🏁 Календарь", "schedule_top"),
            ("🏎️ Пилоты", "drivers_list"),
            ("🎲 Случайный", "driver_random"),
            ("🏆 Indy 500", "indy500_menu"),
            ("📰 Новости", "news"),
            ("🧠 Нико", "ask_nico"),
            ("❤️ Поддержать", "donate"),
            ("ℹ️ О проекте", "about"),
            ("🔄 Сменить уровень", "switch_level"),
        ]
        for text, callback in buttons:
            markup.add(IKB(text, callback_data=callback))
        return markup
    
    @staticmethod
    def _menu_pro() -> IKM:
        markup = IKM(row_width=2)
        buttons = [
            ("🏁 Календарь и топ", "schedule_top"),
            ("🏎️ Пилоты", "drivers_list"),
            ("🎲 Случайный пилот", "driver_random"),
            ("🏆 Indy 500", "indy500_menu"),
            ("📰 Новости", "news"),
            ("🧠 Нико", "ask_nico"),
            ("❤️ Поддержать", "donate"),
            ("ℹ️ О проекте", "about"),
            ("🔄 Сменить уровень", "switch_level"),
        ]
        for text, callback in buttons:
            markup.add(IKB(text, callback_data=callback))
        return markup
    
    @staticmethod
    def drivers_menu() -> IKM:
        markup = IKM(row_width=2)
        teams = {}
        for code, d in DRIVERS.items():
            teams.setdefault(d['team'], []).append((code, d))
        
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
        return markup
    
    @staticmethod
    def indy500_menu() -> IKM:
        markup = IKM(row_width=2)
        markup.add(
            IKB("📅 По году", callback_data="winner_prompt"),
            IKB("🏆 Топ-10 победителей", callback_data="top_winners")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))
        return markup
    
    @staticmethod
    def admin_menu() -> IKM:
        markup = IKM(row_width=2)
        buttons = [
            ("📊 Статистика", "admin_stats"),
            ("👥 Пользователи", "admin_users"),
            ("📈 Команды", "admin_commands"),
            ("🎫 Заявки", "admin_tickets"),
            ("📨 Рассылка", "admin_broadcast"),
            ("🔄 Обновить базу", "admin_update_db"),
            ("🚫 Блокировка", "admin_block"),
            ("🔙 Выйти", "menu"),
        ]
        for text, callback in buttons:
            markup.add(IKB(text, callback_data=callback))
        return markup
    
    @staticmethod
    def guide_menu() -> IKM:
        markup = IKM(row_width=2)
        markup.add(
            IKB("📋 Правила", callback_data="guide_rules"),
            IKB("🏁 Трассы", callback_data="guide_tracks")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))
        return markup
    
    @staticmethod
    def back_to_menu() -> IKM:
        markup = IKM()
        markup.add(IKB("🔙 Назад", callback_data="menu"))
        return markup


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
# ТОП-5
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
        self.menu = MenuBuilder()

        self._register_handlers()
        logger.info("✅ INDY Leader v2.5.0 готов к работе!")

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
            markup = self.menu.main_menu(level)
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
            f"👋 **Привет, {name}!**\n\nЯ — INDY Leader, гид по IndyCar.\n\n**Кто ты?**\n🟢 Новичок — объясню с нуля\n🔴 Продвинутый — максимум фактов",
            reply_markup=markup,
            parse_mode="Markdown"
        )

    def cmd_help(self, m: Message):
        self.bot.send_message(
            m.chat.id,
            "🤖 **INDY Leader — справка**\n\n/start — главное меню\n/switch — сменить уровень\n/admin — админ-панель\n/ticket — заявка в техподдержку\n\nВсе остальные функции доступны через кнопки в меню.",
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

        self.bot.send_message(
            m.chat.id,
            "🔐 **Админ-панель**",
            reply_markup=self.menu.admin_menu(),
            parse_mode="Markdown"
        )

    def cmd_ticket(self, m: Message):
        self.bot.send_message(
            m.chat.id,
            "🎫 **Техническая поддержка**\n\nОпиши свою проблему в одном сообщении.\nФормат: проблема | контакт (например: @username или почта)",
            parse_mode="Markdown"
        )
        self.db.set_state(m.from_user.id, "waiting_ticket")

    # ============================================
    # ОБРАБОТЧИК КНОПОК
    # ============================================

    def _handle_callback(self, call: CallbackQuery):
        self.bot.answer_callback_query(call.id)
        
        data = call.data
        uid = call.from_user.id
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        # ===== ВЫБОР УРОВНЯ =====
        if data == "level_novice":
            self.db.set_user_level(uid, 'novice')
            self._delete_and_send(chat_id, msg_id, "🟢 **Уровень: Новичок**", self.menu.main_menu('novice'))
            return

        if data == "level_pro":
            self.db.set_user_level(uid, 'pro')
            self._delete_and_send(chat_id, msg_id, "🔴 **Уровень: Продвинутый**", self.menu.main_menu('pro'))
            return

        # ===== СМЕНА УРОВНЯ =====
        if data == "switch_level":
            self.cmd_switch(call.message)
            return

        # ===== ГЛАВНОЕ МЕНЮ =====
        if data == "menu":
            level = self.db.get_user_level(uid)
            self._delete_and_send(chat_id, msg_id, "🏁 **Главное меню**", self.menu.main_menu(level))
            return

        # ===== АДМИНКА =====
        if data.startswith("admin_"):
            if uid not in self.admin_ids:
                self.bot.answer_callback_query(call.id, "Нет доступа")
                return
            self._handle_admin(call)
            return

        # ===== ПИЛОТЫ =====
        if data == "drivers_list":
            self._delete_and_send(chat_id, msg_id, "🏎️ **Выбери пилота**", self.menu.drivers_menu())
            return

        if data == "driver_random":
            code, driver = random.choice(list(DRIVERS.items()))
            self._delete_and_send_driver(chat_id, msg_id, driver)
            return

        if data.startswith("driver_"):
            code = data.replace("driver_", "")
            driver = DRIVERS.get(code)
            if driver:
                self._delete_and_send_driver(chat_id, msg_id, driver)
            else:
                self.bot.answer_callback_query(call.id, "Пилот не найден")
            return

        # ===== КАЛЕНДАРЬ =====
        if data == "schedule_top":
            self._delete_and_send(chat_id, msg_id, "⏳ Загружаю календарь...", None)
            self._show_schedule_and_top(chat_id)
            return

        # ===== INDY 500 =====
        if data == "indy500_menu":
            self._delete_and_send(chat_id, msg_id, "🏆 **Indy 500**\n\nЧто хочешь узнать?", self.menu.indy500_menu())
            return

        if data == "top_winners":
            self._delete_and_send(chat_id, msg_id, "🏆 **10 величайших победителей**\n\n" + self._get_top_winners_text(), self.menu.indy500_menu())
            return

        if data == "winner_prompt":
            self.db.set_state(uid, "waiting_year")
            self._delete_and_send(chat_id, msg_id, "📅 **Введи год** (например, 2023):", self.menu.indy500_menu())
            return

        # ===== НОВОСТИ =====
        if data == "news":
            self._delete_and_send(chat_id, msg_id, "📰 Собираю новости...", None)
            self._show_news(chat_id)
            return

        # ===== НИКО =====
        if data == "ask_nico":
            self.db.set_state(uid, "waiting_nico")
            self._delete_and_send(chat_id, msg_id, "🧠 **Нико**\n\nНапиши свой вопрос про IndyCar:", self.menu.back_to_menu())
            return

        # ===== ГАЙД =====
        if data == "guide_intro":
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
            self._delete_and_send(chat_id, msg_id, text, self.menu.guide_menu())
            return

        if data == "guide_rules":
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
            self._delete_and_send(chat_id, msg_id, text, self.menu.guide_menu())
            return

        if data == "guide_tracks":
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
            self._delete_and_send(chat_id, msg_id, text, self.menu.guide_menu())
            return

        # ===== ДОНАТ =====
        if data == "donate":
            self._delete_and_send(
                chat_id, msg_id,
                "❤️ **Поддержать проект**\n\n💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
                self.menu.back_to_menu(),
                disable_web_page_preview=True
            )
            return

        # ===== О ПРОЕКТЕ =====
        if data == "about":
            self._delete_and_send(
                chat_id, msg_id,
                "📘 **О проекте**\n\nНеофициальный бот для фанатов IndyCar.\nНе связан с IndyCar Series, LLC.\n\n🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n🧑‍💻 @Gabriella1488, @Scanialove",
                self.menu.back_to_menu(),
                disable_web_page_preview=True
            )
            return

        # ===== НЕИЗВЕСТНАЯ КНОПКА =====
        self.bot.answer_callback_query(call.id, "Неизвестная команда")

    # ============================================
    # ВСПОМОГАТЕЛЬНЫЕ МЕТОДЫ
    # ============================================

    def _delete_and_send(self, chat_id: int, msg_id: int, text: str, markup: Optional[IKM] = None, **kwargs):
        try:
            self.bot.delete_message(chat_id, msg_id)
        except:
            pass
        self.bot.send_message(chat_id, text, reply_markup=markup, parse_mode="Markdown", **kwargs)

    def _delete_and_send_driver(self, chat_id: int, msg_id: int, driver: Dict):
        try:
            self.bot.delete_message(chat_id, msg_id)
        except:
            pass
        self._send_driver(chat_id, driver)

    def _send_driver(self, chat_id: int, driver: Dict):
        text = f"🏎️ **{driver['name']}**\n🏁 {driver['team']}\n🔢 #{driver['number']}"
        if driver.get('pos'):
            text += f"\n📊 Позиция: {driver['pos']}"

        markup = self.menu.back_to_menu()

        if driver.get('image'):
            try:
                self.bot.send_photo(chat_id, driver['image'], caption=text, parse_mode="Markdown", reply_markup=markup)
                return
            except Exception as e:
                logger.error(f"Photo send error: {e}")

        self.bot.send_message(chat_id, text, parse_mode="Markdown", reply_markup=markup)

    def _show_schedule_and_top(self, chat_id: int):
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

            self.bot.send_message(
                chat_id,
                "\n".join(lines),
                reply_markup=self.menu.back_to_menu(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Schedule error: {e}")
            self.bot.send_message(
                chat_id,
                "⚠️ Ошибка загрузки календаря",
                reply_markup=self.menu.back_to_menu()
            )

    def _show_news(self, chat_id: int):
        articles = self.news_parser.fetch_sync()
        if not articles:
            self.bot.send_message(
                chat_id,
                "📰 Новостей пока нет",
                reply_markup=self.menu.back_to_menu()
            )
            return

        translated = self.news_parser.translate_news(articles)
        article = translated[0]
        text = f"📰 **{article['title']}**\n\n{article['summary']}...\n\n[Читать]({article['link']})"
        self.bot.send_message(
            chat_id,
            text,
            reply_markup=IKM().add(IKB("➡️ Следующая", callback_data="news_next")),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    def _get_top_winners_text(self) -> str:
        wins = Counter()
        for w in WINNERS:
            if w['year'] >= 1911 and 'не проводилась' not in w['driver']:
                wins[w['driver']] += 1

        top = wins.most_common(10)
        medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']

        text = ""
        for i, (driver, count) in enumerate(top):
            text += f"{medals[i]} {driver} — **{count}** побед\n"
        return text

    # ============================================
    # ОБРАБОТЧИК ТЕКСТА
    # ============================================

    def _handle_text(self, m: Message):
        uid = m.from_user.id
        state, data = self.db.get_state(uid)

        if not state:
            level = self.db.get_user_level(uid)
            self.bot.send_message(
                m.chat.id,
                "Используй кнопки в меню 👇",
                reply_markup=self.menu.main_menu(level)
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
        elif state == "waiting_block":
            self._handle_block_input(m)
            self.db.clear_state(uid)
        else:
            level = self.db.get_user_level(uid)
            self.bot.send_message(
                m.chat.id,
                "Используй кнопки в меню 👇",
                reply_markup=self.menu.main_menu(level)
            )

    def _handle_year_input(self, m: Message):
        try:
            year = int(m.text.strip())
        except:
            self.bot.send_message(m.chat.id, "❌ Введи год цифрами", reply_markup=self.menu.indy500_menu())
            return

        for w in WINNERS:
            if w.get('year') == year:
                self.bot.send_message(
                    m.chat.id,
                    f"🏆 **Indy 500 {year}**\n🏁 {w.get('driver', 'Неизвестно')}",
                    parse_mode="Markdown",
                    reply_markup=self.menu.indy500_menu()
                )
                return

        self.bot.send_message(m.chat.id, f"❌ Нет данных за {year}", reply_markup=self.menu.indy500_menu())

    def _handle_nico_input(self, m: Message):
        thinking_msg = self.bot.send_message(m.chat.id, "🧠 Нико думает...")
        try:
            response = self.ai.ask(m.text)
            safe_response = escape_markdown(response)
            self.bot.edit_message_text(
                f"🧠 **Нико:**\n\n{safe_response}",
                thinking_msg.chat.id,
                thinking_msg.message_id,
                reply_markup=self.menu.back_to_menu(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Nico error: {e}")
            self.bot.edit_message_text(
                f"⚠️ Ошибка: {e}",
                thinking_msg.chat.id,
                thinking_msg.message_id,
                reply_markup=self.menu.back_to_menu()
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
            reply_markup=self.menu.back_to_menu()
        )

        for admin_id in self.admin_ids:
            try:
                self.bot.send_message(
                    admin_id,
                    f"🎫 **Новая заявка #{ticket_id}**\nОт: @{contact}\nID: {m.from_user.id}\nПроблема: {issue[:200]}"
                )
            except:
                pass

    def _handle_broadcast_input(self, m: Message):
        try:
            parts = m.text.split('|', 1)
            target = parts[0].strip().lower()
            text = parts[1].strip()
        except:
            self.bot.send_message(m.chat.id, "❌ Неверный формат. Используй: `all | текст`")
            return

        if target == "all":
            users = self.db.get_all_users()
        elif target == "active":
            users = self.db.get_active_users(7)
        else:
            try:
                users = [int(x.strip()) for x in target.split(',')]
            except:
                self.bot.send_message(m.chat.id, "❌ Неверный формат ID. Используй: `12345,67890,11111 | текст`")
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
            reply_markup=self.menu.back_to_menu()
        )

    def _handle_block_input(self, m: Message):
        """Обработка ввода ID для блокировки/разблокировки"""
        try:
            parts = m.text.split()
            action = parts[0].lower()
            user_id = int(parts[1])
        except:
            self.bot.send_message(
                m.chat.id,
                "❌ Неверный формат. Используй:\n`block 123456789` — заблокировать\n`unblock 123456789` — разблокировать",
                parse_mode="Markdown",
                reply_markup=self.menu.admin_menu()
            )
            return

        if action == "block":
            if block_user(user_id):
                self.bot.send_message(
                    m.chat.id,
                    f"🚫 Пользователь `{user_id}` заблокирован.",
                    parse_mode="Markdown",
                    reply_markup=self.menu.admin_menu()
                )
            else:
                self.bot.send_message(
                    m.chat.id,
                    f"❌ Не удалось заблокировать `{user_id}`. Возможно, он уже заблокирован.",
                    parse_mode="Markdown",
                    reply_markup=self.menu.admin_menu()
                )
        elif action == "unblock":
            if unblock_user(user_id):
                self.bot.send_message(
                    m.chat.id,
                    f"✅ Пользователь `{user_id}` разблокирован.",
                    parse_mode="Markdown",
                    reply_markup=self.menu.admin_menu()
                )
            else:
                self.bot.send_message(
                    m.chat.id,
                    f"❌ Не удалось разблокировать `{user_id}`. Возможно, он не был заблокирован.",
                    parse_mode="Markdown",
                    reply_markup=self.menu.admin_menu()
                )
        else:
            self.bot.send_message(
                m.chat.id,
                f"❌ Неизвестное действие: `{action}`. Используй `block` или `unblock`.",
                parse_mode="Markdown",
                reply_markup=self.menu.admin_menu()
            )

    # ============================================
    # АДМИН-ОБРАБОТЧИК
    # ============================================

    def _handle_admin(self, call: CallbackQuery):
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if call.data == "admin_stats":
            stats = get_global_stats()
            text = f"📊 **Глобальная статистика**\n\n👤 Пользователей: {stats['total_users']}\n📝 Команд: {stats['total_commands']}\n\n**Топ-5 команд:**\n"
            for cmd, count in stats['top_commands'][:5]:
                text += f"• {cmd} — {count}\n"
            self._delete_and_send(chat_id, msg_id, text, self.menu.admin_menu())
            return

        if call.data == "admin_users":
            users = get_all_users()
            blocked = get_blocked_users()
            text = f"👥 **Все пользователи ({len(users)})**\n\nАктивных за 7 дней: {len(get_active_users(7))}\nАктивных за 30 дней: {len(get_active_users(30))}\n🚫 Заблокировано: {len(blocked)}\n\nПоследние 10 ID:\n"
            for uid in users[-10:]:
                is_blocked = "🔒" if is_user_blocked(uid) else "🔓"
                text += f"• {is_blocked} `{uid}`\n"
            self._delete_and_send(chat_id, msg_id, text, self.menu.admin_menu())
            return

        if call.data == "admin_commands":
            commands = self.db.get_command_stats()
            text = "📈 **Статистика команд**\n\n"
            for cmd, count in commands[:10]:
                text += f"• {cmd} — {count} раз\n"
            self._delete_and_send(chat_id, msg_id, text, self.menu.admin_menu())
            return

        if call.data == "admin_tickets":
            tickets = get_open_tickets()
            if not tickets:
                text = "🎫 **Открытых заявок нет**"
            else:
                text = f"🎫 **Открытые заявки ({len(tickets)})**\n\n"
                for ticket in tickets[:10]:
                    text += f"#{ticket[0]} | от @{ticket[3]} | {ticket[4][:16]}\n  {ticket[2][:80]}...\n\n"
            self._delete_and_send(chat_id, msg_id, text, self.menu.admin_menu())
            return

        if call.data == "admin_broadcast":
            self.db.set_state(call.from_user.id, "waiting_broadcast")
            self._delete_and_send(
                chat_id, msg_id,
                "📨 **Рассылка**\n\nВведите текст рассылки.\nОпции:\n• `all` — всем пользователям\n• `active` — активным за 7 дней\n• `ID,ID` — конкретным пользователям\n\nПример: `all | Привет!`",
                self.menu.admin_menu()
            )
            return

        if call.data == "admin_update_db":
            self.bot.edit_message_text("🔄 Обновляю базу знаний...", chat_id, msg_id)
            try:
                asyncio.run(update_knowledge_base())
                self.bot.edit_message_text("✅ База знаний обновлена!", chat_id, msg_id, reply_markup=self.menu.admin_menu())
            except Exception as e:
                self.bot.edit_message_text(f"❌ Ошибка: {e}", chat_id, msg_id, reply_markup=self.menu.admin_menu())
            return

        if call.data == "admin_block":
            self.db.set_state(call.from_user.id, "waiting_block")
            self._delete_and_send(
                chat_id, msg_id,
                "🚫 **Блокировка пользователя**\n\nВведите команду:\n`block 123456789` — заблокировать\n`unblock 123456789` — разблокировать\n\nСписок заблокированных:\n" + "\n".join([f"• `{u[0]}` — {u[1][:16]}" for u in get_blocked_users()[:10]]) or "Нет заблокированных",
                self.menu.admin_menu(),
                parse_mode="Markdown"
            )
            return


# ============================================
# FASTAPI — ВЕБХУК
# ============================================

indy_bot = IndyBot(TOKEN)
bot = indy_bot.bot
db = indy_bot.db

app = FastAPI(title="INDY Leader", version="2.5.0")

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
            "last_error": info.last_error_message,
            "version": "2.5.0",
            "ai_model": "gemini-3.6-flash"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 503

@app.get("/")
async def root():
    return {"status": "INDY Leader is running", "webhook_url": WEBHOOK_URL + WEBHOOK_PATH, "version": "2.5.0"}

def set_webhook():
    try:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(url=WEBHOOK_URL + WEBHOOK_PATH, max_connections=40, drop_pending_updates=True)
        logger.info(f"✅ Webhook: {WEBHOOK_URL + WEBHOOK_PATH}")
    except Exception as e:
        logger.error(f"❌ Webhook error: {e}")

# ============================================
# ЗАПУСК
# ============================================

if __name__ == "__main__":
    import uvicorn
    set_webhook()
    logger.info(f"🚀 INDY Leader v2.5.0 запущен на порту {PORT}")
    logger.info(f"🧠 Модель: gemini-3.6-flash")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
