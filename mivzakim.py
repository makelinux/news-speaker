#!/usr/bin/env python3

import sys
import os
import time
import json
import argparse
import subprocess
import textwrap
import html as html_lib
from datetime import datetime, timedelta
from io import BytesIO
from collections import deque

import re
from lxml import etree
import requests
from gtts import gTTS
from pydub import AudioSegment
import pasimple
from langdetect import detect
import yaml

from hebrew_format import format_rtl_text

session = requests.Session()
session.headers.update({
    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
})

def _load_yaml(path):
    if not os.path.exists(path):
        return {}
    try:
        if args.debug:
            print(f"Loading {path}", file=sys.stderr)
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        mark = getattr(e, 'problem_mark', None)
        if mark:
            print(f"{path}:{mark.line + 1}:{mark.column + 1} {e.problem}", file=sys.stderr)
        else:
            print(f"{path}: {e}", file=sys.stderr)
        return {}

def _deep_merge(base, override):
    for k, v in override.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_merge(base[k], v)
        elif k in base and isinstance(base[k], list) and isinstance(v, list):
            base[k] = base[k] + v
        else:
            base[k] = v

def load_config(path=None):
    global config, MAX_ITEMS, POLL_INTERVAL, TTS_VOLUME_ADJUST, TTS_VOICES, BLOCK_WORDS, REPLACE_RULES
    global_path = os.path.expanduser('~/.config/news-reader/config.yaml')
    local_path = path or os.path.join(os.path.dirname(__file__), 'config.yaml')
    config = _load_yaml(global_path)
    _deep_merge(config, _load_yaml(local_path))
    s = config.get('settings', {})
    MAX_ITEMS = s.get('max_items', 10)
    POLL_INTERVAL = s.get('poll_interval', 60)
    TTS_VOLUME_ADJUST = s.get('tts_volume_adjust', -10)
    TTS_VOICES = s.get('tts_voices', [])
    bwf = s.get('block_words_file')
    if bwf:
        p = os.path.expanduser(bwf) if bwf.startswith('~') else os.path.join(os.path.dirname(__file__), bwf)
        try:
            with open(p) as f:
                BLOCK_WORDS = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            print(f"{bwf}: not found", file=sys.stderr)
            BLOCK_WORDS = []
    else:
        BLOCK_WORDS = s.get('block_words', [])
    REPLACE_RULES = [(re.compile(r['pattern']), r.get('replace', ''))
                     for r in s.get('replace', [])]

config = {}
MAX_ITEMS = POLL_INTERVAL = TTS_VOLUME_ADJUST = 0
TTS_VOICES = []
BLOCK_WORDS = []
REPLACE_RULES = []
# Parse arguments
parser = argparse.ArgumentParser(description='Hebrew news reader')
parser.add_argument('-p', '--poll', action='store_true',
                    help='Polling mode')
parser.add_argument('-d', '--debug', action='store_true',
                    help='Enable debug output')
parser.add_argument('-s', '--source', type=str,
                    help='Filter by source name (e.g., "Ynet", "N12")')
parser.add_argument('-u', '--url', type=str,
                    help='RSS URL or HTML page to list links')
parser.add_argument('-c', '--config', type=str,
                    help='Path to config file (default: ./config.yaml)')
parser.add_argument('-D', '--use-description', action='store_true',
                    help='Include description field in display and TTS')
parser.add_argument('--stat', action='store_true',
                    help='Show mean time statistics for all configured sources')
parser.add_argument('-w', '--width', type=int,
                    help='Output width (default: MANWIDTH env or 110)')
parser.add_argument('--audio-active', action='store_true',
                    help='Check if audio playback is active')
parser.add_argument('--no-tts', action='store_true',
                    help='Disable TTS')
parser.add_argument('--word-freq', action='store_true',
                    help='Show word frequencies across all sources')
parser.add_argument('--test-popup', action='store_true',
                    help='Show test popup window')
args = parser.parse_args()

WIDTH = args.width if args.width else int(os.environ.get('MANWIDTH', 110))

poll_mode = args.poll

load_config(args.config)
source_filter = args.source

# Get sources from args or config
if args.url:
    # Look up configured source for URL, merge settings
    src = {'url': args.url, 'name': '', 'use_description': args.use_description}
    for s in config.get('sources', []):
        if s.get('url') == args.url:
            src.update(s)
            break
    enabled_sources = [src]
