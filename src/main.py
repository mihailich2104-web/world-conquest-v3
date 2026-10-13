"""Точка входа World Conquest: инициализация, главный цикл events → update → render.

Игровое время идёт автоматически (core.clock), одиночная игра и LAN идут через core.session.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))   # импорты вида `from core import ...`

import pygame
import pygame_gui

from config import (COUNTRIES_PATH, FPS, HUD_HEIGHT, LEFT_PANEL_W, MAX_DAYS_PER_FRAME, SETTINGS, SIDE_PANEL_W, TECH_PATH,
                    THEME_PATH, TITLE, TUTORIAL_H, USER_DIR, WINDOW_SIZE)
from core import armies as am, scenarios
from core.game_state import GameState, list_saves, load_game, save_game
from core.session import Session
from map.camera import Camera
from map.map_loader import load_world_geo
from map.map_renderer import MapRenderer
from net.client import Client, ClientError
from net.discovery import discover
from net.host import Host
from tutorial.tutor_engine import TutorContext, TutorEngine
from ui import UIContext, get_font
from ui.armies_panel import ArmiesPanel, TERR_RU
from ui.country_panel import CountryPanel
from ui.diplomacy_panel import DiplomacyPanel
from ui.hud import HUD
from ui.join_picker import JoinPicker
from ui.progress_panel import ProgressPanel
from ui.lobby import LobbyInfo, LobbyPanel
from ui.main_menu import MainMenu
from ui.military_panel import MilitaryPanel
from ui.research_panel import ResearchPanel
from ui.tutorial import TutorialOverlay

log = logging.getLogger("world_conquest")
UI_REFRESH_INTERVAL = 0.25     # панели перестраиваются не чаще, чем раз в 0.25 с


def setup_logging() -> None:
    """Логирование в консоль и в файл ~/.world_conquest/game.log."""
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        USER_DIR.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(USER_DIR / "game.log", encoding="utf-8"))
    except OSError:
        pass
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=handlers)


class App:
    """Приложение: меню, LAN-лобби, выбор страны, игровой экран."""

    def __init__(self) -> None:
        pygame.init()
        pygame.display.set_caption(TITLE)
        self.screen = self._set_mode()
        self.size = WINDOW_SIZE
        self.manager = self._make_manager()
        self.ctx = UIContext(self.manager, self.size)
        self.clock = pygame.time.Clock()
        try:
            self.countries: list[dict[str, Any]] = json.loads(COUNTRIES_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            log.exception("countries.json не найден — запустите tools/gen_countries.py")
            self.countries = []
        self.provs, self.geo, self.fallback_map = load_world_geo(self.countries)
        self.adj = self.geo.adj
        try:
            self._tech_ids: Optional[set[str]] = {t["id"] for t in json.loads(TECH_PATH.read_text(encoding="utf-8"))}
        except (OSError, ValueError, KeyError, TypeError):
            self._tech_ids = None
        self._sc_cache: dict[str, dict[str, Any]] = {}
        self.camera = Camera(*self.size)
        self.renderer = MapRenderer(self.provs, self.camera, get_font(13))
        # фон меню: карта мира с затемнением
        self.preview = self._new_state(seed=1)
        self.renderer.rebuild(self.preview)
        self.renderer.make_backdrop(self.size)
        self.t = 0.0
        self.lite = False                                    # авто-режим для слабых устройств
        self._ema = 16.0
        self._slow = 0.0
        self._lite_init = False
        self.mode = "menu"                                   # menu | lobby | play
        self.running = True
        self.menu: Optional[MainMenu] = MainMenu(self.manager, self.size, has_saves=bool(list_saves(1)))
        self.menu.scenarios = self._scenario_list()
        self.sel_army: Optional[int] = None                  # выбранная армия
        self.toasts: list[list[Any]] = []                    # всплывающие уведомления: [текст, ttl, цвет]
        self._toast_n = 0
        self.join_picker: Optional[JoinPicker] = None        # выбор страны при входе в идущую партию
        self._start_speed = 0
        # игровое
        self.session: Optional[Session] = None
        self.state: Optional[GameState] = None
        self.hud: Optional[HUD] = None
        self.panels: dict[str, Any] = {}
        self.active: Optional[str] = None
        self.hover: Optional[str] = None
        self.drag_start: Optional[tuple[int, int]] = None
        self.dragging = False
        self.press_on_ui = False
        self._mouse: tuple[int, int] = (0, 0)
        self._hover_dirty = False
        self.engine: Optional[TutorEngine] = None
        self.overlay: Optional[TutorialOverlay] = None
        self.tctx: Optional[TutorContext] = None
        self.btn_over: Optional[Any] = None
        self._ui_t = 0.0
        self._ui_rev = -1
        # LAN-лобби
        self.lobby: Optional[LobbyInfo] = None
        self.lobby_panel: Optional[LobbyPanel] = None
        self.lobby_host: Optional[Host] = None
        self.lobby_client: Optional[Client] = None
        self._lobby_sig: tuple = ()
        self.chat_entry: Optional[Any] = None
        self.chat_name = "Хост"
        self._join_result: Optional[tuple[str, Any]] = None
        self._disc_result: Optional[list] = None
        self.ctx.open_panel = self.open_panel
        self.ctx.refresh = self.refresh_ui
        self.ctx.start_play = self.start_play
        self.ctx.do = self.do
        self.ctx.select_army = self.select_army
        self.ctx.center_on = self.center_on_region

    # ---------- окно и тема ----------
    def _set_mode(self) -> pygame.Surface:
        flags = pygame.SCALED | (pygame.FULLSCREEN if SETTINGS.fullscreen else 0)
        return pygame.display.set_mode(WINDOW_SIZE, flags)

    def _make_manager(self) -> pygame_gui.UIManager:
        """UIManager с графитовой темой; при любой проблеме с темой — стандартный."""
        try:
            try:
                return pygame_gui.UIManager(self.size, theme_path=str(THEME_PATH), enable_live_theme_updates=False)
            except TypeError:
                return pygame_gui.UIManager(self.size, theme_path=str(THEME_PATH))
        except Exception:
            log.exception("Тема оформления не загружена — стандартная")
            return pygame_gui.UIManager(self.size)

    def _scenario_list(self) -> list[dict[str, Any]]:
        try:
            return scenarios.list_scenarios()
        except Exception:                                   # noqa: BLE001 — сценарии необязательны для запуска
            log.exception("Не удалось прочитать сценарии")
            return []

    def _new_state(self, seed: Optional[int] = None, tutorial: bool = False, scenario: str = "modern",
                   difficulty: Optional[str] = None, ai: Optional[int] = None) -> GameState:
        """Новое состояние мира + выбранный сценарий (при ошибке данных — современная карта)."""
        provs = [(p.iso, p.name, p.continent) for p in self.provs]
        st = GameState.new(self.countries, provs, self.adj, seed=seed, tutorial=tutorial, geo=self.geo)
        try:
            data = self._sc_cache.get(scenario) or scenarios.load_scenario(scenario)
            self._sc_cache[scenario] = data
            scenarios.apply_scenario(st, data, self._tech_ids, difficulty or SETTINGS.difficulty,
                                     SETTINGS.ai_activity if ai is None else ai)
        except (scenarios.ScenarioError, KeyError, ValueError, OSError, TypeError):
            log.exception("Сценарий «%s» не применён — используется современная карта", scenario)
        return st

    # ---------- переходы ----------
    def new_game(self, tutorial: bool, opts: Optional[dict[str, Any]] = None) -> None:
        """Создаёт партию (сценарий и параметры из экрана выбора) и переходит к выбору страны."""
        if not self.countries and not self.provs:
            return
        opts = opts or {}
        self._start_speed = int(opts.get("speed", 0)) if not tutorial else 0
        self.enter_game(self._new_state(tutorial=tutorial, scenario=str(opts.get("scenario", "modern")),
                                        difficulty=opts.get("difficulty"), ai=opts.get("ai")))

    def enter_game(self, state: GameState, session: Optional[Session] = None) -> None:
        """Строит игровой UI вокруг состояния."""
        self.leave_game()
        self._close_lobby_ui()
        if self.menu:
            self.menu.kill()
            self.menu = None
        self.session = session or Session(state)
        self.ctx.session = self.session
        self.state, self.mode = state, "play"
        self.ctx.state, self.ctx.selected = state, state.player_iso
        self.sel_army = self.ctx.sel_army = None
        self.toasts.clear()
        self._toast_n = state.log_seq
        self.renderer.eta_of = lambda a: am.eta_days(state, a)
        if not self.session.multiplayer and not state.tutorial and self._start_speed:
            self.session.clock.set_speed(self._start_speed)
        self.hud = HUD(self.ctx)
        if self.session.multiplayer:
            self.chat_name = (self.session.host.name if self.session.host else
                              (self.session.client.name if self.session.client else "Игрок"))
            self.chat_entry = pygame_gui.elements.UITextEntryLine(
                pygame.Rect(LEFT_PANEL_W + 8, self.size[1] - 36, 420, 30), self.manager,
                placeholder_text="Чат (Enter — отправить)")
            self.chat_entry.set_text_length_limit(200)
        self.panels = {p.NAME: p for p in (CountryPanel(self.ctx), DiplomacyPanel(self.ctx),
                                           ResearchPanel(self.ctx), MilitaryPanel(self.ctx),
                                           ArmiesPanel(self.ctx), ProgressPanel(self.ctx))}
        self.active, state.map_dirty = None, True
        self.renderer.pulse = 0.0
        self._ui_rev = -1
        if state.player_iso:
            self.focus_player(2.0)
            self.open_panel("country")
        else:
            self.camera.focus(1800, 700, 0.6)
            self.open_panel("country")
            if state.tutorial:
                self.tctx = TutorContext(self.ctx.reg, self.renderer, self.camera)
                self.tctx.state, self.tctx.open_panel, self.tctx.select = state, self.open_panel, self.select
                self.engine = TutorEngine(self.tctx)
                self.overlay = TutorialOverlay(self.ctx, self.engine)

    def leave_game(self) -> None:
        """Убирает игровой UI."""
        if self.hud:
            for p in self.panels.values():
                p.close()
            self.hud.kill()
        if self.overlay:
            self.overlay.kill()
        if self.btn_over is not None:
            self.btn_over.kill()
            self.btn_over = None
        if self.chat_entry is not None:
            self.chat_entry.kill()
            self.chat_entry = None
        self.hud, self.panels, self.overlay, self.engine, self.tctx = None, {}, None, None, None
        self.active = None
        self.ctx.reg.clear()
        self.state = None
        self.ctx.state = None
        self.sel_army = self.ctx.sel_army = None
        if self.join_picker is not None:
            self.join_picker.close()
            self.join_picker = None

    def to_menu(self, message: str = "") -> None:
        """Возврат в главное меню (с автосохранением; сетевые соединения закрываются)."""
        st, ses = self.state, self.session
        if st and ses and st.player_iso and not st.game_over and SETTINGS.autosave and not ses.is_client \
                and self.mode == "play":
            save_game(st, "Автосохранение")
        if ses:
            ses.close()
        self.session = None
        self.ctx.session = None
        self._close_lobby_ui(close_net=True)
        self.leave_game()
        self.mode = "menu"
        self.menu = MainMenu(self.manager, self.size, has_saves=bool(list_saves(1)))
        if message:
            self.menu.set_status(message)

    def start_play(self) -> None:
        """Подтверждение выбора страны игроком (одиночная игра)."""
        st = self.state
        if st and self.ctx.selected and st.player_iso is None:
            st.player_iso = self.ctx.selected
            st.humans = {st.player_iso}
            self.focus_player(2.0)
            self.open_panel("country")
            st.log_event(f"Вы играете за: {st.countries[st.player_iso].name}", st.player_iso, True)

    def focus_player(self, zoom: float) -> None:
        """Камера на страну игрока."""
        iso = self.state.player_iso if self.state else None
        w = self.renderer.capital_world(self.state, iso) if (self.state and iso) else None
        if w is not None:
            self.camera.focus(*w, zoom)

    def center_on_region(self, rid: str) -> None:
        """Центрирует карту на регионе (кнопка «Показать на карте»)."""
        p = self.renderer.by_iso.get(rid)
        if p is not None:
            self.camera.focus(*p.center_world, max(self.camera.zoom, 2.4))

    def select_army(self, aid: Optional[int]) -> None:
        """Выбор армии (плашка на карте или список)."""
        self.sel_army = self.ctx.sel_army = aid
        if aid is not None and self.mode == "play":
            if self.active != "armies":
                self.open_panel("armies")
            else:
                self.refresh_ui()
        elif self.active == "armies":
            self.refresh_ui()

    def _order_at(self, pos: tuple[int, int]) -> None:
        """ПКМ по карте: приказ выбранной армии идти в регион под курсором (права и маршрут проверяет ядро)."""
        st = self.state
        a = st.armies.get(self.sel_army) if (st and self.sel_army) else None
        if a is None or a.owner != st.player_iso or self.over_ui(pos):
            return
        rid = self.renderer.province_at(pos)
        if not rid:
            self.ctx.say(False, "Укажите регион на суше")
            return
        self.do({"t": "army_move", "id": a.id, "target": rid})

    # ---------- команды игрока ----------
    def do(self, cmd: dict[str, Any]) -> None:
        """Команда игрока: выполняется локально (одиночная/хост) или отправляется хосту (клиент)."""
        if not self.session:
            return
        res = self.session.do(cmd)
        if res is not None:
            self.ctx.say(*res)
        self.refresh_ui()

    # ---------- панели ----------
    def open_panel(self, name: str) -> None:
        """Открывает панель (остальные закрываются)."""
        if self.mode == "lobby":
            return
        for n, p in self.panels.items():
            if n != name:
                p.close()
        if name in self.panels:
            self.panels[name].open()
            self.active = name

    def toggle_panel(self, name: str) -> None:
        """Открыть/закрыть панель кнопкой HUD."""
        if self.active == name and self.panels[name].is_open:
            self.panels[name].close()
            self.active = None
        else:
            self.open_panel(name)

    def refresh_ui(self) -> None:
        """Перестраивает активную панель."""
        if self.mode == "lobby" and self.lobby_panel:
            self.lobby_panel.refresh()
        elif self.active and self.active in self.panels:
            self.panels[self.active].refresh()

    def select(self, iso: Optional[str]) -> None:
        """Выбор страны на карте."""
        if iso is None:
            return
        if self.state and iso in self.state.owner:          # захваченная страна — выбирается держава-владелец
            iso = self.state.owner[iso]
        self.ctx.selected = iso
        if self.mode == "lobby":
            self.refresh_ui()
        elif self.active is None:
            self.open_panel("country")
        else:
            self.refresh_ui()

    def over_ui(self, pos: tuple[int, int]) -> bool:
        """Находится ли точка над элементами интерфейса."""
        if self.mode == "lobby":
            return bool(self.lobby_panel and self.lobby_panel.rect.collidepoint(pos))
        if self.mode == "join":
            return bool(self.join_picker and self.join_picker.rect.collidepoint(pos))
        if self.mode != "play" or not self.hud:
            return False
        if self.hud.blocks(pos):
            return True
        if self.chat_entry is not None and self.chat_entry.rect.collidepoint(pos):
            return True
        if self.active and self.panels[self.active].is_open and self.panels[self.active].rect.collidepoint(pos):
            return True
        if self.overlay and not (self.engine and self.engine.finished) and self.overlay.box.collidepoint(pos):
            return True
        return bool(self.state and self.state.game_over and not self.state.game_over.startswith("victory:"))

    # ---------- события ----------
    def handle(self, e: pygame.event.Event) -> None:
        """Диспетчер событий."""
        if e.type == pygame.QUIT:
            self.running = False
            return
        self.manager.process_events(e)
        if self.mode == "menu" and self.menu:
            res = self.menu.handle_event(e)
            if res:
                self.menu_action(*res)
            return
        if self.mode == "lobby":
            if self.lobby_panel and self.lobby_panel.handle_event(e):
                return
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                self.to_menu()
            else:
                self.map_event(e)
            return
        if self.mode == "join":
            if self.join_picker and self.join_picker.handle_event(e):
                return
            if e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE:
                self.to_menu()
            else:
                self.map_event(e)
            return
        if self.mode != "play" or not self.state or not self.hud:
            return
        if self.overlay and self.overlay.handle_event(e):
            return
        if e.type == pygame_gui.UI_BUTTON_PRESSED and self.btn_over is not None and e.ui_element is self.btn_over:
            self.to_menu()
            return
        if e.type == pygame.KEYDOWN and e.key == pygame.K_F9:
            self.set_lite(not self.lite)
            return
        ce = self.chat_entry
        if ce is not None:
            if e.type == pygame_gui.UI_TEXT_ENTRY_FINISHED and e.ui_element is ce:
                if self.session:
                    self.session.send_chat(self.chat_name, e.text)
                ce.set_text("")
                ce.unfocus()
                return
            if e.type == pygame.KEYDOWN:
                if ce.is_focused:
                    if e.key == pygame.K_ESCAPE:
                        ce.unfocus()
                    return
                if e.key == pygame.K_RETURN:
                    ce.focus()
                    return
        act = self.hud.handle_event(e)
        if act:
            self.hud_action(act)
            return
        if self.active and self.panels[self.active].handle_event(e):
            return
        if e.type == pygame.KEYDOWN:
            self.on_key(e.key)
        else:
            self.map_event(e)

    def map_event(self, e: pygame.event.Event) -> None:
        """Управление картой: колесо — масштаб, ЛКМ + перетаскивание — сдвиг, клик — выбор страны."""
        if e.type == pygame.MOUSEWHEEL:
            pos = pygame.mouse.get_pos()
            if not self.over_ui(pos):
                self.camera.zoom_at(e.y, *pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 3:
            if self.mode == "play":
                self._order_at(e.pos)
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.drag_start, self.dragging, self.press_on_ui = e.pos, False, self.over_ui(e.pos)
        elif e.type == pygame.MOUSEMOTION:
            if self.drag_start and e.buttons[0] and not self.press_on_ui:
                if self.dragging or abs(e.pos[0] - self.drag_start[0]) + abs(e.pos[1] - self.drag_start[1]) > 5:
                    self.dragging = True
                    self.hover = None                                # во время перетаскивания подсветку не считаем
                    self.camera.pan(*e.rel)
            else:
                self._mouse, self._hover_dirty = e.pos, True       # hover считается раз в кадр, а не на каждое событие
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.drag_start and not self.dragging and not self.press_on_ui:
                aid = self.renderer.army_at(e.pos) if self.mode == "play" else None
                if aid is not None:
                    self.select_army(aid)
                else:
                    self.select(self.renderer.province_at(e.pos))
            self.drag_start = None
            self._mouse, self._hover_dirty = e.pos, True

    def on_key(self, key: int) -> None:
        """Горячие клавиши: Пробел — пауза, 1-5 — скорость, +/- — быстрее/медленнее, . — шаг в день."""
        ses = self.session
        if key == pygame.K_SPACE:
            self.hud_action("pause")
        elif key == pygame.K_ESCAPE:
            if self.sel_army is not None:
                self.select_army(None)
            else:
                self.to_menu()
        elif key in (pygame.K_y, pygame.K_n) and ses and ses.vote:
            ses.cast_vote(key == pygame.K_y)
        elif key == pygame.K_s and ses and self.sel_army is not None:
            self.do({"t": "army_stop", "id": self.sel_army})
        elif key in (pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4, pygame.K_5):
            self.hud_action(f"speed:{key - pygame.K_1}")
        elif key in (pygame.K_EQUALS, pygame.K_PLUS, pygame.K_KP_PLUS) and ses and not ses.is_client:
            ses.clock.faster()
            ses._dirty = True
        elif key in (pygame.K_MINUS, pygame.K_KP_MINUS) and ses and not ses.is_client:
            ses.clock.slower()
            ses._dirty = True
        elif key == pygame.K_PERIOD:
            self.hud_action("next_turn")
        elif key == pygame.K_m:
            self.hud_action("map_mode")
        elif key == pygame.K_F5:
            self.hud_action("save")
        elif key == pygame.K_F9 and self.menu is None and ses and not ses.multiplayer:
            self.menu_action("load", None)
        elif key == pygame.K_c:
            self.focus_player(2.0)

    def hud_action(self, act: str) -> None:
        """Действия кнопок HUD."""
        st, ses = self.state, self.session
        if act.startswith("panel:"):
            self.toggle_panel(act.split(":", 1)[1])
        elif act == "pause" and ses and st and st.player_iso:
            ses.toggle_pause()
        elif act.startswith("speed:") and ses and st and st.player_iso:
            ses.set_speed(int(act.split(":", 1)[1]))
        elif act == "next_turn" and ses and st and st.player_iso:
            ses.step_day()
            self.refresh_ui()
        elif act == "map_mode":
            SETTINGS.map_mode = "political" if SETTINGS.map_mode == "ideology" else "ideology"
            SETTINGS.save()
            if st:
                st.map_dirty = True
        elif act == "save" and st and st.player_iso and ses and not ses.is_client:
            sid = save_game(st, "Ручное сохранение")
            st.log_event("Игра сохранена" if sid else "✖ Не удалось сохранить", st.player_iso)
        elif act == "menu":
            self.to_menu()

    def menu_action(self, action: str, data: Any) -> None:
        """Действия главного меню."""
        if action == "new":
            self.new_game(False)
        elif action == "new_scenario":
            self.new_game(False, data)
        elif action == "tutorial":
            self.new_game(True)
        elif action == "continue":
            self.load(None)
        elif action == "load_menu" and self.menu:
            self.menu.show_load(list_saves())
        elif action == "load":
            self.load(data)
        elif action == "host_lan":
            self.start_host(str(data))
        elif action == "join":
            self.start_join(*data)
        elif action == "discover":
            self.start_discover()
        elif action == "apply_display":
            self.screen = self._set_mode()
            if self.menu:
                self.menu.show_settings()
        elif action == "quit":
            self.running = False

    def load(self, save_id: Optional[int]) -> None:
        """Загружает сохранение (None — последнее)."""
        if save_id is None:
            saves = list_saves(1)
            if not saves:
                return
            save_id = saves[0]["id"]
        st = load_game(save_id, self.adj, self.geo)
        if st is None or set(st.owner) != {p.iso for p in self.provs}:
            log.error("Сохранение несовместимо с текущей картой или повреждено")
            if self.menu:
                self.menu.set_status("Сохранение повреждено или несовместимо")
            return
        if st.player_iso:
            st.humans = {st.player_iso}
        self.enter_game(st)

    # ---------- LAN ----------
    def _enter_lobby(self, role: str, host: Optional[Host] = None, client: Optional[Client] = None) -> None:
        """Открывает лобби: карта для выбора страны и правая панель."""
        if self.menu:
            self.menu.kill()
            self.menu = None
        sid = host.scenario_id if host else str((client.info or {}).get("scenario_id", "modern")) if client else "modern"
        if host:
            sid = SETTINGS.last_scenario if any(x["id"] == SETTINGS.last_scenario for x in self._scenario_list()) else "modern"
            host.scenario_id, host.valid_isos = sid, set()
        self.preview = self._new_state(scenario=sid)
        self._lobby_sid = sid
        if host:
            host.valid_isos = set(self.preview.countries)
            self._lobby_info_update(host)
        self.state = self.preview
        self.ctx.state, self.ctx.selected = self.preview, None
        self.preview.map_dirty = True
        self.mode = "lobby"
        self.lobby_host, self.lobby_client = host, client
        lb = LobbyInfo(role=role, my_pid=0 if host else client.pid)  # type: ignore[union-attr]
        if host:
            lb.ip, lb.port, lb.discovery = host.ip, host.port, host.discovery_ok
            lb.players = host.lobby_view()
        lb.pick, lb.toggle_ready, lb.start, lb.leave = self._lobby_pick, self._lobby_ready, self._lobby_start, self.to_menu
        lb.can_start = host.can_start if host else (lambda: (False, ""))
        lb.scenario = self.preview.scenario.get("name", "")
        if host:
            lb.allow_join = host.allow_join
            lb.toggle_join, lb.cycle_scenario = self._lobby_toggle_join, self._lobby_cycle_scenario
        self.lobby = lb
        self.ctx.lobby = lb                                    # type: ignore[attr-defined]
        self.lobby_panel = LobbyPanel(self.ctx)
        self.lobby_panel.open()
        self._lobby_sig = lb.signature()
        self.camera.focus(1800, 700, 0.6)

    def _close_lobby_ui(self, close_net: bool = False) -> None:
        if self.lobby_panel:
            self.lobby_panel.close()
        self.lobby_panel, self.lobby = None, None
        if close_net:
            for obj in (self.lobby_host, self.lobby_client):
                if obj:
                    obj.close()
            self.lobby_host = self.lobby_client = None
        elif self.mode != "lobby":
            self.lobby_host = self.lobby_client = None

    def _lobby_info_update(self, host: Host) -> None:
        """Описание партии для автопоиска (сценарий и дата старта)."""
        sc = self.preview.scenario
        host.extra_info = {"scenario": sc.get("name", ""), "scenario_id": sc.get("id", "modern"),
                           "date": self.preview.date.isoformat()}

    def _lobby_toggle_join(self) -> None:
        if self.lobby_host and self.lobby:
            self.lobby_host.allow_join = not self.lobby_host.allow_join
            self.lobby.allow_join = self.lobby_host.allow_join

    def _set_lobby_scenario(self, sid: str) -> None:
        """Меняет сценарий лобби (хост) или подстраивает карту под сценарий хоста (клиент)."""
        self.preview = self._new_state(scenario=sid)
        self._lobby_sid = sid
        self.state = self.ctx.state = self.preview
        self.ctx.selected = None
        self.preview.map_dirty = True
        self.renderer.rebuild(self.preview)
        if self.lobby:
            self.lobby.scenario = self.preview.scenario.get("name", "")
        host = self.lobby_host
        if host:
            host.scenario_id, host.valid_isos = sid, set(self.preview.countries)
            for p in host.players.values():
                p.iso, p.ready = None, False
            self._lobby_info_update(host)
            host._changed_lobby()

    def _lobby_cycle_scenario(self) -> None:
        ids = [x["id"] for x in self._scenario_list()] or ["modern"]
        cur = getattr(self, "_lobby_sid", "modern")
        self._set_lobby_scenario(ids[(ids.index(cur) + 1) % len(ids)] if cur in ids else ids[0])

    def start_host(self, name: str) -> None:
        """Создаёт LAN-игру: открывает порт и показывает IP/порт в лобби."""
        host = Host(name, {p.iso for p in self.provs})
        try:
            host.start()
        except OSError as exc:
            log.exception("Не удалось открыть порт")
            if self.menu:
                self.menu.set_status(f"Не удалось открыть порт: {exc}")
            return
        self._enter_lobby("host", host=host)

    def start_join(self, ip: str, port: int, name: str) -> None:
        """Подключение к LAN-игре в фоне (интерфейс не замирает)."""
        if self._join_result == ("busy", None):
            return
        self._join_result = ("busy", None)
        if self.menu:
            self.menu.set_status(f"Подключение к {ip}:{port}...")

        def work() -> None:
            c = Client(name)
            token = SETTINGS.last_token if SETTINGS.last_token_host == f"{ip}:{port}" else ""
            try:
                c.connect(ip, port, token=token)
                SETTINGS.last_token, SETTINGS.last_token_host = c.token, f"{ip}:{port}"
                SETTINGS.save()
                self._join_result = ("ok", c)
            except ClientError as exc:
                self._join_result = ("err", str(exc))
            except Exception as exc:  # любая неожиданная ошибка сети — в статус, а не в падение
                log.exception("Ошибка подключения")
                self._join_result = ("err", f"Ошибка подключения: {exc}")

        threading.Thread(target=work, name="wc-join", daemon=True).start()

    def start_discover(self) -> None:
        """Поиск LAN-серверов в фоне."""
        if self.menu:
            self.menu.set_status("Поиск серверов...")

        def work() -> None:
            try:
                self._disc_result = discover(1.3)
            except Exception:
                log.exception("Ошибка автопоиска")
                self._disc_result = []

        threading.Thread(target=work, name="wc-discover", daemon=True).start()

    def _poll_menu_net(self) -> None:
        if self._join_result and self._join_result[0] in ("ok", "err"):
            kind, val = self._join_result
            self._join_result = None
            if kind == "ok":
                if val.phase == "play":
                    self._enter_late_join(val)
                else:
                    self._enter_lobby("client", client=val)
            elif self.menu:
                self.menu.set_status(val)
        if self._disc_result is not None and self.menu:
            found, self._disc_result = self._disc_result, None
            if self.menu.screen == "join":
                self.menu.show_join(found)
                self.menu.set_status(f"Найдено серверов: {len(found)}" if found else
                                     "Серверы не найдены — введите IP и порт вручную")

    def _lobby_pick(self) -> None:
        iso = self.ctx.selected
        if not iso or not self.lobby:
            return
        if self.lobby_host:
            if not self.lobby_host.pick(0, iso):
                self.lobby.message = "Эта страна уже занята"
        elif self.lobby_client:
            self.lobby_client.pick(iso)

    def _lobby_ready(self) -> None:
        if self.lobby and self.lobby_client:
            me = next((p for p in self.lobby.players if p["pid"] == self.lobby.my_pid), None)
            self.lobby_client.ready(not (me and me["ready"]))

    def _lobby_start(self) -> None:
        host, lb = self.lobby_host, self.lobby
        if not host or not lb:
            return
        ok, why = host.can_start()
        if not ok:
            lb.message = why
            return
        st = self.preview
        st.player_iso = host.players[0].iso
        host.allow_join = lb.allow_join
        st.log_event("LAN-партия началась", None, True)
        ses = Session(st, "host", host=host)
        ses.clock.set_speed(SETTINGS.default_speed)
        ses.begin_play()                                       # стартовые снимки уходят всем клиентам лобби
        self.lobby_host = self.lobby_client = None
        self.enter_game(st, ses)

    def _update_lobby(self) -> None:
        lb = self.lobby
        if lb is None:
            return
        if self.lobby_host:
            self.lobby_host.pump()
            lb.players = self.lobby_host.lobby_view()
        elif self.lobby_client:
            for m in self.lobby_client.pump():
                t = m.get("t")
                if t == "lobby":
                    lb.players = m.get("players", [])
                    sid = str(m.get("scenario", "modern"))
                    if sid != getattr(self, "_lobby_sid", "modern"):
                        self._set_lobby_scenario(sid)
                elif t == "start":
                    self._client_start(m)
                    return
                elif t in ("closed", "reject"):
                    self.to_menu(str(m.get("reason", "Соединение с хостом потеряно")))
                    return
        if lb.signature() != self._lobby_sig and self.lobby_panel:
            self._lobby_sig = lb.signature()
            self.lobby_panel.refresh()

    def _client_start(self, m: dict[str, Any]) -> None:
        """Старт партии из лобби: применяем стартовый снимок хоста."""
        try:
            st = self._new_state()
            ses = Session(st, "client", client=self.lobby_client)
            ses._apply_full(m)
        except (KeyError, ValueError, TypeError) as exc:
            log.exception("Не удалось принять мир от хоста")
            self.to_menu(f"Ошибка начала игры: {exc}")
            return
        self.lobby_host = self.lobby_client = None
        self.enter_game(st, ses)

    def _enter_late_join(self, client: Client) -> None:
        """Подключение к идущей партии: окно выбора страны (если место не удержано) и мгновенная синхронизация."""
        if self.menu:
            self.menu.kill()
            self.menu = None
        st = self._new_state()
        ses = Session(st, "client", client=client)
        self.session = self.ctx.session = ses
        self.state, self.mode = st, "join"
        self.ctx.state, self.ctx.selected = st, None
        st.map_dirty = True
        self.camera.focus(1800, 700, 0.6)
        self.join_picker = JoinPicker(self.ctx, client.info, client.claim, self.to_menu, ses)
        self.join_picker.waiting = client.resumed
        self.join_picker.open()
        self._join_sig: tuple = (len(ses.free), ses.claim_error)

    def _finish_join(self) -> None:
        """Снимок получен: закрываем окно выбора и входим в партию."""
        st, ses = self.state, self.session
        if self.join_picker:
            self.join_picker.close()
            self.join_picker = None
        assert st is not None and ses is not None
        self.enter_game(st, ses)
        self.toasts.append(["Подключение завершено: вы в идущей партии", 6.0, (170, 210, 255)])

    # ---------- кадр ----------
    def update(self, dt: float) -> None:
        """Обновление логики: время, сеть, ИИ (всё внутри Session — по игровым дням, не по кадрам)."""
        self.t += dt
        self.manager.update(dt)
        if self.mode == "menu":
            self._poll_menu_net()
            return
        if self.mode == "lobby":
            self._update_hover()
            self._update_lobby()
            return
        if self.mode == "join":
            self._update_hover()
            ses = self.session
            if ses is None or self.state is None:
                return
            ses.update(dt)
            if ses.closed_reason:
                self.to_menu(ses.closed_reason)
            elif ses.synced and self.state.player_iso:
                self._finish_join()
            elif self.join_picker and (len(ses.free), ses.claim_error) != self._join_sig:
                self._join_sig = (len(ses.free), ses.claim_error)
                if ses.claim_error:
                    self.join_picker.waiting = False
                self.join_picker.refresh()
            return
        self._update_hover()
        ses, st = self.session, self.state
        if self.mode != "play" or not ses or not st or not self.hud:
            return
        if self.engine:
            self.engine.update()
            if self.overlay:
                self.overlay.update()
            if self.engine.finished and st.tutorial:
                st.tutorial = False
        ses.clock.max_days = 1 if self.lite else MAX_DAYS_PER_FRAME
        ses.update(dt)
        if ses.closed_reason:
            self.to_menu(ses.closed_reason + (" Подключитесь снова: место удерживается 5 минут."
                                              if ses.is_client and "потеряно" in ses.closed_reason else ""))
            return
        if self.sel_army is not None and self.sel_army not in st.armies:
            self.select_army(None)
        self._collect_toasts(dt)
        self._ui_t += dt
        if ses.rev != self._ui_rev and self._ui_t >= UI_REFRESH_INTERVAL:
            self._ui_rev, self._ui_t = ses.rev, 0.0
            self.refresh_ui()
        over = st.game_over and not st.game_over.startswith("victory:")
        if over and self.btn_over is None:
            self.btn_over = pygame_gui.elements.UIButton(
                pygame.Rect(self.size[0] // 2 - 120, self.size[1] // 2 + 20, 240, 44), "В главное меню", self.manager)
        self.hud.update(ses)

    def _collect_toasts(self, dt: float) -> None:
        """Всплывающие уведомления: важные события, касающиеся вашей державы (без повторов), и сообщения сети."""
        st, ses = self.state, self.session
        if st is None or ses is None:
            return
        for t in self.toasts:
            t[1] -= dt
        self.toasts = [t for t in self.toasts if t[1] > 0]
        me = st.player_iso
        if me and st.log:
            for e in st.log:
                if e.get("n", 0) <= self._toast_n:
                    continue
                if e.get("iso") == me or me in e.get("isos", ()):
                    if e.get("imp") or e.get("k") in ("capture", "warn"):
                        k = e.get("k")
                        col = (226, 120, 120) if k in ("war", "battle", "warn") else (232, 214, 140) if e.get("imp") \
                            else (214, 218, 224)
                        if not any(t[0] == e["text"] for t in self.toasts):
                            self.toasts.append([e["text"], 7.0, col])
            self._toast_n = max(self._toast_n, st.log[-1].get("n", 0))
        for note in ses.notices:
            self.toasts.append([note, 6.0, (170, 210, 255)])
        ses.notices.clear()
        del self.toasts[:-4]

    def _update_hover(self) -> None:
        """Провинция под курсором — один раз за кадр."""
        if self.dragging:
            return
        if self._hover_dirty:
            self._hover_dirty = False
            self.hover = None if self.over_ui(self._mouse) else self.renderer.province_at(self._mouse)

    def _banner(self, text: str, color: tuple[int, int, int], y: int, size: int = 26) -> None:
        t = get_font(size).render(text, True, color)
        x = (self.size[0] - t.get_width()) // 2
        bg = pygame.Surface((t.get_width() + 28, t.get_height() + 12), pygame.SRCALPHA)
        bg.fill((14, 16, 20, 190))
        self.screen.blit(bg, (x - 14, y - 6))
        self.screen.blit(t, (x, y))

    def render(self, dt: float) -> None:
        """Отрисовка кадра."""
        if self.mode == "menu" and self.menu:
            self.renderer.draw_backdrop(self.screen, self.t)
            self.menu.draw(self.screen)
            self.manager.draw_ui(self.screen)
            return
        st = self.state
        if self.mode == "lobby" and st:
            picked = tuple(p["iso"] for p in (self.lobby.players if self.lobby else []) if p.get("iso"))
            self.renderer.draw(self.screen, st, self.hover, self.ctx.selected, picked)
            self._banner("LAN-лобби: выберите страну кликом по карте", (226, 230, 236), 14, 20)
            self.manager.draw_ui(self.screen)
            return
        if self.mode == "join" and st:
            self.renderer.draw(self.screen, st, self.hover, None, ())
            self._banner("Вход в идущую партию: выберите страну", (226, 230, 236), 14, 20)
            self.manager.draw_ui(self.screen)
            return
        ses = self.session
        if not st or not self.hud or not ses:
            return
        extra = self.engine.extra_countries() if self.engine and not self.engine.finished else ()
        self.renderer.draw(self.screen, st, self.hover, self.ctx.selected, extra)
        pick = self.hover if (self.sel_army is not None and self.hover and not self.dragging) else None
        self.renderer.draw_overlays(self.screen, st, st.player_iso, dt, self.sel_army, pick)
        if st.player_iso and self.engine and not self.engine.finished and self.engine.step and \
                self.engine.step.id in ("capital",):
            self.renderer.draw_marker(self.screen, st.player_iso, dt)
        side = bool(self.active and self.panels[self.active].is_open)
        tut = bool(self.overlay and self.engine and not self.engine.finished)
        bottom = self.size[1] - (TUTORIAL_H + 16 if tut else 0)
        if self.chat_entry is not None:
            bottom -= 36
            chat = ses.chat_log[-6:]
            if chat:
                font = get_font(14)
                h = len(chat) * 20 + 6
                bg = pygame.Surface((min(640, self.size[0] - LEFT_PANEL_W - 24), h), pygame.SRCALPHA)
                bg.fill((12, 14, 18, 170))
                x0, y0 = LEFT_PANEL_W + 8, bottom - h
                self.screen.blit(bg, (x0, y0))
                for i, (nm, tx) in enumerate(chat):
                    self.screen.blit(font.render(f"{nm}: {tx}", True, (170, 210, 255)), (x0 + 6, y0 + 3 + i * 20))
                bottom -= h + 4
        self.hud.draw_log(self.screen, bottom, side)
        if self.hover and self.hover in st.owner and not self.dragging:
            self._tooltip(st, self.hover)
        if tut and self.overlay:
            self.overlay.draw_box(self.screen)
        self.manager.draw_ui(self.screen)
        if tut and self.overlay:
            self.overlay.draw_highlight(self.screen, pygame.Rect(
                LEFT_PANEL_W, HUD_HEIGHT, self.size[0] - LEFT_PANEL_W - (SIDE_PANEL_W if side else 0),
                self.size[1] - HUD_HEIGHT))
        self._draw_toasts()
        if ses.vote:
            v = ses.vote
            self._banner(f"Голосование: {v['by']} — {ses._vote_text(v['kind'], v.get('value', 0))}.  "
                         f"Y — за, N — против  ({v['yes']}/{v['need']}, {v['left']} с)", (232, 214, 140),
                         HUD_HEIGHT + 44, 18)
        if st.player_iso and ses.clock.paused and not st.game_over:
            self._banner("ПАУЗА" + ("  (пробел — продолжить)" if not ses.is_client else "  (хост)"),
                         (214, 218, 224), HUD_HEIGHT + 10, 20)
        me = st.countries.get(st.player_iso) if st.player_iso else None
        if st.game_over:
            go = st.game_over
            if go == "victory" or go == f"victory:{st.player_iso}":
                self._banner("ПОБЕДА — вы владеете миром", (232, 214, 140), self.size[1] // 2 - 40, 38)
            elif go.startswith("victory:"):
                self._banner(f"Победил игрок: {st.countries[go.split(':', 1)[1]].name}", (214, 218, 224),
                             self.size[1] // 2 - 40, 32)
            else:
                self._banner("ПОРАЖЕНИЕ — ваша страна пала", (226, 120, 120), self.size[1] // 2 - 40, 38)
        elif me is not None and not me.alive and ses.multiplayer:
            self._banner("Ваша страна пала — вы наблюдаете за игрой", (226, 120, 120), HUD_HEIGHT + 46, 20)

    def _tooltip(self, st: GameState, rid: str) -> None:
        """Подсказка у курсора: регион, владелец, контролёр, местность, войска."""
        own = st.owner.get(rid, "")
        ctl = st.ctrl.get(rid, own)
        oc, cc = st.countries.get(own), st.countries.get(ctl)
        lines = [st.geo.name.get(rid, rid), f"Владелец: {oc.name if oc else own}"]
        if ctl != own and cc is not None:
            lines.append(f"Под контролем: {cc.name}")
        lines.append(f"Местность: {TERR_RU.get(st.geo.terrain.get(rid, ''), '—')}")
        n = sum(a.units for a in st.armies.values() if a.region == rid and a.progress <= 0)
        if n:
            lines.append(f"Войск в регионе: {n}")
        font = get_font(14)
        surfs = [font.render(t, True, (240, 242, 246)) for t in lines]
        w, h = max(s_.get_width() for s_ in surfs) + 14, sum(s_.get_height() for s_ in surfs) + 10
        mx, my = pygame.mouse.get_pos()
        x = min(mx + 14, self.size[0] - w - 4)
        y = min(my + 14, self.size[1] - h - 4)
        pygame.draw.rect(self.screen, (18, 21, 26), (x, y, w, h))
        pygame.draw.rect(self.screen, (70, 78, 90), (x, y, w, h), 1)
        yy = y + 5
        for s_ in surfs:
            self.screen.blit(s_, (x + 7, yy))
            yy += s_.get_height()

    def _draw_toasts(self) -> None:
        """Компактные всплывающие уведомления под верхней строкой."""
        font = get_font(15)
        y = HUD_HEIGHT + 8
        for text, ttl, col in self.toasts:
            t = font.render(text, True, col)
            bg = pygame.Surface((t.get_width() + 20, t.get_height() + 10), pygame.SRCALPHA)
            bg.fill((14, 16, 20, int(205 * min(1.0, ttl / 1.0))))
            x = self.size[0] - bg.get_width() - 12
            self.screen.blit(bg, (x, y))
            t.set_alpha(int(255 * min(1.0, ttl / 1.0)))
            self.screen.blit(t, (x + 10, y + 5))
            y += bg.get_height() + 4

    def set_lite(self, on: bool) -> None:
        """Режим для слабых устройств (авто или клавиша F9)."""
        self.lite = on
        self.renderer.lite = on
        log.info("Облегчённый режим: %s", on)

    def _perf(self, raw_ms: float, dt: float) -> None:
        """Если игра долго идёт медленнее ~36 кадров/с — включает облегчённый режим (необратимо до выхода)."""
        if not self._lite_init:                              # 1–2 ядра CPU — сразу облегчённый режим
            self._lite_init = True
            if (os.cpu_count() or 4) <= 2:
                self.set_lite(True)
        if self.mode != "play" or self.lite:
            self._ema, self._slow = 16.0, 0.0
            return
        self._ema = self._ema * 0.9 + raw_ms * 0.1
        self._slow = self._slow + dt if self._ema > 28.0 else max(0.0, self._slow - dt)
        if self._slow > 2.0:
            self.set_lite(True)

    def run(self) -> None:
        """Главный цикл."""
        while self.running:
            raw = self.clock.tick(30 if self.lite or self.mode != "play" else FPS)
            dt = min(raw / 1000.0, 0.25)
            self._perf(raw, dt)
            try:
                for e in pygame.event.get():
                    self.handle(e)
                self.update(dt)
                self.render(dt)
            except Exception:
                log.exception("Необработанная ошибка в кадре")
            pygame.display.flip()
        ses, st = self.session, self.state
        if st and ses and st.player_iso and not st.game_over and SETTINGS.autosave and not ses.is_client \
                and self.mode == "play":
            save_game(st, "Автосохранение")
        if ses:
            ses.close()
        self._close_lobby_ui(close_net=True)
        pygame.quit()


def _check_data() -> None:
    """Проверяет наличие обязательных данных; при отсутствии — понятное сообщение вместо падения."""
    from config import DATA_DIR
    missing = []
    if not (DATA_DIR / "countries.json").exists():
        missing.append("countries.json")
    if not (DATA_DIR / "regions.json").exists() and not (DATA_DIR / "provinces.geojson").exists():
        missing.append("regions.json / provinces.geojson")
    if missing:
        msg = f"Не найдены данные игры: {', '.join(missing)}\nПапка: {DATA_DIR}\nПереустановите игру."
        log.error(msg)
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, msg, "World Conquest", 0x10)  # type: ignore[attr-defined]
        except Exception:
            print(msg, file=sys.stderr)
        raise SystemExit(2)


def main() -> None:
    """Запуск приложения."""
    setup_logging()
    _check_data()
    try:
        App().run()
    except Exception:
        log.exception("Критическая ошибка")
        raise


if __name__ == "__main__":
    main()
