"""Put Claude in one seat of the chain and compare it with the rule-based player.

    python run_llm_trial.py --stage retailer --weeks 52
    python run_llm_trial.py --stage wholesaler --share-pos

Costs one API call per week (52 calls for the default game). Needs the
`anthropic` package and an API key or `ant auth login`.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np

from beergame.agents import InfoSharingAgent, InfoSharingFarmer, NaiveAgent, NaiveFarmer
from beergame.llm_agent import ClaudeAgent
from beergame.metrics import summarize
from beergame.sim import STAGES, Config, make_demand, simulate


def build(stage_for_llm: str | None, share_pos: bool):
    agent_cls = InfoSharingAgent if share_pos else NaiveAgent
    agents = {s: agent_cls() for s in STAGES[1:]}
    if stage_for_llm:
        agents[stage_for_llm] = ClaudeAgent(stage_for_llm)
    farm = InfoSharingFarmer() if share_pos else NaiveFarmer()
    return agents, farm


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="retailer", choices=STAGES[1:])
    ap.add_argument("--weeks", type=int, default=52)
    ap.add_argument("--demand", default="spike", choices=["noisy", "step", "spike"])
    ap.add_argument("--share-pos", action="store_true", help="everyone sees customer demand")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    cfg = Config(weeks=args.weeks)
    demand = make_demand(cfg, args.demand, np.random.default_rng(args.seed))

    results = {}
    for label, seat in [("rule-based", None), ("claude", args.stage)]:
        agents, farm = build(seat, args.share_pos)
        r = simulate(cfg, demand, agents, farm, share_pos=args.share_pos, seed=args.seed)
        results[label] = summarize(r)
        if seat:
            out = Path("results")
            out.mkdir(exist_ok=True)
            log_path = out / f"claude_{seat}_log.json"
            log_path.write_text(json.dumps(agents[seat].log, indent=2))
            print(f"Claude's weekly decisions saved to {log_path}")

    keys = [f"bullwhip_{args.stage}", "bullwhip_factory", "empty_shelf_weeks_pct", "chain_cost"]
    print(f"\n{'metric':28s} {'rule-based':>12s} {'claude':>12s}")
    for k in keys:
        print(f"{k:28s} {results['rule-based'][k]:12.2f} {results['claude'][k]:12.2f}")


if __name__ == "__main__":
    main()
