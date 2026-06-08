---
title: Wolf Host — استضافة الذب هوست 🐺
emoji: 🐺
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: true
license: mit
---

# Wolf Host — استضافة الذب هوست 🐺

منصة استضافة البوتات المجانية للمطورين العرب
Autonomous Bot Hosting Platform for Arab Developers

**Developer:** الذئب الأبيض 🐺 | **Telegram:** @j49_c | **Channel:** @O5O6J | **X:** https://x.com/wolfhost_1

---

## Architecture

```
Cloudflare Proxy (CDN + SSL) → Hugging Face Space → FastAPI (subprocess bots)
```

- **Subprocess isolation** — every user bot runs as an isolated Python process
- **FastAPI** — async Python backend with PostgreSQL (Supabase/Neon)
- **Tailwind CSS** — Arabic RTL dashboard
- **Self-healing** — auto-restarts crashed processes every 30s
- **Auto-backup** — database backups pushed to GitHub every 6 hours

---

## Quick Start

```bash
pip install -r backend/requirements.txt
cd backend && python main.py
```

---

## Developer Contact

| Channel | Link |
|---------|------|
| Developer | الذئب الأبيض 🐺 |
| Telegram | @j49_c |
| Channel | @O5O6J |
| X (Twitter) | https://x.com/wolfhost_1 |

Built with ❤️ for the Arab developer community 🐺
