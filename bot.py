import os
import telebot
from apify_client import ApifyClient

# ========== ТОКЕНЫ ИЗ ПЕРЕМЕННЫХ ОКРУЖЕНИЯ ==========
TOKEN = os.getenv("BOT_TOKEN")
APIFY_TOKEN = os.getenv("APIFY_TOKEN")

if not TOKEN:
    raise ValueError("BOT_TOKEN не найден в переменных окружения")

bot = telebot.TeleBot(TOKEN)
client = ApifyClient(token=APIFY_TOKEN)

# ========== БАЗА ГОНЩИКОВ ==========
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

# ========== КОМАНДЫ ==========
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
        # ПРАВИЛЬНЫЙ ВЫЗОВ СОГЛАСНО ДОКУМЕНТАЦИИ APIFY
        run_input = {
            "season": "2026",
            "maxItems": 5
        }
        run = client.actor("parseforge/indycar-stats-scraper").call(run_input=run_input)

        dataset = client.dataset(run["defaultDatasetId"])
        items = dataset.list_items().items

        if not items:
            bot.reply_to(message, "❌ Данных пока нет.")
            return

        text = "🏁 **Топ-5 IndyCar**\n\n"
        for item in items[:5]:
            rank = item.get('rank', '—')
            driver = item.get('driver', 'Неизвестно')
            points = item.get('points', '—')
            text += f"{rank}. {driver} — {points} очков\n"

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

# ========== ЗАПУСК ==========
print("🤖 INDY Leader запущен!")
bot.polling()
