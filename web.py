"""Web application for Hagi Local UI."""

import os
import urllib.parse

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

import db
import exporter


def _get_normalized_media_url(config_obj: dict) -> str | None:
    if not isinstance(config_obj, dict):
        return None
    url = config_obj.get("mediaBaseUrl")
    if isinstance(url, str):
        url = url.strip().rstrip("/")
        if url:
            try:
                parsed = urllib.parse.urlparse(url)
                if (
                    parsed.scheme in ("http", "https")
                    and parsed.hostname
                    and not parsed.query
                    and not parsed.fragment
                    and "?" not in url
                    and "#" not in url
                ):
                    _ = parsed.port
                    return url
            except ValueError:
                pass
    return None

app = FastAPI(title="Hagi Local UI")

# Startup warnings
_has_media_base_url = False
if os.path.exists("config.json"):
    try:
        import json
        with open("config.json", "r") as _f:
            _has_media_base_url = bool(_get_normalized_media_url(json.load(_f)))
    except Exception:
        pass

if not _has_media_base_url:
    print(
        "Warning: 'mediaBaseUrl' not found in config.json. Defaulting to "
        "http://localhost:8000 for Anki media exports. Remote Anki "
        "instances will fail to download media.",
        flush=True
    )

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

# Mount media folder so UI can serve extracted images/audio
os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/", response_class=HTMLResponse)
@app.get("/search/{query}", response_class=HTMLResponse)
@app.get("/sentence/{sentence_id}", response_class=HTMLResponse)
@app.get("/context/{sentence_id}", response_class=HTMLResponse)
async def get_ui(request: Request, query: str = None, sentence_id: int = None):
    """Render the main UI page."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/search")
def search(q: str = "", show: str = None, season: int = None, episode: int = None):
    """Search the database for sentences matching the query and filters."""
    if not q:
        return []

    conn = db.get_db()
    results = db.search_sentences(
        conn, q, show_title=show, season=season, episode=episode
    )
    return [dict(r) for r in results]


@app.get("/api/sentence/{sentence_id}")
def get_sentence(sentence_id: int):
    """Fetch a single sentence by ID using rich search format."""
    conn = db.get_db()
    results = db.search_sentences(conn, "", sentence_id=sentence_id)
    if not results:
        raise HTTPException(status_code=404, detail="Sentence not found")
    return dict(results[0])


@app.get("/api/context/{sentence_id}")
def get_context(sentence_id: int):
    """Get surrounding context sentences for a given sentence ID."""
    conn = db.get_db()
    target = conn.execute(
        """
        SELECT s.media_id, s.start_time, s.language, m.show_title, m.season, m.episode
        FROM sentences s
        JOIN media m ON s.media_id = m.id
        WHERE s.id = ?
        """,
        (sentence_id,),
    ).fetchone()

    if not target:
        raise HTTPException(status_code=404, detail="Sentence not found")

    def fetch_lang(lang):
        start_time = target["start_time"]
        if start_time is None:
            # Fallback to ID-based context if start_time is missing
            if target["show_title"] and target["episode"]:
                return [
                    dict(r)
                    for r in conn.execute(
                        """
                    SELECT s.id, s.start_time, s.end_time, s.text, s.language
                    FROM sentences s
                    JOIN media m ON s.media_id = m.id
                    WHERE m.show_title = ? AND m.season = ? AND m.episode = ?
                    AND s.language = ? AND s.id >= ? AND s.id <= ?
                    ORDER BY s.id ASC
                    """,
                        (
                            target["show_title"],
                            target["season"],
                            target["episode"],
                            lang,
                            sentence_id - 15,
                            sentence_id + 15,
                        ),
                    ).fetchall()
                ]
            else:
                return [
                    dict(r)
                    for r in conn.execute(
                        """
                    SELECT id, start_time, end_time, text, language
                    FROM sentences
                    WHERE media_id = ? AND language = ? AND id >= ? AND id <= ?
                    ORDER BY id ASC
                    """,
                        (
                            target["media_id"],
                            lang,
                            sentence_id - 15,
                            sentence_id + 15,
                        ),
                    ).fetchall()
                ]

        if target["show_title"] and target["episode"]:
            return [
                dict(r)
                for r in conn.execute(
                    """
                SELECT s.id, s.start_time, s.end_time, s.text, s.language
                FROM sentences s
                JOIN media m ON s.media_id = m.id
                WHERE m.show_title = ? AND m.season = ? AND m.episode = ?
                AND s.language = ? AND s.start_time >= ? AND s.start_time <= ?
                ORDER BY s.start_time ASC
                """,
                    (
                        target["show_title"],
                        target["season"],
                        target["episode"],
                        lang,
                        start_time - 30.0,
                        start_time + 30.0,
                    ),
                ).fetchall()
            ]
        else:
            return [
                dict(r)
                for r in conn.execute(
                    """
                SELECT id, start_time, end_time, text, language
                FROM sentences
                WHERE media_id = ? AND language = ? AND start_time >= ? AND start_time <= ?
                ORDER BY start_time ASC
                """,
                    (
                        target["media_id"],
                        lang,
                        start_time - 30.0,
                        start_time + 30.0,
                    ),
                ).fetchall()
            ]

    other_lang = None
    if target["language"] == "jpn":
        if target["show_title"] and target["episode"]:
            row = conn.execute(
                """
                SELECT s.language
                FROM sentences s
                JOIN media m ON s.media_id = m.id
                WHERE m.show_title = ? AND m.season = ? AND m.episode = ? AND s.language != 'jpn'
                ORDER BY CASE s.language
                    WHEN 'spa' THEN 1
                    WHEN 'eng' THEN 2
                    WHEN 'por' THEN 3
                    ELSE 4
                END ASC
                LIMIT 1
                """,
                (target["show_title"], target["season"], target["episode"]),
            ).fetchone()
            if row:
                other_lang = row["language"]
        else:
            row = conn.execute(
                """
                SELECT language FROM sentences
                WHERE media_id = ? AND language != 'jpn'
                ORDER BY CASE language
                    WHEN 'spa' THEN 1
                    WHEN 'eng' THEN 2
                    WHEN 'por' THEN 3
                    ELSE 4
                END ASC
                LIMIT 1
                """,
                (target["media_id"],),
            ).fetchone()
            if row:
                other_lang = row["language"]

    secondary_lang = "jpn" if target["language"] != "jpn" else other_lang
    secondary_context = []
    if secondary_lang:
        secondary_context = fetch_lang(secondary_lang)

    return {
        "target_lang": target["language"],
        "target_context": fetch_lang(target["language"]),
        "secondary_lang": secondary_lang,
        "secondary_context": secondary_context,
    }


class ExtractConfig(BaseModel):
    """Configuration for media extraction."""

    pad_start: float = 0.5
    pad_end: float = 0.5
    target_note_id: int | None = Field(default=None, gt=0)
    search_query: str | None = None


@app.post("/api/extract/{sentence_id}")
def extract(sentence_id: int, config: ExtractConfig):
    """Extract audio and image for a given sentence."""
    success, msg, audio_out, image_out, text = exporter.extract_media(
        sentence_id, "./media", config.pad_start, config.pad_end
    )
    if not success:
        raise HTTPException(status_code=500, detail=msg)

    # Return relative URLs that map to the mounted StaticFiles
    return {
        "success": True,
        "message": "Media extracted",
        "audio_url": f"/media/{os.path.basename(audio_out)}",
        "image_url": f"/media/{os.path.basename(image_out)}",
        "text": text,
    }


@app.post("/api/anki/{sentence_id}")
def export_anki_endpoint(sentence_id: int, config: ExtractConfig):
    """Export a sentence to AnkiConnect using the existing local config."""
    if not os.path.exists("config.json"):
        raise HTTPException(status_code=500, detail="config.json not found.")

    try:
        import json
        with open("config.json", "r") as f:
            app_config = json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to load config: {e}")

    media_base_url = _get_normalized_media_url(app_config)
    if not media_base_url:
        media_base_url = "http://localhost:8000"

    success, msg = exporter.export_ankiconnect(
        sentence_id,
        app_config,
        "./media",
        config.pad_start,
        config.pad_end,
        target_note_id=config.target_note_id,
        base_url=media_base_url,
        search_query=config.search_query
    )
    if not success:
         raise HTTPException(status_code=500, detail=msg)

    return {"success": True, "message": msg}
