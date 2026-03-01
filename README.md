# mivzakim

Hebrew news reader with TTS support.

## תקציר

קורא חדשות בעברית עם תמיכה בהקראה קולית.

**תכונות עיקריות:**\
קריאת RSS ממבזקים.נט או Ynet\
תצוגה מיושרת לעברית עם תוויות מקור\
מצב polling עם התראות קוליות\
סינון לפי מקור חדשות\
מניעת כפילויות (20 פריטים אחרונים)

**שימוש בסיסי:**
```bash
./mivzakim.py           # הצג 10 חדשות אחרונות
./mivzakim.py -p        # מצב ניטור רציף עם הקראה
./mivzakim.py -s Ynet   # סנן לפי מקור
```

## Usage

Normal mode - display 10 latest news items:
```bash
./mivzakim.py
```

Polling mode - continuous monitoring with audio notifications:
```bash
./mivzakim.py -p
```

Filter by source:
```bash
./mivzakim.py -s Ynet
./mivzakim.py -s N12
```

Use Ynet instead of RSS feed:
```bash
./mivzakim.py -y
```

Enable debug output:
```bash
./mivzakim.py -d
```

## Options

- `-p, --poll` - polling mode, checks for new items every 60 seconds
- `-d, --debug` - enable debug output
- `-y, --ynet` - use Ynet HTML source instead of RSS
- `-s, --source` - filter by source name (substring match)

## Features

RSS feed parsing - extracts news from mivzakim.net RSS\
Ynet scraping - alternative HTML-based source\
Source labels - displays news source for each item\
RTL formatting - proper Hebrew text alignment\
TTS support - reads news titles aloud in polling mode\
Media control - pauses/resumes playback during speech\
Smart deduplication - tracks last 20 seen items

## Dependencies

```bash
pip install lxml requests gtts pydub pasimple
```

System: playerctl (for media pause/resume)

## Display format

```
[SOURCE]   [TITLE] - [TIME]
```

Example:
```
Ynet       החדשות האחרונות מהזירה הבינלאומית - 14:23
```
