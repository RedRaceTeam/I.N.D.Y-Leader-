import os
import json
import telebot
import requests
import random
import uvicorn
from fastapi import FastAPI, Request, Response
from data.winners import winners

# --- КОНФИГ ---
TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

WEBHOOK_URL = "https://turbo-train-2b9d.onrender.com/webhook"

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# --- БАЗА ГОНЩИКОВ ---
DRIVERS = {
    "PAL": {"name": "Alex Palou", "team": "Chip Ganassi Racing", "number": 10, "pos": 1},
    "KIR": {"name": "Kyle Kirkwood", "team": "Andretti Global", "number": 27, "pos": 2},
    "MAL": {"name": "David Malukas", "team": "Team Penske", "number": 12, "pos": 3},
    "LUN": {"name": "Christian Lundgaard", "team": "Arrow McLaren", "number": 7, "pos": 4},
    "OWA": {"name": "Pato O'Ward", "team": "Arrow McLaren", "number": 5, "pos": 5},
    "ROS": {"name": "Felix Rosenqvist", "team": "Meyer Shank Racing", "number": 60, "pos": 6},
    "NEW": {"name": "Josef Newgarden", "team": "Team Penske", "number": 2, "pos": 7},
    "MCL": {"name": "Scott McLaughlin", "team": "Team Penske", "number": 3, "pos": 8},
    "ERI": {"name": "Marcus Ericsson", "team": "Andretti Global", "number": 28, "pos": 9},
    "VEE": {"name": "Rinus VeeKay", "team": "Juncos Hollinger Racing", "number": 76, "pos": 10},
    "POW": {"name": "Will Power", "team": "Andretti Global", "number": 26, "pos": 11},
    "DIX": {"name": "Scott Dixon", "team": "Chip Ganassi Racing", "number": 9, "pos": 12},
    "RAH": {"name": "Graham Rahal", "team": "Rahal Letterman Lanigan Racing", "number": 15, "pos": 13},
    "SIM": {"name": "Kyffin Simpson", "team": "Chip Ganassi Racing", "number": 8, "pos": 14},
    "ARM": {"name": "Marcus Armstrong", "team": "Meyer Shank Racing", "number": 66, "pos": 15},
    "ROSS": {"name": "Alexander Rossi", "team": "Ed Carpenter Racing", "number": 20, "pos": 16},
    "FER": {"name": "Santino Ferrucci", "team": "A.J. Foyt Enterprises", "number": 14, "pos": 17},
    "FOS": {"name": "Louis Foster", "team": "Rahal Letterman Lanigan Racing", "number": 45, "pos": 18},
    "SIE": {"name": "Nolan Siegel", "team": "Arrow McLaren", "number": 6, "pos": 19},
    "HAU": {"name": "Dennis Hauger", "team": "Dale Coyne Racing", "number": 19, "pos": 20},
    "GRO": {"name": "Romain Grosjean", "team": "Dale Coyne Racing", "number": 18, "pos": 21},
    "RAS": {"name": "Christian Rasmussen", "team": "Ed Carpenter Racing", "number": 21, "pos": 22},
    "COL": {"name": "Caio Collet", "team": "A.J. Foyt Enterprises", "number": 4, "pos": 23},
    "SCH": {"name": "Mick Schumacher", "team": "Rahal Letterman Lanigan Racing", "number": 47, "pos": 24},
    "ROB": {"name": "Sting Ray Robb", "team": "Juncos Hollinger Racing", "number": 77, "pos": 25},
    "CAS": {"name": "Helio Castroneves", "team": "Meyer Shank Racing", "number": 6, "pos": 30},
    "CAR": {"name": "Ed Carpenter", "team": "Ed Carpenter Racing", "number": 33, "pos": 31},
    "ILO": {"name": "Callum Ilott", "team": "PREMA Racing", "number": 90, "pos": 32},
    "SHW": {"name": "Robert Shwartzman", "team": "PREMA Racing", "number": 83, "pos": 33},
}

# --- ХЕНДЛЕРЫ КОМАНД ---
@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Привет! Я INDY Leader.\n\nИспользуй /help для списка команд.")