else:
    sources = config.get('sources', [])
    enabled_sources = [s for s in sources if s.get('enabled', True)]
    if not enabled_sources:
        enabled_sources = [{'url': 'https://rss.mivzakim.net/rss/category/1', 'name': 'Mivzakim', 'use_description': False}]

if args.debug:
    # Collect all block words: global + per-source
    all_bw = list(BLOCK_WORDS)
    for s in enabled_sources:
        all_bw.extend(s.get('block_words', []))
    bw = sorted({p.strip() for w in all_bw for p in w.split(',') if p.strip()})
    if bw:
        print("block_words:", file=sys.stderr)
        for w in bw:
            print(f"  {w}", file=sys.stderr)

seen = deque(maxlen=10000)
first_poll = True
last_spoken = None

# Per-source backoff state: url -> {skip_until, delay}
backoff = {}
BACKOFF_FILE = os.path.join(os.path.dirname(__file__), '.backoff.json')
BASE_DELAY = 60

def load_backoff():
    global backoff
    try:
        with open(BACKOFF_FILE) as f:
            saved = json.load(f)
        for url, s in saved.items():
            if isinstance(s, dict):
                s['from_file'] = True
                s['skip_until'] = min(s['skip_until'], time.time() + s['delay'])
                backoff[url] = s
            else:
                backoff[url] = {'skip_until': time.time() + s, 'delay': s, 'from_file': True}
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_backoff():
    # Don't save min_interval entries - they're in config
    min_urls = {s['url'] for s in config.get('sources', []) if s.get('min_interval')}
    saved = {url: {'skip_until': s['skip_until'], 'delay': s['delay']}
             for url, s in backoff.items()
             if s['delay'] > BASE_DELAY and url not in min_urls}
    try:
        with open(BACKOFF_FILE, 'w') as f:
            json.dump(saved, f)
    except OSError:
        pass

load_backoff()
net_ok = None  # None=untested, True/False per poll cycle

def check_network():
    global net_ok
    if net_ok is not None:
        return net_ok
    try:
        session.head('https://www.google.com', timeout=5)
        net_ok = True
    except Exception:
        net_ok = False
    return net_ok

def status(msg=''):
    """Show status on current line, wipe with empty call"""
    if msg:
        print(f"\r{msg}\033[K", end='', flush=True, file=sys.stderr)
    else:
        print(f"\r\033[K", end='', flush=True, file=sys.stderr)

def log_debug(msg):
    """Print debug message if debug mode enabled"""
    if args.debug:
        print(f"{msg}", file=sys.stderr)


def list_html_links(url):
    """List links from HTML page"""
    import re
    from urllib.parse import urljoin
    try:
        response = session.get(url, timeout=10)
        response.raise_for_status()

        # Check if it's HTML or RSS/XML
        content_type = response.headers.get('Content-Type', '').lower()
        is_html = 'html' in content_type or '<html' in response.text[:500].lower()

        if not is_html:
            # Not HTML, treat as RSS
            return False

        response.encoding = 'utf-8'
        html = response.text

        # Try multiple section header patterns
        section_patterns = [
            r'<div class="subTitle">([^<]+)</div>',
            r'<h2[^>]*>([^<]+)</h2>',
            r'<h3[^>]*>([^<]+)</h3>',
            r'<div[^>]*class="[^"]*(?:title|header|section)[^"]*"[^>]*>([^<]+)</div>',
        ]

        sections = None
        for pat in section_patterns:
            s = re.split(pat, html)
            if len(s) > 1:
                sections = s
                break

        if sections and len(sections) > 1:
            # Found sections, group links by section
            current_section = None
            for i, part in enumerate(sections):
                if i % 2 == 1:
                    current_section = part.strip()
                    print(f"\n{current_section}")
                elif i % 2 == 0 and current_section:
                    matches = re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', part)
                    for link, text in matches:
                        text = text.strip()
                        if text and not text.isspace():
                            if link.startswith('/'):
                                link = urljoin(url, link)
                            print(f"{text} - {link}")
        else:
            # No sections, just extract all links
            matches = re.findall(r'href="([^"]+)"[^>]*>([^<]+)</a>', html)
            for link, text in matches:
                text = text.strip()
                if text and not text.isspace():
                    if link.startswith('/'):
                        link = urljoin(url, link)
                    print(f"{text} - {link}")

        return True
    except Exception:
        return False


