# Agent Instructions for `local-nadeshiko`

Welcome, AI Agent! This document contains essential context about the `local-nadeshiko` project. Please review this before making changes to the codebase to save time on exploration.

## 🎯 Project Overview
`local-nadeshiko` is a local tool designed to index and search media files (like anime episodes) and their associated subtitles. It allows users to search for specific phrases (especially in Japanese) and instantly retrieve the matching video context alongside corresponding translations in other languages.

## 🏗️ Architecture & Key Files
- **`web.py`**: The FastAPI backend. Contains all the API endpoints (e.g., `/api/search`, `/api/extract`).
- **`db.py`**: Core database logic. Handles initialization (`init_db`), schema definitions, and all SQL queries.
- **`indexer.py` & `exporter.py`**: Handle parsing media/subtitles and extracting audio/video clips.
- **`cli.py`**: Command-line interface for managing the tool.

## 💾 Database Schema (SQLite)
The project uses a single SQLite database (`nadeshiko.db`) with WAL mode enabled.
- **`media` table**: Stores information about indexed video/audio files (path, show title, season, episode).
- **`sentences` table**: Stores individual subtitle lines, linking to `media_id`. Contains `language`, `start_time`, `end_time`, and `text`.
- **`sentences_fts` table**: A Full-Text Search (FTS5) virtual table configured with the `trigram` tokenizer. 
  - *Note:* It is kept in sync with the `sentences` table via SQL triggers. You should use `sentences_fts` for all text-based `LIKE` or `MATCH` queries to guarantee fast lookups.

## 🔍 Search Quirks (Important!)
When a user searches for a term, the engine doesn't just return the matching sentence. It also attempts to find **contextual translations** (English and Spanish).
- It does this by finding sentences in the same media file where `ABS(start_time - target_start_time) < 5.0`.
- Because of these correlated subqueries, **always ensure proper indexing** (e.g., on `media_id`, `language`, and `start_time`) to prevent massive full-table scans.

## 🧪 Testing Environment
- The project uses `pytest`. Tests are located in the `tests/` directory. Linting checks (via `ruff`) are integrated directly into the test suite using `pytest-ruff`.
- The project relies on a Conda environment named `local-nadeshiko`.
- **To run tests & linting:** You must include the root directory in the PYTHONPATH. Run `pytest` on the root directory (rather than `tests/`) so it correctly finds and lints all `.py` files.
  ```bash
  PYTHONPATH=. conda run -n local-nadeshiko pytest
  ```
- **Test Database:** Tests use an in-memory database (`:memory:`) to avoid touching the user's actual `nadeshiko.db`.

## 🛠️ General Guidelines
- Do not run commands using `python3` globally; always use the `local-nadeshiko` conda environment.
- When generating SQL, prefer subqueries or joins on `sentences_fts` for text searching rather than raw `LIKE` on the `sentences` table.
