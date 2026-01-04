# YouTube Digest

Automatisierter Workflow zur Zusammenfassung von YouTube-Videos aus abonnierten Kanälen.

## Features

- **Automatischer Abo-Sync**: OAuth-basierte Synchronisierung aller YouTube-Abonnements
- **Video-Erkennung**: Tägliche Prüfung auf neue Videos mit Duplikat-Vermeidung
- **Transkript-Extraktion**: Automatische Untertitel-Extraktion mit AI-Fallback (Supadata)
- **AI-Zusammenfassung**: Ausführliche deutsche Summaries via Gemini 3.0 Flash
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
│  │   (Celery    │    │   (Celery)   │    │   (SMTP)     │      │
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
| 3 | ✅ | Summarization Service (Gemini 3.0 Flash) |
| 4 | ✅ | Digest Generator, Email Service, HTML-Template |
| 5 | ✅ | Celery Tasks, API Routes, Dashboard, 27 Unit Tests |
| 6 | ⏳ | Docker Compose, Integration Tests, Deployment |

### Services

| Service | Datei | Status |
|---------|-------|--------|
| YouTube | `app/services/youtube_service.py` | ✅ Implementiert |
| Transcript | `app/services/transcript_service.py` | ✅ Implementiert |
| Summarization | `app/services/summarization_service.py` | ✅ Implementiert |
| Digest Generator | `app/services/digest_generator.py` | ✅ Implementiert |
| Email | `app/services/email_service.py` | ✅ Implementiert |

## Kosten-Schätzung

| Service | Nutzung/Monat | Kosten |
|---------|---------------|--------|
| YouTube Data API | ~500 Requests | Kostenlos |
| Gemini 3.0 Flash | ~50 Videos × 10k Tokens | ~$0.60/Monat |
| Supadata | ~10 Videos (Fallback) | Kostenlos (100 Free) |
| VPS | Bereits vorhanden | $0 zusätzlich |

**Geschätzte Gesamtkosten: < $3/Monat**

## Lizenz

Privates Projekt - Mindfield Biosystems Ltd.
