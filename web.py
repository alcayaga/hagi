import os

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

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
    return templates.TemplateResponse(request=request, name="index.html")


@app.get("/api/search")
def search(q: str = ""):
    if not q:
        return []
    conn = db.get_db()
    results = db.search_sentences(conn, q)
    return [dict(r) for r in results]


@app.get("/api/context/{sentence_id}")
def get_context(sentence_id: int):
    conn = db.get_db()
    target = conn.execute(
        "SELECT media_id, start_time FROM sentences WHERE id = ?", (sentence_id,)
    ).fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="Sentence not found")

    context_sentences = conn.execute(
        """
        SELECT id, start_time, text 
        FROM sentences 
        WHERE media_id = ? AND start_time >= ? AND start_time <= ?
        ORDER BY start_time ASC
    """,
        (target["media_id"], target["start_time"] - 15, target["start_time"] + 15),
    ).fetchall()

    return [dict(r) for r in context_sentences]


from pydantic import BaseModel


class ExtractConfig(BaseModel):
    pad_start: float = 0.5
    pad_end: float = 0.5


@app.post("/api/extract/{sentence_id}")
def extract(sentence_id: int, config: ExtractConfig):
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
