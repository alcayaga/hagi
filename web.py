"""Web application for Nadeshiko Local UI."""

import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel

import db
import exporter

app = FastAPI(title="Nadeshiko Local UI")

# Ensure templates directory exists
os.makedirs("templates", exist_ok=True)
templates = Jinja2Templates(directory="templates")

# Mount media folder so UI can serve extracted images/audio
os.makedirs("media", exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")


@app.get("/", response_class=HTMLResponse)
async def get_ui(request: Request):
    """Render the main UI page."""
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/search")
def search(q: str = "", show: str = None, episode: int = None):
    """Search the database for sentences matching the query and filters."""
    if not q:
        return []

    conn = db.get_db()
    results = db.search_sentences(conn, q, show_title=show, episode=episode)
    return [dict(r) for r in results]


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
        if target["show_title"] and target["episode"]:
            return [dict(r) for r in conn.execute(
                """
                SELECT s.id, s.start_time, s.text, s.language
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
                    target["start_time"] - 30.0,
                    target["start_time"] + 30.0,
                ),
            ).fetchall()]
        else:
            return [dict(r) for r in conn.execute(
                """
                SELECT id, start_time, text, language
                FROM sentences
                WHERE media_id = ? AND language = ? AND start_time >= ? AND start_time <= ?
                ORDER BY start_time ASC
                """,
                (
                    target["media_id"],
                    lang,
                    target["start_time"] - 30.0,
                    target["start_time"] + 30.0,
                ),
            ).fetchall()]

    return {
        "target_lang": target["language"],
        "target_context": fetch_lang(target["language"]),
        "jpn_context": fetch_lang("jpn") if target["language"] != "jpn" else []
    }


class ExtractConfig(BaseModel):
    """Configuration for media extraction."""

    pad_start: float = 0.5
    pad_end: float = 0.5


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
