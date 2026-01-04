# YouTube Digest

Automatisierter Workflow zur Zusammenfassung von YouTube-Videos aus abonnierten Kanälen.

## Features

- **Automatischer Abo-Sync**: OAuth-basierte Synchronisierung aller YouTube-Abonnements
- **Video-Erkennung**: Tägliche Prüfung auf neue Videos mit Duplikat-Vermeidung
- **Transkript-Extraktion**: Automatische Untertitel-Extraktion mit AI-Fallback (Supadata)
- **AI-Zusammenfassung**: Ausführliche deutsche Summaries via Gemini 2.0 Flash
- **AI-Kategorisierung**: Automatische Zuordnung zu 8 Kategorien
- **E-Mail-Digest**: Strukturierte HTML-E-Mail mit Prioritäts-Sortierung
- **Web-Dashboard**: Status, Archiv, manuelle Trigger, Konfiguration

## Trigger-Logik

Der Digest wird versendet wenn:
- **14 Tage** seit dem letzten Digest vergangen sind, ODER
- **10+ neue Videos** seit dem letzten Digest erkannt wurden

## Kategorien

| Kategorie | Priorität |
|-----------|-----------|
| Claude Code | Hoch |
| Coding/AI Allgemein | Hoch |
| Brettspiele | Normal |
| Gesundheit | Normal |
| Sport | Normal |
| Beziehung/Sexualität | Normal |
| Beachvolleyball | Normal |
| Sonstige | Normal |

## Quick Start

### Voraussetzungen

- Python 3.11+
- Docker & Docker Compose
- Google Cloud Projekt mit YouTube Data API v3
- Gemini API Key
- Supadata API Key (optional, für Transkript-Fallback)

### Installation

```bash
# Repository klonen
git clone <repo-url>
cd youtube-digest

# Virtual Environment
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# Konfiguration
cp .env.example .env
# .env mit deinen Werten füllen

# OAuth Token erstellen (einmalig)
python -m app.services.youtube_service --auth
```

### Docker Deployment

```bash
# Starten
docker-compose up -d

# Logs
docker-compose logs -f

# Stoppen
docker-compose down
```

## Architektur

```
┌─────────────────────────────────────────────────────────────────┐
│                     Contabo VPS Stack                           │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Scheduler  │───▶│    Worker    │───▶│   Mailer     │      │
│  │   (Celery    │    │   (Celery)   │    │  (Resend)    │      │
│  │    Beat)     │    │              │    │              │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│         │                   │                   │               │
│         ▼                   ▼                   ▼               │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │    Redis     │    │  PostgreSQL  │    │    Gemini    │      │
│  │   (Broker)   │    │   (State)    │    │     API      │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                             │                                   │
│                             ▼                                   │
│                      ┌──────────────┐                          │
│                      │   YouTube    │                          │
│                      │  Data API   │                          │
│                      └──────────────┘                          │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## API Endpoints

| Endpoint | Methode | Beschreibung |
|----------|---------|--------------|
| `/` | GET | Dashboard |
| `/api/status` | GET | System-Status |
| `/api/channels` | GET | Alle Kanäle |
| `/api/videos` | GET | Verarbeitete Videos |
| `/api/digests` | GET | Digest-Historie |
| `/api/trigger-digest` | POST | Manueller Digest |
| `/api/oauth/status` | GET | OAuth-Status |
| `/health` | GET | Healthcheck |

## Umgebungsvariablen

Siehe [.env.example](.env.example) für alle verfügbaren Konfigurationsoptionen.

## Entwicklung

```bash
# Tests ausführen
pytest tests/ -v

# Nur Unit Tests
pytest tests/unit/ -v

# Code-Formatierung
black app/ tests/
isort app/ tests/

# Type-Checking
mypy app/
```

## Implementierungsstand

| Batch | Status | Komponenten |
|-------|--------|-------------|
| 1 | ✅ | Projektstruktur, Config, Models |
| 2 | ✅ | YouTube Service, Transcript Service, 46 Unit Tests |
| 3 | ✅ | Summarization Service (Gemini 2.0 Flash) |
| 4 | ✅ | Digest Generator, Email Service (Resend), HTML-Template |
| 5 | ✅ | Celery Tasks, API Routes, Dashboard, 27 Unit Tests |
| 6 | ✅ | Docker Compose, E2E Tests, Deployment auf Contabo |

### Services

| Service | Datei | Status |
|---------|-------|--------|
| YouTube | `app/services/youtube_service.py` | ✅ Implementiert |
| Transcript | `app/services/transcript_service.py` | ✅ Implementiert (youtube-transcript-api v1.x + Supadata) |
| Summarization | `app/services/summarization_service.py` | ✅ Implementiert (Gemini 2.0 Flash) |
| Digest Generator | `app/services/digest_generator.py` | ✅ Implementiert |
| Email | `app/services/email_service.py` | ✅ Implementiert (Resend API) |

## Test Status

| Test-Typ | Status | Details |
|----------|--------|---------|
| Unit Tests | ✅ 138/138 passed | 19.97s |
| E2E Tests (Server) | ✅ 5/5 passed | YouTube API, Channels, Transcript, Gemini, Celery |
| API Endpoints | ✅ 6/6 working | Health, Status, OAuth, Channels, Videos, Digests |

Siehe [TESTRESULTS.md](TESTRESULTS.md) für detaillierte Testergebnisse.

## Known Limitations

1. **YouTube IP Block**: YouTube blockiert Transkript-Anfragen von Cloud-Provider-IPs
   - Workaround: Supadata API Fallback ist implementiert und funktioniert

## Kosten-Schätzung

| Service | Nutzung/Monat | Kosten |
|---------|---------------|--------|
| YouTube Data API | ~500 Requests | Kostenlos |
| Gemini 2.0 Flash | ~50 Videos × 10k Tokens | ~$0.60/Monat |
| Supadata | ~10 Videos (Fallback) | Kostenlos (100 Free) |
| Resend | ~2 E-Mails | Kostenlos (100 Free/Monat) |
| VPS | Bereits vorhanden | $0 zusätzlich |

**Geschätzte Gesamtkosten: < $3/Monat**

## Lizenz

Privates Projekt - Mindfield Biosystems Ltd.
