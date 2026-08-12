# 🏁 Turbo Train — I.N.D.Y Your Guide in Indycar

**Turbo Train** is a Telegram bot that helps you navigate the world of IndyCar.  
Fast access to driver info, championship standings, and results.  
Built for IndyCar fans who want to know everything about their favourite drivers.

---

## 🚀 Quick Start

### 1. Run it yourself

```bash
# Clone the repo
git clone https://github.com/RedRaceTeam/turbo-train.git
cd turbo-train

# Install dependencies
pip install -r requirements.txt

# Create a .env file with your bot token
echo "BOT_TOKEN=your_token_from_BotFather" > .env

# Run the bot
python bot.py
```

2. Usage

Add the bot to Telegram and try these commands:

```
/start       — Welcome message
/help        — List of commands
/indycar     — Top 5 drivers in the championship
/info <code> — Driver info by IDC (e.g. /info PAL)
/drivers     — List of all available IDC codes
```

---

📁 Project Structure

```
turbo-train/
├── bot.py              # Main bot logic
├── requirements.txt    # Dependencies
├── README.md           # You are here
└── .github/            # GitHub Actions (CI/CD)
```

---

🛠️ Tech Stack

· Python 3.11+
· pyTelegramBotAPI — Telegram Bot API wrapper
· requests — for future API calls
· python-dotenv — environment variables

---

📦 Dependencies

```
pyTelegramBotAPI==4.8.0
requests==2.31.0
python-dotenv==1.0.1
```

---

🧠 How It Works

Driver Data

Driver info is stored directly in the code as a Python dictionary (DRIVERS).
Each driver has a unique 3-letter IDC code (e.g., PAL for Alex Palou).

```python
DRIVERS = {
    "PAL": {"name": "Alex Palou", "team": "Chip Ganassi Racing", "number": 10},
    # ...
}
```

Commands

Each command is a function with a @bot.message_handler decorator.

```python
@bot.message_handler(commands=['info'])
def driver_info(message):
    # search by IDC code
```

---

🤝 How to Contribute

1. Fork the repo.
2. Create a branch (git checkout -b feature/amazing-feature).
3. Commit your changes (git commit -m 'Add something amazing').
4. Push to the branch (git push origin feature/amazing-feature).
5. Open a Pull Request.

---

📌 Roadmap

☐ Connect to OpenF1 API
☐ Auto-update championship standings
☐ /race — next race info
☐ /calendar — season schedule
☐ SQLite database for user settings

---

📄 License

MIT © P4/9

---

by P4/9 <3 · @RedRaceF1
Collaborators: @Gabriella88

[![Поддержать на DonationAlerts](https://img.shields.io/badge/Donate-DonationAlerts-ff69b4?style=for-the-badge&logo=heart)](https://www.donationalerts.com/r/kimi_redrace)
