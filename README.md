# beets-getlrc

A [beets](https://beets.io) plugin that downloads synced `.lrc` lyric files for your music library.

## What does this do?

Some digital audio players (like the Sony Walkman, HiBy R3, Shanling M0 Pro, FiiO devices, etc.) can display **synced lyrics** — the kind that scroll line-by-line as the song plays. However, they usually require a separate `.lrc` file stored right next to each track.

Beets already has a built-in `lyrics` plugin that fetches plain-text lyrics and stores them directly inside your library database. **beets-getlrc** fills a different gap: it fetches *synced* `.lrc` files from [lrclib.net](https://lrclib.net) and saves them as sidecar files directly alongside your FLAC or MP3 files.

## Installation

### If you installed beets with pip:
```bash
pip install beets-getlrc
```
### If you installed beets with pipx (recommended):
```
pipx inject beets beets-getlrc
```

## Configuration
Add getlrc to your beets configuration file (config.yaml)

plugins:
  - getlrc
  -  ... your other plugins

adjust the options as desired:


| Parameter | Default Value | Description |
| :--- | :--- | :--- |
| `auto` | `yes` | If it fetches lrc files automatically on import |
| `overwrite` | `no` | If it looks to replace lrc files already existing files |
| `timeout` | `30` | Seconds until timeout |
| `retries` | `3` | How many times it retries |
| `delay` | `0.5` | How long it waits before lookups (Seconds) |
| `cache_results` | `yes` | Remember lookups in the beets database so it does not look for files its already checked|
| `recheck_days` | `30` | How many days before retrying a song that it already looked for and cahced |
| `stats` | `yes` | Print a summary of the lrc files found |

## Usage
Run it manually any time with the beet getlrc command:
| Command                  | Description                                                           |
| ------------------------ | --------------------------------------------------------------------- |
| `beet getlrc`            | Fetch lyrics for **all** tracks in your library                       |
| `beet getlrc beatles`    | Only fetch for tracks matching "beatles"                              |
| `beet getlrc -a beatles` | Fetch for albums matching "beatles"                                   |
| `beet getlrc -f`         | Force overwrite existing `.lrc` files                                 |
| `beet getlrc -p`         | Pretend mode — shows what *would* be fetched without writing anything |

## How it Works
1. The plugin looks for the artist and other tags in the metadata
2. it looks for the lyrics at lrclib.net
3. if found, it adds them to the folder using the same name as the music file
### Example
#### Code
```
beet getlrc wetleg
getlrc: No synced lyrics: Wet Leg - Wet Leg - Being in Love
getlrc: Created: Wet Leg - Wet Leg - Chaise Longue
```
#### Resulting Directory Structure
text
├── Being in Love.flac
├── Chaise Longue.flac
└── Chaise Longue.lrc      <-- Created by beets-getlrc

## Requirements
- beets 2.0.0 or newer
- python 3.9 or later
- internet
## Notes
- This only fetches synced lyrics, so if lrclib.net does not have them, then no lrc files will be created.