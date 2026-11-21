"""Seasonal contract negotiation between the factory and the farm.

At each planting the two sides bargain over the price terms of a contract:

    unit price = max(floor, 1.0 + share x (spot price - 1.0))

- floor: the least the farmer is paid per unit, whatever the market does
- share: how much of any rise (or fall, down to the floor) in the spot price
  the farmer gets

The weekly quantity is the factory's demand forecast and is not negotiated.
They take turns making offers, factory first, for up to `rounds` turns. A
player can accept the offer on the table, make a counter-offer, or walk away.
No deal means no contract that season: the farmer sells at spot.

Each side judges an offer by replaying recent spot prices through it. Each
side also has private information the other can't see:
  farmer  - its growing cost, how much it fears a bad season, and how much
            below spot it gets when selling alone
  factory - how much a guaranteed supply is worth to it, and how much it
            fears paying high prices
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

FLOORS = np.round(np.arange(0.40, 1.401, 0.05), 2)
SHARES = np.round(np.arange(0.0, 1.001, 0.1), 2)
GRID = [(float(f), float(s)) for f in FLOORS for s in SHARES]


@dataclass(frozen=True)
class Terms:
    floor: float
    share: float


@dataclass
class Context:
    """What both sides know when they sit down."""
    week: int
    quantity: float             # weekly quantity the factory will commit to
    price_history: list[float]  # spot prices so far (public market data)
    base_price: float = 1.0


@dataclass
class Outcome:
    terms: Terms | None         # None = no deal
    rounds: int
    transcript: list[dict] = field(default_factory=list)


def price_samples(ctx: Context, window: int = 52) -> np.ndarray:
    """Recent spot prices, used by both sides as 'what could happen'."""
    p = ctx.price_history[-window:]
    return np.array(p if p else [ctx.base_price])


def unit_price(terms: Terms, prices: np.ndarray, base: float = 1.0) -> np.ndarray:
    return np.maximum(terms.floor, base + terms.share * (prices - base))


def _risk_adjusted(x: np.ndarray, aversion: float) -> float:
    """Average outcome minus a penalty for how bad a 1-in-10 outcome is."""
    return float(x.mean() - aversion * (x.mean() - np.quantile(x, 0.1)))


@dataclass
class FarmerPrefs:
    production_cost: float = 0.6
    risk_aversion: float = 1.0
    spot_discount: float = 0.0   # selling alone, a smallholder gets less than spot

    def utility(self, terms: Terms, ctx: Context) -> float:
        p = price_samples(ctx)
        return _risk_adjusted(unit_price(terms, p, ctx.base_price) - self.production_cost,
                              self.risk_aversion)

    def reservation(self, ctx: Context) -> float:
        """What walking away is worth: selling on the spot market alone."""
        p = price_samples(ctx)
        return _risk_adjusted(p * (1 - self.spot_discount) - self.production_cost,
                              self.risk_aversion)


@dataclass
class FactoryPrefs:
    security_value: float = 0.15  # value of a guaranteed supply, per unit
    risk_aversion: float = 0.5

    def utility(self, terms: Terms, ctx: Context) -> float:
        p = price_samples(ctx)
        paid = unit_price(terms, p, ctx.base_price)
        saving = p + self.security_value - paid   # vs buying the same unit at spot
        return float(saving.mean() - self.risk_aversion * (np.quantile(paid, 0.9) - paid.mean()))

    def reservation(self, ctx: Context) -> float:
        """What walking away is worth: buying at spot, exposed to price spikes."""
        p = price_samples(ctx)
        return float(-self.risk_aversion * (np.quantile(p, 0.9) - p.mean()))


# --- Negotiators ---------------------------------------------------------------

STYLES = {"tough": 0.4, "balanced": 1.0, "eager": 3.0}


class TimeDependentNegotiator:
    """Concedes from its best deal toward its walk-away point as rounds pass.

    `concession` < 1 holds out until late (tough), > 1 gives ground early
    (eager). This is the classic time-dependent tactic used as a baseline
    in automated negotiation research.
    """

    def __init__(self, prefs, concession: float | str = "balanced"):
        self.prefs = prefs
        self.e = STYLES[concession] if isinstance(concession, str) else concession

    def start(self, ctx: Context, rounds: int):
        self.ctx, self.rounds = ctx, rounds
        self.u = {o: self.prefs.utility(Terms(*o), ctx) for o in GRID}
        self.reserve = self.prefs.reservation(ctx)
        self.best = max(self.u.values())

    def target(self, r: int) -> float:
        frac = (r / (self.rounds - 1)) ** (1 / self.e) if self.rounds > 1 else 1.0
        return self.best - (self.best - self.reserve) * frac

    def respond(self, r: int, offer: Terms | None, message: str | None = None):
        """Return (action, terms, message); action is accept, offer or walk."""
        if self.best <= self.reserve:
            return "walk", None, None          # no contract beats walking away
        t = self.target(r)
        if offer is not None and self.prefs.utility(offer, self.ctx) >= t:
            return "accept", offer, None
        ok = [o for o, u in self.u.items() if u >= t]
        if offer is None:
            pick = max(ok, key=self.u.get)
        else:  # the acceptable offer closest to what the other side asked for
            pick = min(ok, key=lambda o: (o[0] - offer.floor) ** 2 + (o[1] - offer.share) ** 2)
        return "offer", Terms(*pick), None


def negotiate(factory, farm, ctx: Context, rounds: int = 10) -> Outcome:
    """Alternating offers, factory first."""
    factory.start(ctx, rounds)
    farm.start(ctx, rounds)
    offer, message, transcript = None, None, []
    for r in range(rounds):
        name, player = ("factory", factory) if r % 2 == 0 else ("farm", farm)
        action, terms, message = player.respond(r, offer, message)
        transcript.append({"round": r, "who": name, "action": action,
                           "floor": terms.floor if terms else None,
                           "share": terms.share if terms else None, "message": message})
        if action == "accept":
            return Outcome(terms, r + 1, transcript)
        if action == "walk":
            return Outcome(None, r + 1, transcript)
        offer = terms
    return Outcome(None, rounds, transcript)


def nash_terms(farm: FarmerPrefs, factory: FactoryPrefs, ctx: Context) -> Terms | None:
    """The 'fair split' benchmark (Nash bargaining solution): the terms that
    maximise the product of both sides' gains over walking away."""
    rf, rc = farm.reservation(ctx), factory.reservation(ctx)
    best, best_val = None, 0.0
    for o in GRID:
        t = Terms(*o)
        gf, gc = farm.utility(t, ctx) - rf, factory.utility(t, ctx) - rc
        if gf > 0 and gc > 0 and gf * gc > best_val:
            best, best_val = t, gf * gc
    return best


class Table:
    """Callable the simulation uses at each planting: runs one negotiation."""

    def __init__(self, factory, farm, rounds: int = 10):
        self.factory, self.farm, self.rounds = factory, farm, rounds

    def __call__(self, ctx: Context) -> Outcome:
        return negotiate(self.factory, self.farm, ctx, self.rounds)


class NashMediator:
    """Skips bargaining and imposes the fair-split terms (a benchmark)."""

    def __init__(self, farm: FarmerPrefs, factory: FactoryPrefs):
        self.farm, self.factory = farm, factory

    def __call__(self, ctx: Context) -> Outcome:
        return Outcome(nash_terms(self.farm, self.factory, ctx), 0)
