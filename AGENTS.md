# Agent Instructions for `hagi`

Welcome, AI Agent! This document contains essential context about the `hagi` project. Please review this before making changes to the codebase to save time on exploration.

## 🎯 Project Overview
`hagi` is a local tool designed to index and search media files (like anime episodes) and their associated subtitles. It allows users to search for specific phrases (especially in Japanese) and instantly retrieve the matching video context alongside corresponding translations in other languages.

## 🏗️ Architecture & Key Files
- **`web.py`**: The FastAPI backend. Contains all the API endpoints (e.g., `/api/search`, `/api/extract`).
- **`db.py`**: Core database logic. Handles initialization (`init_db`), schema definitions, and all SQL queries.
- **`indexer.py` & `exporter.py`**: Handle parsing media/subtitles and extracting audio/video clips. `exporter.py` also handles native `urllib` API calls to AnkiConnect.
- **`cli.py`**: Command-line interface for managing the tool.

## 💾 Database Schema (SQLite)
The project uses a single SQLite database (`hagi.db`) with WAL mode enabled.
- **`media` table**: Stores information about indexed video/audio files (path, show title, season, episode).
- **`sentences` table**: Stores individual subtitle lines, linking to `media_id`. Contains `language`, `start_time`, `end_time`, and `text`.
- **`sentences_fts` table**: A Full-Text Search (FTS5) virtual table configured with the `trigram` tokenizer. 
  - *Note:* It is kept in sync with the `sentences` table via SQL triggers. You should use `sentences_fts` for all text-based `LIKE` or `MATCH` queries to guarantee fast lookups.

## 🔍 Search Quirks (Important!)
When a user searches for a term, the engine doesn't just return the matching sentence. It also attempts to find **contextual translations** (English and Spanish).
- It does this by finding sentences in the same media file where `ABS(start_time - target_start_time) < 5.0`.
- Because of these correlated subqueries, **always ensure proper indexing** (e.g., on `media_id`, `language`, and `start_time`) to prevent massive full-table scans.

## ✂️ Timeline & Extraction Logic
- **Frontend/Backend Parity:** The frontend (`static/js/main.js`) has sophisticated grammatical logic for joining overlapping sentences (e.g., squishing Japanese text together unless it ends in a specific punctuation like `[だですまるかよねわぞ。！？]$`). The backend `exporter.py` must perfectly mirror this logic when extracting to Anki.
- **Midpoint Bounding Logic:** When grabbing overlapping sentences within a padded timeframe, the logic determines inclusion based on the **midpoint** of the subtitle (`(start + end) / 2.0`) falling within the padded bounds. Do not use greedy `start < end AND end > start` overlaps.
- **Carriage Returns:** When cleaning text from raw subtitles, always use the regex `[\r\n]+` to handle Windows-style CRLF breaks natively found in subtitle files.

## 🔌 AnkiConnect Integration
`hagi` integrates natively with local Anki instances via AnkiConnect (`http://127.0.0.1:8765`).
- Configuration is driven by a local `config.json` file, mapping the target `deck`, `noteType`, `tags`, and Anki fields (e.g. `sentenceField`, `audioField`, `imageField`, `sourceField`).
- API interactions use Python's built-in `urllib.request` to avoid extra dependencies.
- **Testing:** When writing tests (e.g. in `tests/test_exporter.py` or `tests/test_cli.py`), Anki network calls (`urllib.request.urlopen`) must be mocked to return valid JSON responses imitating AnkiConnect to prevent hitting a live server.

## 🧪 Testing Environment
- The project uses `pytest`. Tests are located in the `tests/` directory. Linting checks (via `ruff`) are integrated directly into the test suite using `pytest-ruff`.
- The project relies on a Conda environment named `hagi`.
- **To run tests & linting:** You must include the root directory in the PYTHONPATH. Run `pytest` on the root directory (rather than `tests/`) so it correctly finds and lints all `.py` files.
  ```bash
  PYTHONPATH=. conda run -n hagi pytest
  ```
- **Test Database:** Tests use an in-memory database (`:memory:`) to avoid touching the user's actual `hagi.db`.
- **Docstrings & CodeRabbit:** The repository enforces an 80% docstring coverage rule via CodeRabbit PR reviews. You **must** provide standard docstrings for every new function you write, **including** nested mock functions inside your tests.
- **JS Formatting:** The project does not use a heavy `node_modules` toolchain or a custom JS linting config. When modifying frontend JavaScript (e.g., `static/js/main.js`), you must format the file using Prettier via `npx` before committing:
  ```bash
  npx prettier --write static/js/main.js
  ```
## 🛠️ General Guidelines
- **Always use the Conda Environment:** Execute all Python, `pytest`, and `ruff` commands strictly via the `hagi` conda environment (e.g., `conda run -n hagi pytest`). Running them globally will cause them to fail due to missing dependencies.
- When generating SQL, prefer subqueries or joins on `sentences_fts` for text searching rather than raw `LIKE` on the `sentences` table. **However, note that FTS5 trigram indexes do not always work well with short Japanese words of 2 or fewer characters, so `LIKE` may be necessary in those edge cases.**
- **Conventional Commits:** It is mandatory to use the Conventional Commits standard (e.g., `feat:`, `fix:`) for all commit messages. This standard must also be applied to branch naming (e.g., `feat/...`, `fix/...`).

## 🔄 GitHub PR & Review Loop
When committing new features or fixes, you are expected to handle the entire PR lifecycle natively:
1. **Testing is Mandatory:** Existing tests must pass without regression (`PYTHONPATH=. conda run -n hagi pytest`). Furthermore, you **must write new tests** whenever adding new features, fixing bugs, or addressing user feedback. Ensure the full test suite passes before making *any* commit.
2. Commit your changes and push the branch to origin. **Do NOT push multiple commits rapidly**, as this breaks or auto-pauses the CodeRabbit AI review process. Wait for the review to finish before pushing iterative fixes.
3. **Ask for manual user feedback** so the user can test the changes locally before you create the Pull Request.
4. Once the user approves the local test, create the Pull Request using the GitHub CLI (`gh pr create`). You **MUST** format the PR body using the `.github/pull_request_template.md`. When filling out the pre-merge checklist, you must actually validate each checkbox and not just mark it for the sake of it.
5. Monitor the pre-merge GitHub Actions checks by running `gh pr checks <pr_number> --watch --interval 60`. 
6. **Trigger CodeRabbit Manually:** Because this OSS repository is limited to 1 CodeRabbit review per hour, you must **NOT** waste it on failing builds. Only after the standard GitHub Actions tests pass, trigger the CodeRabbit review by running:
   ```bash
   gh pr comment <pr_number> --body "@coderabbitai review"
   ```
7. Monitor the PR comments for CodeRabbit's reply and wait for the review to definitively finish. 
8. Address any actionable review comments from CodeRabbit. Ensure all tests and coverage checks pass before merging via `gh pr merge <pr_number> --squash --delete-branch`.
