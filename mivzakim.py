#!/usr/bin/env python3

import sys
import os
import time
import argparse
import subprocess
import textwrap
import html as html_lib
from datetime import datetime, timedelta
from io import BytesIO
from collections import deque

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

def load_config(path=None):
    global config, MAX_ITEMS, POLL_INTERVAL, TTS_VOLUME_ADJUST, BLOCK_WORDS
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'config.yaml')
    if os.path.exists(path):
        with open(path) as f:
            config = yaml.safe_load(f) or {}
    else:
        config = {}
    s = config.get('settings', {})
    MAX_ITEMS = s.get('max_items', 10)
    POLL_INTERVAL = s.get('poll_interval', 60)
    TTS_VOLUME_ADJUST = s.get('tts_volume_adjust', -10)
    BLOCK_WORDS = s.get('block_words', [])

config = {}
MAX_ITEMS = POLL_INTERVAL = TTS_VOLUME_ADJUST = 0
BLOCK_WORDS = []
load_config()

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
args = parser.parse_args()

WIDTH = args.width if args.width else int(os.environ.get('MANWIDTH', 110))

if args.config:
    load_config(args.config)

poll_mode = args.poll
debug = args.debug
source_filter = args.source

# Get sources from args or config
if args.url:
    # Look up configured name for URL
    name = ''
    for s in config.get('sources', []):
        if s.get('url') == args.url:
            name = s.get('name', '')
            break
    enabled_sources = [{'url': args.url, 'name': name, 'use_description': args.use_description}]
else:
    sources = config.get('sources', [])
    enabled_sources = [s for s in sources if s.get('enabled', True)]
    if not enabled_sources:
        enabled_sources = [{'url': 'https://rss.mivzakim.net/rss/category/1', 'name': 'Mivzakim', 'use_description': False}]

seen = deque(maxlen=100*MAX_ITEMS)
first_poll = True
last_spoken = None

def log_debug(msg):
    """Print debug message if debug mode enabled"""
    if debug:
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
    except Exception as e:
        print(f"Error fetching HTML: {e}", file=sys.stderr)
        sys.exit(1)


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


def pause_media():
    """Pause media playback if playing, return True if was playing"""
    try:
        result = subprocess.run(['playerctl', 'status'],
                                capture_output=True, text=True, timeout=1)
        if result.returncode == 0:
            status = result.stdout.strip()
            log_debug(f"Media status: {status}")
            if status == 'Playing':
                os.system('playerctl pause 2>/dev/null')
                log_debug("Paused media playback")
                return True
    except Exception as e:
        log_debug(f"Media check failed: {e}")
    return False


def resume_media():
    """Resume media playback"""
    try:
        os.system('playerctl play 2>/dev/null')
        log_debug("Resumed media playback")
    except Exception:
        pass


def speak_text(lang, text):
    """Speak text using gTTS"""
    # Skip TTS during videoconferencing (microphone active)
    if is_microphone_active():
        log_debug("Skipping TTS - videoconferencing active")
        return

    was_playing = pause_media()
    if was_playing:
        time.sleep(0.5)
    try:
        if lang == 'he':
            lang = 'iw'
        log_debug(f"TTS: {text[:50]}... (lang: {lang})")
        tts = gTTS(text, lang=lang)
        buf = BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        audio = AudioSegment.from_mp3(buf)
        buf.close()
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
        log_debug("TTS completed")
    except Exception as e:
        print(f"TTS error: {e}", file=sys.stderr)
    finally:
        if was_playing:
            time.sleep(0.5)
            resume_media()


def fetch_rss(source_config, limit=None):
    url = source_config['url']
    if limit is None:
        limit = MAX_ITEMS
    headers = {'Accept': 'application/rss+xml, application/xml, text/xml, */*'}

    # Retry up to 3 times on failure
    root = None
    content_len = 0
    for attempt in range(3):
        try:
            response = session.get(url, timeout=30, headers=headers)
            response.raise_for_status()
            content_len = len(response.content)
            parser = etree.XMLParser(recover=True)
            root = etree.fromstring(response.content, parser)
            break
        except Exception as e:
            if attempt < 2:
                log_debug(f"Attempt {attempt + 1} failed for {url}: {e}, retrying...")
                time.sleep(1)
            else:
                print(f"Error fetching RSS from {url}: {e}", file=sys.stderr)
                return []

    if root is None:
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

            try:
                dt = parse_datetime(dt_str)
                items.append((dt, title_text, dt_str, src, desc, use_desc))
            except Exception as e:
                log_debug(f"Failed to parse date '{dt_str}': {e}")

    log_debug(f"{len(feed_items)} items {content_len} bytes {url}")
    return items


def fetch_news():
    all_items = []
    for source in enabled_sources:
        items = fetch_rss(source)
        all_items.extend(items)

    # Sort by datetime (newest first)
    all_items.sort(key=lambda x: x[0], reverse=True)
    log_debug(f"Total items from all sources: {len(all_items)}")

    return all_items


def print_item(title, ts, src, desc='', use_desc=False):
    """Print news item and optionally speak"""
    global last_spoken

    # Strip trailing text in parentheses
    import re
    title = re.sub(r'\s*\([^)]+\)\s*$', '', title)

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
    log_debug(f"WIDTH={WIDTH}")
    if lang == 'he':
        wrapped_lines = textwrap.wrap(line, width=WIDTH-8)
        log_debug(f"lines={len(wrapped_lines)}")
        for i, wrapped_line in enumerate(wrapped_lines):
            w = WIDTH - 8 if i > 0 else WIDTH
            log_debug(f"{wrapped_line}")
            print(format_rtl_text(wrapped_line, w))

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=WIDTH-38, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(8*' ' + f"{src}")
    else:
        print(textwrap.fill(line, width=WIDTH-8, subsequent_indent=8*' '))

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=WIDTH-38, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(f"{src.rjust(WIDTH-8)}")
    sys.stdout.flush()
    if poll_mode: #and not first_poll:
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
        if source_filter and source_filter not in src:
            continue
        if poll_mode and first_poll:
            # Mark all as seen, collect only first item
            seen.append(title_text)
            if i == 0:
                items.append((title_text, parse_time(dt_str), src, desc, use_desc))
        else:
            # Normal mode or subsequent polls
            if title_text not in seen:
                seen.append(title_text)
                items.insert(0, (title_text, parse_time(dt_str), src, desc, use_desc))
                if not poll_mode and len(items) >= MAX_ITEMS:
                    break

    for item in items:
        title, ts, src = item[0], item[1], item[2]
        desc = item[3] if len(item) > 3 else ''
        use_desc = item[4] if len(item) > 4 else False
        print_item(title, ts, src, desc, use_desc)

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
    elif poll_mode:
        # log_debug("Starting polling mode")
        while True:
            # log_debug("=== Poll cycle start ===")
            news = fetch_news()
            if args.url and first_poll:
                print_mean_time(news)
            show_news(news)
            # log_debug(f"Sleeping {POLL_INTERVAL} seconds...")
            time.sleep(POLL_INTERVAL)
    else:
        log_debug("Running in normal mode")
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
