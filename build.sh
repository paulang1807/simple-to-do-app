#!/bin/bash
# Automation script for building the macOS Standalone Application

set -e

echo "🚀 Starting macOS App Build Process..."

# 1. Ensure we are in the right directory
cd "$(dirname "$0")"

# 2. Setup build environment using uv
if [ ! -d ".venv_build" ]; then
    echo "📦 Creating virtual environment..."
    /Users/pauls/.local/bin/uv venv .venv_build
fi

echo "📥 Installing/Updating build dependencies..."
source .venv_build/bin/activate
/Users/pauls/.local/bin/uv pip install pyinstaller pywebview anthropic openai google-genai

# 3. Clean up previous builds to avoid conflicts
echo "🧹 Cleaning up previous builds..."
pkill -f "Daily Task Manager" || true
rm -rf dist build icon.icns Daily\ Task\ Manager.spec

# 4. Run the build script
echo "🛠️  Building .app bundle..."
python3 setup_app.py

echo "✅ Build Complete! You can find the app in: ./dist/Daily Task Manager.app"
