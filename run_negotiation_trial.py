"""Let Claude negotiate the seasonal contract, and compare with rule-based bargaining.

    python run_negotiation_trial.py                           # Claude farm vs tough factory
    python run_negotiation_trial.py --farmer cooperative
    python run_negotiation_trial.py --claude both             # Claude vs Claude
    python run_negotiation_trial.py --claude factory

Each season is one negotiation of up to 10 turns, so a 2-year game (4 seasons)
makes at most 40 API calls per Claude player. Needs the `anthropic` package
and an API key or `ant auth login`. The full transcript, with messages, is
saved to results/negotiation_<setup>.json.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from beergame.llm_negotiator import ClaudeNegotiator
from beergame.metrics import summarize
from beergame.negotiation import Table, TimeDependentNegotiator
from beergame.scenarios import COOPERATIVE, FACTORY, SMALLHOLDER, negotiated
from beergame.sim import Config, make_demand, simulate

FARMERS = {"smallholder": SMALLHOLDER, "cooperative": COOPERATIVE}


def play(cfg, demand, farmer_prefs, table, seed):
    s = negotiated(farmer_prefs, "tough")(cfg)
    return simulate(s["cfg"], demand, s["agents"], s["farm_agent"], s["share_pos"],
                    seed=seed, negotiation=table)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--farmer", default="smallholder", choices=list(FARMERS))
    ap.add_argument("--claude", default="farm", choices=["farm", "factory", "both"])
    ap.add_argument("--farm-style", default="eager", choices=["tough", "balanced", "eager"],
                    help="style of the rule-based farm it is compared with")
    ap.add_argument("--weeks", type=int, default=104)
    ap.add_argument("--demand", default="noisy", choices=["noisy", "step", "spike"])
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    cfg = Config(weeks=args.weeks)
    demand = make_demand(cfg, args.demand, np.random.default_rng(args.seed))
    farmer = FARMERS[args.farmer]

    rule_table = Table(TimeDependentNegotiator(FACTORY, "tough"),
                       TimeDependentNegotiator(farmer, args.farm_style))
    factory = (ClaudeNegotiator("factory", FACTORY) if args.claude in ("factory", "both")
               else TimeDependentNegotiator(FACTORY, "tough"))
    farm = (ClaudeNegotiator("farm", farmer) if args.claude in ("farm", "both")
            else TimeDependentNegotiator(farmer, args.farm_style))
    claude_table = Table(factory, farm)

    results = {}
    for label, table in [("rule-based", rule_table), ("with Claude", claude_table)]:
        r = play(cfg, demand, farmer, table, args.seed)
        results[label] = summarize(r)
        print(f"\n{label}: season deals (week: floor, share, turns)")
        for week, o in r.deals:
            print(f"  week {week:3d}: " + (f"floor {o.terms.floor:.2f}, share {o.terms.share:.1f}"
                                         if o.terms else "no deal") + f", {o.rounds} turns")
        if label == "with Claude":
            out = Path("results")
            out.mkdir(exist_ok=True)
            path = out / f"negotiation_{args.farmer}_claude_{args.claude}.json"
            path.write_text(json.dumps([{"week": w, "transcript": o.transcript} for w, o in r.deals],
                                       indent=2))
            print(f"  transcript saved to {path}")

    keys = ["deal_rate_pct", "farmer_avg_price", "farm_income", "worst_season_income_x",
            "factory_crop_spend"]
    print(f"\n{'metric':24s} {'rule-based':>12s} {'with Claude':>12s}")
    for k in keys:
        print(f"{k:24s} {results['rule-based'][k]:12.2f} {results['with Claude'][k]:12.2f}")


if __name__ == "__main__":
    main()
