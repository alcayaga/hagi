import typer
from rich.console import Console
from rich.table import Table
import db
import indexer
import exporter

app = typer.Typer()
console = Console()

@app.command()
def init():
    """Initialize the database."""
    db.init_db()
    console.print("[green]Database initialized![/green]")

@app.command()
def index(directory: str):
    """Index a directory containing subtitle files."""
    db.init_db() # ensure db exists
    console.print(f"Indexing directory: [bold]{directory}[/bold]...")
    indexer.index_directory(directory)
    console.print("[green]Indexing complete![/green]")

@app.command()
def search(query: str):
    """Search for a sentence."""
    conn = db.get_db()
    results = db.search_sentences(conn, query)
    
    if not results:
        console.print("[yellow]No results found.[/yellow]")
        return
        
    table = Table("ID", "Time", "Text", "File")
    for r in results:
        time_str = f"{int(r['start_time']//60):02d}:{int(r['start_time']%60):02d}"
        file_name = r['path'].split('/')[-1]
        table.add_row(str(r['id']), time_str, r['text'], file_name)
        
    console.print(table)

@app.command()
def context(sentence_id: int):
    """View surrounding sentences for context."""
    conn = db.get_db()
    
    target = conn.execute("SELECT media_id, start_time FROM sentences WHERE id = ?", (sentence_id,)).fetchone()
    if not target:
        console.print(f"[red]Sentence ID {sentence_id} not found.[/red]")
        return
        
    media_id = target['media_id']
    start_time = target['start_time']
    
    context_sentences = conn.execute("""
        SELECT id, start_time, text 
        FROM sentences 
        WHERE media_id = ? AND start_time >= ? AND start_time <= ?
        ORDER BY start_time ASC
    """, (media_id, start_time - 15, start_time + 15)).fetchall()
    
    for s in context_sentences:
        prefix = ">> " if s['id'] == sentence_id else "   "
        time_str = f"{int(s['start_time']//60):02d}:{int(s['start_time']%60):02d}"
        color = "green" if s['id'] == sentence_id else "white"
        console.print(f"[{color}]{prefix}[{time_str}] {s['text']}[/{color}]")

@app.command()
def export(sentence_id: int, out_dir: str = "./anki_deck"):
    """Export sentence context (audio, image, text) for Anki."""
    console.print(f"Exporting sentence {sentence_id}...")
    success, msg = exporter.export_anki(sentence_id, out_dir)
    if success:
        console.print(f"[green]{msg}[/green]")
    else:
        console.print(f"[red]Error: {msg}[/red]")

if __name__ == "__main__":
    app()

