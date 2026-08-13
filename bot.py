import os
import telebot
import requests
import random
import uvicorn
import logging
from telebot.types import InlineKeyboardMarkup, InlineKeyboardButton
from fastapi import FastAPI, Request, Response
from data.winners import winners
from data.drivers import DRIVERS

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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

def back_to_menu():
    markup = InlineKeyboardMarkup()
    markup.add(InlineKeyboardButton("🔙 Назад в меню", callback_data="menu"))
    return markup

def drivers_list():
    markup = InlineKeyboardMarkup(row_width=2)
    if not DRIVERS:
        markup.add(InlineKeyboardButton("⚠️ Нет данных", callback_data="menu"))
        return markup
    for code, d in DRIVERS.items():
        markup.add(InlineKeyboardButton(f"{code} - {d['name']}", callback_data=f"driver_{code}"))
    return markup

# ===== ОБРАБОТЧИКИ =====
@bot.message_handler(commands=['start'])
def start(message):
    bot.send_message(
        message.chat.id,
        "🏁 **I.N.D.Y Leader**\n\n"
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
    if call.message is None:
        bot.answer_callback_query(call.id, "Сообщение удалено")
        return

    try:
        bot.clear_step_handler(call.message)
    except Exception as e:
        logger.error(f"Clear step handler error: {e}")

    # === НАЗАД (исправлено) ===
    if call.data == "menu":
        try:
            if call.message.text:
                bot.edit_message_text(
                    "🏁 **Главное меню**",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=main_menu(),
                    parse_mode="Markdown"
                )
            else:
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(
                    call.message.chat.id,
                    "🏁 **Главное меню**",
                    reply_markup=main_menu(),
                    parse_mode="Markdown"
                )
        except Exception as e:
            logger.error(f"Menu edit error: {e}")
            try:
                bot.delete_message(call.message.chat.id, call.message.message_id)
            except:
                pass
            bot.send_message(
                call.message.chat.id,
                "🏁 **Главное меню**",
                reply_markup=main_menu(),
                parse_mode="Markdown"
            )
        bot.answer_callback_query(call.id)
        return

    # === ТОП-5 И КАЛЕНДАРЬ ===
    if call.data == "indycar":
        try:
            if call.message.text:
                bot.edit_message_text(
                    "⏳ Загрузка данных...",
                    call.message.chat.id,
                    call.message.message_id
                )
            else:
                bot.delete_message(call.message.chat.id, call.message.message_id)
                msg = bot.send_message(
                    call.message.chat.id,
                    "⏳ Загрузка данных..."
                )
                # Сохраняем новое сообщение для редактирования
                call.message.message_id = msg.message_id
        except Exception as e:
            logger.error(f"Loading edit error: {e}")
        
        try:
            url_cal = "https://site.api.espn.com/apis/site/v2/sports/racing/irl/scoreboard"
            resp = requests.get(url_cal, timeout=10)
            resp.raise_for_status()
            data = resp.json()

            top5 = sorted(
                [d for d in DRIVERS.values() if d.get("pos") and d["pos"] <= 5],
                key=lambda x: x["pos"]
            )

            lines = ["🏁 **Топ-5 пилотов**", ""]
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
            else:
                lines.append("📅 Нет ближайших гонок")

            bot.edit_message_text(
                "\n".join(lines),
                call.message.chat.id,
                call.message.message_id,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"ESPN error: {e}")
            try:
                bot.edit_message_text(
                    "⚠️ Ошибка загрузки календаря. Попробуй позже.",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu()
                )
            except Exception as e2:
                logger.error(f"Error message edit error: {e2}")
        
        bot.answer_callback_query(call.id)
        return

    # === СПИСОК ГОНЩИКОВ ===
    if call.data == "info_list":
        try:
            if not DRIVERS:
                if call.message.text:
                    bot.edit_message_text(
                        "⚠️ Нет данных о гонщиках",
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=back_to_menu()
                    )
                else:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(
                        call.message.chat.id,
                        "⚠️ Нет данных о гонщиках",
                        reply_markup=back_to_menu()
                    )
            else:
                if call.message.text:
                    bot.edit_message_text(
                        "Выбери гонщика:",
                        call.message.chat.id,
                        call.message.message_id,
                        reply_markup=drivers_list()
                    )
                else:
                    bot.delete_message(call.message.chat.id, call.message.message_id)
                    bot.send_message(
                        call.message.chat.id,
                        "Выбери гонщика:",
                        reply_markup=drivers_list()
                    )
        except Exception as e:
            logger.error(f"Info list edit error: {e}")
        bot.answer_callback_query(call.id)
        return

    # === ИНФА О ГОНЩИКЕ ===
    if call.data.startswith("driver_"):
        code = call.data.replace("driver_", "")
        d = DRIVERS.get(code)
        if not d:
            bot.answer_callback_query(call.id, "Гонщик не найден")
            return
        
        text = f"🏎️ **{d['name']}**\n🏁 {d['team']}\n🔢 #{d['number']}\n📊 {d.get('pos', '—')}"
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as e:
            logger.error(f"Delete message error: {e}")
        
        if d.get('image'):
            try:
                bot.send_photo(
                    call.message.chat.id,
                    d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Send photo error: {e}")
                bot.send_message(
                    call.message.chat.id,
                    text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.send_message(
                call.message.chat.id,
                text,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        
        bot.answer_callback_query(call.id)
        return

    # === ЗАПРОС ГОДА ===
    if call.data == "winner_prompt":
        try:
            if call.message.text:
                bot.edit_message_text(
                    "📅 **Введи год** (например, 2023):",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu()
                )
            else:
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(
                    call.message.chat.id,
                    "📅 **Введи год** (например, 2023):",
                    reply_markup=back_to_menu()
                )
            bot.register_next_step_handler(call.message, handle_winner_year)
        except Exception as e:
            logger.error(f"Winner prompt edit error: {e}")
        bot.answer_callback_query(call.id)
        return

    # === СЛУЧАЙНЫЙ ПИЛОТ ===
    if call.data == "random_driver":
        if not DRIVERS:
            bot.send_message(
                call.message.chat.id,
                "⚠️ Нет данных о гонщиках",
                reply_markup=back_to_menu()
            )
            bot.answer_callback_query(call.id)
            return
            
        code, d = random.choice(list(DRIVERS.items()))
        text = f"🎲 **{d['name']}**\n🏁 {d['team']}\n🔢 #{d['number']}"
        
        try:
            bot.delete_message(call.message.chat.id, call.message.message_id)
        except Exception as e:
            logger.error(f"Delete message error: {e}")
        
        if d.get('image'):
            try:
                bot.send_photo(
                    call.message.chat.id,
                    d['image'],
                    caption=text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Random photo error: {e}")
                bot.send_message(
                    call.message.chat.id,
                    text,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown"
                )
        else:
            bot.send_message(
                call.message.chat.id,
                text,
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
        
        bot.answer_callback_query(call.id)
        return

    # === ДОНАТ ===
    if call.data == "donate":
        try:
            if call.message.text:
                bot.edit_message_text(
                    "❤️ **Поддержать проект**\n\n"
                    "💰 DonationAlerts: [тык сюда](https://www.donationalerts.com/r/kimi_redrace)",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            else:
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(
                    call.message.chat.id,
                    "❤️ **Поддержать проект**\n\n"
                    "💰 DonationAlerts: [тык сюда](https://www.donationalerts.com/r/kimi_redrace)",
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
        except Exception as e:
            logger.error(f"Donate edit error: {e}")
        bot.answer_callback_query(call.id)
        return

    # === О ПРОЕКТЕ ===
    if call.data == "about":
        try:
            if call.message.text:
                bot.edit_message_text(
                    "📘 **О проекте**\n\n"
                    "Неофициальный бот для фанатов IndyCar.\n"
                    "Не связан с IndyCar Series, LLC.\n\n"
                    "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                    "🧑‍💻 @RedRaceF1, @Gabriella1488",
                    call.message.chat.id,
                    call.message.message_id,
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
            else:
                bot.delete_message(call.message.chat.id, call.message.message_id)
                bot.send_message(
                    call.message.chat.id,
                    "📘 **О проекте**\n\n"
                    "Неофициальный бот для фанатов IndyCar.\n"
                    "Не связан с IndyCar Series, LLC.\n\n"
                    "🔗 [GitHub](https://github.com/RedRaceTeam/I.N.D.Y-Leader)\n"
                    "🧑‍💻 @RedRaceF1, @Gabriella1488",
                    reply_markup=back_to_menu(),
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
        except Exception as e:
            logger.error(f"About edit error: {e}")
        bot.answer_callback_query(call.id)
        return

    bot.answer_callback_query(call.id, "Неизвестная команда")

def handle_winner_year(message):
    if message.text.lower() in ["назад", "меню", "/start"]:
        start(message)
        bot.clear_step_handler(message)
        return
    
    try:
        year = int(message.text.strip())
    except ValueError:
        bot.send_message(
            message.chat.id,
            "❌ Это не год. Попробуй ещё раз.",
            reply_markup=back_to_menu()
        )
        bot.clear_step_handler(message)
        return

    for entry in winners:
        if entry.get("year") == year:
            driver = entry.get("driver", "Неизвестно")
            bot.send_message(
                message.chat.id,
                f"🏆 **Indy 500 {year}**\n🏁 {driver}",
                reply_markup=back_to_menu(),
                parse_mode="Markdown"
            )
            bot.clear_step_handler(message)
            return

    bot.send_message(
        message.chat.id,
        f"❌ Нет данных за {year}.",
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
    print("✅ Webhook установлен")

if __name__ == "__main__":
    set_webhook()
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