def list_audio():
    """List active audio playback and microphone usage"""
    print("Active audio playback:")
    try:
        result = subprocess.run(['pactl', 'list', 'sink-inputs'],
                                capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            found = False
            for line in result.stdout.split('\n'):
                if 'application.name' in line or 'application.process.binary' in line or 'media.name' in line:
                    print(f"  {line.strip()}")
                    found = True
            if not found:
                print("  None")
    except Exception as e:
        print(f"  Error: {e}")

    print("\nActive microphone usage:")
    try:
        result = subprocess.run(['pactl', 'list', 'source-outputs'],
                                capture_output=True, text=True, timeout=2)
        if result.returncode == 0:
            found = False
            for line in result.stdout.split('\n'):
                if 'application.name' in line or 'application.process.binary' in line or 'media.name' in line:
                    print(f"  {line.strip()}")
                    found = True
            if not found:
                print("  None")
    except Exception as e:
        print(f"  Error: {e}")


def is_microphone_active():
    """Check if microphone is being used (videoconferencing)"""
    try:
        result = subprocess.run(['pactl', 'list', 'source-outputs'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0 and result.stdout.strip():
            # Filter out GNOME Settings (just the level meter)
            for line in result.stdout.split('\n'):
                if 'application.name' in line:
                    app = line.split('=')[1].strip().strip('"')
                    if 'GNOME Settings' not in app:
                        log_debug(f"Active microphone: {app}")
                        return True
    except Exception as e:
        log_debug(f"pactl source check failed: {e}")
    return False


def is_audio_active():
    """Check for any active audio playback (not paused/corked)"""
    # Check PulseAudio/PipeWire for active audio streams
    try:
        result = subprocess.run(['pactl', 'list', 'sink-inputs'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0 and result.stdout.strip():
            # Check if any stream is not corked (i.e., actively playing)
            lines = result.stdout.split('\n')
            current_corked = None
            active_count = 0
            for line in lines:
                if 'Sink Input #' in line:
                    current_corked = None
                if 'Corked:' in line:
                    current_corked = 'yes' in line.lower()
                if 'application.name' in line and current_corked == False:
                    active_count += 1
                    app = line.split('=')[1].strip().strip('"')
                    log_debug(f"Active audio: {app}")
            if active_count > 0:
                log_debug(f"Active (not corked) audio streams: {active_count}")
                return True
    except Exception as e:
        log_debug(f"pactl sink check failed: {e}")

    return False


def is_media_playing():
    try:
        r = subprocess.run(['playerctl', 'status'],
                           capture_output=True, text=True, timeout=1)
        return r.returncode == 0 and r.stdout.strip() == 'Playing'
    except Exception:
        return False

def pause_media():
    if is_media_playing():
        os.system('playerctl pause 2>/dev/null')
        log_debug("Paused media playback")
        return True
    return False


def resume_media():
    """Resume media playback"""
    try:
        os.system('playerctl play 2>/dev/null')
        log_debug("Resumed media playback")
    except Exception:
        pass


def _play_audio(audio):
    audio += TTS_VOLUME_ADJUST
    with pasimple.PaSimple(
        pasimple.PA_STREAM_PLAYBACK,
        pasimple.PA_SAMPLE_S16LE,
        audio.channels,
        audio.frame_rate,
        app_name="news-app",
        stream_name="playback",
    ) as pa:
        pa.write(audio.raw_data)
        pa.drain()

import random

def _speak_gemini(text):
    from google import genai
    from google.genai import types
    voice = random.choice(TTS_VOICES) if TTS_VOICES else 'Kore'
    status(f"TTS {voice}")
    c = genai.Client()
    r = c.models.generate_content(
        model="gemini-3.1-flash-tts-preview",
        contents=text,
        config=types.GenerateContentConfig(
            response_modalities=["AUDIO"],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(
                        voice_name=voice
                    )
                )
            )
        )
    )
    data = r.candidates[0].content.parts[0].inline_data.data
    audio = AudioSegment(data=data, sample_width=2, frame_rate=24000, channels=1)
    _play_audio(audio)
    status()
    print(f"  {voice}", file=sys.stderr)

def _speak_gtts(lang, text):
    if lang == 'he':
        lang = 'iw'
    status("TTS gTTS...")
    tts = gTTS(text, lang=lang)
    buf = BytesIO()
    tts.write_to_fp(buf)
    buf.seek(0)
    audio = AudioSegment.from_mp3(buf)
    buf.close()
    _play_audio(audio)

def speak_text(lang, text):
    try:
        log_debug(f"TTS: {text[:50]}... (lang: {lang})")
        if os.environ.get("GOOGLE_API_KEY"):
            try:
                _speak_gemini(text)
                log_debug("TTS completed (Gemini)")
                return
            except Exception as e:
                log_debug(f"Gemini TTS failed: {e}, falling back to gTTS")
        _speak_gtts(lang, text)
        log_debug("TTS completed (gTTS)")
    except Exception as e:
        print(f"TTS error: {e}", file=sys.stderr)


def fetch_rss(source_config, limit=None):
    url = source_config['url']
    name = source_config.get('name', url)
    if limit is None:
        limit = MAX_ITEMS

    # Check min_interval
    min_iv = source_config.get('min_interval', 0)
    if min_iv and url not in backoff:
        backoff[url] = {'skip_until': 0, 'delay': min_iv}

    # Check backoff
    s = backoff.get(url)
    if s and time.time() < s['skip_until']:
        r = int(s['skip_until'] - time.time())
        t = f"{r // 3600}h" if r >= 3600 else f"{r // 60}m" if r >= 60 else f"{r}s"
        log_debug(f"Backoff {name} for {t}")
        return []

    headers = {'Accept': 'application/rss+xml, application/xml, text/xml, */*'}
    cookies = {}
    har = source_config.get('har')
    if har:
        har_path = os.path.join(os.path.dirname(__file__), har)
        try:
            with open(har_path) as f:
                har_data = json.load(f)
            for entry in har_data['log']['entries']:
                if url in entry['request']['url']:
                    for h in entry['request']['headers']:
                        if not h['name'].startswith(':'):
                            headers[h['name']] = h['value']
                    for c in entry['request'].get('cookies', []):
                        cookies[c['name']] = c['value']
                    break
        except Exception as e:
            log_debug(f"HAR load failed: {e}")
    if source_config.get('cookies'):
        for pair in source_config['cookies'].split('; '):
            if '=' in pair:
                k, v = pair.split('=', 1)
                cookies[k] = v

    # Retry up to 3 times on failure
    root = None
    content_len = 0
    for attempt in range(3):
        try:
            response = session.get(url, timeout=30, headers=headers, cookies=cookies)
            response.raise_for_status()
            content_len = len(response.content)
            parser = etree.XMLParser(recover=True)
            root = etree.fromstring(response.content, parser)
            break
        except requests.exceptions.HTTPError as e:
            if response.status_code in (429,) or response.status_code >= 500:
                if url not in backoff:
                    backoff[url] = {'skip_until': 0, 'delay': BASE_DELAY}
                backoff[url]['delay'] = min(backoff[url]['delay'] * 2, 86400)
                backoff[url]['skip_until'] = time.time() + backoff[url]['delay']
                save_backoff()
                d = backoff[url]['delay']
                t = f"{d // 3600}h" if d >= 3600 else f"{d // 60}m" if d >= 60 else f"{d}s"
                status()
                print(f"{name}: {response.status_code} {response.reason}, backing off {t}", file=sys.stderr)
                return []
            if attempt < 2:
                log_debug(f"Attempt {attempt + 1} failed for {name}: {response.status_code}")
                time.sleep(1)
            else:
                msg = f"{name}: {response.status_code} {response.reason}"
                if response.status_code == 403 and not har:
                    from urllib.parse import urlparse
                    host = urlparse(url).hostname
                    msg += f", download HAR in browser, add to config: har: {host}.har"
                status()
                print(msg, file=sys.stderr)
                return []
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            reason = getattr(e.args[0], 'reason', e) if e.args else e
            if not check_network():
                # General network failure, don't penalize this source
                log_debug(f"{name}: network down, skipping backoff")
                return []
            # Source-specific failure, apply backoff
            if url not in backoff:
                backoff[url] = {'skip_until': 0, 'delay': BASE_DELAY}
            backoff[url]['delay'] = min(backoff[url]['delay'] * 2, 86400)
            backoff[url]['skip_until'] = time.time() + backoff[url]['delay']
            save_backoff()
            d = backoff[url]['delay']
            t = f"{d // 3600}h" if d >= 3600 else f"{d // 60}m" if d >= 60 else f"{d}s"
            status()
            print(f"{name}: {type(reason).__name__}, backing off {t}", file=sys.stderr)
            return []
        except Exception as e:
            if attempt < 2:
                log_debug(f"Attempt {attempt + 1} failed for {name}: {e}, retrying...")
                time.sleep(1)
            else:
                status()
                print(f"{name}: {e}", file=sys.stderr)
                return []

    if root is None:
        return []

    # Success after backoff
    if url in backoff:
        from_file = backoff[url].get('from_file')
        log_debug(f"{name}: success, backoff was {backoff[url]['delay']}s")
        if min_iv:
            backoff[url] = {'skip_until': time.time() + min_iv, 'delay': min_iv}
        else:
            del backoff[url]
        save_backoff()
        if from_file:
            # First recovery after startup - seed seen, don't flood
            for item in root.xpath('//*[local-name()="item" or local-name()="entry"]'):
                g = item.find('guid')
                if g is None:
                    g = item.find('link')
                if g is None:
                    link = item.find('.//{http://www.w3.org/2005/Atom}link')
                    key = link.get('href', '') if link is not None else ''
                else:
                    key = g.text.strip() if g.text else ''
                if not key:
                    t = item.find('title')
                    if t is None:
                        t = item.find('.//{http://www.w3.org/2005/Atom}title')
                    key = t.text.strip() if t is not None and t.text else ''
                if key:
                    seen.append(key)
            return []


    configured_name = source_config.get('name', '')
    channel_name = configured_name
    if not channel_name:
        channel_title_elem = root.find('.//channel/title')
        if channel_title_elem is None:
            channel_title_elem = root.find('.//{http://www.w3.org/2005/Atom}title')
        if channel_title_elem is not None and channel_title_elem.text:
            channel_name = channel_title_elem.text.strip()

    use_desc = args.use_description if args.use_description else source_config.get('use_description', False)
    src_filter = source_config.get('source_filter')
    # Expand comma-separated words and strip whitespace
    all_block_words = BLOCK_WORDS + source_config.get('block_words', [])
    block_words = []
    for word in all_block_words:
        if ',' in word:
            block_words.extend([w.strip().lower() for w in word.split(',')])
        else:
            block_words.append(word.lower())
    src_replace = [(re.compile(r['pattern']), r.get('replace', ''))
                    for r in source_config.get('replace', [])]
    replace_rules = REPLACE_RULES + src_replace
    if block_words:
        log_debug(f"{name} block: {', '.join(block_words)}")

    feed_items = root.xpath('//*[local-name()="item" or local-name()="entry"]')
    items = []
    for item in feed_items:
        if len(items) >= limit:
            break
        title = item.find('title')
        if title is None:
            title = item.find('.//{http://www.w3.org/2005/Atom}title')
        pubdate = item.find('pubDate')
        if pubdate is None:
            pubdate = item.find('.//{http://www.w3.org/2005/Atom}published')
        if pubdate is None:
            pubdate = item.find('.//{http://www.w3.org/2005/Atom}updated')
        source = item.find('source')
        description = item.find('description')
        if description is None:
            description = item.find('.//{http://www.w3.org/2005/Atom}summary')
        guid = item.find('guid')
        if guid is None:
            guid = item.find('link')
        if guid is None:
            link = item.find('.//{http://www.w3.org/2005/Atom}link')
            guid_text = link.get('href', '') if link is not None else ''
        else:
            guid_text = guid.text.strip() if guid.text else ''
        if title is not None and title.text and pubdate is not None and pubdate.text:
            title_text = title.text.strip()
            dt_str = pubdate.text.strip()
            src = configured_name if configured_name else (source.text.strip().split(' - ')[0] if source is not None and source.text else channel_name)
            desc = description.text.strip() if description is not None and description.text else ''

            # Apply source filter if configured
            if src_filter and src_filter not in src:
                continue

            # Skip items with blocked words in title or description
            text = f"{title_text} {desc}".lower()
            if any(word in text for word in block_words):
                continue

            for pat, repl in replace_rules:
                title_text = pat.sub(repl, title_text)
            try:
                dt = parse_datetime(dt_str)
                key = guid_text or title_text
                items.append((dt, title_text, dt_str, src, desc, use_desc, key))
            except Exception as e:
                log_debug(f"Failed to parse date '{dt_str}': {e}")

    log_debug(f"{len(feed_items)} items {content_len} bytes {url}")
    return items


def fetch_news():
    global net_ok
    net_ok = None  # Reset per poll cycle
    all_items = []
    for source in enabled_sources:
        status(source.get('name', source['url']))
        items = fetch_rss(source)
        all_items.extend(items)
    status()

    # Sort by datetime (newest first)
    all_items.sort(key=lambda x: x[0], reverse=True)
    log_debug(f"Total items: {len(all_items)}, seen: {len(seen)}, first_poll: {first_poll}")

    return all_items


popup_window = None

def is_dark_theme():
    try:
        r = subprocess.run(
            ['gsettings', 'get', 'org.gnome.desktop.interface', 'color-scheme'],
            capture_output=True, text=True, timeout=1)
        if r.returncode == 0 and 'dark' in r.stdout.lower():
            return True
        r = subprocess.run(
            ['gsettings', 'get', 'org.gnome.desktop.interface', 'gtk-theme'],
            capture_output=True, text=True, timeout=1)
        if r.returncode == 0 and 'dark' in r.stdout.lower():
            return True
    except Exception:
        pass
    return False

popup_backend = None  # 'tk' or 'gtk'

def show_popup(items):
    """Show news items in a topmost popup window (tkinter or GTK fallback)"""
    global popup_window, popup_backend
    hide_popup()
    if not items:
        return

    dark = is_dark_theme()
    bg = "#222" if dark else "#f0f0f0"
    fg = "#eee" if dark else "#111"

    try:
        _show_popup_tk(items, bg, fg)
        popup_backend = 'tk'
    except Exception:
        _show_popup_gtk(items, bg, fg)
        popup_backend = 'gtk'

def _show_popup_tk(items, bg, fg):
    global popup_window
    import tkinter as tk
    from bidi.algorithm import get_display
    popup_window = tk.Tk()
    popup_window.title("News")
    popup_window.overrideredirect(True)
    popup_window.attributes('-topmost', True)
    popup_window.configure(bg=bg)

    from math import ceil
    import tkinter.font as tkfont
    f = tkfont.Font(family='sans', size=11)

    mw = WIDTH * f.measure('m')
    for title, ts, src, *_ in items:
        tw = f.measure(get_display(title))
        if any('\u0590' <= c <= '\u05FF' for c in title):
            d = dict(anchor='e', justify='right')
        else:
            d = dict(anchor='w', justify='left')
        d.update(bg=bg, fg=fg, font=('sans', 11), padx=15, pady=5)
        d['text'] = get_display(title)
        d['wraplength'] = tw // ceil(tw / mw) + f.measure('m') * 5 if tw > mw else 0
        tk.Label(popup_window, **d).pack(fill='x')

    popup_window.update_idletasks()
    w = popup_window.winfo_reqwidth()
    h = popup_window.winfo_reqheight()
    sw = popup_window.winfo_screenwidth()
    popup_window.geometry(f"{w}x{h}+{(sw - w) // 2}+10")
    popup_window.wait_visibility()
    popup_window.attributes('-alpha', 0.92)
    popup_window.update()

def _show_popup_gtk(items, bg, fg):
    global popup_window
    import gi
    gi.require_version('Gtk', '3.0')
    from gi.repository import Gtk

    css = Gtk.CssProvider()
    css.load_from_data(f"window {{ background-color: {bg}; }} label {{ color: {fg}; }}".encode())

    popup_window = Gtk.Window(title="News")
    popup_window.set_decorated(False)
    popup_window.set_keep_above(True)
    popup_window.set_accept_focus(False)
    popup_window.set_focus_on_map(False)
    popup_window.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
    box.set_margin_start(10)
    box.set_margin_end(10)
    box.set_margin_top(10)
    box.set_margin_bottom(10)
    popup_window.add(box)

    for title, ts, src, *_ in items:
        text = title
        label = Gtk.Label(label=text)
        label.set_xalign(1.0)
        label.set_line_wrap(True)
        label.get_style_context().add_provider(css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        box.pack_start(label, False, False, 0)

    popup_window.set_default_size(WIDTH * 8, -1)
    popup_window.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration()

def hide_popup():
    global popup_window, popup_backend
    if popup_window:
        popup_window.destroy()
        if popup_backend == 'gtk':
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk
            while Gtk.events_pending():
                Gtk.main_iteration()
        popup_window = None
        popup_backend = None


def print_item(title, ts, src, desc='', use_desc=False):
    """Print news item and optionally speak"""
    global last_spoken

    # Decode HTML entities in title
    title = html_lib.unescape(title)

    # Strip HTML tags from description
    if desc:
        desc = re.sub(r'<[^>]+>', '', desc)
        desc = html_lib.unescape(desc)
        desc = ' '.join(desc.split())  # Normalize whitespace

    try:
        lang = detect(title)
    except:
        lang = 'he'

    line = f"{ts} - {title}"
    from math import ceil
    w = WIDTH - 8
    if len(line) > w:
        w = len(line) // ceil(len(line) / w) + 10
    log_debug(f"WIDTH={WIDTH}")
    if lang == 'he':
        wrapped_lines = textwrap.wrap(line, width=w)
        log_debug(f"lines={len(wrapped_lines)}")
        for i, wrapped_line in enumerate(wrapped_lines):
            log_debug(f"{wrapped_line}")
            lw = WIDTH if i == 0 else WIDTH - 8
            print(format_rtl_text(wrapped_line, lw))

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=WIDTH-38, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(8*' ' + f"{src}")
    else:
        print(textwrap.fill(line, width=w, subsequent_indent=8*' '))

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=WIDTH-38, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(f"{src.rjust(WIDTH-8)}")
    sys.stdout.flush()
    if poll_mode and not args.no_tts and not is_microphone_active():
        text_to_speak = f"{title}. {desc}" if desc and use_desc else title
        text_to_speak = text_to_speak.strip()
        if text_to_speak != last_spoken:
            log_debug(f"Speaking: {text_to_speak[:50]}...")
            speak_text(lang, text_to_speak)
            last_spoken = text_to_speak
        else:
            log_debug("Skipping TTS - same as last spoken")


def parse_datetime(dt_str):
    """Parse datetime string to datetime object"""
    try:
        # ISO format (Ynet)
        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        return dt_utc.astimezone()
    except ValueError:
        # RFC 2822 format (RSS)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(dt_str)
        return dt.astimezone()

def parse_time(dt_str):
    """Parse datetime string to local time string"""
    dt = parse_datetime(dt_str)
    today = datetime.now().date()
    if dt.date() == today:
        return dt.strftime('%H:%M')
    else:
        return dt.strftime('%a %H:%M')


def print_mean_time(news_items):
    """Print mean time between messages in URL mode"""
    if len(news_items) < 2:
        return
    mt = (news_items[0][0] - news_items[-1][0]) / (len(news_items) - 1)
    s = str(timedelta(seconds=int(mt.total_seconds())))
    if s.startswith('0:'):
        s = s[2:]
    print(f"Mean time between messages: {s}", file=sys.stderr)


def show_news(news_items):
    global first_poll
    if not news_items:
        return

    items = []
    for i, item in enumerate(news_items):
        dt, title_text, dt_str, src = item[0], item[1], item[2], item[3]
        desc = item[4] if len(item) > 4 else ''
        use_desc = item[5] if len(item) > 5 else False
        key = item[6] if len(item) > 6 else title_text
        if source_filter and source_filter not in src:
            continue
        if poll_mode and first_poll:
            seen.append(key)
            if i == 0:
                items.insert(0, (title_text, parse_time(dt_str), src, desc, use_desc))
        else:
            # Normal mode or subsequent polls
            if key not in seen:
                seen.append(key)
                items.insert(0, (title_text, parse_time(dt_str), src, desc, use_desc))
                if not poll_mode and len(items) >= MAX_ITEMS:
                    break

    # Pause media once before speaking all items
    tts_items = poll_mode and not args.no_tts and items
    was_playing = False
    if tts_items and not is_microphone_active():
        was_playing = pause_media()
        if was_playing:
            time.sleep(0.5)
            if is_media_playing():
                log_debug("Media still playing after pause, skipping TTS")
                tts_items = False

    for item in items:
        title, ts, src = item[0], item[1], item[2]
        desc = item[3] if len(item) > 3 else ''
        use_desc = item[4] if len(item) > 4 else False
        if poll_mode:
            show_popup([(title, ts, src)])
        print_item(title, ts, src, desc, use_desc)
        if poll_mode and not tts_items:
            time.sleep(max(3, len(title) * 0.15))
        time.sleep(1)
        hide_popup()

    if was_playing:
        time.sleep(0.5)
        resume_media()

    first_poll = False


try:
    # Check if URL is HTML (link listing mode)
    if args.url:
        if list_html_links(args.url):
            sys.exit(0)
        # If not HTML, continue to treat as RSS

    if args.audio_active:
        mic_active = is_microphone_active()
        audio_active = is_audio_active()

        if mic_active or audio_active:
            # Show which apps are using audio/mic
            if mic_active:
                try:
                    result = subprocess.run(['pactl', 'list', 'source-outputs'],
                                            capture_output=True, text=True, timeout=1)
                    if result.returncode == 0:
                        apps = []
                        for line in result.stdout.split('\n'):
                            if 'application.name' in line:
                                app = line.split('=')[1].strip().strip('"')
                                if 'GNOME Settings' not in app and app not in apps:
                                    apps.append(app)
                        if apps:
                            print(f"Microphone is active: {', '.join(apps)}")
                except:
                    print("Microphone is active")

            if audio_active:
                try:
                    result = subprocess.run(['pactl', 'list', 'sink-inputs'],
                                            capture_output=True, text=True, timeout=1)
                    if result.returncode == 0:
                        apps = []
                        for line in result.stdout.split('\n'):
                            if 'application.name' in line:
                                app = line.split('=')[1].strip().strip('"')
                                if app not in apps:
                                    apps.append(app)
                        if apps:
                            print(f"Audio playback is active: {', '.join(apps)}")
                except:
                    print("Audio playback is active")
            sys.exit(0)
        else:
            print("No audio activity")
            sys.exit(1)
    elif args.stat:
        print("source: duration items mean_interval")
        for src in enabled_sources:
            items = fetch_rss(src, limit=999999)
            if len(items) < 2:
                continue
            duration = items[0][0] - items[-1][0]
            mt = duration / (len(items) - 1)
            dur_str = str(timedelta(seconds=int(duration.total_seconds())))
            mt_str = str(timedelta(seconds=int(mt.total_seconds())))
            if dur_str.startswith('0:'):
                dur_str = dur_str[2:]
            if mt_str.startswith('0:'):
                mt_str = mt_str[2:]
            name = src.get('name', src.get('url', 'Unknown'))
            print(f"{name}: {dur_str} {len(items)} items {mt_str}")
    elif args.test_popup:
        items = [
            ('הראשון השני השלישי', '12:34', 'test'),
            (' '.join(f'word{i}' for i in range(20)), '12:34', 'test'),
            (' '.join(f'word{i}' for i in range(30)), '13:00', 'test'),
        ]
        show_popup(items)
        if popup_backend == 'gtk':
            import gi
            gi.require_version('Gtk', '3.0')
            from gi.repository import Gtk, GLib
            GLib.timeout_add(5000, Gtk.main_quit)
            Gtk.main()
        else:
            time.sleep(5)
        hide_popup()
    elif args.word_freq:
        import re
        from collections import Counter
        words = Counter()
        for src in enabled_sources:
            status(src.get('name', src['url']))
            for item in fetch_rss(src, limit=999999):
                for w in re.findall(r'\w+', f"{item[1]} {item[4]}".lower()):
                    if len(w) > 1:
                        words[w] += 1
        status()
        total = sum(words.values())
        for w, c in sorted(words.items(), key=lambda x: x[1]):
            print(f"{c}\t{c/total:.2e}\t{w}")
    elif poll_mode:
        # log_debug("Starting polling mode")
        while True:
            load_config(args.config)
            if not args.url:
                enabled_sources = [s for s in config.get('sources', []) if s.get('enabled', True)]
            news = fetch_news()
            if args.url and first_poll:
                print_mean_time(news)
            show_news(news)
            status("waiting")
            time.sleep(POLL_INTERVAL)
            status()
    else:
        log_debug("Running in normal mode")
        if not args.debug:
            os.system('clear')
        current_time = datetime.now().strftime('%H:%M')
        print(" " * 80, f"  {current_time}\n")
        news = fetch_news()
        if args.url:
            print_mean_time(news)
        show_news(news)
except KeyboardInterrupt:
    print("\nExiting...", file=sys.stderr)
    sys.exit(0)
