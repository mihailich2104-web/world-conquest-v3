"""Точка входа World Conquest: инициализация, главный цикл events → update → render.

Игровое время идёт автоматически (core.clock), одиночная игра и LAN идут через core.session.
"""
from __future__ import annotations

import json
import logging
import sys
import threading
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))   # импорты вида `from core import ...`

import pygame
import pygame_gui

from config import (COUNTRIES_PATH, FPS, HUD_HEIGHT, LEFT_PANEL_W, SETTINGS, SIDE_PANEL_W, THEME_PATH, TITLE,
                    TUTORIAL_H, USER_DIR, WINDOW_SIZE)
from core.game_state import GameState, list_saves, load_game, save_game
from core.session import Session
from map.camera import Camera
from map.map_loader import load_world
from map.map_renderer import MapRenderer
from net.client import Client, ClientError
from net.discovery import discover
from net.host import Host
from tutorial.tutor_engine import TutorContext, TutorEngine
from ui import UIContext, get_font
from ui.country_panel import CountryPanel
from ui.diplomacy_panel import DiplomacyPanel
from ui.hud import HUD
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
        self.provs, self.adj, self.fallback_map = load_world(self.countries)
        self.camera = Camera(*self.size)
        self.renderer = MapRenderer(self.provs, self.camera, get_font(13))
        # фон меню: карта мира с затемнением
        self.preview = self._new_state(seed=1)
        self.renderer.rebuild(self.preview)
        self.renderer.make_backdrop(self.size)
        self.t = 0.0
        self.mode = "menu"                                   # menu | lobby | play
        self.running = True
        self.menu: Optional[MainMenu] = MainMenu(self.manager, self.size, has_saves=bool(list_saves(1)))
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

    def _new_state(self, seed: Optional[int] = None, tutorial: bool = False) -> GameState:
        provs = [(p.iso, p.name, p.continent) for p in self.provs]
        return GameState.new(self.countries, provs, self.adj, seed=seed, tutorial=tutorial)

    # ---------- переходы ----------
    def new_game(self, tutorial: bool) -> None:
        """Создаёт партию и переходит к выбору страны."""
        if not self.countries and not self.provs:
            return
        self.enter_game(self._new_state(tutorial=tutorial))

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
        self.hud = HUD(self.ctx)
        if self.session.multiplayer:
            self.chat_name = (self.session.host.name if self.session.host else
                              (self.session.client.name if self.session.client else "Игрок"))
            self.chat_entry = pygame_gui.elements.UITextEntryLine(
                pygame.Rect(LEFT_PANEL_W + 8, self.size[1] - 36, 420, 30), self.manager,
                placeholder_text="Чат (Enter — отправить)")
            self.chat_entry.set_text_length_limit(200)
        self.panels = {p.NAME: p for p in (CountryPanel(self.ctx), DiplomacyPanel(self.ctx),
                                           ResearchPanel(self.ctx), MilitaryPanel(self.ctx))}
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
        if iso and iso in self.renderer.by_iso:
            self.camera.focus(*self.renderer.by_iso[iso].capital_world, zoom)

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
        if self.mode != "play" or not self.state or not self.hud:
            return
        if self.overlay and self.overlay.handle_event(e):
            return
        if e.type == pygame_gui.UI_BUTTON_PRESSED and self.btn_over is not None and e.ui_element is self.btn_over:
            self.to_menu()
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
        elif e.type == pygame.MOUSEBUTTONDOWN and e.button == 1:
            self.drag_start, self.dragging, self.press_on_ui = e.pos, False, self.over_ui(e.pos)
        elif e.type == pygame.MOUSEMOTION:
            if self.drag_start and e.buttons[0] and not self.press_on_ui:
                if self.dragging or abs(e.pos[0] - self.drag_start[0]) + abs(e.pos[1] - self.drag_start[1]) > 5:
                    self.dragging = True
                    self.camera.pan(*e.rel)
            else:
                self._mouse, self._hover_dirty = e.pos, True       # hover считается раз в кадр, а не на каждое событие
        elif e.type == pygame.MOUSEBUTTONUP and e.button == 1:
            if self.drag_start and not self.dragging and not self.press_on_ui:
                self.select(self.renderer.province_at(e.pos))
            self.drag_start = None

    def on_key(self, key: int) -> None:
        """Горячие клавиши: Пробел — пауза, 1-5 — скорость, +/- — быстрее/медленнее, . — шаг в день."""
        ses = self.session
        if key == pygame.K_SPACE:
            self.hud_action("pause")
        elif key == pygame.K_ESCAPE:
            self.to_menu()
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
        st = load_game(save_id, self.adj)
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
        self.preview = self._new_state()
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
            try:
                c.connect(ip, port)
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
        st.humans = set(host.humans())
        st.log_event("LAN-партия началась", None, True)
        ses = Session(st, "host", host=host)
        host.begin(st.to_dict(), ses.clock.to_dict())
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
        try:
            st = GameState.from_dict(m["state"], self.adj)
            if set(st.owner) != {p.iso for p in self.provs}:
                raise ValueError("карта хоста отличается от вашей")
        except (KeyError, ValueError) as exc:
            log.exception("Не удалось принять мир от хоста")
            self.to_menu(f"Ошибка начала игры: {exc}")
            return
        st.player_iso = m.get("you")
        client = self.lobby_client
        ses = Session(st, "client", client=client)
        ses.clock.load(m.get("clock", {}))
        self.lobby_host = self.lobby_client = None
        self.enter_game(st, ses)

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
        ses.update(dt)
        if ses.closed_reason:
            self.to_menu(ses.closed_reason)
            return
        self._ui_t += dt
        if ses.rev != self._ui_rev and self._ui_t >= UI_REFRESH_INTERVAL:
            self._ui_rev, self._ui_t = ses.rev, 0.0
            self.refresh_ui()
        over = st.game_over and not st.game_over.startswith("victory:")
        if over and self.btn_over is None:
            self.btn_over = pygame_gui.elements.UIButton(
                pygame.Rect(self.size[0] // 2 - 120, self.size[1] // 2 + 20, 240, 44), "В главное меню", self.manager)
        self.hud.update(ses)

    def _update_hover(self) -> None:
        """Провинция под курсором — один раз за кадр."""
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
        ses = self.session
        if not st or not self.hud or not ses:
            return
        extra = self.engine.extra_countries() if self.engine and not self.engine.finished else ()
        self.renderer.draw(self.screen, st, self.hover, self.ctx.selected, extra)
        self.renderer.draw_overlays(self.screen, st, st.player_iso, dt)
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
        if self.hover and self.hover in st.countries:
            name = st.countries[st.owner.get(self.hover, self.hover)].name
            t = get_font(15).render(name, True, (240, 242, 246))
            mx, my = pygame.mouse.get_pos()
            pygame.draw.rect(self.screen, (18, 21, 26), (mx + 12, my + 10, t.get_width() + 8, t.get_height() + 4))
            self.screen.blit(t, (mx + 16, my + 12))
        if tut and self.overlay:
            self.overlay.draw_box(self.screen)
        self.manager.draw_ui(self.screen)
        if tut and self.overlay:
            self.overlay.draw_highlight(self.screen, pygame.Rect(
                LEFT_PANEL_W, HUD_HEIGHT, self.size[0] - LEFT_PANEL_W - (SIDE_PANEL_W if side else 0),
                self.size[1] - HUD_HEIGHT))
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

    def run(self) -> None:
        """Главный цикл."""
        while self.running:
            dt = min(self.clock.tick(FPS) / 1000.0, 0.25)
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


def main() -> None:
    """Запуск приложения."""
    setup_logging()
    try:
        App().run()
    except Exception:
        log.exception("Критическая ошибка")
        raise


if __name__ == "__main__":
    main()
