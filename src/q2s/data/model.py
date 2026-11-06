"""What the factory's systems can see, and what the decision models can decide.

These types are shared by the simulated world and the decision models. Nothing here is truth:
a quote request is what a customer uploads (their design and BOM), not how price-sensitive they
are; an order is what the ERP holds, not when it will really finish.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Design:
    boards: int
    parts_per_board: int
    unique_parts: int
    two_sided: bool
    second_side_share: float          # share of a board's parts placed on side 2
    boards_per_panel: int
    tht_parts: int                    # through-hole parts per board, placed by hand
    fine_pitch_share: float           # share of parts with fine-pitch packages (from the BOM)
    bom: tuple[tuple[str, int], ...]  # (sku, uses per board)

    @property
    def panels(self) -> int:
        return -(-self.boards // self.boards_per_panel)

    @property
    def sides(self) -> int:
        return 2 if self.two_sided else 1

    def parts_on_side(self, side: int) -> int:
        if not self.two_sided:
            return self.parts_per_board
        second = round(self.parts_per_board * self.second_side_share)
        return second if side == 2 else self.parts_per_board - second


@dataclass(frozen=True)
class QuoteRequest:
    request_id: str
    day: int
    segment: str
    design: Design


@dataclass(frozen=True)
class Quote:
    request_id: str
    price: float
    promise_days: int                 # working days from the quote day to shipping
    explore: bool = False             # priced off-policy on purpose, to keep learning price response


@dataclass
class Order:
    order_id: str
    request_id: str
    segment: str
    design: Design
    price: float
    quote_day: int
    due_day: int
    released_day: int | None = None
    completed_minute: float | None = None
    shipped_day: int | None = None


@dataclass(frozen=True)
class PurchaseOrder:
    sku: str
    qty: int


@dataclass
class Decisions:
    quotes: list[Quote] = field(default_factory=list)
    release: list[str] = field(default_factory=list)       # order ids, in priority order
    purchases: list[PurchaseOrder] = field(default_factory=list)


@dataclass
class Observation:
    """What the planning systems know at 06:00 on a working day."""

    day: int
    requests: list[QuoteRequest]
    backlog: list[Order]                                    # accepted, not yet released
    in_progress: list[Order]                                # released, not shipped
    inventory: dict[str, int]                               # on hand, not allocated
    open_purchases: list[tuple[str, int, int]]              # (sku, qty, ordered day)
    catalog: dict[str, float]                               # sku -> unit cost
