# 🏁 I.N.D.Y Leader — Your IndyCar Assistant

[![Try the Bot](https://img.shields.io/badge/🤖_Try_Bot-2CA5E0?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/Opandksksk_bot)
[![Source Code](https://img.shields.io/badge/📂_Source_Code-181717?style=for-the-badge&logo=github&logoColor=white)](https://github.com/RedRaceTeam/I.N.D.Y-Leader)
[![Donate](https://img.shields.io/badge/❤️_Donate-ff69b4?style=for-the-badge&logo=heart&logoColor=white)](https://www.donationalerts.com/r/kimi_redrace)
[![P4/9](https://img.shields.io/badge/P4/9-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/P4Devl)
[![Gabriella](https://img.shields.io/badge/Gabriella_Projects-26A5E4?style=for-the-badge&logo=telegram&logoColor=white)](https://t.me/GabriellaProjekts)

---

**I.N.D.Y Leader** is a Telegram bot for IndyCar fans.  
Fast access to driver info, standings, calendar, winners, and AI-powered answers.

---

## ⚡️ Features

| Command / Button | Description |
|------------------|-------------|
| 🏁 **Top 5 & Calendar** | Live championship standings and upcoming races |
| 🏎️ **Driver Info** | Team, number, position, and official photo |
| 🏆 **Indy 500 Winners** | Full history from 1911 to 2026 |
| 🎲 **Random Driver** | Get a random driver to root for |
| 🧠 **Ask Nico** | AI expert on IndyCar (powered by Groq) |
| 🎯 **Admin Panel** | Stats, users, and command analytics |

---

## 🛠️ Tech Stack

| Component | Technology |
|-----------|------------|
| Language | Python 3.11+ |
| Bot Framework | pyTelegramBotAPI |
| Web Server | FastAPI + Uvicorn |
| AI | Gemini (Gemini 3,6 ) |
| Database | SQLite (users, stats, history) |
| RAG | LangChain + FAISS |
| Deployment | Render |
| Webhook | Telegram Bot API |

---

## 📁 Project Structure

```

I.N.D.Y-Leader/
├── bot.py              # Main bot logic (FastAPI + Webhook)
├── data/
│   ├── drivers.py      # 33 drivers with photos
│   ├── winners.py      # Indy 500 winners (1911–2026)
│   └── knowledge.txt   # Base knowledge for Nico AI
├── requirements.txt
├── Dockerfile
├── README.md
└── .env

```

---

## 📌 Links

| Link | URL |
|------|-----|
| 🤖 **Try Bot** | [t.me/Opandksksk_bot](https://t.me/Opandksksk_bot) |
| 📂 **GitHub** | [github.com/RedRaceTeam/I.N.D.Y-Leader](https://github.com/RedRaceTeam/I.N.D.Y-Leader) |
| ❤️ **Donate** | [donationalerts.com/r/kimi_redrace](https://www.donationalerts.com/r/kimi_redrace) |
| 📱 **P4/9 Channel** | [t.me/P4Devl](https://t.me/P4Devl) |
| 📱 **Gabriella Projects** | [t.me/GabriellaProjekts](https://t.me/GabriellaProjekts) |

---

## 🤖 AI — Nico

**Nico** is an AI expert on IndyCar, powered by **Gemini (Gemini 3,6)**.

It works as a **RAG (Retrieval-Augmented Generation)** system:
1. Searches for relevant info in the knowledge base.
2. Uses context from previous messages (last 50).
3. Generates accurate, human-like responses.

---

## 👑 Admin Panel

Only accessible to authorized admins via `/admin`.

Features:
- 📊 **Stats** — total users and commands.
- 👥 **Users** — last 20 active users.
- 📈 **Commands** — most used commands.

---

## 🚀 Quick Start

### 1. Run Locally


# Clone the repo
git clone https://github.com/RedRaceTeam/I.N.D.Y-Leader.git
cd I.N.D.Y-Leader

# Install dependencies
pip install -r requirements.txt

# Create .env file
echo "BOT_TOKEN=your_token_from_BotFather" > .env
echo "GROQ_API_KEY=your_groq_api_key" >> .env

# Run (polling mode for local testing)
python bot.py


2. Deploy on Render

1. Push code to GitHub.
2. Create a Web Service on Render.
3. Set environment variables:
   · BOT_TOKEN
   · GROQ_API_KEY
   · WEBHOOK_URL (your Render URL)
4. Deploy and enjoy.

---

🧑‍💻 Authors


Gabriella88 Idea, project lead, data, testing @Gabriella1488

P4/9 (Kimi) Development, architecture, AI, webhook @Scanialove

---

❤️ Support the Project

If you like I.N.D.Y Leader, you can support its development:

https://img.shields.io/badge/Donate_on_DonationAlerts-ff69b4?style=for-the-badge&logo=heart&logoColor=white

---

📄 License

This project is licensed under the MIT License.
 
