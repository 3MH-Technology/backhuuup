# إستضافة (Estidafa) — Free Micro-SaaS Bot Hosting Platform

A professional, lightweight, and completely free hosting platform for **Arab developers** to host **Python Telegram Bots** and **PHP scripts**.

Built with **FastAPI** + **Tailwind CSS** (Arabic RTL support).

---

## System Architecture

```
┌─────────────────────────────────────────────────────┐
│                   Client Browser                     │
│  (Tailwind CSS Dashboard — RTL Arabic / English)     │
└──────────────────────┬──────────────────────────────┘
                       │ HTTP / WebSocket
                       ▼
┌─────────────────────────────────────────────────────┐
│              FastAPI Backend (Python)                 │
│  ┌──────────┐  ┌──────────┐  ┌──────────────────┐   │
│  │ Auth      │  │ Bot API  │  │ WebSocket        │   │
│  │ (JWT)     │  │ (CRUD)   │  │ (Log Streaming)  │   │
│  └──────────┘  └──────────┘  └──────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │         Process Manager & Security Layer      │   │
│  │  (subprocess + psutil + code scanner)         │   │
│  └──────────────────────────────────────────────┘   │
│  ┌──────────────────────────────────────────────┐   │
│  │              SQLite Database                   │   │
│  │  (Users, Bots metadata, JWT tokens)           │   │
│  └──────────────────────────────────────────────┘   │
└──────────────────────┬──────────────────────────────┘
                       │ Isolated subprocesses
                       ▼
┌─────────────────────────────────────────────────────┐
│              User Bot Processes                       │
│  ┌──────────────┐    ┌──────────────┐                │
│  │ Python Bot 1  │    │  PHP Bot 2   │    ...        │
│  │ (256MB max)   │    │  (256MB max) │                │
│  │ (50% CPU max) │    │  (50% CPU max)│               │
│  └──────────────┘    └──────────────┘                │
└─────────────────────────────────────────────────────┘
```

### How It Works

1. **User** registers/logs in through the RTL Arabic dashboard.
2. **User** creates a new bot, selects type (Python or PHP), pastes code + requirements.
3. **Backend** scans code for malicious patterns (security layer).
4. **Process Manager** starts an isolated subprocess with resource limits.
5. **WebSocket** streams real-time logs to the dashboard.
6. **User** can Start/Stop/Restart from the control panel.
7. **Resource monitoring** tracks CPU and RAM usage every 5 seconds.
8. **Automatic sleep** — idle/crashed bots are detected and freed.

---

## Directory Structure

```
micro-saas-hosting/
├── backend/
│   ├── main.py                  # FastAPI entry point
│   ├── config.py                # Settings & environment config
│   ├── requirements.txt         # Python dependencies
│   ├── .env                     # Environment variables
│   ├── models/
│   │   ├── __init__.py
│   │   ├── database.py          # SQLAlchemy async engine
│   │   ├── user.py              # User model
│   │   └── bot.py               # Bot model
│   ├── routes/
│   │   ├── __init__.py
│   │   ├── auth.py              # Login/Register endpoints
│   │   ├── bots.py              # Bot CRUD & control
│   │   ├── logs.py              # Log viewer & WebSocket
│   │   └── frontend.py          # Page routes
│   ├── services/
│   │   ├── __init__.py
│   │   ├── auth_service.py      # JWT & password hashing
│   │   ├── process_manager.py   # Subprocess lifecycle
│   │   ├── security.py          # Code scanner & sanitizer
│   │   └── log_streamer.py     # WebSocket log streaming
│   ├── templates/
│   │   ├── login.html           # RTL login page
│   │   ├── register.html        # RTL signup page
│   │   └── dashboard.html       # Main control panel
│   └── static/
│       ├── css/
│       │   └── app.css          # Custom styles
│       └── js/
│           └── app.js           # Frontend logic
├── user_data/
│   ├── bots/                    # Isolated bot directories
│   └── logs/                    # Bot log files
└── README.md
```

---

## Quick Start

### Prerequisites

- Python 3.10+
- PHP 8.x (for PHP bot hosting)
- pip

### Installation

```bash
cd backend
pip install -r requirements.txt

# Edit .env with your secret key
python main.py
```

The server starts at `http://localhost:8000`.

### Usage

1. Open `http://localhost:8000` in your browser.
2. Create an account.
3. Click "بوت جديد" (New Bot).
4. Choose **Python** or **PHP**, paste your code.
5. Add dependencies in `requirements.txt`.
6. Click "انشر البوت" (Deploy Bot).
7. Use **تشغيل** (Start), **إيقاف** (Stop), **إعادة تشغيل** (Restart).
8. Watch live logs in the console viewer.

---

## Security Features

| Feature | Description |
|---------|-------------|
| **Code Scanner** | Blocks dangerous patterns (`os`, `subprocess`, `eval`, `exec`, etc.) |
| **Resource Limits** | 256MB RAM, 50% CPU max per bot via psutil |
| **Process Isolation** | Each bot runs in its own subprocess |
| **Input Validation** | Sanitizes filenames, validates bot names |
| **JWT Authentication** | Secure token-based auth with bcrypt hashing |
| **Rate Limiting** | Max 3 bots per free-tier user |
| **Timeout Protection** | Bots are killed after 1 hour inactivity |

### Blocked Patterns

**Python:** `import os`, `subprocess`, `eval(`, `exec(`, `ctypes`, `socket`, `fork(`, `remove(`, and more.

**PHP:** `exec(`, `shell_exec(`, `system(`, `eval(`, `file_get_contents`, `include`, `proc_open`, and more.

---

## API Endpoints

### Authentication
| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/register` | Create account |
| POST | `/api/auth/login` | Login |
| GET | `/api/auth/me` | Current user info |

### Bot Management
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/bots/` | List user's bots |
| POST | `/api/bots/` | Create new bot |
| GET | `/api/bots/{id}` | Bot details + resource usage |
| POST | `/api/bots/{id}/start` | Start bot |
| POST | `/api/bots/{id}/stop` | Stop bot |
| POST | `/api/bots/{id}/restart` | Restart bot |
| PUT | `/api/bots/{id}/code` | Update bot code |
| DELETE | `/api/bots/{id}` | Delete bot |

### Logs
| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/logs/{id}` | Get recent logs |
| WS | `/api/logs/ws/{id}` | Real-time log stream |

---

## Deployment Recommendations

1. **Use a reverse proxy** (nginx/Caddy) with SSL in front of FastAPI.
2. **Run with a process manager** like `systemd` or `supervisor`.
3. **Set up Docker** for true container isolation instead of subprocess (see `deploy/docker-compose.yml`).
4. **Configure proper firewall** and fail2ban for production.
5. **Use PostgreSQL** instead of SQLite for multi-worker deployments.
6. **Set `SECRET_KEY`** to a strong random value.
7. **Monitor system resources** with `htop` or Netdata.

---

## License

MIT — Free for all. Built for the Arab developer community ❤️
