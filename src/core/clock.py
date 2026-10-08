"""Игровое время: единые часы с паузой и скоростями 1X/2X/4X/8X/16X (без таймеров на каждое событие)."""
from __future__ import annotations

from config import DAY_SECONDS, MAX_DAYS_PER_FRAME, SPEEDS


class GameClock:
    """Накапливает реальное время и выдаёт число игровых дней, которые нужно просчитать в этом кадре."""

    def __init__(self, paused: bool = False, speed_idx: int = 0) -> None:
        self.paused = paused
        self.speed_idx = max(0, min(len(SPEEDS) - 1, speed_idx))
        self.acc = 0.0
        self.max_days = MAX_DAYS_PER_FRAME     # на слабых устройствах main.py снижает до 1

    @property
    def speed(self) -> int:
        """Текущий множитель скорости (1, 2, 4, 8, 16)."""
        return SPEEDS[self.speed_idx]

    def set_speed(self, idx: int) -> None:
        """Устанавливает скорость по индексу."""
        self.speed_idx = max(0, min(len(SPEEDS) - 1, int(idx)))

    def toggle_pause(self) -> None:
        """Пауза / продолжение."""
        self.paused = not self.paused

    def faster(self) -> None:
        """Следующая скорость."""
        self.set_speed(self.speed_idx + 1)

    def slower(self) -> None:
        """Предыдущая скорость."""
        self.set_speed(self.speed_idx - 1)

    def update(self, dt: float) -> int:
        """Сколько игровых дней прошло за dt секунд (0 при паузе). Остаток копится, а не теряется."""
        if self.paused:
            return 0
        self.acc += max(0.0, dt) * self.speed
        days = int((self.acc + 1e-9) // DAY_SECONDS)   # допуск на погрешность float
        if days <= 0:
            return 0
        self.acc -= days * DAY_SECONDS
        if days > self.max_days:               # защита от «спирали смерти» при лагах
            days = self.max_days
            self.acc = 0.0
        return days

    def to_dict(self) -> dict:
        """Состояние часов для снимка/сохранения."""
        return {"paused": self.paused, "speed_idx": self.speed_idx}

    def load(self, d: dict) -> None:
        """Применяет состояние часов (клиент LAN)."""
        self.paused = bool(d.get("paused", False))
        self.set_speed(d.get("speed_idx", 0))
