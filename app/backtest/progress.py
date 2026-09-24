from dataclasses import dataclass


class Cancelled(Exception):
    """El usuario cancelo un analisis largo."""


@dataclass
class Progress:
    """Estado compartido entre el hilo que calcula y la interfaz que lo muestra."""

    total: int = 0
    done: int = 0
    message: str = ""
    cancelled: bool = False

    @property
    def fraction(self) -> float:
        return min(self.done / self.total, 1.0) if self.total else 0.0

    def step(self, message: str | None = None) -> None:
        self.done += 1
        if message is not None:
            self.message = message

    def check(self) -> None:
        if self.cancelled:
            raise Cancelled()
