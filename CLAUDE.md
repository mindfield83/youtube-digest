# CLAUDE.md - YouTube Digest

Projektspezifische Anweisungen für Claude Code.

## Projektübersicht

**YouTube Digest** ist ein selbst-gehosteter, automatisierter Workflow, der YouTube-Abonnements überwacht und regelmäßig AI-generierte Zusammenfassungen neuer Videos per E-Mail versendet.

| Aspekt | Details |
|--------|---------|
| **Ziel** | Effiziente Konsumption von YouTube-Inhalten ohne Zeitverlust |
| **Nutzer** | Niko Rockensüß (Single-User) |
| **Hosting** | Contabo VPS (`vps-ubuntu.mindfield.de`) |
| **Stack** | FastAPI, Celery, PostgreSQL, Redis |

## Tech Stack

| Komponente | Technologie |
|------------|-------------|
| **Backend** | FastAPI + Python 3.11 |
| **Task Queue** | Celery + Redis |
| **Database** | PostgreSQL 16 Alpine |
| **AI/LLM** | Google Gemini 2.0 Flash (`gemini-2.0-flash`) |
| **Transcripts** | youtube-transcript-api v1.x + Supadata (Fallback) |
| **Email** | Resend API |
| **Container** | Docker Compose (5 Services) |
| **Reverse Proxy** | Caddy (youtube-digest.vps-ubuntu.mindfield.de) |

## Projektstruktur

```
youtube-digest/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI Application
│   ├── config.py            # Pydantic Settings
│   ├── models.py            # SQLAlchemy Models
│   ├── tasks.py             # Celery Tasks
│   ├── api/
│   │   ├── routes.py        # Dashboard API
│   │   └── schemas.py       # Pydantic Schemas
│   ├── services/
│   │   ├── youtube_service.py
│   │   ├── transcript_service.py
│   │   ├── summarization_service.py
│   │   ├── digest_generator.py
│   │   └── email_service.py
│   ├── templates/
│   │   ├── digest_email.html
│   │   └── dashboard.html
│   └── static/
│       └── dashboard.css
├── credentials/             # .gitignore'd - OAuth tokens
├── tests/
├── docs/
│   └── PRD.md              # Product Requirements Document
├── docker-compose.yml
├── Dockerfile
├── requirements.txt
├── .env.example
├── CHANGELOG.md
└── DEVLOG.md
```

## Datenbank-Schema

### Tabellen

| Tabelle | Beschreibung |
|---------|--------------|
| `channels` | Abonnierte YouTube-Kanäle |
| `processed_videos` | Verarbeitete Videos mit Transkript + Summary |
| `digest_history` | Historie der gesendeten Digest-E-Mails |
| `oauth_tokens` | OAuth Token Storage |

### Wichtige Felder

```python
# ProcessedVideo.summary (JSONB)
{
    "core_message": "...",
    "detailed_summary": "...",
    "key_takeaways": ["...", "..."],
    "timestamps": [{"time": "00:00", "description": "..."}],
    "action_items": ["..."]
}
```

## Kategorien

Videos werden automatisch kategorisiert:

1. **Claude Code** (höchste Priorität)
2. **Coding/AI Allgemein** (hohe Priorität)
3. Brettspiele
4. Gesundheit
5. Sport
6. Beziehung/Sexualität
7. Beachvolleyball
8. Sonstige

## API Keys & Credentials

| Service | Env Variable | Status |
|---------|--------------|--------|
| YouTube OAuth | `credentials/youtube_token.json` | ✅ Auf Server vorhanden |
| YouTube Client | `credentials/youtube_oauth.json` | ✅ Konfiguriert |
| Gemini API | `GEMINI_API_KEY` | ✅ Konfiguriert |
| Supadata API | `SUPADATA_API_KEY` | ✅ Konfiguriert |
| Resend API | `RESEND_API_KEY` | ✅ Konfiguriert |

