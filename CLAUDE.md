# CLAUDE.md

## Project Overview

Credit card statement automation system (信用卡對帳單自動化處理系統). Python/FastAPI app that receives Gmail credit card statements, parses PDF attachments, and imports transactions into Firefly III.

## Commands

```bash
# Run dev server
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload

# Run with Docker
docker-compose up

# Install dependencies
pip install -r requirements.txt

# Test health
curl http://localhost:8000/health

# Upload a PDF for parsing
curl -X POST "http://localhost:8000/api/upload?bank_code=sinopac" -F "file=@statement.pdf"
```

## Architecture

```
Gmail Pub/Sub → webhook.py → import_service.py → mail_classifier.py
                                                → pdf_service.py (unlock)
                                                → parsers/<bank>_parser.py (parse)
                                                → firefly_service.py (import)
```

- **Entry points**: `app/main.py` (FastAPI app), `app/routers/webhook.py` (Gmail push), `app/routers/statements.py` (REST API)
- **Config**: `config/config.yaml` loaded via `app/config.py` (Pydantic models). Env vars `FIREFLY_BASE_URL` and `FIREFLY_API_TOKEN` override config.
- **Database**: SQLite at `./data/statements.db`, models in `app/models/database.py` (SQLModel). Two tables: `statements` and `transactions`.
- **Parsers**: Plugin architecture. Each bank has a parser in `app/parsers/` extending `BaseParser`. Registered in `ParserFactory._register_all()`.
- **Currently implemented parser**: `sinopac` (永豐銀行). Other 6 banks are configured but parsers not yet implemented.

## Key Files

- `app/config.py` — Settings model, YAML loading, `get_settings()` singleton
- `app/models/database.py` — Statement/Transaction SQLModel tables, `get_engine()`, `get_session()`
- `app/parsers/base_parser.py` — Abstract base with `classify_transaction_type()` (withdrawal/deposit/transfer keywords)
- `app/parsers/sinopac_parser.py` — Parses deposit tables + credit card tables from 永豐 PDF
- `app/services/import_service.py` — Two flows: `process_notification()` (Gmail push) and `process_pdf_file()` (upload)
- `app/services/firefly_service.py` — Firefly III REST client, retry with exponential backoff, dedup via `external_id`
- `app/utils/account_mapper.py` — Keyword → category mapping (50+ spending categories)

## Conventions

- Language: Python 3.11+, type hints throughout
- Framework: FastAPI with async endpoints, SQLModel for ORM
- Config: YAML file + Pydantic validation, sensitive values via env vars
- Logging: structlog (structured, JSON-capable)
- PDF password templates use `{id_number}`, `{birthday}`, `{phone}` variables
- Transaction types: `withdrawal` (消費), `deposit` (退款), `transfer` (繳費)
- External IDs format: `stmt-{bank_code}-{date}-{index:03d}`
- All dates stored as ISO format strings (YYYY-MM-DD)

## Adding a New Bank Parser

1. Add bank config to `config/config.yaml` under `banks:`
2. Create `app/parsers/<bank>_parser.py` extending `BaseParser`
3. Implement `parse(pdf_path) -> List[Dict]` returning transaction dicts
4. Register in `app/parsers/__init__.py` `_register_all()`
5. Transaction dict keys: `transaction_date`, `posting_date`, `description`, `amount`, `currency`, `card_last_four`, `transaction_type`, `source_account`, `destination_account`, `external_id`, `raw_data`, `notes`

## Sensitive Files (do not commit)

- `config/config.yaml` — contains user PII (id_number, birthday, phone)
- `credentials.json` — Google OAuth client secret
- `token.json` — Google OAuth access token
- `data/statements.db` — parsed financial data
