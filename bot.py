#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
I.N.D.Y Leader v3.0.0-beta "Helio" — асинхронный гид по IndyCar (BETA, НЕ ПУБЛИЧНО)
Автор: P4/9 · Gabriella Projects
Архитектура: FastAPI + Webhook + aiogram 3.x (полностью async)

Что поменялось относительно монолита на pyTelegramBotAPI (v2.6.1-fix "Astra"):

1. AIOGRAM ВМЕСТО TELEBOT — весь бот теперь честно асинхронный. Раньше
   `TeleBot(threaded=False)` внутри `asyncio.create_task(...)` на деле блокировал
   event loop FastAPI на каждый синхронный вызов (send_message, sqlite3.connect
   и т.д.) — параллелизм из asyncio.create_task был иллюзией. aiogram изначально
   строится вокруг asyncio, так что этой проблемы больше нет.

2. AIOSQLITE ВМЕСТО sqlite3 — все запросы к БД теперь `await`-ятся, не блокируют
   event loop. Заодно нашёл и починил баг в оригинале: `get_active_users()`
   дергал `timezone.timedelta(...)`, а `timedelta` — не атрибут `timezone`,
   это отдельный класс в модуле `datetime`. В оригинале это уронило бы функцию
   при первом вызове. Здесь — `from datetime import timedelta` и всё ок.

3. FSM — вместо своей таблицы `user_states` + фонового потока с `time.sleep(30)`
   для очистки состояния используется штатный FSM aiogram. Чтобы не потерять
   плюс оригинала (состояние переживает рестарт бота), ниже — свой
   `SQLiteStorage(BaseStorage)` на aiosqlite вместо `MemoryStorage` из коробки.
   Ручной сброс состояния через 30 секунд убрал — он был нужен как раз из-за
   отсутствия нормального FSM; теперь состояние сбрасывается явно при переходе
   в меню, это надёжнее таймера в отдельном потоке.

4. РАССЫЛКА — `time.sleep(0.05)` в цикле broadcast заменён на `asyncio.sleep`,
   так что рассылка больше не замораживает бота для всех остальных на время
   отправки сотен сообщений.

5. НОВОЕ: БЛОГЕРСКИЙ РАЗДЕЛ (см. класс BloggerAI и хендлеры blogger_*) —
   кабинет для доверенных партнёров: доступ по Telegram ID (таблица `bloggers`),
   собственный контент-ассистент, который тянет свежие новости через
   существующий NewsParser, переводит через Translator и оформляет черновик
   поста для ИХ канала через Gemini — с промптом, который решает проблему
   "Gemini пишет с markdown и слишком коротко", которую ловили в проде:
   жёсткий запрет markdown + few-shot пример нужного объёма/стиля +
   regex-страховка (strip_markdown) на случай, если модель всё равно
   проскочит с ** или #. Дневной лимит генераций на блогера, лог черновиков
   в БД (blogger_posts).

ЧТО СОЗНАТЕЛЬНО НЕ ПЕРЕНЕСЕНО ИЗ ОРИГИНАЛА (чтобы не раздувать файл ещё
сильнее) — раздел админки "🔄 Обновить базу" (admin_update_db), который
дёргал ESPN standings и писал data/knowledge.txt. Логику несложно вернуть
по образцу show_schedule_and_top() ниже, просто не стал дублировать ради
экономии места в файле.

НУЖНЫЕ ЗАВИСИМОСТИ (добавить/заменить в requirements.txt):
    aiogram>=3.7.0
    aiosqlite
    fastapi
    uvicorn
    aiohttp
    feedparser
    deep-translator
    google-genai

