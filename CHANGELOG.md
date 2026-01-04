# Changelog

Alle wichtigen Änderungen werden hier dokumentiert.
Format basiert auf [Keep a Changelog](https://keepachangelog.com/de/1.0.0/).

## [1.0.0] - 2026-01-04

### Hinzugefügt

#### Batch 6 - Production Deployment & Fixes
- Docker Compose Deployment auf Contabo VPS
- Caddy Reverse Proxy mit SSL
- OAuth Token Management (file-based)
- E2E Test Suite
- TESTRESULTS.md Dokumentation

### Geändert
- Email Service: SMTP → Resend API
- OAuth Status Route: Database → File-based Token
- Transcript Service: youtube-transcript-api v1.x Kompatibilität
- Supadata Response Parsing: String + List Support

### Behoben
- `fix(api): read OAuth token from file instead of database`
- `fix(transcript): handle Supadata response formats correctly`
- `fix(transcript): update youtube-transcript-api to v1.x API`

---

## [Unreleased]

### Hinzugefügt

#### Batch 1 - Projektstruktur
- Initiale Projektstruktur
- Pydantic Settings Konfiguration (`app/config.py`)
- SQLAlchemy Models (`app/models.py`):
  - `Channel` - Abonnierte YouTube-Kanäle
  - `ProcessedVideo` - Verarbeitete Videos mit Transkript + Summary
  - `DigestHistory` - Historie der gesendeten Digest-E-Mails
  - `OAuthToken` - OAuth Token Storage
- YouTube OAuth Credentials Setup
- Projektdokumentation:
  - `README.md` - Projektübersicht
  - `CLAUDE.md` - Claude Code Anweisungen
  - `DEVLOG.md` - Entwicklungslog
  - `docs/PRD.md` - Product Requirements Document

#### Batch 2 - YouTube & Transcript Services
- YouTube Service (`app/services/youtube_service.py`):
  - OAuth 2.0 Flow mit automatischem Token-Refresh
  - Subscription-Abruf mit Pagination
  - Video-Details-Abruf in Batches (max 50 pro Request)
  - Shorts-Filter (< 60 Sekunden)
  - Livestream-Filter (liveStreamingDetails, liveBroadcastContent)
  - ISO 8601 Duration Parsing
  - CLI für OAuth-Flow (`--auth`) und Tests (`--test`)
- Transcript Service (`app/services/transcript_service.py`):
  - YouTube-Transkripte via youtube-transcript-api
  - Sprach-Präferenz: DE > EN > andere
  - Manuelle Transkripte bevorzugt vor Auto-Generated
  - Automatische Übersetzung zu Deutsch wenn möglich
  - Supadata API Fallback für Videos ohne Untertitel
  - Transcript-Chunking für lange Videos
  - Timestamps-Formatierung (alle ~2 Minuten)
- Unit Tests (46 Tests):
  - `tests/unit/test_youtube_service.py` - 22 Tests
  - `tests/unit/test_transcript_service.py` - 24 Tests
  - `tests/conftest.py` - Pytest Fixtures

#### Batch 3 - Summarization Service
- Summarization Service (`app/services/summarization_service.py`):
  - Gemini 3.0 Flash Integration (`gemini-3-flash-preview`)
  - Structured Output via Pydantic Models → JSON Schema
  - 8 Kategorien mit Claude Code als höchste Priorität
  - Chunking + Synthesis für Videos >500k Zeichen
  - Retry-Logik mit exponential backoff (1s, 2s, 4s)
  - CLI für Tests (`python -m app.services.summarization_service VIDEO_ID`)
- Unit Tests für Summarization Service

#### Batch 4 - Digest Generator & Email Service
- Digest Generator (`app/services/digest_generator.py`):
  - Kategorie-Gruppierung mit Prioritätssortierung
  - Videos nach Kategorie: Claude Code → Coding/AI → Rest → Sonstige
  - E-Mail-Inhalt: core_message + key_takeaways
  - Links zu YouTube-Video + Dashboard-Zusammenfassung
  - ASCII-only Plain-Text für maximale Kompatibilität
  - Statistiken: Video-Count, Gesamtdauer, Kategorie-Counts
  - Max 50 Videos pro Digest
  - CLI für Tests (`python -m app.services.digest_generator --days 14`)
- Email Service (`app/services/email_service.py`):
  - SMTP mit STARTTLS (Port 587)
  - Multipart: HTML + Plain-Text Fallback
  - Retry-Logik: 3 Versuche mit 2s, 5s, 10s Backoff
  - Input-Validierung: Subject-Länge (RFC 5321), E-Mail-Größe (10MB)
  - Socket-Timeout für alle SMTP-Operationen
  - Test-Methoden: `test_connection()`, `send_test_email()`
  - CLI für Tests (`python -m app.services.email_service --test-connection`)
- HTML E-Mail Template (`app/templates/digest_email.html`):
  - Mindfield Design System (Blue: #17214B, Red: #E31C23)
  - Table-Layout für Outlook-Kompatibilität
  - Responsive Design (max-width: 600px)
  - Inline CSS für E-Mail-Client-Kompatibilität
- Unit Tests:
  - `tests/unit/test_digest_generator.py` - Digest Generator Tests
  - `tests/unit/test_email_service.py` - Email Service Tests inkl. Validierung
- Design-Dokument: `docs/plans/2026-01-03-batch4-digest-email-design.md`

#### Batch 5 - Celery Tasks, API Routes & Dashboard
- Celery App (`app/celery_app.py`):
  - Redis als Broker und Result Backend
  - Task Serialization: JSON
  - Konfiguration für Retries: `max_retries=3`, `retry_backoff=True`
  - Celery Beat Schedule:
    - `check-for-new-videos`: Täglich um 06:00 UTC
    - `check-digest-conditions`: Täglich um 07:00 UTC
- Celery Tasks (`app/tasks.py`):
  - `check_for_new_videos`: Subscriptions prüfen, neue Videos discovern
  - `process_video`: Transkript abrufen + Gemini-Zusammenfassung
  - `generate_and_send_digest`: Digest erstellen nach Bedingungen (14 Tage / 10 Videos)
  - `check_digest_conditions`: Automatische Trigger-Prüfung
  - `bind=True` Pattern für Self-Referenz bei Retries
- API Schemas (`app/api/schemas.py`):
  - Pydantic v2 Models für alle Response-Typen
  - `VideoResponse`, `ChannelResponse`, `DigestResponse`, `StatusResponse`
  - Pagination: `VideoListResponse`, `ChannelListResponse`, `DigestListResponse`
- API Routes (`app/api/routes.py`):
  - `GET /health` - Healthcheck (DB + Celery)
  - `GET /api/status` - Dashboard Status Cards (HTMX Partial)
  - `GET /api/videos` - Video-Liste mit Filtern (category, status)
  - `GET /api/videos/{id}` - Video-Detail
  - `GET /api/channels` - Kanal-Liste
  - `GET /api/digests` - Digest-Historie
  - `POST /api/trigger-digest` - Manueller Digest-Trigger
  - `GET /api/oauth/status` - OAuth Token Status
- Dashboard (`app/templates/dashboard.html`):
  - HTMX für Server-Side Rendering ohne JS-Framework
  - Status Cards mit Auto-Refresh (alle 30s)
  - Tabs: Videos, Channels, Digest History
  - Filter: Kategorie, Processing Status
  - Actions: Generate Digest Now, OAuth Status
- Dashboard CSS (`app/static/dashboard.css`):
  - Mindfield Design System (Blue: #17214B, Red: #E31C23)
  - Responsive Grid Layout
  - Status Badges, Category Badges
  - Tab Navigation, Filter Controls
- FastAPI Main App Update (`app/main.py`):
  - Lifespan Context Manager für Startup/Shutdown
  - Static Files Mounting
  - Jinja2 Templates
  - Dashboard Route (`/`)
  - Global Exception Handler
- Unit Tests (27 Tests):
  - `tests/unit/test_celery_app.py` - 7 Tests (Config, Schedule, Routing)
  - `tests/unit/test_tasks.py` - 5 Tests (Task Registration, Logic)
  - `tests/unit/test_schemas.py` - 5 Tests (Pydantic Models)
  - `tests/unit/test_routes.py` - 10 Tests (API Endpoints)
- Integration Tests (`tests/integration/test_api_integration.py`):
  - PostgreSQL-only Tests (automatisch geskippt ohne PostgreSQL)
  - Full Endpoint Tests mit echter Datenbank

### Abgeschlossen

Alle Batches 1-6 wurden erfolgreich implementiert und deployed.
Siehe [TESTRESULTS.md](TESTRESULTS.md) für detaillierte Testergebnisse.
