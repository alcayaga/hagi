# Hagi

A barebones version of [Nadeshiko](https://github.com/BrigadaSOS/Nadeshiko) designed to index, search, and extract Japanese sentences from your local anime collection.

## Features

- **Local Indexing:** Scans directories for `.mkv` video files and `.ass`/`.srt` subtitle files.
- **Advanced Subtitle Parsing:** Automatically extracts *all* embedded subtitle tracks from `.mkv` files, dynamically detects the language based on content (not just filenames), and perfectly handles Dual-Audio (AO) releases by extracting the correct Japanese audio track via `ffprobe`.
- **Lightning Fast Search:** Uses dynamic SQLite queries for instantaneous multi-lingual sentence searching (with support for exact phrase matching).
- **Web UI & Context Viewer:** Spin up a beautiful local web interface to search visually, view surrounding sentence context, and play extracted audio clips instantly right in your browser.
- **Media Extraction & Anki Export:** Automatically slices exact audio clips and screenshots using `ffmpeg`.

## Prerequisites

- **Python 3.10+**
- **ffmpeg** (Must be installed and available in your system's PATH)

## Installation

Using Conda (Recommended as it installs `ffmpeg` for you):
```bash
conda env create -f environment.yml
conda activate hagi
```

## Usage

**1. Initialize the Database**
Creates a local SQLite database at `./hagi.db` in your project folder.
```bash
./hagi init
```

**2. Index your Anime**
Scans your directory and builds the search index. You can pass a specific directory directly:
```bash
./hagi index "/Volumes/NAS/Anime/Shaman King"
```
Alternatively, if you create a `config.json` file in the root folder containing a list of directories, you can simply run the command with no arguments to index all of them automatically:
```json
{
  "directories": [
    "/Volumes/NAS/Anime/Show 1",
    "/Volumes/NAS/Anime/Show 2"
  ]
}
```
```bash
./hagi index
```

**3. Launch the Web UI (Recommended)**
Start the local FastAPI web server to search visually and extract media with the click of a button!
```bash
./hagi ui
```
Open **[http://localhost:8000](http://localhost:8000)** in your browser.

**4. CLI Search**
If you prefer the terminal, you can search directly:
```bash
./hagi search "彼女"
```

**5. CLI Export to Anki**
Extracts the exact audio snippet, a screenshot, and text into `./anki_deck` formatted for easy importing.
```bash
./hagi export <id> --out ./anki_deck
```

## Code Maintenance
This project uses `ruff` to ensure lightning-fast linting and formatting. 
To format the code, simply run:
```bash
ruff format .
ruff check --fix .
```