БД для беты отдельная от прода (DB_PATH="indyleader_beta.db" по умолчанию) —
чтобы бета не портила прод-данные, пока обкатываешь на себе и друге.
"""

import os
import sys
import json
import logging
import asyncio
import random
import re
from contextlib import asynccontextmanager
from collections import Counter
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import aiosqlite
import feedparser
from fastapi import FastAPI, Request, Response

from aiogram import Bot, Dispatcher, Router, F
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup, StateType
from aiogram.fsm.storage.base import BaseStorage, StorageKey
from aiogram.types import (
    Message, CallbackQuery, Update,
    InlineKeyboardMarkup, InlineKeyboardButton,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

# ===== GEMINI SDK =====
from google import genai
from google.genai import types

# ===== ПЕРЕВОДЧИК (deep-translator) =====
from deep_translator import GoogleTranslator

# ============================================
# ИМПОРТ ДАННЫХ (те же файлы, что и в проде)
# ============================================

from data.drivers import DRIVERS
from data.winners import WINNERS

# ============================================
# ЛОГИРОВАНИЕ
# ============================================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# ============================================
# КОНФИГ
# ============================================

TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "http://localhost:8000")
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET")
WEBHOOK_PATH = "/webhook"
PORT = int(os.getenv("PORT", 8000))
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
NEWS_API_KEY = os.getenv("NEWS_API_KEY")
DB_PATH = os.getenv("DB_PATH", "indyleader_beta.db")
BLOGGER_DAILY_LIMIT = int(os.getenv("BLOGGER_DAILY_LIMIT", "5"))

if not TOKEN:
    logger.error("❌ BOT_TOKEN не задан")
    sys.exit(1)

if not GEMINI_API_KEY:
    logger.warning("⚠️ GEMINI_API_KEY не задан — Нико и блогерка не будут работать")

ADMIN_IDS = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
if not ADMIN_IDS:
    logger.warning("⚠️ ADMIN_IDS не задан — админ-панель недоступна")

logger.info(f"✅ Админы: {ADMIN_IDS}")
logger.info(f"🧪 BETA build — Helio v3.0.0-beta, БД: {DB_PATH}")


# ============================================
# БАЗА ДАННЫХ (aiosqlite, полностью async)
# ============================================

class Database:
    def __init__(self, path: str = DB_PATH):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT,
                    first_seen TEXT,
                    last_seen TEXT,
                    total_commands INTEGER DEFAULT 0,
                    level TEXT DEFAULT 'novice'
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    command TEXT,
                    user_id INTEGER,
                    timestamp TEXT
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS tickets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    issue TEXT,
                    contact TEXT,
                    status TEXT DEFAULT 'open',
                    created_at TEXT
                )
            ''')
            # ===== НОВОЕ: блогерский раздел =====
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS bloggers (
                    user_id INTEGER PRIMARY KEY,
                    channel_name TEXT,
                    channel_link TEXT,
                    added_at TEXT
                )
            ''')
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS blogger_posts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    blogger_id INTEGER,
                    topic TEXT,
                    content TEXT,
                    created_at TEXT
                )
            ''')
            await conn.execute('PRAGMA journal_mode=WAL')
            await conn.commit()

    # ---------- пользователи ----------

    async def add_user(self, uid: int, username: str, first_name: str):
        now = datetime.now().isoformat()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT user_id FROM users WHERE user_id = ?', (uid,))
            exists = await cur.fetchone()
            if exists:
                await conn.execute('''
                    UPDATE users SET username=?, first_name=?, last_seen=?, total_commands=total_commands+1
                    WHERE user_id=?
                ''', (username, first_name, now, uid))
            else:
                await conn.execute('''
                    INSERT INTO users (user_id, username, first_name, first_seen, last_seen, total_commands)
                    VALUES (?, ?, ?, ?, ?, 1)
                ''', (uid, username, first_name, now, now))
            await conn.commit()

    async def log_command(self, uid: int, cmd: str):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute(
                'INSERT INTO stats (command, user_id, timestamp) VALUES (?, ?, ?)',
                (cmd, uid, datetime.now().isoformat())
            )
            await conn.commit()

    async def get_user_level(self, uid: int) -> str:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT level FROM users WHERE user_id = ?', (uid,))
            row = await cur.fetchone()
            return row[0] if row else 'novice'

    async def set_user_level(self, uid: int, level: str):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('UPDATE users SET level = ? WHERE user_id = ?', (level, uid))
            await conn.commit()

    async def get_all_users(self) -> List[int]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT user_id FROM users')
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def get_active_users(self, days: int = 7) -> List[int]:
        # ФИКС бага из оригинала: timezone.timedelta не существует,
        # timedelta — отдельный класс в datetime, а не атрибут timezone.
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('''
                SELECT DISTINCT user_id FROM stats
                WHERE timestamp > ?
                GROUP BY user_id
                HAVING COUNT(*) > 1
            ''', (cutoff,))
            rows = await cur.fetchall()
            return [r[0] for r in rows]

    async def get_stats(self) -> Dict[str, int]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT COUNT(*) FROM users')
            users = (await cur.fetchone())[0]
            cur = await conn.execute('SELECT COUNT(*) FROM stats')
            commands = (await cur.fetchone())[0]
            return {'users': users, 'commands': commands}

    async def get_command_stats(self) -> List[Tuple[str, int]]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                'SELECT command, COUNT(*) FROM stats GROUP BY command ORDER BY COUNT(*) DESC'
            )
            return await cur.fetchall()

    # ---------- тикеты ----------

    async def create_ticket(self, uid: int, issue: str, contact: str) -> int:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('''
                INSERT INTO tickets (user_id, issue, contact, created_at)
                VALUES (?, ?, ?, ?)
            ''', (uid, issue, contact, datetime.now().isoformat()))
            await conn.commit()
            return cur.lastrowid

    async def get_open_tickets(self) -> List[tuple]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('''
                SELECT id, user_id, issue, contact, created_at
                FROM tickets WHERE status = 'open'
                ORDER BY created_at DESC
            ''')
            return await cur.fetchall()

    # ---------- блогеры ----------

    async def is_blogger(self, uid: int) -> bool:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT 1 FROM bloggers WHERE user_id = ?', (uid,))
            return (await cur.fetchone()) is not None

    async def add_blogger(self, uid: int, channel_name: str, channel_link: str):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                INSERT OR REPLACE INTO bloggers (user_id, channel_name, channel_link, added_at)
                VALUES (?, ?, ?, ?)
            ''', (uid, channel_name, channel_link, datetime.now().isoformat()))
            await conn.commit()

    async def get_blogger(self, uid: int) -> Optional[Dict[str, str]]:
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                'SELECT channel_name, channel_link FROM bloggers WHERE user_id = ?', (uid,)
            )
            row = await cur.fetchone()
            if not row:
                return None
            return {'channel_name': row[0], 'channel_link': row[1]}

    async def log_blogger_post(self, uid: int, topic: str, content: str):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                INSERT INTO blogger_posts (blogger_id, topic, content, created_at)
                VALUES (?, ?, ?, ?)
            ''', (uid, topic, content, datetime.now().isoformat()))
            await conn.commit()

    async def get_blogger_usage_today(self, uid: int) -> int:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).isoformat()
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute(
                'SELECT COUNT(*) FROM blogger_posts WHERE blogger_id = ? AND created_at >= ?',
                (uid, today_start)
            )
            row = await cur.fetchone()
            return row[0] if row else 0


# ============================================
# FSM-ХРАНИЛИЩЕ НА AIOSQLITE
# Состояние (waiting_year, waiting_nico и т.д.) переживает рестарт бота —
# это то, за что хвалили оригинал; MemoryStorage из коробки это бы потерял.
# ============================================

class SQLiteStorage(BaseStorage):
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS fsm_storage (
                    storage_key TEXT PRIMARY KEY,
                    state TEXT,
                    data TEXT
                )
            ''')
            await conn.commit()

    @staticmethod
    def _key(key: StorageKey) -> str:
        return f"{key.bot_id}:{key.chat_id}:{key.user_id}:{key.destiny}"

    async def set_state(self, key: StorageKey, state: StateType = None) -> None:
        value = state.state if isinstance(state, State) else state
        sk = self._key(key)
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                INSERT INTO fsm_storage (storage_key, state, data)
                VALUES (?, ?, '{}')
                ON CONFLICT(storage_key) DO UPDATE SET state = excluded.state
            ''', (sk, value))
            await conn.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        sk = self._key(key)
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT state FROM fsm_storage WHERE storage_key = ?', (sk,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Dict[str, Any]) -> None:
        sk = self._key(key)
        payload = json.dumps(data)
        async with aiosqlite.connect(self.path) as conn:
            await conn.execute('''
                INSERT INTO fsm_storage (storage_key, state, data)
                VALUES (?, NULL, ?)
                ON CONFLICT(storage_key) DO UPDATE SET data = excluded.data
            ''', (sk, payload))
            await conn.commit()

    async def get_data(self, key: StorageKey) -> Dict[str, Any]:
        sk = self._key(key)
        async with aiosqlite.connect(self.path) as conn:
            cur = await conn.execute('SELECT data FROM fsm_storage WHERE storage_key = ?', (sk,))
            row = await cur.fetchone()
            return json.loads(row[0]) if row and row[0] else {}

    async def close(self) -> None:
        pass


class Flow(StatesGroup):
    waiting_year = State()
    waiting_nico = State()
    waiting_ticket = State()
    waiting_broadcast = State()
    waiting_blogger_topic = State()
    # пошаговое добавление блогера кнопкой, без ручного /addblogger
    waiting_addblogger_id = State()
    waiting_addblogger_name = State()
    waiting_addblogger_link = State()


# ============================================
# ПЕРЕВОДЧИК (deep-translator, обёрнут в to_thread)
# ============================================

class Translator:
    def __init__(self):
        self._translator = GoogleTranslator(source='auto', target='ru')
        logger.info("✅ Переводчик Google (deep-translator) инициализирован")

    async def translate(self, text: str) -> str:
        if not text:
            return text
        try:
            return await asyncio.to_thread(self._translator.translate, text)
        except Exception as e:
            logger.warning(f"Translation error: {e}")
            return text

    async def translate_news(self, articles: List[dict]) -> List[dict]:
        async def _one(article: dict) -> dict:
            try:
                return {
                    'title': await self.translate(article['title']),
                    'summary': await self.translate(article['summary']),
                    'link': article['link'],
                    'source': article['source'],
                }
            except Exception as e:
                logger.warning(f"Translation failed for {article.get('source', '?')}: {e}")
                return article
        # переводим статьи параллельно, а не одну за другой, как в оригинале
        return list(await asyncio.gather(*(_one(a) for a in articles)))


# ============================================
# НИКО (GEMINI, обёрнут в to_thread — SDK синхронный)
# ============================================

NICO_SYSTEM_PROMPT = """
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


