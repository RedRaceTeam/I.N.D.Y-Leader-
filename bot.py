#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I.N.D.Y Leader v2.6.1-fix — адаптивный гид по IndyCar (PRODUCTION-READY)
Автор: P4/9 · Gabriella Projects
Архитектура: FastAPI + Webhook + ООП
Модель: Gemini 3.6 Flash (google-genai)

FIXES v2.6.1-fix:
- Исправлена гонка состояний в _handle_text (состояние не перезаписывается)
- Добавлена пагинация новостей (кнопка "Следующая" работает)
- Новости теперь асинхронные (не блокируют event loop)
- Поток очистки состояний проверяет актуальность
- Исправлено экранирование markdown (ссылки не ломаются)
- requests заменён на aiohttp в админке
- Добавлена обработка ошибок KeyError в WINNERS
- Меню в 3 колонки
- Каналы P4/9 и Gabriella Projects внутри "О проекте"
"""

import os
import sys
import logging
import sqlite3
import asyncio
import aiohttp
import feedparser
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
    InlineKeyboardButton as IKB
)

# ===== GEMINI SDK =====
from google import genai
from google.genai import types

# ===== ПЕРЕВОДЧИК (deep-translator) =====
from deep_translator import GoogleTranslator

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
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8000))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")  # опционально

if not TOKEN:
    logger.error("❌ BOT_TOKEN не задан")
    sys.exit(1)

if not GEMINI_API_KEY:
    logger.warning("⚠️ GEMINI_API_KEY не задан — Нико не будет работать")

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
if not ADMIN_IDS:
    logger.warning("⚠️ ADMIN_IDS не задан — админ-панель недоступна")

logger.info(f"✅ Админы: {ADMIN_IDS}")

# ============================================
# БАЗА ДАННЫХ
# ============================================

class Database:
    def __init__(self, path: str = 'indyleader.db'):
        self.path = path
        self._init()

    def _init(self):
        with sqlite3.connect(self.path, check_same_thread=False) as conn:
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

    def add_user(self, uid: int, username: str, first_name: str):
        with sqlite3.connect(self.path) as conn:
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

    def log_command(self, uid: int, cmd: str):
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('INSERT INTO stats (command, user_id, timestamp) VALUES (?, ?, ?)',
                      (cmd, uid, datetime.now().isoformat()))
            conn.commit()

    def get_user_level(self, uid: int) -> str:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT level FROM users WHERE user_id = ?', (uid,))
            row = c.fetchone()
            return row[0] if row else 'novice'

    def set_user_level(self, uid: int, level: str):
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, uid))
            conn.commit()

    def get_all_users(self) -> list:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT user_id FROM users')
            return [row[0] for row in c.fetchall()]

    def get_active_users(self, days: int = 7) -> list:
        cutoff = (datetime.now() - timezone.timedelta(days=days)).isoformat()
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('''
                SELECT DISTINCT user_id FROM stats
                WHERE timestamp > ?
                GROUP BY user_id
                HAVING COUNT(*) > 1
            ''', (cutoff,))
            return [row[0] for row in c.fetchall()]

    def get_stats(self) -> Dict:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM users')
            users = c.fetchone()[0]
            c.execute('SELECT COUNT(*) FROM stats')
            commands = c.fetchone()[0]
            return {'users': users, 'commands': commands}

    def get_command_stats(self) -> list:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT command, COUNT(*) FROM stats GROUP BY command ORDER BY COUNT(*) DESC')
            return c.fetchall()

    def get_users_list(self) -> list:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT user_id, username, first_name, level, total_commands, last_seen FROM users ORDER BY last_seen DESC')
            return c.fetchall()

    def set_state(self, uid: int, state: str, data: str = None):
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT OR REPLACE INTO user_states (user_id, state, data, updated_at)
                VALUES (?, ?, ?, ?)
            ''', (uid, state, data, datetime.now().isoformat()))
            conn.commit()

    def get_state(self, uid: int) -> tuple:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('SELECT state, data FROM user_states WHERE user_id = ?', (uid,))
            return c.fetchone() or (None, None)

    def clear_state(self, uid: int):
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('DELETE FROM user_states WHERE user_id = ?', (uid,))
            conn.commit()

    def create_ticket(self, uid: int, issue: str, contact: str) -> int:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('''
                INSERT INTO tickets (user_id, issue, contact, created_at)
                VALUES (?, ?, ?, ?)
            ''', (uid, issue, contact, datetime.now().isoformat()))
            conn.commit()
            return c.lastrowid

    def get_open_tickets(self) -> list:
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('''
                SELECT id, user_id, issue, contact, created_at
                FROM tickets WHERE status = 'open'
                ORDER BY created_at DESC
            ''')
            return c.fetchall()

    def close_ticket(self, ticket_id: int):
        with sqlite3.connect(self.path) as conn:
            c = conn.cursor()
            c.execute('UPDATE tickets SET status = "closed" WHERE id = ?', (ticket_id,))
            conn.commit()


# ============================================
# ПЕРЕВОДЧИК (deep-translator)
# ============================================

class Translator:
    def __init__(self):
        self.translator = GoogleTranslator(source='auto', target='ru')
        logger.info("✅ Переводчик Google (deep-translator) инициализирован")

    def translate(self, text: str) -> str:
        if not text:
            return text
        try:
            return self.translator.translate(text)
        except Exception as e:
            logger.error(f"Translation error: {e}")
            return text


# ============================================
# НИКО (GEMINI 3.6 FLASH)
# ============================================

class NicoAI:
    def __init__(self):
        self.api_key = GEMINI_API_KEY
        logger.info("✅ Gemini 3.6 Flash готов к использованию")

    def ask(self, question: str) -> str:
        if not self.api_key:
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
            with genai.Client(api_key=self.api_key) as client:
                response = client.models.generate_content(
                    model="gemini-3.6-flash",
                    contents=question,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.7,
                        max_output_tokens=1000,
                    )
                )
                if hasattr(response, 'text') and response.text:
                    return response.text
                return "⚠️ Gemini вернул пустой ответ"
        except Exception as e:
            logger.error(f"Gemini API ошибка: {e}")
            return f"⚠️ Ошибка Gemini: {str(e)}"


# ============================================
# ПОСТРОЕНИЕ МЕНЮ (3 КОЛОНКИ)
# ============================================

class MenuBuilder:
    @staticmethod
    def main_menu(level: str) -> IKM:
        if level == 'novice':
            return MenuBuilder._menu_novice()
        return MenuBuilder._menu_pro()

    @staticmethod
    def _menu_novice() -> IKM:
        markup = IKM(row_width=3)
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
        for i in range(0, len(buttons), 3):
            row_buttons = buttons[i:i+3]
            markup.add(*[IKB(text, callback_data=callback) for text, callback in row_buttons])
        return markup

    @staticmethod
    def _menu_pro() -> IKM:
        markup = IKM(row_width=3)
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
        for i in range(0, len(buttons), 3):
            row_buttons = buttons[i:i+3]
            markup.add(*[IKB(text, callback_data=callback) for text, callback in row_buttons])
        return markup

    @staticmethod
    def drivers_menu() -> IKM:
        markup = IKM(row_width=3)
        teams = {}
        for code, d in DRIVERS.items():
            teams.setdefault(d['team'], []).append((code, d))

        for team, drivers in sorted(teams.items())[:8]:
            markup.add(IKB(f"━━ {team} ━━", callback_data="noop"))
            row = []
            for code, d in drivers[:4]:
                surname = d['name'].split()[-1]
                row.append(IKB(f"{surname} #{d['number']}", callback_data=f"driver_{code}"))
                if len(row) == 3:
                    markup.add(*row)
                    row = []
            if row:
                markup.add(*row)

        markup.add(IKB("🔙 Назад", callback_data="menu"))
        return markup

    @staticmethod
    def indy500_menu() -> IKM:
        markup = IKM(row_width=3)
        markup.add(
            IKB("📅 По году", callback_data="winner_prompt"),
            IKB("🏆 Топ-10 победителей", callback_data="top_winners")
        )
        markup.add(IKB("🔙 Назад", callback_data="menu"))
        return markup

    @staticmethod
    def admin_menu() -> IKM:
        markup = IKM(row_width=3)
        buttons = [
            ("📊 Статистика", "admin_stats"),
            ("👥 Пользователи", "admin_users"),
            ("📈 Команды", "admin_commands"),
            ("🎫 Заявки", "admin_tickets"),
            ("📨 Рассылка", "admin_broadcast"),
            ("🔄 Обновить базу", "admin_update_db"),
            ("🔙 Выйти", "menu"),
        ]
        for i in range(0, len(buttons), 3):
            row_buttons = buttons[i:i+3]
            markup.add(*[IKB(text, callback_data=callback) for text, callback in row_buttons])
        return markup

    @staticmethod
    def guide_menu() -> IKM:
        markup = IKM(row_width=3)
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
        'autosport': 'https://www.autosport.com/indycar/rss/',
        'racer': 'https://racer.com/indycar/feed/',
        'indycar_official': 'https://www.indycar.com/~/api/rss/News',
        'f1_technical': 'https://www.f1technical.net/forum/app.php/feed/news',
        'reddit': 'https://www.reddit.com/r/INDYCAR/.rss',
    }

    def __init__(self):
        self.translator = Translator()

    async def _fetch_rss_content(self, url: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as resp:
                return await resp.text()

    async def _fetch_newsapi(self) -> list:
        if not NEWS_API_KEY:
            return []
        try:
            url = f"https://newsapi.org/v2/everything?q=indycar&apiKey={NEWS_API_KEY}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    return [{
                        'title': a['title'],
                        'summary': a['description'][:300] if a.get('description') else '',
                        'link': a['url'],
                        'source': 'newsapi'
                    } for a in data.get('articles', [])[:5]]
        except Exception as e:
            logger.warning(f"NewsAPI ошибка: {e}")
            return []

    async def fetch_all(self) -> list:
        all_news = []
        
        for name, url in self.SOURCES.items():
            if name in ['espn', 'newsapi']:
                continue
            try:
                content = await self._fetch_rss_content(url)
                feed = feedparser.parse(content)
                for entry in feed.entries[:2]:
                    all_news.append({
                        'title': entry.get('title', ''),
                        'summary': entry.get('summary', '')[:300],
                        'link': entry.get('link', '#'),
                        'source': name
                    })
            except Exception as e:
                logger.warning(f"RSS ошибка {name}: {e}")

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
        except Exception as e:
            logger.warning(f"ESPN ошибка: {e}")

        newsapi_news = await self._fetch_newsapi()
        all_news.extend(newsapi_news)

        return all_news

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
            except Exception as e:
                logger.warning(f"Translation failed for {article.get('source', 'unknown')}: {e}")
                translated.append(article)
        return translated


# ============================================
# ТОП-5 (АСИНХРОННЫЙ)
# ============================================

class StandingsFetcher:
    @staticmethod
    async def fetch() -> list:
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()
                    top5 = []
                    for entry in data.get('standings', [{}])[0].get('entries', [])[:5]:
                        top5.append({
                            'name': entry.get('athlete', {}).get('displayName', 'Неизвестно'),
                            'points': entry.get('points', 0)
                        })
                    return top5
        except Exception as e:
            logger.warning(f"Standings error: {e}")
            return []


# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================

def escape_markdown(text: str) -> str:
    """Экранирование для Telegram Markdown (без ломания ссылок)"""
    if not text:
        return text
    escape_chars = r'_*~`>#+-=|{}.!'
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

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
        self.news_cache = {}  # Кеш для пагинации новостей
        self.state_timers = {}  # Таймеры для очистки состояний

        self._register_handlers()
        logger.info("✅ INDY Leader v2.6.1-fix готов к работе!")

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
        if not self.admin_ids:
            self.bot.reply_to(m, "⛔ Админ-панель отключена")
            return
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

        # ===== НОВОСТИ (ПАГИНАЦИЯ) =====
        if data.startswith("news_"):
            page = int(data.replace("news_", ""))
            self._show_news(chat_id, page)
            return

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
            if not self.admin_ids:
                self.bot.answer_callback_query(call.id, "Админ-панель отключена")
                return
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
            asyncio.create_task(self._show_schedule_and_top(chat_id))
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
            asyncio.create_task(self._show_news_async(chat_id))
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

        # ===== О ПРОЕКТЕ (С КАНАЛАМИ) =====
        if data == "about":
            text = (
                "📘 **О проекте**\n\n"
                "Неофициальный бот для фанатов IndyCar.\n"
                "Не связан с IndyCar Series, LLC.\n\n"
                "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                "🧑‍💻 @Gabriella1488, @Scanialove\n\n"
                "📢 **Наши каналы:**\n"
                "• [P4/9 Dev](https://t.me/P4Devl) — канал команды разработчиков\n"
                "• [Gabriella Projects](https://t.me/GabriellaProjekts) — проекты и разработки"
            )
            self._delete_and_send(
                chat_id, msg_id,
                text,
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

    async def _show_schedule_and_top(self, chat_id: int):
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard?seasontype=2&level=3"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=10) as resp:
                    data = await resp.json()

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

            top5 = await StandingsFetcher.fetch()
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

    def _show_news(self, chat_id: int, page: int = 0):
        """Отображение новости с пагинацией"""
        articles = self.news_cache.get(chat_id, [])
        if not articles:
            self.bot.send_message(
                chat_id,
                "📰 Новости не загружены. Нажмите кнопку еще раз.",
                reply_markup=self.menu.back_to_menu()
            )
            return

        if page >= len(articles):
            self.bot.send_message(
                chat_id,
                "📰 Новости закончились",
                reply_markup=self.menu.back_to_menu()
            )
            return

        article = articles[page]
        text = f"📰 **{article['title']}**\n\n{article['summary']}...\n\n[Читать]({article['link']})"
        
        markup = IKM(row_width=2)
        if page < len(articles) - 1:
            markup.add(IKB("➡️ Следующая", callback_data=f"news_{page+1}"))
        markup.add(IKB("🔙 Назад", callback_data="menu"))
        
        self.bot.send_message(
            chat_id,
            text,
            reply_markup=markup,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )

    async def _show_news_async(self, chat_id: int):
        """Асинхронная загрузка новостей"""
        articles = await self.news_parser.fetch_all()
        if not articles:
            self.bot.send_message(
                chat_id,
                "📰 Новостей пока нет",
                reply_markup=self.menu.back_to_menu()
            )
            return

        translated = self.news_parser.translate_news(articles)
        self.news_cache[chat_id] = translated
        self._show_news(chat_id, 0)

    def _get_top_winners_text(self) -> str:
        wins = Counter()
        for w in WINNERS:
            driver = w.get('driver', '')
            if w.get('year', 0) >= 1911 and 'не проводилась' not in driver:
                wins[driver] += 1

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

        # Обработка без перезаписи состояния
        if state == "waiting_year":
            self._handle_year_input(m)
        elif state == "waiting_nico":
            self._handle_nico_input(m)
        elif state == "waiting_ticket":
            self._handle_ticket_input(m)
        elif state == "waiting_broadcast":
            self._handle_broadcast_input(m)
        else:
            level = self.db.get_user_level(uid)
            self.bot.send_message(
                m.chat.id,
                "Используй кнопки в меню 👇",
                reply_markup=self.menu.main_menu(level)
            )
            return

        # Очищаем состояние через 30 секунд с проверкой
        def clear_after_timeout():
            time.sleep(30)
            current_state, _ = self.db.get_state(uid)
            if current_state == state:
                self.db.clear_state(uid)
        threading.Thread(target=clear_after_timeout, daemon=True).start()

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

    # ============================================
    # АДМИН-ОБРАБОТЧИК
    # ============================================

    def _handle_admin(self, call: CallbackQuery):
        chat_id = call.message.chat.id
        msg_id = call.message.message_id

        if call.data == "admin_stats":
            stats = self.db.get_stats()
            commands = self.db.get_command_stats()
            text = f"📊 **Глобальная статистика**\n\n👤 Пользователей: {stats['users']}\n📝 Команд: {stats['commands']}\n\n**Топ-5 команд:**\n"
            for cmd, count in commands[:5]:
                text += f"• {cmd} — {count}\n"
            self._delete_and_send(chat_id, msg_id, text, self.menu.admin_menu())
            return

        if call.data == "admin_users":
            users = self.db.get_all_users()
            text = f"👥 **Все пользователи ({len(users)})**\n\nАктивных за 7 дней: {len(self.db.get_active_users(7))}\nАктивных за 30 дней: {len(self.db.get_active_users(30))}\n\nПоследние 10 ID:\n"
            for uid in users[-10:]:
                text += f"• `{uid}`\n"
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
            tickets = self.db.get_open_tickets()
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
                async def update_knowledge():
                    async with aiohttp.ClientSession() as session:
                        async with session.get("https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings", timeout=10) as resp:
                            data = await resp.json()
                            standings = []
                            for entry in data.get('standings', [{}])[0].get('entries', [])[:10]:
                                standings.append({
                                    'name': entry.get('athlete', {}).get('displayName', 'Unknown'),
                                    'points': entry.get('points', 0),
                                    'team': entry.get('team', {}).get('displayName', '')
                                })
                            return standings

                standings = asyncio.run(update_knowledge())
                os.makedirs("data", exist_ok=True)
                with open("data/knowledge.txt", "w", encoding="utf-8") as f:
                    f.write(f"# База знаний INDY Leader\n# Обновлено: {datetime.now().isoformat()}\n\n## ТУРНИРНАЯ ТАБЛИЦА (ТОП-10)\n")
                    for i, driver in enumerate(standings, 1):
                        f.write(f"{i}. {driver['name']} — {driver['points']} очков ({driver['team']})\n")

                self.bot.edit_message_text("✅ База знаний обновлена!", chat_id, msg_id, reply_markup=self.menu.admin_menu())
            except Exception as e:
                self.bot.edit_message_text(f"❌ Ошибка: {e}", chat_id, msg_id, reply_markup=self.menu.admin_menu())
            return


# ============================================
# FASTAPI — ВЕБХУК
# ============================================

indy_bot = IndyBot(TOKEN)
bot = indy_bot.bot
db = indy_bot.db

app = FastAPI(title="INDY Leader", version="2.6.1-fix")

@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != WEBHOOK_SECRET:
            logger.warning("Неверный секретный токен")
            return Response(content="Unauthorized", status_code=403)

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
            "version": "2.6.1-fix",
            "ai_model": "gemini-3.6-flash"
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}, 503

@app.get("/")
async def root():
    return {"status": "INDY Leader is running", "webhook_url": WEBHOOK_URL + WEBHOOK_PATH, "version": "2.6.1-fix"}

def set_webhook():
    try:
        bot.remove_webhook()
        time.sleep(0.5)
        bot.set_webhook(
            url=WEBHOOK_URL + WEBHOOK_PATH,
            max_connections=40,
            drop_pending_updates=True,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"]
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
    logger.info(f"🚀 INDY Leader v2.6.1-fix запущен на порту {PORT}")
    logger.info(f"🧠 Модель: gemini-3.6-flash")
    uvicorn.run(app, host="0.0.0.0", port=PORT)
