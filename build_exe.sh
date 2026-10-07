#!/bin/bash
# Локальная сборка (Windows: запускать из Git Bash; Linux/macOS: из терминала)
set -e
cd "$(dirname "$0")"
pip install -r requirements.txt
python tools/prepare_map.py
python tools/gen_countries.py
pyinstaller build/world_conquest.spec --clean --noconfirm
echo "✅ Готово: dist/WorldConquest (на Windows — dist/WorldConquest.exe)"
