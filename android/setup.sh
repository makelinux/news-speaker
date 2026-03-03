#!/bin/bash
# Setup script for Android project

cd "$(dirname "$0")"

# Download gradle if needed
if [ ! -f gradlew ]; then
    echo "Setting up Gradle wrapper..."
    gradle wrapper --gradle-version 8.2 2>/dev/null || {
        curl -L https://services.gradle.org/distributions/gradle-8.2-bin.zip -o gradle.zip
        unzip -q gradle.zip
        gradle-8.2/bin/gradle wrapper
        rm -rf gradle-8.2 gradle.zip
    }
fi

echo "Setup complete. Build with: ./gradlew assembleDebug"
