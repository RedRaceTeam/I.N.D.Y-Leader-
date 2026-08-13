import os
import telebot
import requests
import random
import uvicorn
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
    buttons = [
        InlineKeyboardButton("🏁 Топ-5 и календарь", callback_data="indycar"),
        InlineKeyboardButton("🏎️ Инфо о гонщике", callback_data="info_list"),
        InlineKeyboardButton("🏆 Победители Indy 500", callback_data="winner_prompt"),
        InlineKeyboardButton("🎲 Случайный пилот", callback_data="random_driver"),
        InlineKeyboardButton("❤️ Поддержать проект", callback_data="donate"),
        InlineKeyboardButton("ℹ️ О проекте", callback_data="about")
    ]
    markup.add(*buttons)
    return markup

def back_to_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад в меню", callback_data="menu"))
    return markup

def drivers_list():
    markup = InlineKeyboardMarkup(row_width=2)
    for code, d in DRIVERS.items():
        markup.add(InlineKeyboardButton(f"{code} - {d['name']}", callback_data=f"driver_{code}"))
    return markup

# ===== ОБРАБОТЧИКИ =====
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "🏁 **I.N.D.Y Leader**\n\nЯ бот для фанатов IndyCar.\n\nВыбери действие:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    # Сброс всех ожиданий
    bot.clear_step_handler(call.message)

    # Кнопка "Назад"
    if call.data == "menu":
        bot.edit_message_text(
            "🏁 **Главное меню**",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=main_menu(),
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
        return

    # Топ-5
    if call.data == "indycar":
        bot.edit_message_text(
            "⏳ Загрузка...",
            call.message.chat.id,
            call.message.message_id
        )
        try:
            resp = requests.get("https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard", timeout=10)
            data = resp.json()
            top5 = sorted([d for d in DRIVERS.values() if d.get("pos") and d["pos"] <= 5], key=lambda x: x["pos"])
            lines = ["🏁 **Чемпионат IndyCar 2026**", "📊 **Топ-5**", ""]
            for d in top5:
                lines.append(f"{d['pos']}. {d['name']} — {d['team']}")
            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        except:
            bot.edit_message_text(
                "⚠️ Ошибка загрузки",
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu()
            )
        bot.answer_callback_query(call.id)
        return

    # Список гонщиков
    if call.data == "info_list":
        bot.edit_message_text(
            "Выбери гонщика:",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=drivers_list()
        )
        bot.answer_callback_query(call.id)
        return

    # Инфа о гонщике
    if call.data.startswith("driver_"):
        code = call.data.replace("driver_", "")
        d = DRIVERS.get(code)
        if not d:
            bot.answer_callback_query(call.id, "Не найден")
            return
        text = f"🏎️ {d['name']}\n🏁 {d['team']}\n🔢 #{d['number']}\n📊 {d.get('pos', '—')}"
        if d.get('image'):
            try:
                bot.send_photo(
                    call.message.chat.id,
                    d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return

    # Запрос года
    if call.data == "winner_prompt":
        bot.edit_message_text(
            "📅 Введи год (например, 2023):",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu()
        )
        bot.register_next_step_handler(call.message, handle_winner_year)
        bot.answer_callback_query(call.id)
        return

    # Случайный пилот
    if call.data == "random_driver":
        code, d = random.choice(list(DRIVERS.items()))
        text = f"🎲 {d['name']}\n🏁 {d['team']}\n🔢 #{d['number']}"
        if d.get('image'):
            try:
                bot.send_photo(
                    call.message.chat.id,
                    d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                bot.edit_message_text(
                    text,
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.edit_message_text(
                text,
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return

    # Донат
    if call.data == "donate":
        bot.edit_message_text(
            "❤️ Поддержать проект: [DonationAlerts](https://www.donationalerts.com/r/kimi_redrace)",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
        bot.answer_callback_query(call.id)
        return

    # О проекте
    if call.data == "about":
        bot.edit_message_text(
            "📘 Бот для фанатов IndyCar. Не связан с IndyCar Series.\nРазработка: @RedRaceF1, @Gabriella1488",
            call.message.chat.id,
            call.message.message_id,
            reply_markup=back_to_menu(),
            parse_mode="Markdown"
        )
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id, "Неизвестно")

def handle_winner_year(message):
    if message.text.lower() in ["назад", "меню", "/start"]:
        start(message)
        bot.clear_step_handler(message)
        return
    try:
        year = int(message.text.strip())
    except:
        bot.send_message(message.chat.id, "❌ Это не год.", reply_markup=back_to_menu())
        bot.clear_step_handler(message)
        return
    for entry in winners:
        if entry.get("year") == year:
            bot.send_message(
                message.chat.id,
                f"🏆 {year}: {entry.get('driver', 'Неизвестно')}",
                reply_markup=back_to_menu()
            )
            bot.clear_step_handler(message)
            return
    bot.send_message(message.chat.id, f"❌ Нет данных за {year}.", reply_markup=back_to_menu())
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
    return {"status": "INDY Leader is running"}

def set_webhook():
    bot.remove_webhook()
    bot.set_webhook(url=WEBHOOK_URL)
    print("✅ Webhook установлен")

if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
