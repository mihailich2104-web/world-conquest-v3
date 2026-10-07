"""LAN-лобби: список игроков, выбор страны на карте, готовность и старт (панель справа)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import SidePanel


@dataclass
class LobbyInfo:
    """Данные и действия лобби. Заполняет App; панель только показывает и вызывает колбэки."""
    role: str                                   # "host" | "client"
    ip: str = ""
    port: int = 0
    players: list[dict[str, Any]] = field(default_factory=list)
    message: str = ""
    my_pid: int = 0
    pick: Callable[[], None] = lambda: None
    toggle_ready: Callable[[], None] = lambda: None
    start: Callable[[], None] = lambda: None
    leave: Callable[[], None] = lambda: None
    can_start: Callable[[], tuple[bool, str]] = lambda: (False, "")
    discovery: bool = True

    def signature(self) -> tuple:
        """Для обнаружения изменений (перестраивать панель только при необходимости)."""
        return (tuple((p["pid"], p["name"], p["ready"], p["iso"]) for p in self.players), self.message)


class LobbyPanel(SidePanel):
    """Правая панель лобби."""
    NAME = "lobby"

    def build(self) -> None:
        ctx = self.ctx
        lb: Optional[LobbyInfo] = getattr(ctx, "lobby", None)
        st = ctx.state
        if lb is None or st is None:
            return
        lines = ["<b>LAN-лобби</b>"]
        if lb.role == "host":
            lines += [f"IP: <b>{lb.ip}</b>", f"Порт: <b>{lb.port}</b>"]
            if not lb.discovery:
                lines.append("Автопоиск недоступен — вводите IP и порт вручную")
        lines.append("")
        lines.append("<b>Игроки</b>")
        for p in lb.players:
            cname = st.countries[p["iso"]].name if p["iso"] in st.countries else "выбирает страну"
            mark = "готов" if p["ready"] else "не готов"
            who = " (хост)" if p["host"] else ""
            me = " ← вы" if p["pid"] == lb.my_pid else ""
            lines.append(f"{p['name']}{who}{me}: {cname} — {mark}")
        self.add_text("<br>".join(lines), len(lines) + 1)
        sel = ctx.selected
        label = f"Выбрать страну: {st.countries[sel].name}" if sel in st.countries else "Выберите страну на карте"
        self.add_button(label, lb.pick, enabled=sel in st.countries)
        if lb.role == "host":
            ok, why = lb.can_start()
            self.add_button("Начать игру", lb.start, enabled=ok)
            if not ok and why:
                self.add_text(why, 1)
        else:
            me = next((p for p in lb.players if p["pid"] == lb.my_pid), None)
            ready = bool(me and me["ready"])
            self.add_button("Не готов" if ready else "Готов", lb.toggle_ready, enabled=bool(me and me["iso"]))
        self.add_button("Покинуть лобби", lb.leave)
        if lb.message:
            self.add_text(lb.message, 2)
