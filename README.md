# MedBot

Telegram bot, FastAPI service, and background worker that ingest lab report PDFs from email and expose them to Telegram users.

## Included

- Python package layout under `app/`
- `FastAPI` app with a healthcheck endpoint
- `aiogram` bot bootstrap with `/start`
- Background worker that polls `mail.ru` via IMAP and stores PDF attachments
- Shared configuration via environment variables
- `Dockerfile` and `docker-compose.yml` for local startup
- Deduplication by IMAP `UID` and attachment checksum per email

## Environment variables

Copy `.env.example` to `.env` and fill in the values below before the first launch.

| Variable | Required | Default | Purpose |
| --- | --- | --- | --- |
| `APP_NAME` | no | `MedBot` | Service name used in configuration and logs. |
| `APP_ENV` | no | `development` | Runtime environment name. |
| `DEBUG` | no | `true` in compose | Enables debug mode for local development. |
| `API_HOST` | no | `0.0.0.0` | Bind address for the FastAPI container. |
| `API_PORT` | no | `8000` | Internal API port used by the `api` service. |
| `TELEGRAM_BOT_TOKEN` | yes | empty | Telegram bot token from BotFather. |
| `DATABASE_URL` | no | `sqlite+aiosqlite:////data/medbot.db` | Database connection string. The default stores SQLite inside the Docker volume. |
| `REPORTS_DIR` | no | `/data/reports` | Directory where saved PDF files are stored. |
| `MAIL_IMAP_HOST` | no | `imap.mail.ru` | IMAP host for the mailbox with lab reports. |
| `MAIL_IMAP_PORT` | no | `993` | IMAP SSL port. |
| `MAIL_USERNAME` | yes | empty | Mailbox login used by the worker. |
| `MAIL_PASSWORD` | yes | empty | Mailbox password or app password for IMAP. |
| `MAIL_FOLDER` | no | `INBOX` | IMAP folder that the worker polls. |
| `MAIL_POLL_INTERVAL_SECONDS` | no | `300` | Delay between sync cycles in the worker. |
| `MAIL_ALLOWED_SENDERS` | no | empty | Comma-separated list of allowed sender fragments. If empty together with the subject filter, any email with PDF attachments is accepted. |
| `MAIL_ALLOWED_SUBJECT_FRAGMENT` | no | empty | Optional subject fragment used to allow emails for processing. |
| `GIGACHAT_BASE_URL` | no | empty in compose | Base URL for GigaChat API requests. Leave empty if AI advice is not needed. |
| `GIGACHAT_CLIENT_ID` | no | empty | Optional client identifier for GigaChat requests. |
| `GIGACHAT_AUTHORIZATION_KEY` | no | empty | Authorization key used in the `Basic` header for `POST /api/v2/oauth`. Required only for the `Совет ИИ` feature. |
| `GIGACHAT_SCOPE` | no | `GIGACHAT_API_PERS` | OAuth scope for GigaChat token acquisition. |

`GIGACHAT_MODEL`, `GIGACHAT_TIMEOUT_SECONDS`, and `GIGACHAT_AUTH_URL` are present in `.env.example` for direct app configuration, but they are not forwarded by the current `docker-compose.yml`.

## Local run

### Prerequisites

- Docker Desktop with Docker Compose support
- A Telegram bot token
- A mailbox accessible over IMAP, currently expected to be `mail.ru`

### Startup

1. Create a local env file:

   ```bash
   cp .env.example .env
   ```

   On Windows PowerShell, use:

   ```powershell
   Copy-Item .env.example .env
   ```

2. Open `.env` and set at least:
   - `TELEGRAM_BOT_TOKEN`
   - `MAIL_USERNAME`
   - `MAIL_PASSWORD`
   - `MAIL_ALLOWED_SENDERS` if you want to limit processing to specific senders during tests
   - `GIGACHAT_AUTHORIZATION_KEY` if you want the `Совет ИИ` button to work
3. Build and start all services:

   ```bash
   docker compose up --build
   ```

4. Verify the API healthcheck in a browser or with `curl`:

   ```text
   http://localhost:8000/health
   ```

5. Stop the stack when finished:

   ```bash
   docker compose down
   ```

### What starts

- `api`: FastAPI healthcheck and service endpoints
- `bot`: Telegram polling process
- `worker`: IMAP sync loop that saves new PDF attachments into `REPORTS_DIR/mail/<uid>/`
- The worker can optionally filter emails by sender and subject via `MAIL_ALLOWED_SENDERS` and `MAIL_ALLOWED_SUBJECT_FRAGMENT`

The default setup mounts a persistent Docker volume at `/data`, so the SQLite database and downloaded PDFs survive container restarts.

## GigaChat advice

- The button `Совет ИИ` in the report card requests a short recommendation from `GigaChat` on demand.
- Only the report category, recognized patient name, and a compact summary are sent to the model.
- The prompt explicitly forbids diagnosis, treatment, dosages, and overconfident conclusions.
- Each generated recommendation is stored in `ai_recommendations` and always includes a disclaimer that it is not a diagnosis.

## First version limitations

- The worker currently targets IMAP access for `mail.ru`; other providers are untested.
- Only PDF attachments are ingested from email. Non-PDF files are ignored.
- PDF parsing is text-based. Scanned documents or image-only PDFs may be saved but still require manual review.
- Family members appear in Telegram only after a report has been parsed and matched to a recognized patient name.
- The local setup uses SQLite by default, which is fine for a single-user or small local deployment but not for production scaling.
- `GigaChat` advice is optional and only available when its credentials are configured.
- The current `docker-compose.yml` exposes only the env variables listed above; advanced GigaChat overrides from `.env.example` are not wired into containers yet.

## Healthcheck

After startup, the API healthcheck is available at `http://localhost:8000/health`.
