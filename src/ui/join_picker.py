"""Выбор страны при входе в уже идущую LAN-партию (короткое окно: выбрал — сразу в игру)."""
from __future__ import annotations

from typing import Any, Callable

from . import SidePanel


class JoinPicker(SidePanel):
    """Список свободных стран хоста; после выбора клиент получает актуальный снимок и входит в партию."""
    NAME = "join"

    def __init__(self, ctx, info: dict[str, Any], claim: Callable[[str], None], leave: Callable[[], None],  # noqa: ANN001
                 session: Any) -> None:
        super().__init__(ctx)
        self.info, self.claim, self.leave, self.ses = info, claim, leave, session
        self.waiting = False

    def build(self) -> None:
        free = list(self.ses.free) if self.ses is not None else []
        title = f"<b>Партия уже идёт</b><br>{self.info.get('scenario', '')} — {self.info.get('date', '')}"
        self.add_text(title + "<br>Выберите свободную страну:", 3)
        if self.waiting:
            self.add_text("Синхронизация с партией…", 1)
        elif self.ses is not None and self.ses.claim_error:
            self.add_text(f"<font color='#e08080'>{self.ses.claim_error}</font>", 2)
        for r in free[:10]:
            tag = " (война)" if r.get("war") else ""
            self.add_button(f"{r['name']}: {r['regions']} рег., армия {r['army']}{tag}", (lambda i=r["iso"]: self._pick(i)),
                            h=28, enabled=not self.waiting)
        if free:
            self.add_button("Любая свободная (крупнейшая)", lambda: self._pick(free[0]["iso"]), enabled=not self.waiting)
        elif not self.waiting:
            self.add_text("Свободных стран нет.", 1)
        self.add_button("Отмена", self.leave)

    def _pick(self, iso: str) -> None:
        self.waiting = True
        if self.ses is not None:
            self.ses.claim_error = ""
        self.claim(iso)