@bot.message_handler(commands=['help'])
def help_command(message):
    text = """📋 Доступные команды:
/start — приветствие
/help — список команд
/indycar — календарь + топ‑5
/info <код> — информация о гонщике
/drivers — список всех кодов
/winner <год> — победитель Indy 500 за указанный год
/indy500 <год> — то же самое, что /winner
/youinindy — случайный пилот"""
    bot.reply_to(message, text)

@bot.message_handler(commands=['indycar'])
def indycar(message):
    bot.reply_to(message, "🏁 Собираю данные IndyCar...")
    try:
        url_cal = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard"
        resp = requests.get(url_cal, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        top5 = sorted(
            [d for d in DRIVERS.values() if d.get("pos") and d["pos"] <= 5],
            key=lambda x: x["pos"]
        )

        lines = ["🏁 **Чемпионат IndyCar 2026**", "📊 **Топ‑5 пилотов**", ""]
        for d in top5:
            lines.append(f"{d['pos']}. {d['name']} — {d['team']}")
        lines.append("")

        calendar = data.get('leagues', [{}])[0].get('calendar', [])
        if calendar:
            lines.append("📅 **Ближайшие гонки**")
            for event in calendar[:3]:
                label = event.get('label', 'Неизвестно')
                start_date = event.get('startDate', '')
                date_str = start_date[:10] if start_date else ''
                lines.append(f"• {label} — {date_str}" if date_str else f"• {label}")

        bot.reply_to(message, "\n".join(lines))
    except requests.exceptions.RequestException:
        bot.reply_to(message, "⚠️ Не удалось загрузить календарь. Попробуй позже.")
    except Exception:
        bot.reply_to(message, "⚠️ Ошибка при обработке данных.")

@bot.message_handler(commands=['info'])
def driver_info(message):
    try:
        code = message.text.split()[1].upper()
    except IndexError:
        bot.reply_to(message, "❌ Укажи код гонщика. Например: /info PAL")
        return

    d = DRIVERS.get(code)
    if not d:
        bot.reply_to(message, f"❌ Гонщик с кодом {code} не найден.")
        return

    text = f"🏎️ <b>{d['name']}</b>\n"
    text += f"🏁 Команда: {d['team']}\n"
    text += f"🔢 Номер: {d['number']}\n"
    text += f"📊 Позиция в чемпионате: {d.get('pos', '—')}"
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['drivers'])
def list_drivers(message):
    text = "🏁 <b>Список кодов гонщиков</b>\n\n"
    for code, d in DRIVERS.items():
        text += f"{code} — {d['name']}\n"
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['winner', 'indy500'])
def get_winner(message):
    try:
        year = int(message.text.split()[1])
    except (IndexError, ValueError):
        bot.reply_to(message, "❌ Укажи год. Например: /winner 2020")
        return

    for entry in winners:
        if entry.get("year") == year:
            driver = entry.get("driver", "Неизвестно")
            text = f"🏆 **Indy 500 {year}**\n"
            text += f"🏁 Победитель: {driver}"
            bot.reply_to(message, text, parse_mode="Markdown")
            return

    bot.reply_to(message, f"❌ Нет данных о победителе за {year} год.")

@bot.message_handler(commands=['youinindy'])
def random_driver(message):
    code, d = random.choice(list(DRIVERS.items()))
    text = f"🎲 Тебе выпал:\n\n🏎️ <b>{d['name']}</b>\n"
    text += f"🏁 Команда: {d['team']}\n"
    text += f"🔢 Номер: {d['number']}"
    bot.reply_to(message, text, parse_mode="HTML")

# --- WEBHOOK ЭНДПОИНТЫ ---
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = telebot.types.Update.de_json(data)
    bot.process_new_updates([update])
    return Response(content="OK", status_code=200)

@app.get("/")
def root():
    return {"status": "INDY Leader is running", "webhook_url": WEBHOOK_URL}

# --- УСТАНОВКА ВЕБХУКА ---
def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

# --- ЗАПУСК ---
if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
