"""Glue between the web demo and the model. Runs inside the browser (Pyodide).

The demo calls `run_json(...)` with the visitor's choices and gets back the
weekly series and summary numbers as JSON. It uses the same `beergame`
package as the experiments, so the demo and the report can't disagree.
"""

import json
from dataclasses import replace

import numpy as np

from beergame.metrics import season_income, summarize
from beergame.scenarios import (CONTRACT_DESIGNS, NEGOTIATION_SETUPS, SCENARIOS)
from beergame.sim import Config, make_demand, simulate

# Every setup the demo offers, in menu order.
SETUPS = {
    "baseline": ("Play alone (no data sharing)", SCENARIOS["baseline"]),
    "info_sharing": ("Share demand data, spot price", SCENARIOS["info_sharing"]),
    "contract_last_season": ("Contract: last season's price", CONTRACT_DESIGNS["last_season_price"]),
    "contract_fixed": ("Contract: fixed price", CONTRACT_DESIGNS["fixed_price"]),
    "contract_floor_share": ("Contract: floor + share of upside", CONTRACT_DESIGNS["floor_plus_share"]),
    "government_farmgate": ("Government-set farmgate price", CONTRACT_DESIGNS["government_farmgate"]),
    "neg_smallholder": ("Negotiated: smallholder, concedes fast", NEGOTIATION_SETUPS["smallholder"]),
    "neg_holds_out": ("Negotiated: smallholder, holds out", NEGOTIATION_SETUPS["smallholder_holds_out"]),
    "neg_cooperative": ("Negotiated: cooperative", NEGOTIATION_SETUPS["cooperative"]),
    "neg_fair_split": ("Negotiated: fair split (Nash)", NEGOTIATION_SETUPS["fair_split_smallholder"]),
}


def setups_json() -> str:
    return json.dumps([{"id": k, "label": v[0]} for k, v in SETUPS.items()])


def _run(setup: str, demand_kind: str, seed: int, price_response: float,
         yield_sd: float, floor: float, upside: float, farmgate_share: float):
    s = SETUPS[setup][1](Config())
    s["cfg"] = replace(s["cfg"], yield_sd=yield_sd, floor_price=floor,
                       upside_share=upside,
                       farmgate_share=farmgate_share if s["cfg"].farmgate_share is not None else None)
    s["farm_agent"].price_response = price_response
    demand = make_demand(s["cfg"], demand_kind, np.random.default_rng(seed))
    r = simulate(s["cfg"], demand, s["agents"], s["farm_agent"], s["share_pos"],
                 seed=seed, negotiation=s.get("negotiation"))
    return r, summarize(r)


def run_json(setup: str, demand_kind: str, seed: int, price_response: float,
             yield_sd: float, floor: float, upside: float, farmgate_share: float,
             many_seeds: int = 0) -> str:
    r, m = _run(setup, demand_kind, seed, price_response, yield_sd, floor, upside, farmgate_share)
    out = {
        "demand": r.demand.round(1).tolist(),
        "factory_orders": r.orders[1].round(1).tolist(),
        "price": r.price.round(3).tolist(),
        "season_income": season_income(r).round(1).tolist(),
        "plantings": [[int(t), round(float(q), 1)] for t, q in r.plantings],
        "deals": [{"week": int(w), "floor": o.terms.floor if o.terms else None,
                   "share": o.terms.share if o.terms else None, "turns": int(o.rounds)}
                  for w, o in r.deals],
        "metrics": {k: (None if v != v else round(float(v), 3)) for k, v in m.items()},  # NaN -> None
    }
    if many_seeds:
        keys = ["bullwhip_factory", "farm_income", "worst_season_income_x",
                "empty_shelf_weeks_pct", "factory_crop_spend", "farmer_avg_price"]
        runs = [_run(setup, demand_kind, s, price_response, yield_sd, floor, upside,
                     farmgate_share)[1] for s in range(many_seeds)]
        out["median"] = {k: round(float(np.median([x[k] for x in runs])), 3) for k in keys}
    return json.dumps(out)
