# Mivzakim Android App

Hebrew news reader for Android.

## Build and install

```bash
# First time setup
cd android
./setup.sh

# Build APK
./gradlew assembleDebug

# Install to connected phone
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

## Features

- Fetches news from mivzakim.net RSS feed
- RTL Hebrew text support
- Material Design 3 UI
- Shows 10 latest news items with source and time

## Requirements

- Android 7.0+ (API 24)
- Internet permission
- USB debugging enabled on phone

## Quick test

```bash
# Check phone connected
adb devices

# Build and install in one command
./gradlew installDebug
```
