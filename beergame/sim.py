"""Farm-to-shelf Beer Game.

Five players in a line, customers at the end:

    farm -> factory -> distributor -> wholesaler -> retailer -> customers

Each week:
  1. Goods that were shipped `ship_delay` weeks ago arrive. The farm instead
     harvests whatever it planted `season_weeks` ago, and old crop spoils.
  2. Each player receives an order: customers order from the retailer, and
     every other player receives the order its downstream neighbour placed
     `order_delay` weeks ago. Under a forward contract, the farm delivers the
     committed amount every week whatever the factory orders.
  3. Each player ships what it can (new order + any backlog) from inventory.
  4. Each player decides what to order from upstream. The farm cannot order:
     once per season it decides how much to plant.
  5. Holding, backlog and spoilage costs are added up.

The farm sells at a spot farmgate price that rises when buyers want more crop
than the farm can supply before its next harvest and falls when there is a
glut. Under a forward contract, the contracted units are paid at a price
agreed when the contract is signed.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from .negotiation import Context as NegotiationContext

STAGES = ["farm", "factory", "distributor", "wholesaler", "retailer"]
FARM, FACTORY, RETAILER = 0, 1, len(STAGES) - 1


@dataclass
class Config:
    weeks: int = 208              # 4 years, so the farm plants 8 times
    mean_demand: float = 100.0    # customer demand per week before any shock
    ship_delay: int = 2           # weeks for goods to travel one link
    order_delay: int = 1          # weeks for an order to reach upstream
    season_weeks: int = 26        # planting -> harvest (2 harvests a year)
    shelf_life: int = 30          # weeks a harvest stays usable
    yield_sd: float = 0.15        # harvest = planted x yield, yield ~ N(1, sd)
    start_inventory_weeks: float = 3.0  # each player starts with 3 weeks of stock
    holding_cost: float = 0.5     # per unit per week (classic Beer Game value)
    farm_holding_cost: float = 0.05  # storing crop in a barn is much cheaper
    land_capacity: float = 1.6    # most a farm can plant, as a multiple of a normal season
    backlog_cost: float = 1.0     # per unit per week (classic Beer Game value)
    spoilage_cost: float = 1.0    # per unit of crop that rots at the farm
    warmup: int = 10              # weeks ignored when computing metrics
    contract: bool = False        # forward contract between farm and factory
    noise_sd: float = 0.08        # week-to-week demand noise, as a share of mean demand
    # Prices (money per unit of crop at the farm gate)
    base_price: float = 1.0       # price when supply and demand are balanced
    production_cost: float = 0.6  # seed, labour, fertiliser per unit planted
    price_elasticity: float = 1.0 # how strongly scarcity moves the price
    price_floor: float = 0.4      # glut: price can't fall below 40% of normal
    price_cap: float = 3.0        # shortage: price can't rise above 3x normal
    price_speed: float = 0.3      # how fast the price moves toward its new level each week
    # How the contract price is set (only used when contract=True)
    #   last_season - last season's average spot price, locked for the season
    #   fixed       - base_price, never changes
    #   floor_share - each week: max(floor_price, base + upside_share x (spot - base))
    #   negotiated  - floor and share bargained each season (see negotiation.py)
    contract_price_rule: str = "last_season"
    farm_spot_discount: float = 0.0   # a smallholder selling alone gets less than spot
    floor_price: float = 0.8
    upside_share: float = 0.5
    # Government-set farmgate price (like cocoa in Cote d'Ivoire and Ghana):
    # every unit is paid farmgate_share x last season's average spot price.
    # Buyers still pay spot; the difference stays with the middleman.
    farmgate_share: float | None = None


@dataclass
class Observation:
    """What a downstream player can see when it decides its order."""
    week: int
    stage: str
    inventory: float
    backlog: float
    incoming_order: float
    supply_line: float            # ordered from upstream but not yet received
    lead_time: int                # order_delay + ship_delay
    order_history: list[float]    # orders this player received, oldest first
    pos_history: list[float] | None  # customer demand, only if shared


@dataclass
class FarmObservation:
    """What the farm can see when it decides how much to plant."""
    week: int
    inventory: float
    backlog: float
    order_history: list[float]
    pos_history: list[float] | None
    season_weeks: int
    shelf_life: int
    contract_per_week: float | None  # set when a forward contract is signed
    contract_price: float | None     # price per unit agreed in the contract
    price_history: list[float]       # price the farmer was offered each week, oldest first
    base_price: float
    production_cost: float


@dataclass
class Result:
    config: Config
    demand: np.ndarray
    orders: np.ndarray        # [stage, week] orders placed upstream (farm: 0)
    inventory: np.ndarray     # [stage, week] end-of-week inventory
    backlog: np.ndarray       # [stage, week] end-of-week backlog
    shipped: np.ndarray       # [stage, week] units shipped downstream
    cost: np.ndarray          # [stage, week]
    plantings: list[tuple[int, float]] = field(default_factory=list)
    harvests: list[tuple[int, float]] = field(default_factory=list)
    spoiled: np.ndarray | None = None  # [week] units rotted at the farm
    price: np.ndarray | None = None    # [week] spot farmgate price
    revenue: np.ndarray | None = None  # [week] money the farm received for crop shipped
    factory_spend: np.ndarray | None = None  # [week] money the factory paid for that crop
    deals: list = field(default_factory=list)  # (week, negotiation Outcome) per season
    production_spend: np.ndarray | None = None  # [week] money the farm spent planting


def make_demand(cfg: Config, kind: str, rng: np.random.Generator) -> np.ndarray:
    """Customer demand per week.

    step   - demand jumps 20% at week 30 and stays there (a new market)
    spike  - panic buying: +60% for 8 weeks, then back to normal
    noisy  - no shock, just week-to-week noise
    """
    base = np.full(cfg.weeks, cfg.mean_demand)
    if kind == "step":
        base[30:] *= 1.2
    elif kind == "spike":
        base[30:38] *= 1.6
    elif kind != "noisy":
        raise ValueError(f"unknown demand kind: {kind}")
    noise = rng.normal(0, cfg.noise_sd * cfg.mean_demand, cfg.weeks)
    return np.maximum(0, np.round(base + noise))


def contract_quantity(pos_history: list[float], window: int = 8) -> float:
    """Weekly quantity the factory commits to buy for the coming season.

    The factory bases it on real customer demand, which it can see because a
    contract only makes sense if the two sides share information.
    """
    recent = pos_history[-window:]
    return float(np.mean(recent))


def contract_price(price_history: list[float], base_price: float, window: int = 26) -> float:
    """Price per unit locked in at signing: last season's average spot price."""
    if not price_history:
        return base_price
    return float(np.mean(price_history[-window:]))


