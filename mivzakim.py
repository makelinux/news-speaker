#!/usr/bin/env python3

import sys
import os
import time
import argparse
import subprocess
import textwrap
from datetime import datetime
from io import BytesIO
from collections import deque

from lxml import html, etree
import requests
from gtts import gTTS
from pydub import AudioSegment
import pasimple
from langdetect import detect
import yaml

lri = '\u2066'
rli = '\u2067'
pdi = '\u2069'

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

# Get URL from args or config
if args.url:
    rss_url = args.url
    current_source = {'use_description': args.use_description}
else:
    sources = config.get('sources', [])
    enabled = [s for s in sources if s.get('enabled', True)]
    if enabled:
        current_source = enabled[0]
        rss_url = current_source['url']
    else:
        rss_url = 'https://rss.mivzakim.net/rss/category/1'
        current_source = {'use_description': False}

# Command-line arg overrides config
use_description = args.use_description if args.use_description else current_source.get('use_description', True)
seen = deque(maxlen=10*MAX_ITEMS)
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


def fetch_rss():
    try:
        response = requests.get(rss_url, timeout=30)
        response.raise_for_status()
        log_debug(f"Fetched {len(response.content)} bytes from RSS")
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(response.content, parser)

        items = []
        for item in root.xpath('//item'):
            title = item.find('title')
            pubdate = item.find('pubDate')
            source = item.find('source')
            description = item.find('description')
            if title is not None and title.text and pubdate is not None and pubdate.text:
                title_text = title.text.strip()
                dt_str = pubdate.text.strip()
                src = source.text.strip().split(' - ')[0] if source is not None and source.text else ''
                desc = description.text.strip() if description is not None and description.text else ''
                items.append((title_text, dt_str, src, desc))

        log_debug(f"Found {len(items)} news items from RSS")
        return items
    except Exception as e:
        print(f"Error fetching RSS: {e}")
        return []


def fetch_ynet():
    try:
        response = requests.get('https://www.ynet.co.il/news/category/184',
                                timeout=30)
        response.raise_for_status()
        log_debug(f"Fetched {len(response.content)} bytes from Ynet")
        doc = html.fromstring(response.content)

        titles = doc.xpath('//div[@class="title"]')
        times = doc.xpath('//time[contains(@class, "DateDisplay")]')

        items = []
        for t, tm in zip(titles[:MAX_ITEMS], times[:MAX_ITEMS]):
            title_text = "".join(t.itertext()).strip()
            dt_str = tm.get("datetime")
            if dt_str:
                items.append((title_text, dt_str, 'Ynet', ''))

        log_debug(f"Found {len(items)} news items from Ynet")
        return items
    except requests.RequestException as e:
        print(f"Error fetching Ynet: {e}")
        return []


def fetch_news():
    return fetch_ynet() if use_ynet else fetch_rss()


def print_item(title, ts, src, desc=''):
    """Print news item and optionally speak"""
    global last_spoken
    lang = detect(title)
    if lang == 'he':
        title_rj = title.rjust(110)
        print(f"{rli} {title_rj} {pdi}{lri}- {ts}{pdi}")
        if desc and use_description:
            wrapped = textwrap.fill(desc, width=100, initial_indent='\t', subsequent_indent='\t')
            print(f"\n{wrapped}")
        print(f"{src}")
    else:
        print(f"{ts} - {title}")
        if desc and use_description:
            wrapped = textwrap.fill(desc, width=100, initial_indent='\t', subsequent_indent='\t')
            print(f"\n{wrapped}")
        print(f"{src.rjust(110)}")
    sys.stdout.flush()
    if poll_mode: #and not first_poll:
        text_to_speak = f"{title}. {desc}" if desc and use_description else title
        text_to_speak = text_to_speak.strip()
        if text_to_speak != last_spoken:
            log_debug(f"Speaking: {text_to_speak[:50]}...")
            speak_text(lang, text_to_speak)
            last_spoken = text_to_speak
        else:
            log_debug("Skipping TTS - same as last spoken")


def parse_time(dt_str):
    """Parse datetime string to local time string"""
    try:
        # ISO format (Ynet)
        dt_utc = datetime.fromisoformat(dt_str.replace("Z", "+00:00"))
        dt_local = dt_utc.astimezone()
        return dt_local.strftime('%H:%M')
    except ValueError:
        # RFC 2822 format (RSS)
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(dt_str)
        return dt.strftime('%H:%M')


def show_news(news_items):
    global first_poll
    if not news_items:
        return

    items = []
    for i, item in enumerate(news_items):
        title_text, dt_str, src = item[0], item[1], item[2]
        desc = item[3] if len(item) > 3 else ''
        if source_filter and source_filter not in src:
            continue
        if poll_mode and first_poll:
            # Mark all as seen, collect only first item
            seen.append(title_text)
            if i == 0:
                items.append((title_text, parse_time(dt_str), src, desc))
        else:
            # Normal mode or subsequent polls
            if title_text not in seen:
                seen.append(title_text)
                items.insert(0, (title_text, parse_time(dt_str), src, desc))
                if not poll_mode and len(items) >= MAX_ITEMS:
                    break

    for item in items:
        title, ts, src = item[0], item[1], item[2]
        desc = item[3] if len(item) > 3 else ''
        print_item(title, ts, src, desc)

    first_poll = False


if poll_mode:
    # log_debug("Starting polling mode")
    while True:
        # log_debug("=== Poll cycle start ===")
        news = fetch_news()
        show_news(news)
        # log_debug(f"Sleeping {POLL_INTERVAL} seconds...")
        time.sleep(POLL_INTERVAL)
else:
    log_debug("Running in normal mode")
    os.system('clear')
    current_time = datetime.now().strftime('%H:%M')
    print(" " * 80, f"  {current_time}\n")
    news = fetch_news()
    show_news(news)
