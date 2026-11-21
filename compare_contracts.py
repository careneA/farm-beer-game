"""Compare ways of setting the price the farmer is paid.

    python compare_contracts.py              # 30 seeds per combination
    python compare_contracts.py --seeds 100

All designs share demand information, so only the payment rule changes.
Outputs go to results/: contract_designs.csv, contract_designs.md and
contract_designs.png.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from beergame.metrics import summarize
from beergame.scenarios import CONTRACT_DESIGNS, DESIGN_LABELS
from beergame.sim import Config, make_demand, simulate
from run_experiments import AXIS, GRID, INK, INK2, MUTED, SURFACE

DEMANDS = ["noisy", "step", "spike"]
OUT = Path("results")
# Demand patterns get their own validated colors so they are never confused
# with the strategy colors in the other charts.
DEMAND_COLORS = {"noisy": "#6250d6", "step": "#eda100"}
DEMAND_LABELS = {"noisy": "Noisy demand", "step": "Demand rises 20% and stays"}

COLUMNS = {
    "farm_income": "Farmer income (4 yrs)",
    "worst_season_income_x": "Worst season (× normal profit)",
    "income_swing": "Income swing",
    "factory_crop_spend": "Buyer's crop spend",
    "middleman_margin": "Middleman margin",
    "farmer_share_pct": "Farmer's share of buyer's money (%)",
    "farm_short_weeks_pct": "Farm short (% of weeks)",
    "empty_shelf_weeks_pct": "Empty shelves (% of weeks)",
}


def run(seeds: int, designs: dict | None = None) -> pd.DataFrame:
    designs = designs or CONTRACT_DESIGNS
    cfg = Config()
    rows = []
    for kind in DEMANDS:
        for name, make in designs.items():
            for seed in range(seeds):
                s = make(cfg)
                demand = make_demand(s["cfg"], kind, np.random.default_rng(seed))
                r = simulate(s["cfg"], demand, s["agents"], s["farm_agent"], s["share_pos"],
                             seed=seed, negotiation=s.get("negotiation"))
                rows.append({"demand": kind, "design": name, "seed": seed, **summarize(r)})
    return pd.DataFrame(rows)


def chart(summary: pd.DataFrame, designs: list[str] | None = None,
          labels: dict | None = None, path: Path | None = None,
          title: str = "Who gets paid what under each payment design (median of seeds)"):
    panels = [("farm_income", "Farmer's total income, 4 years", "{:,.0f}"),
              ("worst_season_income_x", "Farmer's worst season (× normal profit)", "{:.2f}"),
              ("factory_crop_spend", "What the buyer pays for crop", "{:,.0f}")]
    designs = designs or list(CONTRACT_DESIGNS)
    labels = labels or DESIGN_LABELS
    path = path or OUT / "contract_designs.png"
    y = np.arange(len(designs))[::-1]
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.6), facecolor=SURFACE, sharey=True)
    for ax, (col, panel_title, fmt) in zip(axes, panels):
        ax.set_facecolor(SURFACE)
        for side in ("top", "right", "left"):
            ax.spines[side].set_visible(False)
        ax.spines["bottom"].set_color(AXIS)
        ax.tick_params(colors=MUTED, labelsize=9, left=False)
        ax.xaxis.grid(True, color=GRID, linewidth=0.8)
        ax.set_axisbelow(True)
        if col == "worst_season_income_x":
            ax.axvline(0, color=AXIS, linewidth=1)
        for k, (kind, offset) in enumerate([("noisy", 0.16), ("step", -0.16)]):
            vals = [summary.loc[(kind, d), col] for d in designs]
            ax.scatter(vals, y + offset, s=70, color=DEMAND_COLORS[kind], edgecolor=SURFACE,
                       linewidth=2, zorder=3, label=DEMAND_LABELS[kind])
            for v, yy in zip(vals, y + offset):
                ax.annotate(fmt.format(v), (v, yy), xytext=(7, 0), textcoords="offset points",
                            va="center", fontsize=8, color=INK2)
        lo, hi = ax.get_xlim()
        ax.set_xlim(lo, hi + (hi - lo) * 0.18)   # room for value labels
        ax.set_title(panel_title, loc="left", color=INK, fontsize=10.5)
    axes[0].set_yticks(y, [labels[d] for d in designs], fontsize=9, color=INK)
    axes[0].set_ylim(-0.6, len(designs) - 0.4)
    handles, labels = axes[0].get_legend_handles_labels()
    fig.legend(handles, labels, frameon=False, fontsize=9, labelcolor=INK2,
               loc="upper right", ncol=2, bbox_to_anchor=(0.99, 0.99))
    fig.suptitle(title, x=0.01, ha="left", color=INK, fontsize=12)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(path, dpi=160)
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)

    df = run(args.seeds)
    summary = df.drop(columns="seed").groupby(["demand", "design"], sort=False).median()
    table = summary[list(COLUMNS)].rename(columns=COLUMNS)
    table.to_csv(OUT / "contract_designs.csv")
    (OUT / "contract_designs.md").write_text(
        f"Median over {args.seeds} seeds per row.\n\n" + table.round(2).to_markdown() + "\n")
    chart(summary)

    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:,.2f}".format):
        print(table)
    print(f"\nSaved results to {OUT.resolve()}/")


if __name__ == "__main__":
    main()