def spot_price_target(cfg: Config, recent_orders: list[float], backlog: float,
                      farm_stock: float, weeks_to_harvest: int) -> float:
    """Price the market would settle at this week.

    Demand pressure: what buyers ordered recently, plus a share of the backlog.
    Supply: the crop on hand, spread over the weeks until the next harvest.
    More pressure than supply pushes the price up; a glut pushes it down.
    """
    pressure = float(np.mean(recent_orders)) + backlog / 4
    supply = farm_stock / max(1, weeks_to_harvest)
    ratio = pressure / max(supply, 1e-6)
    return cfg.base_price * float(np.clip(ratio ** cfg.price_elasticity,
                                          cfg.price_floor, cfg.price_cap))


def simulate(cfg: Config, demand: np.ndarray, agents: dict, farm_agent,
             share_pos: bool = False, seed: int = 0, negotiation=None) -> Result:
    """Run one game.

    agents      - {"factory": agent, ..., "retailer": agent}; each has .order(obs)
    farm_agent  - has .plant(farm_obs) returning units to plant
    share_pos   - if True every player sees real customer demand (POS data)
    negotiation - for contract_price_rule="negotiated": called at each planting
                  with a negotiation Context, returns an Outcome (see negotiation.py)
    """
    if cfg.contract and cfg.contract_price_rule == "negotiated" and negotiation is None:
        raise ValueError("contract_price_rule='negotiated' needs a negotiation table")
    rng = np.random.default_rng(seed)
    n, T = len(STAGES), cfg.weeks
    D = cfg.mean_demand
    lead = cfg.order_delay + cfg.ship_delay

    inventory = np.full(n, cfg.start_inventory_weeks * D)
    backlog = np.zeros(n)
    # Goods travelling to stage i (index 1..n-1); start full, as in steady state.
    in_transit = [deque([D] * cfg.ship_delay) for _ in range(n)]
    # Orders travelling from stage i+1 to stage i.
    orders_in_transit = [deque([D] * cfg.order_delay) for _ in range(n)]

    # The farm stores crop as batches so old crop can spoil: [harvest_week, qty]
    batches: list[list[float]] = [[0, D * cfg.season_weeks]]
    inventory[FARM] = D * cfg.season_weeks
    scheduled_harvest: dict[int, float] = {}
    contract_per_week: float | None = None
    agreed_price: float | None = None
    c_anchor = c_floor = c_share = 0.0    # contract price terms for the current season
    price = cfg.base_price
    prices: list[float] = []          # spot price
    farmer_prices: list[float] = []   # price the farmer is offered (spot or farmgate)
    farmgate = (cfg.farmgate_share * cfg.base_price
                if cfg.farmgate_share is not None else None)

    res = Result(
        config=cfg, demand=demand,
        orders=np.zeros((n, T)), inventory=np.zeros((n, T)),
        backlog=np.zeros((n, T)), shipped=np.zeros((n, T)),
        cost=np.zeros((n, T)), spoiled=np.zeros(T),
        price=np.zeros(T), revenue=np.zeros(T), production_spend=np.zeros(T),
        factory_spend=np.zeros(T),
    )
    received_orders: list[list[float]] = [[] for _ in range(n)]
    pos: list[float] = []

    for t in range(T):
        # 1. Arrivals --------------------------------------------------------
        for i in range(1, n):
            inventory[i] += in_transit[i].popleft()

        if t in scheduled_harvest:
            qty = scheduled_harvest.pop(t)
            batches.append([t, qty])
            res.harvests.append((t, qty))

        rotted = 0.0
        for b in batches:
            if t - b[0] >= cfg.shelf_life and b[1] > 0:
                rotted += b[1]
                b[1] = 0
        batches[:] = [b for b in batches if b[1] > 0]
        res.spoiled[t] = rotted
        inventory[FARM] = sum(b[1] for b in batches)

        # 2. Orders arrive -----------------------------------------------------
        pos.append(float(demand[t]))
        incoming = np.zeros(n)
        incoming[RETAILER] = demand[t]
        for i in range(n - 1):
            incoming[i] = orders_in_transit[i].popleft()
        for i in range(n):
            received_orders[i].append(float(incoming[i]))

        # 2b. Farmgate spot price moves toward what the market will bear --------
        weeks_to_harvest = cfg.season_weeks - (t % cfg.season_weeks)
        target = spot_price_target(cfg, received_orders[FARM][-4:], backlog[FARM],
                                   inventory[FARM], weeks_to_harvest)
        price += cfg.price_speed * (target - price)
        prices.append(price)
        farmer_prices.append(farmgate if farmgate is not None else price)
        res.price[t] = price

        # 3. Ship what you can -------------------------------------------------
        for i in range(n):
            owed = backlog[i] + incoming[i]
            if i == FARM:
                ship = _take_from_batches(batches, owed, oldest_first=True)
                inventory[FARM] = sum(b[1] for b in batches)
                farm_spot = price * (1 - cfg.farm_spot_discount)  # what the farm gets selling alone
                if contract_per_week is not None:
                    # Contracted units are paid at the contract price, the rest at spot.
                    unit = max(c_floor, c_anchor + c_share * (price - c_anchor))
                    at_contract = min(ship, contract_per_week)
                    extra = ship - at_contract
                    res.revenue[t] = at_contract * unit + extra * farm_spot
                    res.factory_spend[t] = at_contract * unit + extra * price
                elif farmgate is not None:
                    # Farmer gets the set farmgate price; the buyer pays spot.
                    res.revenue[t] = ship * farmgate
                    res.factory_spend[t] = ship * price
                else:
                    res.revenue[t] = ship * farm_spot
                    res.factory_spend[t] = ship * price
            else:
                ship = min(inventory[i], owed)
                inventory[i] -= ship
            backlog[i] = owed - ship
            res.shipped[i, t] = ship
            if i < RETAILER:
                in_transit[i + 1].append(ship)

        # 4. Decisions -----------------------------------------------------------
        shared = list(pos) if share_pos else None
        for i in range(1, n):
            supply_line = (sum(orders_in_transit[i - 1]) + backlog[i - 1]
                           + sum(in_transit[i]))
            obs = Observation(
                week=t, stage=STAGES[i], inventory=float(inventory[i]),
                backlog=float(backlog[i]), incoming_order=float(incoming[i]),
                supply_line=float(supply_line), lead_time=lead,
                order_history=list(received_orders[i]), pos_history=shared,
            )
            q = max(0.0, float(agents[STAGES[i]].order(obs)))
            if i == FACTORY and contract_per_week is not None:
                # Under contract the farm delivers the committed amount every
                # week; the factory can buy extra on top, never less.
                q = max(q, contract_per_week)
            res.orders[i, t] = q
            orders_in_transit[i - 1].append(q)

        if t % cfg.season_weeks == 0:
            if cfg.contract:
                # Every rule becomes: unit price = max(floor, anchor + share x (spot - anchor))
                contract_per_week = contract_quantity(pos)
                rule = cfg.contract_price_rule
                if rule in ("last_season", "fixed"):
                    agreed_price = (contract_price(prices, cfg.base_price)
                                    if rule == "last_season" else cfg.base_price)
                    c_anchor, c_floor, c_share = agreed_price, agreed_price, 0.0
                elif rule == "floor_share":
                    c_anchor, c_floor, c_share = cfg.base_price, cfg.floor_price, cfg.upside_share
                    agreed_price = cfg.floor_price   # the guaranteed minimum
                elif rule == "negotiated":
                    ctx = NegotiationContext(week=t, quantity=contract_per_week,
                                             price_history=list(prices),
                                             base_price=cfg.base_price)
                    outcome = negotiation(ctx)
                    res.deals.append((t, outcome))
                    if outcome.terms is None:
                        contract_per_week = agreed_price = None   # no deal: spot this season
                    else:
                        c_anchor, c_floor, c_share = (cfg.base_price, outcome.terms.floor,
                                                      outcome.terms.share)
                        agreed_price = outcome.terms.floor
                else:
                    raise ValueError(f"unknown contract_price_rule: {rule}")
            if cfg.farmgate_share is not None and t > 0:
                farmgate = cfg.farmgate_share * contract_price(prices, cfg.base_price)
            fobs = FarmObservation(
                week=t, inventory=float(inventory[FARM]), backlog=float(backlog[FARM]),
                order_history=list(received_orders[FARM]), pos_history=shared,
                season_weeks=cfg.season_weeks, shelf_life=cfg.shelf_life,
                contract_per_week=contract_per_week, contract_price=agreed_price,
                price_history=list(farmer_prices), base_price=cfg.base_price,
                production_cost=cfg.production_cost,
            )
            land = cfg.land_capacity * D * cfg.season_weeks
            planted = min(land, max(0.0, float(farm_agent.plant(fobs))))
            crop_yield = max(0.0, rng.normal(1.0, cfg.yield_sd))
            scheduled_harvest[t + cfg.season_weeks] = planted * crop_yield
            res.plantings.append((t, planted))
            res.production_spend[t] = planted * cfg.production_cost

        # 5. Costs ---------------------------------------------------------------
        for i in range(n):
            hold = cfg.farm_holding_cost if i == FARM else cfg.holding_cost
            res.cost[i, t] = hold * inventory[i] + cfg.backlog_cost * backlog[i]
        res.cost[FARM, t] += cfg.spoilage_cost * rotted
        res.inventory[:, t] = inventory
        res.backlog[:, t] = backlog

    return res


def _take_from_batches(batches: list[list[float]], qty: float, oldest_first: bool) -> float:
    """Remove up to qty units from the farm's batches; return what was taken."""
    taken = 0.0
    order = batches if oldest_first else reversed(batches)
    for b in order:
        if taken >= qty:
            break
        use = min(b[1], qty - taken)
        b[1] -= use
        taken += use
    batches[:] = [b for b in batches if b[1] > 0]
    return taken