def _extract_gemini_text(response) -> str:
    """Достаёт текст из ответа Gemini и по-человечески объясняет, если текста нет.

    БАГ, который чинит эта функция: "thinking"-модели (Gemini 2.5+/3.x, в т.ч.
    gemini-3.6-flash) тратят часть max_output_tokens на внутренние рассуждения
    ДО того, как начнут писать видимый текст. Если бюджет токенов мал (или
    thinking не отключён явно), модель может упереться в лимит ещё на стадии
    "думания" — тогда response.text == "" при finish_reason == MAX_TOKENS,
    хотя запрос отработал без ошибок. Раньше это тихо превращалось в "пустой
    ответ" без объяснения причины — теперь причина видна в логах и в сообщении
    пользователю."""
    if response.text:
        return response.text

    try:
        reason = response.candidates[0].finish_reason
    except (IndexError, AttributeError):
        reason = None
    reason_name = getattr(reason, "name", str(reason))

    if reason_name == "MAX_TOKENS":
        raise RuntimeError(
            "модель упёрлась в лимит токенов на стадии 'размышлений', "
            "не успев написать текст — увеличь max_output_tokens"
        )
    if reason_name in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION"):
        raise RuntimeError(f"ответ заблокирован фильтрами безопасности ({reason_name})")
    raise RuntimeError(f"пустой ответ, finish_reason={reason_name}")


class NicoAI:
    def __init__(self, api_key: Optional[str]):
        self.api_key = api_key
        logger.info("✅ Нико (Gemini) готов к использованию")

    async def ask(self, question: str) -> str:
        if not self.api_key:
            return "⚠️ Нико не настроен (нет API-ключа Gemini)"

        knowledge = ""
        try:
            with open("data/knowledge.txt", "r", encoding="utf-8") as f:
                knowledge = f.read()[:2000]
        except FileNotFoundError:
            pass

        system_prompt = NICO_SYSTEM_PROMPT.format(knowledge=knowledge)

        def _call() -> str:
            with genai.Client(api_key=self.api_key) as client:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=question,
                    config=types.GenerateContentConfig(
                        system_instruction=system_prompt,
                        temperature=0.7,
                        max_output_tokens=1500,
                        # thinking_budget=0 — отключает "размышления" явно, а не
                        # надеется, что модель уложится в лимит. Это и есть
                        # основной фикс "обрубков" у Нико.
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    )
                )
                return _extract_gemini_text(response)

        try:
            return await asyncio.to_thread(_call)
        except Exception as e:
            logger.error(f"Gemini API ошибка (Нико): {e}")
            return f"⚠️ Ошибка Gemini: {e}"


# ============================================
# НОВОЕ: БЛОГЕРСКИЙ ИИ-АССИСТЕНТ
# ============================================

BLOGGER_SYSTEM_PROMPT = """
Ты пишешь пост для Telegram-канала про IndyCar на основе новостного материала.

СТРОГИЕ ПРАВИЛА ФОРМАТА:
- Только обычный текст, без markdown-разметки: никаких **, __, ##, `, -, >
- Эмодзи можно использовать как обычный текст (не как маркеры списка)
- Объём: 700-900 символов (это примерно 120-150 слов) — не меньше
- Структура: цепляющий первый абзац (1-2 предложения) → основной блок с
  фактами и контекстом → короткий вывод/затравка для читателей

ПРИМЕР ХОРОШЕГО ПОСТА:
🏁 Ганнам-Дуссо снова в топе — но не там, где ждали

На тренировке в Портленде француз показал третье время, хотя команда весь
уик-энд жаловалась на баланс машины на поворотах. Инженеры признались, что
настройки подвески меняли буквально до последней минуты перед выходом на
трассу.

Для чемпионата это важно: если завтра квалификация пройдёт так же ровно, у
него появится реальный шанс зайти в топ-5 по очкам сезона впервые с июня.

Что думаете — рискнёт ли команда с агрессивной стратегией на гонку?

Теперь напиши пост в ТАКОМ ЖЕ стиле и объёме на основе темы и материала,
которые пришлют дальше. Не повторяй пример дословно.
"""


def strip_markdown(text: str) -> str:
    """Страховка на случай, если Gemini всё равно проскочит с markdown."""
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)
    text = re.sub(r'__(.+?)__', r'\1', text)
    text = re.sub(r'#{1,6}\s*', '', text)
    text = re.sub(r'`(.+?)`', r'\1', text)
    text = re.sub(r'^[-•]\s*', '', text, flags=re.MULTILINE)
    return text.strip()


class BloggerAI:
    """Контент-ассистент для партнёров: собирает новости по теме, переводит
    и оформляет черновик поста для ИХ канала (не рассылается юзерам бота)."""

    def __init__(self, api_key: Optional[str], news_parser: "NewsParser", translator: Translator):
        self.api_key = api_key
        self.news_parser = news_parser
        self.translator = translator

    async def _gather_context(self, topic: str) -> str:
        articles = await self.news_parser.fetch_all()
        if not articles:
            return "Свежих новостей не нашлось — пиши по общим знаниям об IndyCar."
        topic_lower = topic.lower()
        relevant = [
            a for a in articles
            if topic_lower in (a.get('title', '') + a.get('summary', '')).lower()
        ]
        chosen = (relevant or articles)[:5]
        translated = await self.translator.translate_news(chosen)
        return "\n\n".join(f"• {a['title']}: {a['summary']}" for a in translated)

    async def generate_post(self, topic: str) -> str:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY не задан")

        context = await self._gather_context(topic)
        contents = f"Тема поста: {topic}\n\nМатериал для поста:\n{context}"

        def _call() -> str:
            with genai.Client(api_key=self.api_key) as client:
                response = client.models.generate_content(
                    model=GEMINI_MODEL,
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=BLOGGER_SYSTEM_PROMPT,
                        temperature=0.8,
                        max_output_tokens=1200,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    )
                )
                return _extract_gemini_text(response)

        raw = await asyncio.to_thread(_call)
        return strip_markdown(raw)


# ============================================
# ПАРСЕР НОВОСТЕЙ (уже был async в оригинале, почти без изменений)
# ============================================

