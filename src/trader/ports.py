"""Future adapter boundaries. No concrete network or order-writing adapter exists yet."""
from typing import Iterable, Protocol
from .core import Book, Candidate


class StrategyScanner(Protocol):
    def scan(self) -> Iterable[Candidate]:
        """Return point-in-time setups/evidence from the pinned ChatGptTrader strategy."""
        ...


class ReconciledBookProvider(Protocol):
    def read_book(self) -> Book:
        """Return isolated strategy cash, marked holdings and pending-buy reservations."""
        ...


class PaperExecutionDisabled(RuntimeError):
    pass


def submit_order(*args, **kwargs):
    raise PaperExecutionDisabled('Order submission is deliberately absent in the architecture milestone')
