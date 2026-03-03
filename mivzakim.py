#!/usr/bin/env python3

import sys
import os
import time
import argparse
import subprocess
from datetime import datetime
from io import BytesIO
from collections import deque

from lxml import html, etree
import requests
from gtts import gTTS
from pydub import AudioSegment
import pasimple

lri = '\u2066'
rli = '\u2067'
pdi = '\u2069'

MAX_ITEMS = 10

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
args = parser.parse_args()

poll_mode = args.poll
debug = args.debug
use_ynet = args.ynet
source_filter = args.source
rss_url = args.url or 'https://rss.mivzakim.net/rss/category/1'
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


def speak_text(text):
    """Speak text using gTTS"""
    was_playing = pause_media()
    if was_playing:
        time.sleep(0.5)
    try:
        log_debug(f"TTS: {text[:50]}...")
        tts = gTTS(text, lang='iw')
        buf = BytesIO()
        tts.write_to_fp(buf)
        buf.seek(0)
        audio = AudioSegment.from_mp3(buf)
        audio -= 10
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
            if title is not None and title.text and pubdate is not None and pubdate.text:
                title_text = title.text.strip()
                dt_str = pubdate.text.strip()
                src = source.text.strip().split(' - ')[0] if source is not None and source.text else 'RSS'
                items.append((title_text, dt_str, src))

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
                items.append((title_text, dt_str, 'Ynet'))

        log_debug(f"Found {len(items)} news items from Ynet")
        return items
    except requests.RequestException as e:
        print(f"Error fetching Ynet: {e}")
        return []


def fetch_news():
    return fetch_ynet() if use_ynet else fetch_rss()


def print_item(title, ts, src):
    """Print news item and optionally speak"""
    global last_spoken
    title_rj = title.rjust(110)
    print(f"{rli} {title_rj} {pdi}{lri} - {ts}{pdi}")
    print(f"{src}")
    sys.stdout.flush()
    if poll_mode: #and not first_poll:
        title_stripped = title.strip()
        if title_stripped != last_spoken:
            log_debug(f"Speaking: {title_stripped[:30]}...")
            speak_text(title_stripped)
            last_spoken = title_stripped
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
    for i, (title_text, dt_str, src) in enumerate(news_items):
        if source_filter and source_filter not in src:
            continue
        if poll_mode and first_poll:
            # Mark all as seen, collect only first item
            seen.append(title_text)
            if i == 0:
                items.append((title_text, parse_time(dt_str), src))
        else:
            # Normal mode or subsequent polls
            if title_text not in seen:
                seen.append(title_text)
                items.insert(0, (title_text, parse_time(dt_str), src))
                if not poll_mode and len(items) >= MAX_ITEMS:
                    break

    for title, ts, src in items:
        print_item(title, ts, src)

    first_poll = False


if poll_mode:
    # log_debug("Starting polling mode")
    while True:
        # log_debug("=== Poll cycle start ===")
        news = fetch_news()
        show_news(news)
        # log_debug("Sleeping 60 seconds...")
        time.sleep(60)
else:
    log_debug("Running in normal mode")
    os.system('clear')
    current_time = datetime.now().strftime('%H:%M')
    print(" " * 80, f"  {current_time}\n")
    news = fetch_news()
    show_news(news)
