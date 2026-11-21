"""Numbers that tell you whether a strategy worked."""

from __future__ import annotations

import numpy as np

from .sim import FARM, RETAILER, STAGES, Result


def bullwhip(res: Result) -> dict[str, float]:
    """Variance of the orders a player places / variance of customer demand.

    1.0 means the player passes demand on unchanged. Above 1 means it
    amplifies swings. Computed for the four players that place orders.
    """
    w = res.config.warmup
    dvar = np.var(res.demand[w:])
    return {STAGES[i]: float(np.var(res.orders[i, w:]) / dvar)
            for i in range(1, len(STAGES))}


def planting_swing(res: Result) -> float:
    """Coefficient of variation of the farm's plantings (std / mean).

    Compare it with demand: customer demand varies by roughly 10-20% across
    seasons in these scenarios, so a swing far above that is the bullwhip
    reaching the farmer.
    """
    planted = np.array([q for _, q in res.plantings[1:]])  # skip the first, set at start
    return float(np.std(planted) / np.mean(planted)) if planted.mean() > 0 else 0.0


def season_income(res: Result) -> np.ndarray:
    """Farmer's profit for each season after the first.

    Crop sold in season k was planted at the start of season k-1, so its
    growing cost is matched with that season's sales.
    """
    S = res.config.season_weeks
    n_seasons = res.config.weeks // S
    revenue = [res.revenue[k * S:(k + 1) * S].sum() for k in range(n_seasons)]
    spend = [res.production_spend[k * S:(k + 1) * S].sum() for k in range(n_seasons)]
    return np.array([revenue[k] - spend[k - 1] for k in range(1, n_seasons)])


def money(res: Result) -> dict[str, float]:
    w = res.config.warmup
    income = season_income(res)
    normal = res.config.mean_demand * res.config.season_weeks * (
        res.config.base_price - res.config.production_cost)
    p = res.price[w:]
    sold = res.shipped[FARM, w:].sum()
    deals = [o for _, o in res.deals]
    return {
        "farm_income": float(income.sum()),
        "farmer_avg_price": float(res.revenue[w:].sum() / sold) if sold else 0.0,
        # Only for negotiated contracts: share of seasons that ended in a deal.
        "deal_rate_pct": float(100 * np.mean([o.terms is not None for o in deals])) if deals else float("nan"),
        "avg_rounds": float(np.mean([o.rounds for o in deals])) if deals else float("nan"),
        # 1.0 = a season earning what a balanced market would pay.
        "worst_season_income_x": float(income.min() / normal),
        "income_swing": float(np.std(income) / abs(np.mean(income))) if income.mean() else 0.0,
        "avg_price": float(p.mean()),
        "price_swing": float(p.std() / p.mean()),
        "factory_crop_spend": float(res.factory_spend[w:].sum()),
        # What the buyer paid minus what the farmer got (nonzero only with a set farmgate price).
        "middleman_margin": float((res.factory_spend[w:] - res.revenue[w:]).sum()),
        # Share of the buyer's money that reaches the farmer.
        "farmer_share_pct": float(100 * res.revenue[w:].sum() / res.factory_spend[w:].sum())
        if res.factory_spend[w:].sum() else 0.0,
    }


def summarize(res: Result) -> dict[str, float]:
    w = res.config.warmup
    harvested = sum(q for _, q in res.harvests)
    out = {f"bullwhip_{k}": v for k, v in bullwhip(res).items()}
    out.update({
        "planting_swing": planting_swing(res),
        "farm_spoiled_pct": float(100 * res.spoiled.sum() / harvested) if harvested else 0.0,
        "farm_cost": float(res.cost[FARM, w:].sum()),
        "chain_cost": float(res.cost[:, w:].sum()),
        "empty_shelf_weeks_pct": float(100 * np.mean(res.backlog[RETAILER, w:] > 0)),
        "factory_cost": float(res.cost[1, w:].sum()),
        # For demand shocks, variance ratios get huge; these are easier to read.
        "peak_factory_order_x": float(res.orders[1, w:].max() / res.demand[w:].max()),
        "farm_short_weeks_pct": float(100 * np.mean(res.backlog[FARM, w:] > 0)),
    })
    out.update(money(res))
    return out
