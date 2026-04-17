# news-speaker

News reader with multi-engine text-to-speech.

## Usage

Normal mode - display latest news items:
```bash
./news-speaker.py
```

Polling mode - continuous monitoring with TTS and popup:
```bash
./news-speaker.py -p
```

Filter by source:
```bash
./news-speaker.py -s techmeme
```

Custom RSS URL or HTML page:
```bash
./news-speaker.py -u https://example.com/feed
```

Statistics - mean time between messages per source:
```bash
./news-speaker.py --stat
```

Word frequency analysis:
```bash
./news-speaker.py --word-freq
```

## Options

- `-p, --poll` - polling mode, checks every 60 seconds
- `-d, --debug` - enable debug output
- `-s, --source` - filter by source name
- `-u, --url` - RSS URL or HTML page
- `-c, --config` - path to config file
- `-D, --use-description` - include description in display and TTS
- `-w, --width` - output width (default: MANWIDTH env or 110)
- `--stat` - show mean time statistics for all sources
- `--word-freq` - show word frequencies across all sources
- `--no-tts` - disable TTS
- `--audio-active` - check if audio playback is active

## Features

- Multi-source RSS/Atom feed parsing
- RTL formatting - proper alignment for Hebrew, Arabic, Persian, Urdu, Yiddish
- TTS - Gemini 3.1 Flash (cloud) -> Piper (offline) -> gTTS fallback chain
- Popup window - shows new items on screen
- GUID-based deduplication - handles edited headlines
- HAR file support for bot-protected sources
- Global + local config merge (~/.config/news-speaker/ + local)
- Per-source exponential backoff on 429/5xx/timeout errors
- Network-aware backoff - skips penalty on general outages
- Per-source `min_interval` config for rate-limited sources
- Config hot-reload in polling mode
- Block words filtering (global and per-source)
- Inline status line during fetch and wait

## Dependencies

```bash
pip install lxml requests gtts pydub pasimple langdetect pyyaml screeninfo python-bidi
```

Optional: `pip install google-genai` (Gemini TTS), `pip install piper-tts` (offline TTS)\
System: playerctl (media pause/resume)