class NewsParser:
    SOURCES = {
        'espn': 'https://site.api.espn.com/apis/site/v2/sports/racing/irl/news',
        'therace': 'https://www.the-race.com/category/indycar/rss',
        'motorsport': 'https://www.motorsport.com/indycar/rss/',
        'autosport': 'https://www.autosport.com/indycar/rss/',
        'racer': 'https://racer.com/indycar/feed/',
        'indycar_official': 'https://www.indycar.com/~/api/rss/News',
        'reddit': 'https://www.reddit.com/r/INDYCAR/.rss',
    }

    async def _fetch_rss_content(self, url: str) -> str:
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                return await resp.text()

    async def _fetch_newsapi(self) -> List[dict]:
        if not NEWS_API_KEY:
            return []
        try:
            url = f"https://newsapi.org/v2/everything?q=indycar&apiKey={NEWS_API_KEY}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    return [{
                        'title': a['title'],
                        'summary': (a.get('description') or '')[:300],
                        'link': a['url'],
                        'source': 'newsapi'
                    } for a in data.get('articles', [])[:5]]
        except Exception as e:
            logger.warning(f"NewsAPI ошибка: {e}")
            return []

    async def fetch_all(self) -> List[dict]:
        all_news: List[dict] = []

        for name, url in self.SOURCES.items():
            if name == 'espn':
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
                async with session.get(self.SOURCES['espn'], timeout=aiohttp.ClientTimeout(total=10)) as resp:
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

        all_news.extend(await self._fetch_newsapi())
        return all_news


class StandingsFetcher:
    @staticmethod
    async def fetch() -> List[dict]:
        try:
            url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
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


def get_top_winners_text() -> str:
    wins = Counter()
    for w in WINNERS:
        driver = w.get('driver', '')
        if w.get('year', 0) >= 1911 and 'не проводилась' not in driver:
            wins[driver] += 1
    top = wins.most_common(10)
    medals = ['🥇', '🥈', '🥉', '4️⃣', '5️⃣', '6️⃣', '7️⃣', '8️⃣', '9️⃣', '🔟']
    return "\n".join(f"{medals[i]} {driver} — **{count}** побед" for i, (driver, count) in enumerate(top))


# ============================================
# МЕНЮ (InlineKeyboardBuilder вместо ручного IKM)
# ============================================

def _grid(buttons: List[Tuple[str, str]], *row_sizes: int) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    for text, cb in buttons:
        builder.button(text=text, callback_data=cb)
    builder.adjust(*(row_sizes or (1,)))
    return builder.as_markup()


class Menu:
    @staticmethod
    def main(level: str, is_blogger: bool = False) -> InlineKeyboardMarkup:
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
        if is_blogger:
            buttons.append(("🖋️ Кабинет блогера", "blogger_panel"))
        return _grid(buttons, 3)

    @staticmethod
    def drivers() -> InlineKeyboardMarkup:
        builder = InlineKeyboardBuilder()
        teams: Dict[str, list] = {}
        for code, d in DRIVERS.items():
            teams.setdefault(d['team'], []).append((code, d))

        for team, drivers in sorted(teams.items())[:8]:
            builder.row(InlineKeyboardButton(text=f"━━ {team} ━━", callback_data="noop"))
            row: List[InlineKeyboardButton] = []
            for code, d in drivers[:4]:
                surname = d['name'].split()[-1]
                row.append(InlineKeyboardButton(text=f"{surname} #{d['number']}", callback_data=f"driver_{code}"))
                if len(row) == 3:
                    builder.row(*row)
                    row = []
            if row:
                builder.row(*row)

        builder.row(InlineKeyboardButton(text="🔙 Назад", callback_data="menu"))
        return builder.as_markup()

    @staticmethod
    def indy500() -> InlineKeyboardMarkup:
        buttons = [("📅 По году", "winner_prompt"), ("🏆 Топ-10 победителей", "top_winners"), ("🔙 Назад", "menu")]
        return _grid(buttons, 2, 1)

    @staticmethod
    def guide() -> InlineKeyboardMarkup:
        buttons = [("📋 Правила", "guide_rules"), ("🏁 Трассы", "guide_tracks"), ("🔙 Назад", "menu")]
        return _grid(buttons, 2, 1)

    @staticmethod
    def admin() -> InlineKeyboardMarkup:
        buttons = [
            ("📊 Статистика", "admin_stats"),
            ("👥 Пользователи", "admin_users"),
            ("📈 Команды", "admin_commands"),
            ("🎫 Заявки", "admin_tickets"),
            ("📨 Рассылка", "admin_broadcast"),
            ("➕ Добавить блогера", "admin_addblogger"),
            ("🔙 Выйти", "menu"),
        ]
        return _grid(buttons, 3)

    @staticmethod
    def back() -> InlineKeyboardMarkup:
        return _grid([("🔙 Назад", "menu")], 1)

    @staticmethod
    def blogger() -> InlineKeyboardMarkup:
        buttons = [("✍️ Сгенерировать пост", "blogger_generate"), ("🔙 Назад", "menu")]
        return _grid(buttons, 1, 1)


# ============================================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ============================================

bot = Bot(token=TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN))
storage = SQLiteStorage(DB_PATH)
dp = Dispatcher(storage=storage)
router = Router()

db = Database()
translator = Translator()
nico = NicoAI(GEMINI_API_KEY)
news_parser = NewsParser()
blogger_ai = BloggerAI(GEMINI_API_KEY, news_parser, translator)

# кэш новостей для пагинации — ephemeral, не обязан переживать рестарт
news_cache: Dict[int, list] = {}


async def render(call: CallbackQuery, text: str, markup: Optional[InlineKeyboardMarkup],
                  parse_mode: Optional[str] = "Markdown") -> None:
    """Редактирует текущее сообщение; если не вышло (например это было фото) —
    удаляет и шлёт новое. Дешевле, чем delete+send на каждый чих, как в оригинале."""
    try:
        await call.message.edit_text(text, reply_markup=markup, parse_mode=parse_mode)
    except Exception:
        try:
            await call.message.delete()
        except Exception:
            pass
        await call.message.answer(text, reply_markup=markup, parse_mode=parse_mode)


async def send_driver(call: CallbackQuery, driver: Dict) -> None:
    text = f"🏎️ **{driver['name']}**\n🏁 {driver['team']}\n🔢 #{driver['number']}"
    if driver.get('pos'):
        text += f"\n📊 Позиция: {driver['pos']}"
    markup = Menu.back()
    try:
        await call.message.delete()
    except Exception:
        pass
    if driver.get('image'):
        try:
            await call.message.answer_photo(driver['image'], caption=text, parse_mode="Markdown", reply_markup=markup)
            return
        except Exception as e:
            logger.error(f"Photo send error: {e}")
    await call.message.answer(text, parse_mode="Markdown", reply_markup=markup)


