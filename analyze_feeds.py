#!/usr/bin/env python3

import re
import requests
from lxml import etree
from datetime import datetime
from email.utils import parsedate_to_datetime
from statistics import mean
import sys
import pickle
import os

def extract_rss_urls(readme_path):
    """Extract RSS feed URLs from README.md"""
    urls = []
    with open(readme_path) as f:
        for line in f:
            match = re.search(r'\(RSS feed: (https?://[^\)]+)\)', line)
            if match:
                urls.append(match.group(1))
    return urls

def fetch_feed(url, timeout=30):
    """Fetch and parse RSS feed, return (dates, titles)"""
    try:
        r = requests.get(url, timeout=timeout, headers={'User-Agent': 'Mozilla/5.0'})
        r.raise_for_status()
        parser = etree.XMLParser(recover=True)
        root = etree.fromstring(r.content, parser)

        items = []
        titles = []
        for item in root.xpath('//item | //entry'):
            title_elem = item.find('title')
            if title_elem is None:
                title_elem = item.find('.//{http://www.w3.org/2005/Atom}title')

            title = title_elem.text if title_elem is not None and title_elem.text else ''

            pubdate = item.find('pubDate')
            if pubdate is None:
                pubdate = item.find('.//{http://www.w3.org/2005/Atom}published')
            if pubdate is None:
                pubdate = item.find('.//{http://www.w3.org/2005/Atom}updated')

            if pubdate is not None and pubdate.text:
                try:
                    dt = parsedate_to_datetime(pubdate.text)
                    items.append(dt)
                    titles.append(title)
                except:
                    try:
                        dt = datetime.fromisoformat(pubdate.text.replace('Z', '+00:00'))
                        items.append(dt)
                        titles.append(title)
                    except:
                        pass

        sorted_items = sorted(zip(items, titles), reverse=True)
        return [dt for dt, _ in sorted_items], [t for _, t in sorted_items]
    except Exception as e:
        print(f"Error fetching {url}: {e}", file=sys.stderr)
        return [], []

def is_ai_related(title):
    """Check if title is AI-related"""
    kw = ['ai', 'artificial intelligence', 'machine learning', 'ml', 'deep learning',
          'neural network', 'llm', 'gpt', 'chatgpt', 'claude', 'gemini', 'transformer',
          'openai', 'anthropic', 'diffusion', 'stable diffusion', 'generative',
          'langchain', 'embedding', 'rag', 'agent', 'prompt', 'nlp', 'computer vision',
          'llama', 'mistral', 'model training', 'fine-tuning', 'reinforcement learning']
    t = title.lower()
    return any(k in t for k in kw)

def calc_stats(dates):
    """Calculate mean time between messages"""
    if len(dates) < 2:
        return None, len(dates), None

    intervals = []
    for i in range(len(dates) - 1):
        delta = dates[i] - dates[i + 1]
        intervals.append(delta.total_seconds() / 3600)  # hours

    mean_hours = mean(intervals)
    total_span_hours = (dates[0] - dates[-1]).total_seconds() / 3600

    return mean_hours, len(dates), total_span_hours

def main():
    readme = '/tmp/allainews_sources/README.md'
    cache_file = '/tmp/feed_cache.pkl'
    urls = extract_rss_urls(readme)

    cache = {}
    if os.path.exists(cache_file):
        with open(cache_file, 'rb') as f:
            cache = pickle.load(f)
        print(f"Loaded cache with {len(cache)} feeds\n", file=sys.stderr)

    print(f"Found {len(urls)} RSS feeds\n")
    print(f"{'Feed URL':<60} {'Count':>6} {'Mean':>10} {'Total Days':>11} {'Items/Day':>10} {'AI%':>6}")
    print("=" * 110)

    results = []
    for i, url in enumerate(urls, 1):
        if url in cache:
            print(f"Using cached {i}/{len(urls)}: {url[:60]}...", file=sys.stderr)
            dates, titles = cache[url]
        else:
            print(f"Fetching {i}/{len(urls)}: {url[:60]}...", file=sys.stderr)
            dates, titles = fetch_feed(url)
            cache[url] = (dates, titles)

        ai_titles = [t for t in titles if is_ai_related(t)]
        ai_pct = 100 * len(ai_titles) / len(titles) if titles else 0

        mean_hours, count, total_hours = calc_stats(dates)

        if mean_hours is not None and total_hours:
            total_days = total_hours / 24
            items_per_day = count / total_days if total_days > 0 else 0

            # Format mean interval
            if mean_hours < 0.017:  # < 1 minute
                mean_str = f"{mean_hours * 3600:.1f}s"
            elif mean_hours < 1:  # < 1 hour
                mean_str = f"{mean_hours * 60:.1f}m"
            else:
                mean_str = f"{mean_hours:.1f}h"

            results.append((url, count, mean_hours, total_days, items_per_day, ai_pct))
            print(f"{url[:60]:<60} {count:>6} {mean_str:>10} {total_days:>11.1f} {items_per_day:>10.1f} {ai_pct:>5.0f}%")
        elif count > 0:
            print(f"{url[:60]:<60} {count:>6} {'N/A':>10} {'N/A':>11} {'N/A':>10} {ai_pct:>5.0f}%")
        else:
            print(f"{url[:60]:<60} {'FAILED':>6} {'N/A':>10} {'N/A':>11} {'N/A':>10} {'N/A':>6}")

    print("\n" + "=" * 110)
    print(f"\nSuccessfully analyzed {len(results)} feeds")

    with open(cache_file, 'wb') as f:
        pickle.dump(cache, f)
    print(f"Saved cache to {cache_file}\n", file=sys.stderr)

    if results:
        # Filter AI-related feeds (>50% AI content)
        ai_feeds = [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in results if r[5] >= 50]
        ai_sorted = sorted(ai_feeds, key=lambda x: x[2])

        print("\nTop 20 most active AI-related feeds (>50% AI content):")
        for url, count, mean_h, days, per_day, ai_pct in ai_sorted[:20]:
            if mean_h < 0.017:
                mean_str = f"{mean_h * 3600:.0f}s"
            elif mean_h < 1:
                mean_str = f"{mean_h * 60:.0f}m"
            else:
                mean_str = f"{mean_h:.1f}h"
            print(f"  {mean_str:>6} - {count:>4} items ({per_day:>5.1f}/day, {ai_pct:>3.0f}% AI) - {url[:55]}")

        # Recommended feeds (3-7 hour range)
        recommended = [r for r in ai_sorted if 2.5 <= r[2] <= 7.0]

        print("\n" + "=" * 110)
        print("\nRecommended AI feeds (3-7 hour range) with titles:\n")

        for url, count, mean_h, days, per_day, ai_pct in recommended[:10]:
            if mean_h < 1:
                mean_str = f"{mean_h * 60:.0f}m"
            else:
                mean_str = f"{mean_h:.1f}h"

            print(f"\n{url}")
            print(f"  {mean_str} interval, {per_day:.1f}/day, {ai_pct:.0f}% AI")

            if url in cache:
                dates, titles = cache[url]
                ai_titles = [t for t in titles[:15] if is_ai_related(t)]
                print(f"  Recent AI titles:")
                for i, title in enumerate(ai_titles[:10], 1):
                    print(f"    {i}. {title[:90]}")
            print()

if __name__ == '__main__':
    main()