## Wichtige Befehle

```bash
# Lokale Entwicklung
python -m venv venv
source venv/bin/activate  # oder venv\Scripts\activate auf Windows
pip install -r requirements.txt

# OAuth Token erstellen (einmalig, lokal)
python -m app.services.youtube_service --auth

# Tests
pytest tests/

# Docker
docker-compose up -d
docker-compose logs -f worker
```

## Celery Tasks

| Task | Schedule | Beschreibung |
|------|----------|--------------|
| `check_for_new_videos` | Täglich 06:00 UTC | Prüft auf neue Videos |
| `process_video` | On-demand | Transkript + Summary |
| `generate_and_send_digest` | On-demand | Digest erstellen & senden |
| `check_digest_conditions` | Täglich 07:00 UTC | Prüft Trigger-Bedingungen (14 Tage / 10 Videos) |

## API Endpoints

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/health` | GET | Healthcheck (DB + Celery) |
| `/api/status` | GET | Dashboard Status Cards |
| `/api/videos` | GET | Video-Liste mit Filtern |
| `/api/videos/{id}` | GET | Video-Detail |
| `/api/channels` | GET | Kanal-Liste |
| `/api/digests` | GET | Digest-Historie |
| `/api/trigger-digest` | POST | Manueller Digest-Trigger |
| `/api/oauth/status` | GET | OAuth Token Status |

## Deployment

**URL:** `https://youtube-digest.vps-ubuntu.mindfield.de`
**Stack-Pfad:** `/home/raguser/youtube-digest`
**GitHub:** `github.com/mindfield83/youtube-digest`

```bash
# Auf Contabo VPS
cd /home/raguser/youtube-digest
git pull
docker compose up -d --build

# Container Status
docker ps --filter "name=youtube-digest"

# Logs
docker logs youtube-digest-app
docker logs youtube-digest-worker
docker logs youtube-digest-beat
```

## Implementierungsplan

Siehe [docs/PRD.md](docs/PRD.md) für den vollständigen Plan.

### Batch-Übersicht

| Batch | Status | Inhalt |
|-------|--------|--------|
| **1** | ✅ Erledigt | Projektstruktur, Config, Models, Credentials |
| **2** | ✅ Erledigt | YouTube Service, Transcript Service, Unit Tests |
| **3** | ✅ Erledigt | Summarization Service (Gemini 2.0 Flash) |
| **4** | ✅ Erledigt | Digest Generator, Email Service, HTML-Template |
| **5** | ✅ Erledigt | Celery Tasks, API Routes, Dashboard, HTMX |
| **6** | ✅ Erledigt | Docker, E2E Tests, Resend Email, Deployment |

## Konventionen

- **Sprache:** Alle Summaries auf Deutsch
- **Code:** Python Black + isort, Type Hints
- **Commits:** Conventional Commits (feat:, fix:, docs:)
- **Logs:** Strukturiertes Logging mit `structlog`

## Filter-Regeln

Videos werden ignoriert wenn:
- Dauer < 60 Sekunden (Shorts)
- `liveStreamingDetails` vorhanden (Livestreams)
- Titel enthält typische Werbe-Pattern

## Known Limitations

1. **YouTube IP Block**: YouTube blockiert Transkript-Anfragen von Cloud-Provider-IPs
   - Supadata API dient als zuverlässiger Fallback
   - Betrifft nur Transkript-Extraktion, nicht YouTube Data API

## Test Status

| Test-Typ | Status | Details |
|----------|--------|---------|
| Unit Tests | ✅ 138/138 | 19.97s |
| E2E Tests | ✅ 5/5 | YouTube API, Transcript, Gemini, Celery |
| API Endpoints | ✅ 6/6 | Health, Status, OAuth, Channels, Videos, Digests |

Siehe [TESTRESULTS.md](TESTRESULTS.md) für detaillierte Testergebnisse.