async def show_schedule_and_top(chat_id: int) -> None:
    try:
        url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard?seasontype=2&level=3"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                data = await resp.json()

        calendar = data.get('leagues', [{}])[0].get('calendar', [])
        now = datetime.now(timezone.utc)
        future_races = []
        for event in calendar:
            start_date = event.get('startDate', '')
            if not start_date:
                continue
            try:
                event_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                if event_date > now:
                    future_races.append({
                        'label': event.get('label', 'Неизвестная гонка'),
                        'date': event_date.strftime('%d.%m.%Y')
                    })
            except ValueError:
                continue
        future_races = future_races[:10]

        lines = ["🏁 **Ближайшие гонки**", ""]
        if not future_races:
            lines.append("🏁 Сезон завершен или календарь не загружен")
        else:
            lines.extend(f"📅 {r['date']} — **{r['label']}**" for r in future_races)

        top5 = await StandingsFetcher.fetch()
        if top5:
            lines.extend(["", "🏆 **Топ-5 чемпионата**", ""])
            lines.extend(f"{i}. {d['name']} — {d['points']} очков" for i, d in enumerate(top5, 1))

        await bot.send_message(chat_id, "\n".join(lines), reply_markup=Menu.back(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Schedule error: {e}")
        await bot.send_message(chat_id, "⚠️ Ошибка загрузки календаря", reply_markup=Menu.back())


async def show_news_page(chat_id: int, page: int) -> None:
    articles = news_cache.get(chat_id, [])
    if not articles:
        await bot.send_message(chat_id, "📰 Новости не загружены. Нажми кнопку ещё раз.", reply_markup=Menu.back())
        return
    if page >= len(articles):
        await bot.send_message(chat_id, "📰 Новости закончились", reply_markup=Menu.back())
        return

    article = articles[page]
    text = f"📰 **{article['title']}**\n\n{article['summary']}...\n\n[Читать]({article['link']})"
    builder = InlineKeyboardBuilder()
    if page < len(articles) - 1:
        builder.button(text="➡️ Следующая", callback_data=f"news_{page + 1}")
    builder.button(text="🔙 Назад", callback_data="menu")
    builder.adjust(2)

    await bot.send_message(chat_id, text, reply_markup=builder.as_markup(),
                            parse_mode="Markdown", disable_web_page_preview=True)


# ============================================
# КОМАНДЫ
# ============================================

@router.message(CommandStart())
async def cmd_start(message: Message):
    uid = message.from_user.id
    name = message.from_user.first_name or 'Пользователь'
    username = message.from_user.username or 'без_юзернейма'

    await db.add_user(uid, username, name)
    level = await db.get_user_level(uid)

    if level in ('novice', 'pro'):
        level_name = '🟢 Новичок' if level == 'novice' else '🔴 Продвинутый'
        is_bl = await db.is_blogger(uid)
        await message.answer(
            f"🏁 **С возвращением, {name}!**\n\nТвой уровень: **{level_name}**\n\n/switch — сменить уровень",
            reply_markup=Menu.main(level, is_bl),
            parse_mode="Markdown"
        )
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Новичок", callback_data="level_novice")
    builder.button(text="🔴 Продвинутый", callback_data="level_pro")
    builder.adjust(2)
    await message.answer(
        f"👋 **Привет, {name}!**\n\nЯ — INDY Leader, гид по IndyCar.\n\n"
        f"**Кто ты?**\n🟢 Новичок — объясню с нуля\n🔴 Продвинутый — максимум фактов",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.message(Command("help"))
async def cmd_help(message: Message):
    await message.answer(
        "🤖 **INDY Leader — справка**\n\n/start — главное меню\n/switch — сменить уровень\n"
        "/admin — админ-панель\n/ticket — заявка в техподдержку\n\n"
        "Все остальные функции доступны через кнопки в меню.",
        parse_mode="Markdown"
    )


@router.message(Command("switch"))
async def cmd_switch(message: Message):
    current = await db.get_user_level(message.from_user.id)
    cur_name = '🟢 Новичок' if current == 'novice' else '🔴 Продвинутый'
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Новичок", callback_data="level_novice")
    builder.button(text="🔴 Продвинутый", callback_data="level_pro")
    builder.button(text="🔙 Назад", callback_data="menu")
    builder.adjust(2, 1)
    await message.answer(
        f"⚙️ **Смена уровня**\n\nТекущий уровень: **{cur_name}**\n\nВыбери новый:",
        reply_markup=builder.as_markup(),
        parse_mode="Markdown"
    )


@router.message(Command("admin"))
async def cmd_admin(message: Message):
    if not ADMIN_IDS:
        await message.reply("⛔ Админ-панель отключена")
        return
    if message.from_user.id not in ADMIN_IDS:
        await message.reply("⛔ У вас нет доступа к админ-панели")
        return
    await message.answer("🔐 **Админ-панель**", reply_markup=Menu.admin(), parse_mode="Markdown")


@router.message(Command("ticket"))
async def cmd_ticket(message: Message, state: FSMContext):
    await message.answer(
        "🎫 **Техническая поддержка**\n\nОпиши свою проблему в одном сообщении.\n"
        "Формат: проблема | контакт (например: @username или почта)",
        parse_mode="Markdown"
    )
    await state.set_state(Flow.waiting_ticket)


@router.message(Command("blogger"))
async def cmd_blogger(message: Message):
    if not await db.is_blogger(message.from_user.id):
        return
    blogger = await db.get_blogger(message.from_user.id)
    used = await db.get_blogger_usage_today(message.from_user.id)
    await message.answer(
        f"🖋️ **Кабинет блогера — {blogger['channel_name']}**\n\n"
        f"Сегодня сгенерировано: {used}/{BLOGGER_DAILY_LIMIT}\n\n"
        f"Собираю свежие новости по теме, перевожу и оформляю черновик поста для твоего канала.",
        parse_mode="Markdown",
        reply_markup=Menu.blogger()
    )


@router.message(Command("addblogger"))
async def cmd_add_blogger(message: Message):
    """Админ-команда: /addblogger <telegram_id> <название канала> <ссылка>"""
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        await message.answer("Формат: `/addblogger <id> <канал> <ссылка>`", parse_mode="Markdown")
        return
    _, uid_str, channel_name, channel_link = parts
    try:
        uid = int(uid_str)
    except ValueError:
        await message.answer("❌ ID должен быть числом")
        return
    await db.add_blogger(uid, channel_name, channel_link)
    await message.answer(f"✅ Блогер **{channel_name}** (ID `{uid}`) добавлен", parse_mode="Markdown")


# ============================================
# CALLBACK: УРОВЕНЬ / МЕНЮ
# ============================================

@router.callback_query(F.data.in_({"level_novice", "level_pro"}))
async def cb_level(call: CallbackQuery):
    await call.answer()
    level = "novice" if call.data == "level_novice" else "pro"
    await db.set_user_level(call.from_user.id, level)
    label = "🟢 Уровень: Новичок" if level == "novice" else "🔴 Уровень: Продвинутый"
    is_bl = await db.is_blogger(call.from_user.id)
    await render(call, f"**{label}**", Menu.main(level, is_bl))


@router.callback_query(F.data == "switch_level")
async def cb_switch_level(call: CallbackQuery):
    await call.answer()
    current = await db.get_user_level(call.from_user.id)
    cur_name = "🟢 Новичок" if current == "novice" else "🔴 Продвинутый"
    builder = InlineKeyboardBuilder()
    builder.button(text="🟢 Новичок", callback_data="level_novice")
    builder.button(text="🔴 Продвинутый", callback_data="level_pro")
    builder.button(text="🔙 Назад", callback_data="menu")
    builder.adjust(2, 1)
    await render(call, f"⚙️ **Смена уровня**\n\nТекущий уровень: **{cur_name}**\n\nВыбери новый:", builder.as_markup())


@router.callback_query(F.data == "menu")
async def cb_menu(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    level = await db.get_user_level(call.from_user.id)
    is_bl = await db.is_blogger(call.from_user.id)
    await render(call, "🏁 **Главное меню**", Menu.main(level, is_bl))


@router.callback_query(F.data == "noop")
async def cb_noop(call: CallbackQuery):
    await call.answer()


# ============================================
# CALLBACK: ПИЛОТЫ
# ============================================

@router.callback_query(F.data == "drivers_list")
async def cb_drivers_list(call: CallbackQuery):
    await call.answer()
    await render(call, "🏎️ **Выбери пилота**", Menu.drivers())


@router.callback_query(F.data == "driver_random")
async def cb_driver_random(call: CallbackQuery):
    await call.answer()
    _, driver = random.choice(list(DRIVERS.items()))
    await send_driver(call, driver)


@router.callback_query(F.data.startswith("driver_") & (F.data != "driver_random"))
async def cb_driver(call: CallbackQuery):
    await call.answer()
    code = call.data.removeprefix("driver_")
    driver = DRIVERS.get(code)
    if not driver:
        await call.answer("Пилот не найден", show_alert=True)
        return
    await send_driver(call, driver)


# ============================================
# CALLBACK: КАЛЕНДАРЬ / INDY 500
# ============================================

@router.callback_query(F.data == "schedule_top")
async def cb_schedule(call: CallbackQuery):
    await call.answer()
    await render(call, "⏳ Загружаю календарь...", None)
    await show_schedule_and_top(call.message.chat.id)


@router.callback_query(F.data == "indy500_menu")
async def cb_indy500_menu(call: CallbackQuery):
    await call.answer()
    await render(call, "🏆 **Indy 500**\n\nЧто хочешь узнать?", Menu.indy500())


@router.callback_query(F.data == "top_winners")
async def cb_top_winners(call: CallbackQuery):
    await call.answer()
    await render(call, "🏆 **10 величайших победителей**\n\n" + get_top_winners_text(), Menu.indy500())


@router.callback_query(F.data == "winner_prompt")
async def cb_winner_prompt(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Flow.waiting_year)
    await render(call, "📅 **Введи год** (например, 2023):", Menu.indy500())


@router.message(Flow.waiting_year)
async def on_year_input(message: Message, state: FSMContext):
    await state.clear()
    try:
        year = int(message.text.strip())
    except ValueError:
        await message.answer("❌ Введи год цифрами", reply_markup=Menu.indy500())
        return
    for w in WINNERS:
        if w.get('year') == year:
            await message.answer(
                f"🏆 **Indy 500 {year}**\n🏁 {w.get('driver', 'Неизвестно')}",
                parse_mode="Markdown", reply_markup=Menu.indy500()
            )
            return
    await message.answer(f"❌ Нет данных за {year}", reply_markup=Menu.indy500())


# ============================================
# CALLBACK: НОВОСТИ
# ============================================

@router.callback_query(F.data == "news")
async def cb_news(call: CallbackQuery):
    await call.answer()
    await render(call, "📰 Собираю новости...", None)
    chat_id = call.message.chat.id
    articles = await news_parser.fetch_all()
    if not articles:
        await bot.send_message(chat_id, "📰 Новостей пока нет", reply_markup=Menu.back())
        return
    news_cache[chat_id] = await translator.translate_news(articles)
    await show_news_page(chat_id, 0)


@router.callback_query(F.data.startswith("news_"))
async def cb_news_page(call: CallbackQuery):
    await call.answer()
    page = int(call.data.removeprefix("news_"))
    await show_news_page(call.message.chat.id, page)


# ============================================
# CALLBACK: НИКО
# ============================================

@router.callback_query(F.data == "ask_nico")
async def cb_ask_nico(call: CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(Flow.waiting_nico)
    await render(call, "🧠 **Нико**\n\nНапиши свой вопрос про IndyCar:", Menu.back())


@router.message(Flow.waiting_nico)
async def on_nico_question(message: Message, state: FSMContext):
    await state.clear()
    thinking = await message.answer("🧠 Нико думает...")
    answer = await nico.ask(message.text)
    safe = escape_markdown(answer)
    try:
        await thinking.edit_text(f"🧠 **Нико:**\n\n{safe}", reply_markup=Menu.back(), parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Nico render error: {e}")
        await thinking.edit_text(f"⚠️ Ошибка: {e}", reply_markup=Menu.back())


# ============================================
# CALLBACK: ГАЙД / ДОНАТ / О ПРОЕКТЕ
# ============================================

@router.callback_query(F.data == "guide_intro")
async def cb_guide_intro(call: CallbackQuery):
    await call.answer()
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
    await render(call, text, Menu.guide())


@router.callback_query(F.data == "guide_rules")
async def cb_guide_rules(call: CallbackQuery):
    await call.answer()
    text = (
        "📋 **Правила IndyCar**\n\n"
        "**Очки:**\n1 место — 50\n2 место — 40\n3 место — 35\n...\n10 место — 10\n"
        "+1 за поул\n+1 за быстрый круг\n\n"
        "**Штрафы:**\n• Превышение на пит-лейн\n• Блокировка\n• Нарушение флагов"
    )
    await render(call, text, Menu.guide())


@router.callback_query(F.data == "guide_tracks")
async def cb_guide_tracks(call: CallbackQuery):
    await call.answer()
    text = (
        "🏁 **Трассы IndyCar**\n\n"
        "🏟️ **Овалы** (7 этапов)\n• Indianapolis (2.5 мили)\n• Texas (1.5 мили)\n\n"
        "🔄 **Шоссейные** (5 этапов)\n• Road America (4 мили)\n• Mid-Ohio (2.25 мили)\n\n"
        "🏙️ **Уличные** (5 этапов)\n• St. Petersburg (1.8 мили)\n• Long Beach (1.97 мили)\n\n"
        "🏆 **Indy 500** — главная гонка"
    )
    await render(call, text, Menu.guide())


@router.callback_query(F.data == "donate")
async def cb_donate(call: CallbackQuery):
    await call.answer()
    await render(
        call,
        "❤️ **Поддержать проект**\n\n💰 [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
        Menu.back()
    )


@router.callback_query(F.data == "about")
async def cb_about(call: CallbackQuery):
    await call.answer()
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
    await render(call, text, Menu.back())


# ============================================
# CALLBACK / MESSAGE: БЛОГЕРСКИЙ РАЗДЕЛ
# ============================================

@router.callback_query(F.data == "blogger_panel")
async def cb_blogger_panel(call: CallbackQuery):
    await call.answer()
    if not await db.is_blogger(call.from_user.id):
        await call.answer("Доступ только для партнёров", show_alert=True)
        return
    blogger = await db.get_blogger(call.from_user.id)
    used = await db.get_blogger_usage_today(call.from_user.id)
    await render(
        call,
        f"🖋️ **Кабинет блогера — {blogger['channel_name']}**\n\n"
        f"Сегодня сгенерировано: {used}/{BLOGGER_DAILY_LIMIT}",
        Menu.blogger()
    )


@router.callback_query(F.data == "blogger_generate")
async def cb_blogger_generate(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not await db.is_blogger(call.from_user.id):
        await call.answer("Доступ только для партнёров", show_alert=True)
        return
    used = await db.get_blogger_usage_today(call.from_user.id)
    if used >= BLOGGER_DAILY_LIMIT:
        await call.answer(f"Лимит {BLOGGER_DAILY_LIMIT} генераций в день исчерпан, приходи завтра", show_alert=True)
        return
    await state.set_state(Flow.waiting_blogger_topic)
    await render(
        call,
        "✍️ Введи тему или ключевые слова для поста\n(например: «Ганнам-Дуссо Портленд» или «итоги квалификации»):",
        Menu.back()
    )


def _blogger_result_markup() -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="🔄 Другой вариант", callback_data="blogger_regenerate")
    builder.button(text="🔙 Назад", callback_data="blogger_panel")
    builder.adjust(1, 1)
    return builder.as_markup()


@router.message(Flow.waiting_blogger_topic)
async def on_blogger_topic(message: Message, state: FSMContext):
    await state.clear()
    if not await db.is_blogger(message.from_user.id):
        return
    topic = message.text.strip()
    status = await message.answer("⏳ Собираю новости и пишу черновик...")
    try:
        draft = await blogger_ai.generate_post(topic)
    except Exception as e:
        logger.error(f"Blogger post error: {e}")
        await status.edit_text(f"⚠️ Не получилось собрать пост: {e}", reply_markup=Menu.blogger())
        return

    await db.log_blogger_post(message.from_user.id, topic, draft)
    await state.update_data(last_blogger_topic=topic)
    # parse_mode=None — черновик уже прогнан через strip_markdown, лишний парсинг ни к чему
    await status.edit_text(f"📝 Черновик готов:\n\n{draft}", reply_markup=_blogger_result_markup(), parse_mode=None)


@router.callback_query(F.data == "blogger_regenerate")
async def cb_blogger_regenerate(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if not await db.is_blogger(call.from_user.id):
        return
    data = await state.get_data()
    topic = data.get("last_blogger_topic")
    if not topic:
        await call.answer("Тема потерялась, начни заново", show_alert=True)
        return
    used = await db.get_blogger_usage_today(call.from_user.id)
    if used >= BLOGGER_DAILY_LIMIT:
        await call.answer(f"Лимит {BLOGGER_DAILY_LIMIT} генераций в день исчерпан", show_alert=True)
        return
    await call.message.edit_text("⏳ Собираю новости и пишу черновик...")
    try:
        draft = await blogger_ai.generate_post(topic)
    except Exception as e:
        logger.error(f"Blogger regenerate error: {e}")
        await call.message.edit_text(f"⚠️ Не получилось: {e}", reply_markup=Menu.blogger())
        return
    await db.log_blogger_post(call.from_user.id, topic, draft)
    await call.message.edit_text(f"📝 Черновик готов:\n\n{draft}", reply_markup=_blogger_result_markup(), parse_mode=None)


# ============================================
# CALLBACK / MESSAGE: ТИКЕТЫ
# ============================================

@router.message(Flow.waiting_ticket)
async def on_ticket_input(message: Message, state: FSMContext):
    await state.clear()
    parts = message.text.split('|')
    issue = parts[0].strip()
    contact = parts[1].strip() if len(parts) > 1 else (message.from_user.username or "Не указан")

    ticket_id = await db.create_ticket(message.from_user.id, issue, contact)
    await message.answer(
        f"✅ **Заявка #{ticket_id}** создана!\n\nАдмины скоро ответят.",
        parse_mode="Markdown", reply_markup=Menu.back()
    )
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"🎫 **Новая заявка #{ticket_id}**\nОт: @{contact}\nID: {message.from_user.id}\n"
                f"Проблема: {issue[:200]}",
                parse_mode="Markdown"
            )
        except Exception:
            pass


# ============================================
# CALLBACK / MESSAGE: АДМИНКА
# ============================================

@router.callback_query(F.data.startswith("admin_"))
async def cb_admin(call: CallbackQuery, state: FSMContext):
    await call.answer()
    if call.from_user.id not in ADMIN_IDS:
        await call.answer("Нет доступа", show_alert=True)
        return
    action = call.data

    if action == "admin_stats":
        stats = await db.get_stats()
        commands = await db.get_command_stats()
        text = (
            f"📊 **Глобальная статистика**\n\n👤 Пользователей: {stats['users']}\n"
            f"📝 Команд: {stats['commands']}\n\n**Топ-5 команд:**\n"
        )
        text += "".join(f"• {cmd} — {count}\n" for cmd, count in commands[:5])
        await render(call, text, Menu.admin())

    elif action == "admin_users":
        users = await db.get_all_users()
        active7 = await db.get_active_users(7)
        active30 = await db.get_active_users(30)
        text = (
            f"👥 **Все пользователи ({len(users)})**\n\n"
            f"Активных за 7 дней: {len(active7)}\nАктивных за 30 дней: {len(active30)}\n\n"
            f"Последние 10 ID:\n"
        )
        text += "".join(f"• `{uid}`\n" for uid in users[-10:])
        await render(call, text, Menu.admin())

    elif action == "admin_commands":
        commands = await db.get_command_stats()
        text = "📈 **Статистика команд**\n\n" + "".join(f"• {cmd} — {count} раз\n" for cmd, count in commands[:10])
        await render(call, text, Menu.admin())

    elif action == "admin_tickets":
        tickets = await db.get_open_tickets()
        if not tickets:
            text = "🎫 **Открытых заявок нет**"
        else:
            text = f"🎫 **Открытые заявки ({len(tickets)})**\n\n"
            text += "".join(f"#{t[0]} | от @{t[3]} | {t[4][:16]}\n  {t[2][:80]}...\n\n" for t in tickets[:10])
        await render(call, text, Menu.admin())

    elif action == "admin_broadcast":
        await state.set_state(Flow.waiting_broadcast)
        await render(
            call,
            "📨 **Рассылка**\n\nВведите текст рассылки.\nОпции:\n"
            "• `all` — всем пользователям\n• `active` — активным за 7 дней\n• `ID,ID` — конкретным пользователям\n\n"
            "Пример: `all | Привет!`",
            Menu.admin()
        )

    elif action == "admin_addblogger":
        await state.set_state(Flow.waiting_addblogger_id)
        await render(
            call,
            "➕ **Добавление блогера**\n\nШаг 1/3 — пришли Telegram ID партнёра\n"
            "(число; узнать можно через @userinfobot)",
            Menu.admin()
        )


@router.message(Flow.waiting_broadcast)
async def on_broadcast_input(message: Message, state: FSMContext):
    await state.clear()
    if message.from_user.id not in ADMIN_IDS:
        return
    try:
        target_raw, text = message.text.split('|', 1)
        target = target_raw.strip().lower()
        text = text.strip()
    except ValueError:
        await message.answer("❌ Неверный формат. Используй: `all | текст`", parse_mode="Markdown")
        return

    if target == "all":
        users = await db.get_all_users()
    elif target == "active":
        users = await db.get_active_users(7)
    else:
        try:
            users = [int(x.strip()) for x in target.split(',')]
        except ValueError:
            await message.answer("❌ Неверный формат ID. Используй: `12345,67890 | текст`", parse_mode="Markdown")
            return

    if not users:
        await message.answer("⚠️ Нет пользователей для рассылки")
        return

    success = failed = 0
    for uid in users:
        try:
            await bot.send_message(uid, text, parse_mode="Markdown")
            success += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.05)  # не блокирует event loop, в отличие от time.sleep в оригинале

    await message.answer(
        f"📨 **Рассылка завершена**\n\n✅ Успешно: {success}\n❌ Ошибок: {failed}",
        parse_mode="Markdown", reply_markup=Menu.back()
    )


# ---------- пошаговое добавление блогера кнопкой ----------

@router.message(Flow.waiting_addblogger_id)
async def on_addblogger_id(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    try:
        blogger_id = int(message.text.strip())
    except ValueError:
        await message.answer("❌ ID должен быть числом. Попробуй ещё раз:")
        return
    await state.update_data(new_blogger_id=blogger_id)
    await state.set_state(Flow.waiting_addblogger_name)
    await message.answer("Шаг 2/3 — название канала (как отображать партнёра):")


@router.message(Flow.waiting_addblogger_name)
async def on_addblogger_name(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    await state.update_data(new_blogger_name=message.text.strip())
    await state.set_state(Flow.waiting_addblogger_link)
    await message.answer("Шаг 3/3 — ссылка на канал (https://t.me/...):")


@router.message(Flow.waiting_addblogger_link)
async def on_addblogger_link(message: Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await state.clear()
        return
    data = await state.get_data()
    await state.clear()
    blogger_id = data.get("new_blogger_id")
    channel_name = data.get("new_blogger_name")
    channel_link = message.text.strip()

    await db.add_blogger(blogger_id, channel_name, channel_link)
    await message.answer(
        f"✅ Блогер **{channel_name}** (ID `{blogger_id}`) добавлен\nСсылка: {channel_link}",
        parse_mode="Markdown", reply_markup=Menu.admin()
    )
    try:
        await bot.send_message(
            blogger_id,
            f"🖋️ Тебе выдан доступ к кабинету блогера для канала «{channel_name}».\n"
            f"Набери /blogger, чтобы начать.",
        )
    except Exception:
        pass  # партнёр мог ещё не писать боту — /start он сделает сам


# ============================================
# ФОЛЛБЭК: СВОБОДНЫЙ ТЕКСТ БЕЗ АКТИВНОГО СОСТОЯНИЯ
# Регистрируется ПОСЛЕДНИМ — иначе перехватит сообщения, которые должны были
# уйти в хендлеры выше (aiogram проверяет фильтры в порядке регистрации).
# ============================================

@router.message(F.text)
async def on_free_text(message: Message):
    level = await db.get_user_level(message.from_user.id)
    is_bl = await db.is_blogger(message.from_user.id)
    await message.answer("Используй кнопки в меню 👇", reply_markup=Menu.main(level, is_bl))


dp.include_router(router)


# ============================================
# FASTAPI — ВЕБХУК
# ============================================

async def _setup_bot_commands():
    from aiogram.types import BotCommand
    await bot.set_my_commands([
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="switch", description="Сменить уровень"),
        BotCommand(command="ticket", description="Техподдержка"),
        BotCommand(command="help", description="Справка"),
    ])
    # /admin и /blogger сознательно не в этом списке — они не для всех, и
    # Telegram показывает set_my_commands() ВСЕМ пользователям одинаково.


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.init()
    await storage.init()
    await _setup_bot_commands()

    full_url = WEBHOOK_URL.rstrip("/") + WEBHOOK_PATH
    if not WEBHOOK_URL or WEBHOOK_URL.startswith("http://localhost") or not WEBHOOK_URL.startswith("https://"):
        # Это и есть самая частая причина "вебхук не встал сам, ставлю руками":
        # Telegram принимает ТОЛЬКО https-адрес. Если переменная WEBHOOK_URL не
        # задана на Render (или задана без https://), ниже это будет видно в
        # логах явно, а не тихо провалится.
        logger.error(
            f"❌ WEBHOOK_URL выглядит некорректно для Telegram: '{WEBHOOK_URL}'. "
            f"Нужен полный https-адрес твоего Render-сервиса, например "
            f"https://indyleader-beta.onrender.com — проверь Environment Variables на Render."
        )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await asyncio.sleep(0.5)
        ok = await bot.set_webhook(
            url=full_url,
            secret_token=WEBHOOK_SECRET,
            allowed_updates=["message", "callback_query"],
            drop_pending_updates=True,
        )
        if ok:
            logger.info(f"✅ Webhook установлен автоматически: {full_url}")
        else:
            logger.error(f"❌ Telegram отклонил set_webhook для {full_url} (вернул False)")
    except Exception as e:
        # Раньше исключение тут роняло весь процесс на старте, и было непонятно,
        # почему бот не встаёт вообще. Теперь бот всё равно поднимется, а
        # причину будет видно в логах — и можно быстро руками выставить вебхук,
        # уже зная, что именно сломалось.
        logger.error(f"❌ Не удалось установить webhook автоматически: {e}")

    logger.info("🚀 INDY Leader Helio (beta) запущен")
    yield
    await bot.session.close()


app = FastAPI(title="INDY Leader Beta — Helio", version="3.0.0-beta", lifespan=lifespan)


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    if WEBHOOK_SECRET:
        secret = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
        if secret != WEBHOOK_SECRET:
            logger.warning("Неверный секретный токен")
            return Response(content="Unauthorized", status_code=403)

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
    except Exception as e:
        logger.error(f"Webhook error: {e}")
    # Telegram ретраит одно и то же обновление, если не получит 200 — поэтому
    # отвечаем OK даже при внутренней ошибке, как и в оригинале.
    return Response(content="OK", status_code=200)


@app.get("/health")
async def health_check():
    try:
        info = await bot.get_webhook_info()
        me = await bot.get_me()
        return {
            "status": "ok",
            "bot": me.username,
            "webhook": info.url,
            "pending": info.pending_update_count,
            "last_error": info.last_error_message,
            "version": "3.0.0-beta",
            "codename": "Helio",
            "ai_model": GEMINI_MODEL,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


@app.get("/")
async def root():
    return {"status": "INDY Leader Beta (Helio) is running", "webhook_url": WEBHOOK_URL + WEBHOOK_PATH}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
