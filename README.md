# Local Nadeshiko

A barebones, command-line version of [Nadeshiko](https://github.com/BrigadaSOS/Nadeshiko) designed to index, search, and extract Japanese sentences from your local anime collection.

## Features

- **Local Indexing:** Scans directories for `.mkv` video files and `.ass`/`.srt` subtitle files.
- **Embedded Subtitle Support:** Automatically extracts embedded subtitle tracks from `.mkv` files if external subtitles are not found.
- **Lightning Fast Search:** Uses SQLite FTS5 for instantaneous multi-lingual sentence searching.
- **Context Viewer:** View the surrounding sentences for any search result to understand the full context.
- **Anki Export:** Automatically slices exact audio clips and screenshots using `ffmpeg` and formats them into a TSV file ready for Anki import.

## Prerequisites

- **Python 3.10+**
- **ffmpeg** (Must be installed and available in your system's PATH)

## Installation

Using Conda (Recommended as it installs `ffmpeg` for you):
```bash
conda env create -f environment.yml
conda activate local-nadeshiko
```

Using standard pip (Make sure you install `ffmpeg` separately):
```bash
python3 -m pip install -r requirements.txt
```

## Usage

**1. Initialize the Database**
Creates the local SQLite database at `~/.local_nadeshiko.db`.
```bash
./nadeshiko init
```

**2. Index your Anime**
Scans your directory and builds the search index.
```bash
./nadeshiko index /Volumes/NAS/Anime
```

**3. Search for Sentences**
```bash
./nadeshiko search "彼女"
```

**4. View Context**
Get the sentences before and after a specific result ID.
```bash
./nadeshiko context <id>
```

**5. Export to Anki**
Extracts the exact audio snippet, a screenshot, and text into `./anki_deck` formatted for easy importing.
```bash
./nadeshiko export <id> --out ./anki_deck
```
