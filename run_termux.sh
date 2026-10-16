#!/data/data/com.termux/files/usr/bin/bash
# Использование:
#   ./run_termux.sh build   — запустить сборку в GitHub Actions, дождаться и скачать артефакты (основной сценарий)
#   ./run_termux.sh play    — попытка запустить игру (нужны termux-x11 и pygame, не гарантировано)
set -e
cd "$(dirname "$0")"
pkg install -y python git gh

case "${1:-build}" in
  build)
    gh auth status >/dev/null 2>&1 || gh auth login
    echo -e "dist_from_ci/\n*.log" >> .gitignore; sort -u .gitignore -o .gitignore
    git rm -r -q --cached dist_from_ci build.log build_failed.log 2>/dev/null || true
    git add -A && git commit -m "update" || true
    git push
    gh workflow run build.yml || true
    sleep 5
    gh run watch "$(gh run list --workflow build.yml --limit 1 --json databaseId -q '.[0].databaseId')"
    RID="$(gh run list --workflow build.yml --limit 1 --json databaseId -q '.[0].databaseId')"
    gh run view "$RID" --log-failed 2>/dev/null | grep -v "^$" | tail -60 || true
    rm -rf dist_from_ci
    gh run download "$RID" -D dist_from_ci
    echo "✅ Артефакты в dist_from_ci/"
    ;;
  play)
    pkg install -y x11-repo termux-x11-nightly || true
    pip install pygame pygame_gui numpy
    export DISPLAY=:0
    python src/main.py
    ;;
esac
