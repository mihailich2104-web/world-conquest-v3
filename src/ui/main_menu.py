"""Главное меню: Новая игра / Продолжить / Мультиплеер / Загрузить игру / Настройки / Выход.

Фон — затемнённая карта мира (рисует MapRenderer), слева графитовая панель с кнопками.
handle_event возвращает (действие, данные) или None.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import pygame
import pygame_gui
from pygame_gui.elements import UIButton, UITextEntryLine

from config import NET_DEFAULT_PORT, SETTINGS, TITLE
from . import get_font

log = logging.getLogger(__name__)
AGGR = ((0.0, "Мирный"), (0.5, "Низкая"), (1.0, "Обычная"), (2.0, "Высокая"))
PANEL_W = 430
TXT = (226, 230, 236)
TXT_DIM = (150, 158, 170)


class MainMenu:
    """Меню с подменю. Все экраны строятся из кнопок и текстовых полей pygame_gui."""

    def __init__(self, manager: pygame_gui.UIManager, size: tuple[int, int], has_saves: bool = False) -> None:
        self.m, self.size = manager, size
        self.has_saves = has_saves
        self.buttons: dict[UIButton, tuple[str, Any]] = {}
        self.entries: dict[str, UITextEntryLine] = {}
        self.captions: list[tuple[str, int, int, tuple[int, int, int], int]] = []   # текст, x, y, цвет, размер
        self.status = ""
        self.screen = "main"
        self._y = 0
        self.show_main()

    # ---------- построение ----------
    def _clear(self) -> None:
        for b in self.buttons:
            b.kill()
        for e in self.entries.values():
            e.kill()
        self.buttons.clear()
        self.entries.clear()
        self.captions.clear()
        self._y = 220

    def _add(self, items: list[tuple[str, str, Any]], disabled: tuple[str, ...] = ()) -> None:
        w, h, gap = PANEL_W - 80, 44, 10
        for text, action, data in items:
            b = UIButton(pygame.Rect(40, self._y, w, h), text, self.m)
            if action in disabled:
                b.disable()
            self.buttons[b] = (action, data)
            self._y += h + gap

    def _entry(self, key: str, caption: str, initial: str) -> None:
        self.captions.append((caption, 40, self._y, TXT_DIM, 14))
        e = UITextEntryLine(relative_rect=pygame.Rect(40, self._y + 20, PANEL_W - 80, 34), manager=self.m,
                            initial_text=initial)
        self.entries[key] = e
        self._y += 62

    def kill(self) -> None:
        """Удаляет все элементы меню."""
        self._clear()

    def set_status(self, text: str) -> None:
        """Строка состояния внизу панели (ошибки подключения и т.п.)."""
        self.status = text

    def show_main(self) -> None:
        """Корневое меню."""
        self._clear()
        self.screen = "main"
        self._add([("Новая игра", "new_menu", None), ("Продолжить", "continue", None),
                   ("Мультиплеер", "mp_menu", None), ("Загрузить игру", "load_menu", None),
                   ("Настройки", "settings", None), ("Выход", "quit", None)],
                  disabled=() if self.has_saves else ("continue",))

    def show_new(self) -> None:
        """Выбор: свободная игра или обучение."""
        self._clear()
        self.screen = "new"
        self._add([("Свободная игра", "new", None), ("Обучение", "tutorial", None), ("Назад", "main", None)])

    def show_load(self, saves: list[dict[str, Any]]) -> None:
        """Список последних сохранений."""
        self._clear()
        self.screen = "load"
        items = [(f"{s['name']} | {s['player']} | {s['game_date']}", "load", s["id"]) for s in saves]
        self._add(items + [("Назад", "main", None)] if saves else [("Сохранений нет", "main", None)])

    def show_settings(self) -> None:
        """Настройки."""
        self._clear()
        self.screen = "settings"
        aggr = next((n for v, n in AGGR if v == SETTINGS.ai_aggression), "Обычная")
        self._add([
            (f"Полный экран: {'вкл' if SETTINGS.fullscreen else 'выкл'}", "toggle_fullscreen", None),
            (f"Агрессия ИИ: {aggr}", "toggle_aggr", None),
            (f"Режим карты: {'идеологии' if SETTINGS.map_mode == 'ideology' else 'политический'}", "toggle_map", None),
            (f"Автосохранение: {'вкл' if SETTINGS.autosave else 'выкл'}", "toggle_autosave", None),
            ("Назад", "main", None)])

    def show_mp(self) -> None:
        """Мультиплеер: имя игрока, создание и подключение."""
        self._clear()
        self.screen = "mp"
        self._entry("name", "Ваше имя", SETTINGS.player_name)
        self._add([("Создать LAN игру", "host_lan", None), ("Подключиться к LAN", "join_menu", None),
                   ("Назад", "main", None)])

    def show_join(self, servers: Optional[list[dict[str, Any]]] = None) -> None:
        """Подключение: ручной ввод IP/порта и автопоиск."""
        self._clear()
        self.screen = "join"
        self._y = 190
        self._entry("ip", "IP хоста", SETTINGS.last_ip)
        self._entry("port", "Порт", str(SETTINGS.last_port or NET_DEFAULT_PORT))
        self._add([("Подключиться", "join", None), ("Найти серверы в сети", "discover", None)])
        for s in (servers or [])[:3]:
            self._add([(f"{s['name']}  {s['ip']}:{s['port']}  ({s['players']}/{s['max']})", "join_found",
                        (s["ip"], int(s["port"])))])
        self._add([("Назад", "mp_menu", None)])

    # ---------- события ----------
    def _name(self) -> str:
        e = self.entries.get("name")
        return (e.get_text().strip() if e else "") or SETTINGS.player_name or "Игрок"

    def handle_event(self, event: pygame.event.Event) -> Optional[tuple[str, Any]]:
        """Обрабатывает кнопки; внутренние переключатели применяет сам."""
        if event.type != pygame_gui.UI_BUTTON_PRESSED or event.ui_element not in self.buttons:
            return None
        action, data = self.buttons[event.ui_element]
        self.status = ""
        if action == "main":
            self.show_main()
        elif action == "new_menu":
            self.show_new()
        elif action == "settings":
            self.show_settings()
        elif action == "mp_menu":
            self.show_mp()
        elif action == "join_menu":
            SETTINGS.player_name = self._name()
            SETTINGS.save()
            self.show_join()
        elif action == "host_lan":
            SETTINGS.player_name = self._name()
            SETTINGS.save()
            return "host_lan", SETTINGS.player_name
        elif action in ("join", "join_found"):
            if action == "join":
                ip = self.entries["ip"].get_text().strip()
                try:
                    port = int(self.entries["port"].get_text().strip())
                    if not 1 <= port <= 65535:
                        raise ValueError
                except ValueError:
                    self.status = "Порт — число от 1 до 65535"
                    return None
                if not ip:
                    self.status = "Введите IP хоста"
                    return None
            else:
                ip, port = data
            SETTINGS.last_ip, SETTINGS.last_port = ip, port
            SETTINGS.save()
            return "join", (ip, port, SETTINGS.player_name)
        elif action == "toggle_aggr":
            vals = [v for v, _ in AGGR]
            i = vals.index(SETTINGS.ai_aggression) if SETTINGS.ai_aggression in vals else 2
            SETTINGS.ai_aggression = vals[(i + 1) % len(vals)]
            SETTINGS.save()
            self.show_settings()
        elif action == "toggle_map":
            SETTINGS.map_mode = "political" if SETTINGS.map_mode == "ideology" else "ideology"
            SETTINGS.save()
            self.show_settings()
        elif action == "toggle_autosave":
            SETTINGS.autosave = not SETTINGS.autosave
            SETTINGS.save()
            self.show_settings()
        elif action == "toggle_fullscreen":
            SETTINGS.fullscreen = not SETTINGS.fullscreen
            SETTINGS.save()
            return "apply_display", None
        else:
            return action, data
        return None

    # ---------- отрисовка ----------
    def draw(self, screen: pygame.Surface) -> None:
        """Графитовая панель, заголовок и подписи (фон-карту рисует App)."""
        h = self.size[1]
        panel = pygame.Surface((PANEL_W, h), pygame.SRCALPHA)
        panel.fill((20, 23, 28, 232))
        screen.blit(panel, (0, 0))
        pygame.draw.line(screen, (58, 64, 74), (PANEL_W, 0), (PANEL_W, h), 1)
        screen.blit(get_font(34).render(TITLE.upper(), True, TXT), (40, 70))
        screen.blit(get_font(15).render("Глобальная стратегия", True, TXT_DIM), (42, 122))
        pygame.draw.line(screen, (58, 64, 74), (40, 156), (PANEL_W - 40, 156), 1)
        for text, x, y, col, sz in self.captions:
            screen.blit(get_font(sz).render(text, True, col), (x, y))
        if self.status:
            screen.blit(get_font(14).render(self.status, True, (222, 150, 140)), (40, h - 60))
