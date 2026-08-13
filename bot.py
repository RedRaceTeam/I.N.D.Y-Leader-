import os
import telebot
import requests
import random
import uvicorn
import threading
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, Request, Response
from data.winners import winners
from data.drivers import DRIVERS

TOKEN = os.getenv("BOT_TOKEN")
if not TOKEN:
    raise ValueError("BOT_TOKEN не найден")

WEBHOOK_URL = "https://turbo-train-2b9d.onrender.com/webhook"

bot = telebot.TeleBot(TOKEN)
app = FastAPI()

# ===== КЛАВИАТУРЫ =====
def main_menu():
    markup = InlineKeyboardMarkup(row_width=2)
    markup.add(
        InlineKeyboardButton("🏁 Топ-5 и календарь", callback_data="indycar"),
        InlineKeyboardButton("🏎️ Инфо о гонщике", callback_data="info_list"),
        InlineKeyboardButton("🏆 Победители Indy 500", callback_data="winner_prompt"),
        InlineKeyboardButton("🎲 Случайный пилот", callback_data="random_driver"),
        InlineKeyboardButton("❤️ Поддержать проект", callback_data="donate"),
        InlineKeyboardButton("ℹ️ О проекте", callback_data="about")
    )
    return markup

def drivers_list_keyboard():
    markup = InlineKeyboardMarkup(row_width=2)
    buttons = []
    for code, d in DRIVERS.items():
        buttons.append(InlineKeyboardButton(f"{code} - {d['name']}", callback_data=f"driver_{code}"))
    markup.add(*buttons)
    return markup

def back_to_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад в меню", callback_data="menu"))
    return markup

# ===== ОБРАБОТЧИКИ =====
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        chat_id=message.chat.id,
        text="🏁 **I.N.D.Y Leader**\n\n"
             "Я бот для фанатов IndyCar. Что хочешь узнать?\n\n"
             "• Топ-5 чемпионата и календарь\n"
             "• Информацию о любом гонщике\n"
             "• Победителей Indy 500 по годам\n"
             "• Случайного пилота\n\n"
             "Выбирай кнопку ниже 👇",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    # Проверка на пустое сообщение
    if call.message is None:
        bot.answer_callback_query(callback_query_id=call.id, text="Сообщение не найдено")
        return

    # Сброс всех ожиданий
    bot.clear_step_handler(call.message)

    # === КНОПКА НАЗАД ===
    if call.data == "menu":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🏁 **I.N.D.Y Leader**\n\nГлавное меню. Что хочешь узнать?",
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === ТОП-5 ===
    if call.data == "indycar":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="🏁 Собираю данные IndyCar..."
        )
        try:
            url_cal = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard"
            resp = requests.get(url_cal, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            top5 = sorted(
                [d for d in DRIVERS.values() if d.get("pos") and d["pos"] <= 5],
                key=lambda x: x["pos"]
            )

            lines = ["🏁 **Чемпионат IndyCar 2026**", "📊 **Топ-5 пилотов**", ""]
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

            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="\n".join(lines),
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        except Exception as e:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text="⚠️ Ошибка при загрузке данных. Возврат в меню.",
                reply_markup=main_menu()
            )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === СПИСОК ГОНЩИКОВ ===
    if call.data == "info_list":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="Выбери гонщика:",
            reply_markup=drivers_list_keyboard()
        )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === ИНФА О ГОНЩИКЕ ===
    if call.data.startswith("driver_"):
        code = call.data.replace("driver_", "")
        d = DRIVERS.get(code)
        if not d:
            bot.answer_callback_query(callback_query_id=call.id, text="Гонщик не найден")
            return

        text = f"🏎️ **{d['name']}**\n🏁 Команда: {d['team']}\n🔢 Номер: {d['number']}\n📊 Позиция: {d.get('pos', '—')}"

        if d.get('image'):
            try:
                bot.send_photo(
                    chat_id=call.message.chat.id,
                    photo=d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
                bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            except Exception:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === ЗАПРОС ГОДА ===
    if call.data == "winner_prompt":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="📅 **Введи год** (например, 2023):\n\nЯ покажу победителя Indy 500 за этот год.",
            reply_markup=back_to_menu()
        )
        bot.register_next_step_handler(call.message, handle_winner_year)
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === СЛУЧАЙНЫЙ ПИЛОТ ===
    if call.data == "random_driver":
        code, d = random.choice(list(DRIVERS.items()))
        text = f"🎲 **Тебе выпал:**\n\n🏎️ {d['name']}\n🏁 Команда: {d['team']}\n🔢 Номер: {d['number']}"

        if d.get('image'):
            try:
                bot.send_photo(
                    chat_id=call.message.chat.id,
                    photo=d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
                bot.delete_message(chat_id=call.message.chat.id, message_id=call.message.message_id)
            except Exception:
                bot.edit_message_text(
                    chat_id=call.message.chat.id,
                    message_id=call.message.message_id,
                    text=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.edit_message_text(
                chat_id=call.message.chat.id,
                message_id=call.message.message_id,
                text=text,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === ДОНАТ ===
    if call.data == "donate":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="❤️ **Поддержать проект**\n\n"
                 "Если тебе нравится I.N.D.Y Leader — ты можешь поддержать развитие проекта.\n\n"
                 "💰 DonationAlerts: [тык сюда](https://www.donationalerts.com/r/kimi_redrace)\n\n"
                 "Спасибо, что ты с нами 🏁",
            reply_markup=back_to_menu(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === О ПРОЕКТЕ ===
    if call.data == "about":
        bot.edit_message_text(
            chat_id=call.message.chat.id,
            message_id=call.message.message_id,
            text="📘 **О проекте I.N.D.Y Leader**\n\n"
                 "Это неофициальный Telegram-бот для фанатов IndyCar.\n"
                 "Мы используем открытые данные и официальные фото пилотов.\n\n"
                 "Проект создан для популяризации автоспорта.\n"
                 "Не связан с IndyCar Series, LLC.\n\n"
                 "🔗 Исходный код: [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                 "🧑‍💻 Разработка: @RedRaceF1, @Gabriella1488\n"
                 "💰 Поддержать: [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
            reply_markup=back_to_menu(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        bot.answer_callback_query(callback_query_id=call.id)
        return

    # === НЕИЗВЕСТНАЯ КНОПКА ===
    bot.answer_callback_query(callback_query_id=call.id, text="Неизвестная команда")

def handle_winner_year(message):
    # Если пользователь ввел "назад" или "меню"
    if message.text.lower() in ["назад", "меню", "/start"]:
        start(message)
        bot.clear_step_handler(message)
        return

    try:
        year = int(message.text.strip())
    except ValueError:
        bot.send_message(
            chat_id=message.chat.id,
            text="❌ Это не похоже на год. Попробуй ещё раз.",
            reply_markup=back_to_menu()
        )
        bot.clear_step_handler(message)
        return

    for entry in winners:
        if entry.get("year") == year:
            driver = entry.get("driver", "Неизвестно")
            bot.send_message(
                chat_id=message.chat.id,
                text=f"🏆 **Indy 500 {year}**\n🏁 Победитель: {driver}",
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
            bot.clear_step_handler(message)
            return

    bot.send_message(
        chat_id=message.chat.id,
        text=f"❌ Нет данных о победителе за {year} год.",
        reply_markup=back_to_menu()
    )
    bot.clear_step_handler(message)

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
    print(f"✅ Webhook установлен: {WEBHOOK_URL}")

if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
