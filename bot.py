import os
import telebot
import requests

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден")

bot = telebot.TeleBot(TOKEN)

DRIVERS = {
    "PAL": {"name": "Alex Palou", "team": "Chip Ganassi Racing", "number": 10},
    "MCL": {"name": "Scott McLaughlin", "team": "Team Penske", "number": 3},
    "MAL": {"name": "David Malukas", "team": "Meyer Shank Racing", "number": 6},
    "KIR": {"name": "Kyle Kirkwood", "team": "Andretti Global", "number": 27},
    "LUN": {"name": "Christian Lundgaard", "team": "Arrow McLaren", "number": 7},
    "OWA": {"name": "Pato O'Ward", "team": "Arrow McLaren", "number": 5},
    "CAS": {"name": "Helio Castroneves", "team": "Meyer Shank Racing", "number": 6},
    "COL": {"name": "Caio Collet", "team": "A.J. Foyt Enterprises", "number": 4},
    "ARM": {"name": "Marcus Armstrong", "team": "Meyer Shank Racing", "number": 66},
    "RAS": {"name": "Christian Rasmussen", "team": "Ed Carpenter Racing", "number": 21},
    "SHW": {"name": "Robert Shwartzman", "team": "PREMA Racing", "number": 83},
    "SIE": {"name": "Nolan Siegel", "team": "Arrow McLaren", "number": 6},
    "SIM": {"name": "Kyffin Simpson", "team": "Chip Ganassi Racing", "number": 8},
    "FOS": {"name": "Louis Foster", "team": "Rahal Letterman Lanigan Racing", "number": 45},
    "HAU": {"name": "Dennis Hauger", "team": "Dale Coyne Racing", "number": 19},
    "VEE": {"name": "Rinus VeeKay", "team": "Juncos Hollinger Racing", "number": 76},
    "ERI": {"name": "Marcus Ericsson", "team": "Andretti Global", "number": 28},
    "RAH": {"name": "Graham Rahal", "team": "Rahal Letterman Lanigan Racing", "number": 15},
    "GRO": {"name": "Romain Grosjean", "team": "Dale Coyne Racing", "number": 18},
    "POW": {"name": "Will Power", "team": "Andretti Global", "number": 26},
    "DIX": {"name": "Scott Dixon", "team": "Chip Ganassi Racing", "number": 9},
    "ROS": {"name": "Felix Rosenqvist", "team": "Meyer Shank Racing", "number": 60},
    "ROSS": {"name": "Alexander Rossi", "team": "Ed Carpenter Racing", "number": 20},
    "NEW": {"name": "Josef Newgarden", "team": "Team Penske", "number": 2},
    "ILO": {"name": "Callum Ilott", "team": "PREMA Racing", "number": 90},
    "SCH": {"name": "Mick Schumacher", "team": "Rahal Letterman Lanigan Racing", "number": 47},
}

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "👋 Привет! Я INDY Leader.\n\nИспользуй /help для списка команд.")

@bot.message_handler(commands=['help'])
def help_command(message):
    text = """📋 Доступные команды:
/start — приветствие
/help — список команд
/indycar — топ-5 гонщиков
/info <код> — информация о гонщике
/drivers — список всех кодов"""
    bot.reply_to(message, text)

@bot.message_handler(commands=['indycar'])
def indycar(message):
    bot.reply_to(message, "🏁 Собираю данные IndyCar...")

    try:
        # Используем ESPN API для получения актуальных данных IndyCar
        url = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard"
        response = requests.get(url, timeout=10)
        response.raise_for_status()
        data = response.json()

        # Извлекаем сезон и календарь
        season = data.get('leagues', [{}])[0].get('season', {})
        year = season.get('year', '2026')

        # Формируем список гонок из календаря
        calendar = data.get('leagues', [{}])[0].get('calendar', [])
        if not calendar:
            bot.reply_to(message, "❌ Данных о гонках пока нет.")
            return

        text = f"🏁 **IndyCar {year}**\n"
        text += "📅 **Календарь гонок**\n\n"
        for event in calendar[:5]:
            label = event.get('label', 'Неизвестно')
            start_date = event.get('startDate', '')
            if start_date:
                date_str = start_date[:10]  # Берем только дату
                text += f"• {label} — {date_str}\n"
            else:
                text += f"• {label}\n"

        bot.reply_to(message, text)

    except Exception as e:
        bot.reply_to(message, f"⚠️ Ошибка: {e}")

@bot.message_handler(commands=['info'])
def driver_info(message):
    try:
        code = message.text.split()[1].upper()
    except IndexError:
        bot.reply_to(message, "❌ Укажи код гонщика. Например: /info PAL")
        return

    driver = DRIVERS.get(code)
    if not driver:
        bot.reply_to(message, f"❌ Гонщик с кодом {code} не найден.")
        return

    text = f"🏎️ <b>{driver['name']}</b>\n"
    text += f"🏁 Команда: {driver['team']}\n"
    text += f"🔢 Номер: {driver['number']}"
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(commands=['drivers'])
def list_drivers(message):
    text = "🏁 <b>Список кодов гонщиков</b>\n\n"
    for code, data in DRIVERS.items():
        text += f"{code} — {data['name']}\n"
    bot.reply_to(message, text, parse_mode="HTML")

@bot.message_handler(func=lambda m: True)
def echo_all(message):
    bot.reply_to(message, "❓ Неизвестна команда. Используй /help.")

print("🤖 INDY Leader запущен!")
bot.polling()
