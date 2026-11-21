"""Rule-based players.

Every downstream player has one method, `order(obs) -> float`.
The farm has one method, `plant(farm_obs) -> float`.
Write your own agent with the same method and drop it into the game.
"""

from __future__ import annotations

import numpy as np

from .sim import FarmObservation, Observation


class NaiveAgent:
    """How people actually play the Beer Game (Sterman's anchor-and-adjust rule).

    - Forecasts demand from the orders *it* receives, not real customer demand.
    - Closes the gap to a fixed inventory target slowly (alpha).
    - Mostly forgets what it already ordered (beta < 1), so it re-orders the
      same shortage several times. This is the main cause of the bullwhip.

    Default values are close to the averages Sterman (1989) estimated from
    people playing the game.
    """

    def __init__(self, alpha: float = 0.26, beta: float = 0.34, theta: float = 0.36,
                 target_inventory: float = 200.0):
        self.alpha, self.beta, self.theta = alpha, beta, theta
        self.target_inventory = target_inventory   # about 2 weeks of normal demand
        self.forecast: float | None = None

    def _signal(self, obs: Observation) -> float:
        return obs.incoming_order

    def order(self, obs: Observation) -> float:
        x = self._signal(obs)
        self.forecast = x if self.forecast is None else (
            self.theta * x + (1 - self.theta) * self.forecast)
        net_inventory = obs.inventory - obs.backlog
        inventory_gap = self.target_inventory - net_inventory
        supply_gap = self.forecast * obs.lead_time - obs.supply_line
        return self.forecast + self.alpha * (inventory_gap + self.beta * supply_gap)


class InfoSharingAgent(NaiveAgent):
    """Same rule, but the player sees real customer demand (shared POS data)
    and fully counts what it already has on order (beta = 1)."""

    def __init__(self, **kw):
        kw.setdefault("beta", 1.0)
        super().__init__(**kw)

    def _signal(self, obs: Observation) -> float:
        if obs.pos_history:
            return obs.pos_history[-1]
        return obs.incoming_order


# --- Farm ----------------------------------------------------------------------

class NaiveFarmer:
    """Plants for what buyers ordered recently, and plants more when prices are high.

    It looks at the last `window` weeks of factory orders, assumes that rate
    holds for the whole next season, and plants a buffer for bad harvests.
    It then scales up or down with the recent price: a high price says "the
    market wants more", even though the harvest only arrives months later.
    Both habits feed the 'hog cycle'.
    """

    def __init__(self, window: int = 8, buffer: float = 0.10, price_response: float = 0.5):
        self.window, self.buffer = window, buffer
        self.price_response = price_response   # 0 = ignores price

    def expected_weekly(self, obs: FarmObservation) -> float:
        return float(np.mean(obs.order_history[-self.window:]))

    def price_factor(self, obs: FarmObservation) -> float:
        if not obs.price_history:
            return 1.0
        recent = float(np.mean(obs.price_history[-self.window:]))
        return float(np.clip((recent / obs.base_price) ** self.price_response, 0.5, 2.0))

    def plant(self, obs: FarmObservation) -> float:
        weekly = self.expected_weekly(obs) * self.price_factor(obs)
        need = weekly * obs.season_weeks * (1 + self.buffer)
        # Crop on hand now that will be left over when the new harvest comes in.
        # It only counts if it can still be sold before it rots.
        leftover = obs.inventory - obs.backlog - weekly * obs.season_weeks
        usable_weeks = max(0, obs.shelf_life - obs.season_weeks)
        leftover = min(max(0.0, leftover), weekly * usable_weeks)
        return max(0.0, need - leftover)


class InfoSharingFarmer(NaiveFarmer):
    """Plants for real customer demand instead of the factory's orders.
    It still reacts to the spot price, because its income depends on it."""

    def expected_weekly(self, obs: FarmObservation) -> float:
        if obs.pos_history:
            return float(np.mean(obs.pos_history[-self.window:]))
        return super().expected_weekly(obs)


class ContractFarmer(InfoSharingFarmer):
    """Plants exactly what the factory committed to buy, plus a yield buffer.
    The price is already agreed, so the spot price doesn't change its plan."""

    def expected_weekly(self, obs: FarmObservation) -> float:
        if obs.contract_per_week is not None:
            return obs.contract_per_week
        return super().expected_weekly(obs)

    def price_factor(self, obs: FarmObservation) -> float:
        if obs.contract_per_week is not None:
            return 1.0
        return super().price_factor(obs)
