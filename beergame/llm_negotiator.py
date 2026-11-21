"""A negotiator that asks Claude what to do at each turn.

It plays the same protocol as TimeDependentNegotiator (start / respond), so
it can sit on either side of the table, against a rule-based player or
against another Claude:

    from beergame.llm_negotiator import ClaudeNegotiator
    farm = ClaudeNegotiator("farm", SMALLHOLDER)

Claude sees its own private information and, for any offer, the numbers its
side would get, computed by the same code the rule-based players use. It
never sees the other side's private information. It can send a short message
with each move, and the other side sees it.
"""

from __future__ import annotations

import json

import anthropic
import numpy as np

from .negotiation import FactoryPrefs, FarmerPrefs, Terms, price_samples, unit_price

RULES = """You are negotiating a crop supply contract for one 26-week growing season,
on behalf of the {side}. The other side is the {other}.

The contract: the farm delivers {quantity:.0f} units every week and the factory takes them.
The price per unit is set by two numbers you negotiate:
    price = max(floor, 1.0 + share x (spot price - 1.0))
floor is between 0.40 and 1.40 (steps of 0.05). share is between 0.0 and 1.0 (steps of 0.1).
share = 0 means a fixed price of max(floor, 1.0). share = 1 means the spot price, never below floor.

Turns alternate, factory first, {rounds} turns in total. On your turn you either
accept the offer on the table, make a counter-offer, or walk away. If time runs out
or someone walks away, there is no contract this season.

{private}

Reply with JSON only."""

FARM_PRIVATE = """Private to you (the factory cannot see this):
- Growing costs you {cost:.2f} per unit.
- Without a contract you sell on the spot market and get {discount:.0%} below the spot price.
- A bad season hurts you badly; you care about the bad case, not just the average."""

FACTORY_PRIVATE = """Private to you (the farm cannot see this):
- A guaranteed supply is worth about {security:.2f} per unit to you (fewer shortages).
- Without a contract you buy at the spot price and are exposed to price spikes."""

MOVE_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["accept", "offer", "walk"]},
        "floor": {"type": "number", "description": "Your offer's floor (ignored unless action is offer)"},
        "share": {"type": "number", "description": "Your offer's share (ignored unless action is offer)"},
        "message": {"type": "string", "description": "One or two sentences to the other side"},
    },
    "required": ["action", "floor", "share", "message"],
    "additionalProperties": False,
}


class ClaudeNegotiator:
    def __init__(self, side: str, prefs: FarmerPrefs | FactoryPrefs,
                 model: str = "claude-opus-5-5", client: anthropic.Anthropic | None = None):
        assert side in ("farm", "factory")
        self.side, self.prefs, self.model = side, prefs, model
        self.client = client or anthropic.Anthropic()
        self.log: list[dict] = []

    def start(self, ctx, rounds: int):
        self.ctx, self.rounds = ctx, rounds
        if self.side == "farm":
            private = FARM_PRIVATE.format(cost=self.prefs.production_cost,
                                          discount=self.prefs.spot_discount)
        else:
            private = FACTORY_PRIVATE.format(security=self.prefs.security_value)
        self.system = RULES.format(side=self.side, other="factory" if self.side == "farm" else "farm",
                                   quantity=ctx.quantity, rounds=rounds, private=private)
        self.history: list[str] = []

    def _per_unit(self, terms: Terms | None) -> tuple[float, float]:
        """Average and bad-case (1 in 10) result per unit for this side,
        replaying the last year of spot prices."""
        p = price_samples(self.ctx)
        if self.side == "farm":
            paid = unit_price(terms, p) if terms else p * (1 - self.prefs.spot_discount)
            x = paid - self.prefs.production_cost
            return float(x.mean()), float(np.quantile(x, 0.1))
        paid = unit_price(terms, p) if terms else p
        x = p + (self.prefs.security_value if terms else 0.0) - paid
        return float(x.mean()), float(np.quantile(x, 0.1))

    def _prompt(self, r: int, offer: Terms | None, message: str | None) -> str:
        p = price_samples(self.ctx)
        label = "profit" if self.side == "farm" else "saving vs buying at spot"
        lines = [f"Turn {r + 1} of {self.rounds}.",
                 f"Spot price over the last year: average {p.mean():.2f}, "
                 f"low {p.min():.2f}, high {p.max():.2f}, latest {p[-1]:.2f}."]
        avg, bad = self._per_unit(None)
        lines.append(f"With no contract, your {label} per unit: average {avg:.2f}, bad case {bad:.2f}.")
        if self.history:
            lines.append("So far:\n" + "\n".join(self.history))
        if offer is None:
            lines.append("No offer on the table yet. Make the opening offer.")
        else:
            avg, bad = self._per_unit(offer)
            lines.append(f"Offer on the table: floor {offer.floor:.2f}, share {offer.share:.1f}. "
                         f"Your {label} per unit under it: average {avg:.2f}, bad case {bad:.2f}.")
            if message:
                lines.append(f'Their message: "{message}"')
        lines.append("What do you do?")
        return "\n".join(lines)

    def respond(self, r: int, offer: Terms | None, message: str | None = None):
        if offer is not None:
            self.history.append(f"- They offered floor {offer.floor:.2f}, share {offer.share:.1f}"
                                + (f' ("{message}")' if message else ""))
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=4000,
            system=self.system,
            messages=[{"role": "user", "content": self._prompt(r, offer, message)}],
            output_config={"effort": "medium",
                           "format": {"type": "json_schema", "schema": MOVE_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            move = {"action": "walk", "floor": 0, "share": 0, "message": ""}
        else:
            move = json.loads(next(b.text for b in response.content if b.type == "text"))
        self.log.append({"week": self.ctx.week, "turn": r, **move})

        action = move["action"]
        if action == "accept" and offer is None:
            action = "offer"          # nothing to accept on the opening turn
        if action == "accept":
            return "accept", offer, move["message"]
        if action == "walk":
            return "walk", None, move["message"]
        terms = Terms(floor=round(float(np.clip(move["floor"], 0.40, 1.40)) * 20) / 20,
                      share=round(float(np.clip(move["share"], 0.0, 1.0)), 1))
        self.history.append(f"- You offered floor {terms.floor:.2f}, share {terms.share:.1f}")
        return "offer", terms, move["message"]
