"""Who wins when the contract is negotiated? Compare bargaining setups.

    python compare_negotiation.py              # 30 seeds per combination
    python compare_negotiation.py --seeds 100

Rule-based negotiators only (no API calls). Outputs go to results/:
negotiation.csv, negotiation.md and negotiation.png.
"""

from __future__ import annotations

import argparse

import pandas as pd

from compare_contracts import OUT, chart, run
from beergame.scenarios import (CONTRACT_DESIGNS, DESIGN_LABELS, NEGOTIATION_LABELS,
                                NEGOTIATION_SETUPS)

# Two fixed designs from compare_contracts.py, as reference points.
SETUPS = {
    "no_contract": CONTRACT_DESIGNS["no_contract"],
    "floor_plus_share": CONTRACT_DESIGNS["floor_plus_share"],
    **NEGOTIATION_SETUPS,
}
LABELS = {
    "no_contract": DESIGN_LABELS["no_contract"],
    "floor_plus_share": "Fixed rule: floor + half the upside",
    **NEGOTIATION_LABELS,
}
COLUMNS = {
    "deal_rate_pct": "Seasons with a deal (%)",
    "avg_rounds": "Turns to agree",
    "farmer_avg_price": "Farmer's average price",
    "farm_income": "Farmer income (4 yrs)",
    "worst_season_income_x": "Worst season (× normal profit)",
    "factory_crop_spend": "Buyer's crop spend",
    "empty_shelf_weeks_pct": "Empty shelves (% of weeks)",
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    df = run(args.seeds, SETUPS)
    summary = df.drop(columns="seed").groupby(["demand", "design"], sort=False).median()
    table = summary[list(COLUMNS)].rename(columns=COLUMNS)
    table.to_csv(OUT / "negotiation.csv")
    (OUT / "negotiation.md").write_text(
        f"Median over {args.seeds} seeds per row.\n\n" + table.round(2).to_markdown() + "\n")
    chart(summary, list(SETUPS), LABELS, OUT / "negotiation.png",
          "Negotiated contracts: who wins depends on bargaining power (median of seeds)")

    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:,.2f}".format):
        print(table)
    print(f"\nSaved results to {OUT.resolve()}/")


if __name__ == "__main__":
    main()
