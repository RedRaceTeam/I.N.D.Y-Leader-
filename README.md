# 🏁 Turbo Train — I.N.D.Y Leader

**Turbo Train** is a Telegram bot for IndyCar fans.  
Fast access to driver info, championship standings, calendar, and Indy 500 winners.  
Built for fans who want to know everything about their favorite drivers.

---

## 🚀 Quick Start

### 1. Run locally

```bash
# Clone the repo
git clone https://github.com/RedRaceTeam/I.N.D.Y-Leader.git
cd I.N.D.Y-Leader

# Install dependencies
pip install -r requirements.txt

# Create .env with your bot token
echo "BOT_TOKEN=your_token_from_BotFather" > .env

# Run the bot (uses polling for local testing)
python bot.py
```

For production on Render, the bot uses Webhook (FastAPI + uvicorn).
Environment variables must be set in the Render dashboard.

---

📋 Bot Commands

Command Description
/start Welcome message
/help List of all commands
/indycar Top 5 drivers + upcoming races
/info <code> Driver info by IDC code (e.g. /info PAL)
/drivers List of all available IDC codes
/winner <year> Indy 500 winner for a specific year
/indy500 <year> Same as /winner
/youinindy Random driver from the championship

---

📁 Project Structure

```
I.N.D.Y-Leader/
├── bot.py              # Main logic (FastAPI + Webhook)
├── data/
│   └── winners.py      # Full Indy 500 winners list (1911–2026)
├── requirements.txt    # Dependencies
├── README.md           # This file
└── .env                # Environment variables (not in repo)
```

---

🛠️ Tech Stack

· Python 3.11+
· pyTelegramBotAPI — Telegram Bot API wrapper
· FastAPI + uvicorn — Web server for Webhook (production)
· requests — HTTP requests to ESPN API
· python-dotenv — Environment variables

---

📦 Dependencies

```
pyTelegramBotAPI==4.15.2
requests==2.31.0
fastapi==0.109.0
uvicorn[standard]==0.27.0
python-dotenv==1.0.0
```

---

🧠 How It Works

Driver Data

Driver info is stored in the DRIVERS dictionary inside bot.py.
Each driver has a unique 3-letter IDC code.

```python
DRIVERS = {
    "PAL": {"name": "Alex Palou", "team": "Chip Ganassi Racing", "number": 10, "pos": 1},
    # ...
}
```

Indy 500 Winners

Winner data from 1911 to 2026 is stored in data/winners.py as a list of dictionaries.

```python
winners = [
    {"year": 2020, "driver": "Takuma Sato"},
    # ...
]
```

Webhook (Production)

Instead of bot.polling(), the bot uses FastAPI + Webhook.
Telegram sends updates to the /webhook endpoint.

```python
@app.post("/webhook")
async def webhook(request: Request):
    data = await request.json()
    update = telebot.types.Update.de_json(data)
    bot.process_new_updates([update])
    return Response(content="OK", status_code=200)
```

---

🚀 Deploy on Render

1. Push your code to GitHub.
2. In Render, create a Web Service and connect your repo.
3. Set environment variables:
   · BOT_TOKEN — your bot token
   · WEBHOOK_URL — https://your-service.onrender.com/webhook
   · PORT — 8000 (Render sets it automatically)
4. Render will install dependencies from requirements.txt and start the bot.

---

🤝 How to Contribute

1. Fork the repo.
2. Create a branch (git checkout -b feature/amazing-thing).
3. Commit your changes (git commit -m 'Add amazing thing').
4. Push to the branch (git push origin feature/amazing-thing).
5. Open a Pull Request to main.

---

📌 Roadmap

☑ /winner and /indy500 commands with full Indy 500 history
☑ Webhook for stable operation on Render
☐ Connect to OpenF1 API for real-time data
☐ Auto-update championship standings
☐ /race — next race info
☐ Database for user settings

---

📄 License

MIT © P4/9

---

🙌 Authors

· Gabriella88 — idea, project lead, Indy 500 winners data, testing
· P4/9 (Kimi) — development, architecture, webhook implementating

## 💖 Support the Project

If you like Turbo Train and want to support its development:

[![Donate on DonationAlerts](https://img.shields.io/badge/Donate-DonationAlerts-ff69b4?style=for-the-badge&logo=heart)](https://www.donationalerts.com/r/kimi_redrace)

Every donation helps keep the bot running and motivates us to add new features. 🏁
