#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
admin_tools.py — Админские функции для INDY Leader

Содержит:
- Работа с пользователями (все, активные, статистика)
- Рассылка
- Заявки в техподдержку
- Обновление базы знаний
- Автообновление по расписанию
"""

import sqlite3
import time
import threading
import schedule
import asyncio
import requests
from datetime import datetime, timedelta
from typing import List, Dict, Tuple, Optional

# ============================================
# ПУТЬ К БАЗЕ
# ============================================

DB_PATH = "indyleader.db"

# ============================================
# ПОЛЬЗОВАТЕЛИ
# ============================================

def get_all_users() -> List[int]:
    """Возвращает список всех user_id"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def get_active_users(days: int = 7) -> List[int]:
    """Возвращает активных пользователей за последние N дней"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
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

def get_user_stats(user_id: int) -> Tuple[Optional[Tuple], List[Tuple]]:
    """Получить статистику по конкретному пользователю"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''
        SELECT username, first_name, last_name, total_commands, first_seen, last_seen
        FROM users WHERE user_id = ?
    ''', (user_id,))
    user = c.fetchone()
    
    c.execute('''
        SELECT command, COUNT(*) FROM stats WHERE user_id = ?
        GROUP BY command ORDER BY COUNT(*) DESC LIMIT 5
    ''', (user_id,))
    top_commands = c.fetchall()
    
    conn.close()
    return user, top_commands

def get_global_stats() -> Dict:
    """Глобальная статистика"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM users')
    total_users = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM stats')
    total_commands = c.fetchone()[0]
    
    c.execute('''
        SELECT command, COUNT(*) FROM stats 
        GROUP BY command ORDER BY COUNT(*) DESC LIMIT 10
    ''')
    top_commands = c.fetchall()
    
    c.execute('''
        SELECT DATE(timestamp) as day, COUNT(*) 
        FROM stats 
        WHERE timestamp > datetime('now', '-7 days')
        GROUP BY day
    ''')
    daily_activity = c.fetchall()
    
    conn.close()
    return {
        'total_users': total_users,
        'total_commands': total_commands,
        'top_commands': top_commands,
        'daily_activity': daily_activity
    }

# ============================================
# РАССЫЛКА
# ============================================

def send_broadcast(bot, user_ids: List[int], message: str, parse_mode: str = 'Markdown') -> Tuple[int, int]:
    """
    Отправляет рассылку с прогрессом
    
    Args:
        bot: объект TeleBot
        user_ids: список ID получателей
        message: текст сообщения
        parse_mode: Markdown или HTML
    
    Returns:
        (success, failed) — количество успешных и неудачных отправок
    """
    success = 0
    failed = 0
    
    for user_id in user_ids:
        try:
            bot.send_message(user_id, message, parse_mode=parse_mode)
            success += 1
        except Exception as e:
            failed += 1
            print(f"Ошибка отправки {user_id}: {e}")
        
        # Задержка чтобы не упасть в лимиты Telegram
        time.sleep(0.05)
    
    return success, failed

# ============================================
# БАЗА ЗНАНИЙ (ДЛЯ НИКО)
# ============================================

async def update_knowledge_base() -> bool:
    """
    Обновляет базу знаний для Нико (data/knowledge.txt)
    
    Берёт данные с ESPN API (турнирная таблица)
    """
    print(f"[{datetime.now()}] Обновление базы знаний...")
    
    try:
        import os
        os.makedirs("data", exist_ok=True)
        
        # Парсим ESPN для турнирной таблицы
        standings = []
        try:
            resp = requests.get(
                "https://site.api.espn.com/apis/site/v2/sports/racing/irl/standings",
                timeout=10
            )
            data = resp.json()
            for entry in data.get('standings', [{}])[0].get('entries', [])[:10]:
                standings.append({
                    'name': entry.get('athlete', {}).get('displayName', 'Unknown'),
                    'points': entry.get('points', 0),
                    'team': entry.get('team', {}).get('displayName', '')
                })
        except:
            pass
        
        # Формируем файл базы знаний
        with open("data/knowledge.txt", "w", encoding="utf-8") as f:
            f.write(f"# База знаний INDY Leader\n")
            f.write(f"# Обновлено: {datetime.now().isoformat()}\n\n")
            
            if standings:
                f.write("## ТУРНИРНАЯ ТАБЛИЦА (ТОП-10)\n")
                for i, driver in enumerate(standings, 1):
                    f.write(f"{i}. {driver['name']} — {driver['points']} очков ({driver['team']})\n")
            else:
                f.write("## Нет данных\n")
        
        print(f"✅ База знаний обновлена! Пилоты: {len(standings)}")
        return True
        
    except Exception as e:
        print(f"❌ Ошибка обновления базы: {e}")
        return False

# ============================================
# АВТО-ОБНОВЛЕНИЕ ПО РАСПИСАНИЮ
# ============================================

def start_auto_update():
    """Запускает автопарсинг каждые 6 часов"""
    schedule.every(6).hours.do(lambda: asyncio.run(update_knowledge_base()))
    
    while True:
        schedule.run_pending()
        time.sleep(60)

# ============================================
# ТЕХНИЧЕСКАЯ ПОДДЕРЖКА (ЗАЯВКИ)
# ============================================

def create_ticket(user_id: int, issue: str, contact: str) -> int:
    """Создаёт заявку в поддержку"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Создаём таблицу заявок если нет
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
    
    c.execute('''
        INSERT INTO tickets (user_id, issue, contact, created_at)
        VALUES (?, ?, ?, ?)
    ''', (user_id, issue, contact, datetime.now().isoformat()))
    
    conn.commit()
    ticket_id = c.lastrowid
    conn.close()
    return ticket_id

def get_open_tickets() -> List[Tuple]:
    """Получает все открытые заявки"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, user_id, issue, contact, created_at 
        FROM tickets WHERE status = 'open'
        ORDER BY created_at DESC
    ''')
    tickets = c.fetchall()
    conn.close()
    return tickets

def close_ticket(ticket_id: int) -> bool:
    """Закрывает заявку"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE tickets SET status = "closed" WHERE id = ?', (ticket_id,))
    conn.commit()
    affected = c.rowcount
    conn.close()
    return affected > 0

def get_ticket_by_id(ticket_id: int) -> Optional[Tuple]:
    """Получает заявку по ID"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT id, user_id, issue, contact, status, created_at
        FROM tickets WHERE id = ?
    ''', (ticket_id,))
    ticket = c.fetchone()
    conn.close()
    return ticket

# ============================================
# ПРОВЕРКА ПОДКЛЮЧЕНИЯ К БАЗЕ
# ============================================

def db_check() -> bool:
    """Проверяет, доступна ли база данных"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT 1')
        conn.close()
        return True
    except:
        return False

# ============================================
# ОЧИСТКА СТАРЫХ ДАННЫХ
# ============================================

def cleanup_old_stats(days: int = 90):
    """Удаляет статистику старше N дней"""
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM stats WHERE timestamp < ?', (cutoff,))
    deleted = c.rowcount
    conn.commit()
    conn.close()
    print(f"🧹 Удалено {deleted} записей статистики старше {days} дней")
