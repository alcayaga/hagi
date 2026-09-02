"""Command-line interface for Hagi Local."""

import os
import json
from typing import Optional
import typer
from rich.console import Console
from rich.table import Table

import db
import exporter
import indexer

app = typer.Typer()
console = Console()


@app.command()
def init():
    """Initialize the database."""
    db.init_db()
    console.print("[green]Database initialized![/green]")


@app.command()
def index(directory: Optional[str] = typer.Argument(None)):
    """Index a directory or multiple directories from config.json."""
    db.init_db()  # ensure db exists

    directories_to_index = []

    if directory:
        directories_to_index.append(directory)
    else:
        # Try to load config.json
        if os.path.exists("config.json"):
            with open("config.json", "r") as f:
                try:
                    config = json.load(f)
                    if "directories" in config and isinstance(config["directories"], list):
                        directories_to_index.extend(config["directories"])
                except Exception as e:
                    console.print(f"[red]Error parsing config.json: {e}[/red]")
                    raise typer.Exit(code=1)

        if not directories_to_index:
            console.print("[red]Error: Please provide a directory argument or specify 'directories' in config.json.[/red]")
            raise typer.Exit(code=1)

    for dir_path in directories_to_index:
        if not os.path.isdir(dir_path):
            if directory:
                console.print(f"[red]Error: Directory '{dir_path}' does not exist or is not a directory.[/red]")
                raise typer.Exit(code=1)
            else:
                console.print(f"[yellow]Warning: Directory '{dir_path}' does not exist or is not a directory. Skipping.[/yellow]")
                continue

        console.print(f"Indexing directory: [bold]{dir_path}[/bold]...")
        indexer.index_directory(dir_path)

    console.print("[green]Indexing complete![/green]")


@app.command()
def search(query: str):
    """Search for a sentence."""
    conn = db.get_db()
    results = db.search_sentences(conn, query)

    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return

    table = Table("ID", "Lang", "Time", "Text", "File")
    for r in results:
        if r["start_time"] is not None:
            time_str = f"{int(r['start_time'] // 60):02d}:{int(r['start_time'] % 60):02d}"
        else:
            time_str = "??:??"
        file_name = r["path"].split("/")[-1]

        # Color code the language tag for readability
        lang = r["language"]
        lang_color = "cyan" if lang == "jpn" else "yellow" if lang == "eng" else "white"
        lang_display = f"[{lang_color}]{lang}[/{lang_color}]"

        table.add_row(str(r["id"]), lang_display, time_str, r["text"], file_name)

    console.print(table)


@app.command()
def context(sentence_id: int):
    """View surrounding sentences for context."""
    conn = db.get_db()

    target = conn.execute("SELECT media_id, start_time FROM sentences WHERE id = ?", (sentence_id,)).fetchone()
    if not target:
        console.print(f"[red]Sentence ID {sentence_id} not found.[/red]")
        return

    media_id = target["media_id"]
    start_time = target["start_time"]

    if start_time is None:
        context_sentences = conn.execute(
            """
            SELECT id, start_time, text
            FROM sentences
            WHERE media_id = ? AND id >= ? AND id <= ?
            ORDER BY id ASC
        """,
            (media_id, sentence_id - 10, sentence_id + 10),
        ).fetchall()
    else:
        context_sentences = conn.execute(
            """
            SELECT id, start_time, text
            FROM sentences
            WHERE media_id = ? AND start_time >= ? AND start_time <= ?
            ORDER BY start_time ASC
        """,
            (media_id, start_time - 15, start_time + 15),
        ).fetchall()

    for s in context_sentences:
        prefix = ">> " if s["id"] == sentence_id else "   "
        if s["start_time"] is not None:
            time_str = f"{int(s['start_time'] // 60):02d}:{int(s['start_time'] % 60):02d}"
        else:
            time_str = "??:??"
        color = "green" if s["id"] == sentence_id else "white"
        console.print(f"[{color}]{prefix}[{time_str}] {s['text']}[/{color}]")


@app.command()
def extract(
    sentence_id: int,
    out_dir: str = "./media",
    pad_start: float = typer.Option(0.25, "--pad-start", "-ps", help="Seconds to pad before the sentence"),
    pad_end: float = typer.Option(0.0, "--pad-end", "-pe", help="Seconds to pad after the sentence"),
):
    """Extract raw audio and image for a sentence without Anki formatting."""
    console.print(f"Extracting media for sentence {sentence_id}...")
    success, msg, audio_out, image_out, text, is_cached = exporter.extract_media(sentence_id, out_dir, pad_start, pad_end)
    if success:
        console.print(f"[green]Extracted audio to: {audio_out}[/green]")
        console.print(f"[green]Extracted image to: {image_out}[/green]")
        console.print(f"[cyan]Text: {text}[/cyan]")
    else:
        console.print(f"[red]Error: {msg}[/red]")


@app.command()
def export(
    sentence_id: int,
    out_dir: str = "./anki_deck",
    pad_start: float = typer.Option(0.25, "--pad-start", "-ps", help="Seconds to pad before the sentence"),
    pad_end: float = typer.Option(0.0, "--pad-end", "-pe", help="Seconds to pad after the sentence"),
):
    """Export sentence context (audio, image, text) for Anki."""
    console.print(f"Exporting sentence {sentence_id} to Anki format...")
    success, msg = exporter.export_anki(sentence_id, out_dir, pad_start, pad_end)
    if success:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[red]Error: {msg}[/red]")


@app.command()
def anki(
    sentence_id: int,
    note_id: Optional[int] = typer.Option(
        None, "--note-id", "-n", help="Target specific Anki Note ID. Defaults to last created note."
    ),
    pad_start: float = typer.Option(0.25, "--pad-start", "-ps", help="Seconds to pad before the sentence"),
    pad_end: float = typer.Option(0.0, "--pad-end", "-pe", help="Seconds to pad after the sentence"),
):
    """Export sentence directly to Anki via AnkiConnect."""
    if not os.path.exists("config.json"):
        console.print("[red]Error: config.json not found. Please create it with AnkiConnect settings.[/red]")
        raise typer.Exit(code=1)

    try:
        with open("config.json", "r") as f:
            config = json.load(f)
    except Exception as e:
        console.print(f"[red]Error parsing config.json: {e}[/red]")
        raise typer.Exit(code=1)

    console.print(f"Exporting sentence {sentence_id} via AnkiConnect...")

    # We use the standard media directory for the extracted files
    out_dir = "./media"
    success, msg, is_cached = exporter.export_ankiconnect(
        sentence_id, config, out_dir, pad_start, pad_end, target_note_id=note_id
    )
    if success:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[red]Error: {msg}[/red]")
        raise typer.Exit(code=1)


@app.command()
def ui(port: int = 8000, host: str = "127.0.0.1"):
    """Launch the Hagi local web interface."""
    import uvicorn

    from web import app as web_app

    # Ensure database migrations are run
    db.init_db()

    console.print(f"[green]Starting Hagi Web UI at http://{host}:{port}[/green]")
    console.print("Press Ctrl+C to quit.")

    # We use log_level warning to keep the terminal clean while using the app
    uvicorn.run(web_app, host=host, port=port, log_level="warning")


if __name__ == "__main__":
    app()
