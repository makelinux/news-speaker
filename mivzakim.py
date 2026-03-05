#!/usr/bin/env python3

import sys
import os
import time
import argparse
import subprocess
import textwrap
from datetime import datetime, timedelta
from io import BytesIO
from collections import deque

from lxml import html, etree
import requests
from gtts import gTTS
from pydub import AudioSegment
import pasimple
from langdetect import detect
import yaml

rli = '\u2066'  # Right-to-Left Isolate
lri = '\u2067'  # Left-to-Right Isolate
pdi = '\u2069'  # Pop Directional Isolate

# Load config
config = {}
config_file = os.path.join(os.path.dirname(__file__), 'config.yaml')
if os.path.exists(config_file):
    with open(config_file) as f:
        config = yaml.safe_load(f) or {}

MAX_ITEMS = config.get('settings', {}).get('max_items', 10)
POLL_INTERVAL = config.get('settings', {}).get('poll_interval', 60)
TTS_VOLUME_ADJUST = config.get('settings', {}).get('tts_volume_adjust', -10)

# Parse arguments
parser = argparse.ArgumentParser(description='Hebrew news reader')
parser.add_argument('-p', '--poll', action='store_true',
                    help='Polling mode')
parser.add_argument('-d', '--debug', action='store_true',
                    help='Enable debug output')
parser.add_argument('-y', '--ynet', action='store_true',
                    help='Use Ynet HTML source instead of RSS')
parser.add_argument('-s', '--source', type=str,
                    help='Filter by source name (e.g., "Ynet", "N12")')
parser.add_argument('-u', '--url', type=str,
                    help='Custom RSS URL')
parser.add_argument('-c', '--config', type=str,
                    help='Path to config file (default: ./config.yaml)')
parser.add_argument('-D', '--use-description', action='store_true',
                    help='Include description field in display and TTS')
parser.add_argument('--stat', action='store_true',
                    help='Show mean time statistics for all configured sources')
args = parser.parse_args()

# Reload config if custom path specified
if args.config:
    with open(args.config) as f:
        config = yaml.safe_load(f) or {}
    MAX_ITEMS = config.get('settings', {}).get('max_items', 10)
    POLL_INTERVAL = config.get('settings', {}).get('poll_interval', 60)
    TTS_VOLUME_ADJUST = config.get('settings', {}).get('tts_volume_adjust', -10)

poll_mode = args.poll
debug = args.debug
use_ynet = args.ynet
source_filter = args.source

# Get sources from args or config
if args.url:
    enabled_sources = [{'url': args.url, 'name': '', 'use_description': args.use_description}]
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


def fetch_rss(source_config):
    url = source_config['url']
    headers = {
        'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
        'Accept': 'application/rss+xml, application/xml, text/xml, */*'
    }

    # Retry up to 3 times on failure
    root = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=30, headers=headers)
            response.raise_for_status()
            log_debug(f"Fetched {len(response.content)} bytes from {url}")
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

    channel_title_elem = root.find('.//channel/title')
    if channel_title_elem is None:
        channel_title_elem = root.find('.//{http://www.w3.org/2005/Atom}title')
    channel_name = channel_title_elem.text.strip() if channel_title_elem is not None and channel_title_elem.text else source_config.get('name', '')

    use_desc = args.use_description if args.use_description else source_config.get('use_description', True)
    src_filter = source_config.get('source_filter')

    items = []
    for item in root.xpath('//item | //entry'):
        if len(items) >= MAX_ITEMS:
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
            src = source.text.strip().split(' - ')[0] if source is not None and source.text else channel_name
            desc = description.text.strip() if description is not None and description.text else ''

            # Apply source filter if configured
            if src_filter and src_filter not in src:
                continue

            try:
                dt = parse_datetime(dt_str)
                items.append((dt, title_text, dt_str, src, desc, use_desc))
            except Exception as e:
                log_debug(f"Failed to parse date '{dt_str}': {e}")

    log_debug(f"Found {len(items)} news items from {url} (limited to {MAX_ITEMS})")
    return items


