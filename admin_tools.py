import sqlite3
import threading
import time
import schedule
import asyncio
import requests
from datetime import datetime, timedelta
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton

# ===== БАЗА ДАННЫХ =====
DB_PATH = "indyleader.db"

def get_all_users():
    """Возвращает список всех user_id"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT user_id FROM users')
    users = [row[0] for row in c.fetchall()]
    conn.close()
    return users

def get_active_users(days=7):
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

def get_user_stats(user_id):
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

def get_global_stats():
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

def send_broadcast(bot, user_ids, message, parse_mode='Markdown'):
    """Отправляет рассылку с прогрессом"""
    success = 0
    failed = 0

    for user_id in user_ids:
        try:
            bot.send_message(user_id, message, parse_mode=parse_mode)
            success += 1
        except Exception as e:
            failed += 1
            print(f"Ошибка отправки {user_id}: {e}")

        time.sleep(0.05)

    return success, failed

async def update_knowledge_base():
    """Обновляет базу знаний для RAG"""
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
        output = []
        output.append(f"# База знаний INDY Leader")
        output.append(f"# Обновлено: {datetime.now().isoformat()}")
        output.append("")

        if standings:
            output.append("## ТУРНИРНАЯ ТАБЛИЦА (ТОП-10)")
            for i, driver in enumerate(standings, 1):
                output.append(f"{i}. {driver['name']} — {driver['points']} очков ({driver['team']})")
            output.append("")

        with open("data/knowledge.txt", "w", encoding="utf-8") as f:
            f.write("\n".join(output))

        print(f"✅ База знаний обновлена! Пилоты: {len(standings)}")
        return True
    except Exception as e:
        print(f"❌ Ошибка обновления базы: {e}")
        return False

def start_auto_update():
    """Запускает автопарсинг каждые 6 часов"""
    schedule.every(6).hours.do(lambda: asyncio.run(update_knowledge_base()))

    while True:
        schedule.run_pending()
        time.sleep(60)

def create_ticket(user_id, issue, contact):
    """Создает заявку в поддержку"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

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

def get_open_tickets():
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

def close_ticket(ticket_id):
    """Закрывает заявку"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE tickets SET status = "closed" WHERE id = ?', (ticket_id,))
    conn.commit()
    conn.close() 
