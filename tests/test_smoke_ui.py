"""Дымовой тест без окна: меню, новая игра, автоматическое время, пауза, скорости, панели, LAN-лобби."""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")


def frames(app, n, dt=0.05):
    for _ in range(n):
        app.update(dt)
        app.render(dt)


def main() -> None:
    import pygame
    import pygame_gui
    assert hasattr(pygame, "DIRECTION_LTR"), "Нужен pygame-ce, а не pygame (pip uninstall pygame; pip install pygame-ce)"
    from config import SETTINGS
    SETTINGS.autosave = False
    import main as game
    app = game.App()
    assert app.provs, "карта не загружена"
    assert not app.fallback_map, "используется запасная карта — нет provinces.geojson"
    frames(app, 5)                                    # главное меню
    app.new_game(tutorial=False)
    assert app.mode == "play"
    iso = "DEU" if "DEU" in app.state.countries else next(iter(app.state.countries))
    app.ctx.selected = iso
    app.start_play()
    d0 = app.state.date
    frames(app, 60)                                   # 3 секунды при 1X -> время идёт само
    assert app.state.date > d0, "игровое время не идёт автоматически"
    app.hud_action("pause")
    d1 = app.state.date
    frames(app, 40)
    assert app.state.date == d1, "время идёт на паузе"
    app.hud_action("speed:4")                         # 16X
    frames(app, 40)
    assert app.state.date > d1 and app.session.clock.speed == 16
    for name in ("country", "diplomacy", "research", "military"):
        app.open_panel(name)
        frames(app, 2)
    app.do({"t": "build", "kind": "factory"})
    frames(app, 3)
    app.to_menu()
    # LAN-лобби хоста (порт выбирается автоматически)
    app.start_host("Тест")
    assert app.mode == "lobby" and app.lobby_host is not None
    frames(app, 5)
    app.to_menu()
    frames(app, 3)
    pygame.quit()
    print("SMOKE OK", pygame_gui.__name__)


if __name__ == "__main__":
    main()