def fetch_ynet(source_config):
    url = source_config.get('url', 'https://www.ynet.co.il/news/category/184')
    channel_name = source_config.get('name', 'Ynet')
    use_desc = args.use_description if args.use_description else source_config.get('use_description', False)
    src_filter = source_config.get('source_filter')

    # Retry up to 3 times on failure
    doc = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            log_debug(f"Fetched {len(response.content)} bytes from {url}")
            doc = html.fromstring(response.content)
            break
        except Exception as e:
            if attempt < 2:
                log_debug(f"Attempt {attempt + 1} failed for {url}: {e}, retrying...")
                time.sleep(1)
            else:
                print(f"Error fetching HTML from {url}: {e}", file=sys.stderr)
                return []

    if doc is None:
        return []

    titles = doc.xpath('//div[@class="title"]')
    times = doc.xpath('//time[contains(@class, "DateDisplay")]')

    items = []
    for t, tm in zip(titles[:MAX_ITEMS], times[:MAX_ITEMS]):
        title_text = "".join(t.itertext()).strip()
        dt_str = tm.get("datetime")
        if dt_str:
            try:
                dt = parse_datetime(dt_str)
                items.append((dt, title_text, dt_str, channel_name, '', use_desc))
            except Exception as e:
                log_debug(f"Failed to parse date '{dt_str}': {e}")

    log_debug(f"Found {len(items)} news items from {url} (limited to {MAX_ITEMS})")
    return items


def fetch_news():
    if use_ynet:
        return fetch_ynet({'url': 'https://www.ynet.co.il/news/category/184', 'name': 'Ynet', 'use_description': False})

    all_items = []
    for source in enabled_sources:
        if source.get('type') == 'html':
            items = fetch_ynet(source)
        else:
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

    # Strip HTML tags from description
    if desc:
        desc = re.sub(r'<[^>]+>', '', desc)
        desc = re.sub(r'&\w+;', ' ', desc)  # Remove HTML entities
        desc = ' '.join(desc.split())  # Normalize whitespace

    lang = detect(title)
    if lang == 'he':
        # Isolate Latin characters for proper RTL rendering
        def isolate_latin(text):
            return re.sub(r'([A-Za-z0-9]+(?:-[A-Za-z0-9]+)*)', rf'{lri}\1{pdi}', text)

        title_isolated = isolate_latin(title)
        # Count added isolation marks (2 per Latin sequence) and adjust rjust
        num_marks = (len(title_isolated) - len(title))
        title_isolated_rj = title_isolated.rjust(102 + num_marks)

        # RLI wrap with time on right (swapped order: time then dash)
        print(f"{rli}{title_isolated_rj}{pdi} {lri}{ts} -{pdi}")

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=100, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(f"{src}")
    else:
        # Wrap long titles
        title_line = f"{ts} - {title}"
        if len(title_line) > 100:
            wrapped_title = textwrap.fill(title_line, width=100, subsequent_indent=8*' ')
            print(wrapped_title)
        else:
            print(title_line)

        if desc and use_desc:
            wrapped = textwrap.fill(desc, width=100, initial_indent=8*' ', subsequent_indent=8*' ')
            print(f"\n{wrapped}")
        print(f"{src.rjust(110)}")
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
    return parse_datetime(dt_str).strftime('%H:%M')


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
    if args.stat:
        for src in enabled_sources:
            items = fetch_rss(src) if src.get('type') != 'html' else fetch_ynet(src)
            if len(items) < 2:
                continue
            mt = (items[0][0] - items[-1][0]) / (len(items) - 1)
            s = str(timedelta(seconds=int(mt.total_seconds())))
            if s.startswith('0:'):
                s = s[2:]
            name = src.get('name', src.get('url', 'Unknown'))
            print(f"{name}: {s}")
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
