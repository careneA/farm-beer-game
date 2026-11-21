"""Run every strategy under every demand pattern and save results + charts.

    python run_experiments.py              # 30 random seeds per combination
    python run_experiments.py --seeds 100

Outputs go to results/: runs.csv, summary.csv, summary.md and four PNG charts.
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
from beergame.scenarios import SCENARIOS
from beergame.sim import STAGES, Config, make_demand, simulate

DEMANDS = ["noisy", "step", "spike"]
OUT = Path("results")

# Colors (validated categorical palette, fixed order) and chart ink.
COLORS = {"baseline": "#2a78d6", "info_sharing": "#eb6834", "forward_contract": "#1baf7a"}
LABELS = {"baseline": "Baseline (play alone)", "info_sharing": "Info sharing",
          "forward_contract": "Info sharing + contract"}
INK, INK2, MUTED, GRID, AXIS, SURFACE = "#0b0b0b", "#52514e", "#898781", "#e1e0d9", "#c3c2b7", "#fcfcfb"


def run_one(name: str, kind: str, seed: int, cfg: Config):
    s = SCENARIOS[name](cfg)
    demand = make_demand(s["cfg"], kind, np.random.default_rng(seed))
    return simulate(s["cfg"], demand, s["agents"], s["farm_agent"], s["share_pos"], seed=seed)


def style(ax):
    ax.set_facecolor(SURFACE)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color(AXIS)
    ax.tick_params(colors=MUTED, labelsize=9)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def chart_bullwhip(df: pd.DataFrame):
    """How much each player amplifies demand, moving upstream (noisy demand)."""
    order = ["retailer", "wholesaler", "distributor", "factory"]
    sub = df[df.demand == "noisy"]
    fig, ax = plt.subplots(figsize=(8, 4.5), facecolor=SURFACE)
    style(ax)
    x = np.arange(len(order))
    for name in SCENARIOS:
        y = [sub[sub.scenario == name][f"bullwhip_{s}"].median() for s in order]
        ax.plot(x, y, color=COLORS[name], linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=LABELS[name])
        ax.annotate(f"{LABELS[name]}  {y[-1]:.1f}×", (x[-1], y[-1]), xytext=(10, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK2)
    ax.axhline(1, color=AXIS, linewidth=1, linestyle="--")
    ax.set_yscale("log")
    ax.set_xticks(x, [s.capitalize() for s in order])
    ax.set_xlim(-0.3, len(order) + 0.9)
    ax.set_ylabel("Order variance ÷ demand variance (log scale)", color=INK2, fontsize=9)
    ax.set_xlabel("Customer side  →  upstream, toward the farm", color=INK2, fontsize=9)
    ax.set_title("Bullwhip effect by player, noisy demand (median of seeds)",
                 loc="left", color=INK, fontsize=12)
    ax.legend(frameon=False, fontsize=9, loc="upper left", labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(OUT / "bullwhip_by_stage.png", dpi=160)
    plt.close(fig)


def chart_spike(cfg: Config, seed: int = 3):
    """One panic-buying run per strategy: customer demand vs factory orders."""
    fig, axes = plt.subplots(1, 3, figsize=(13, 4), facecolor=SURFACE)
    for ax, name in zip(axes, SCENARIOS):
        style(ax)
        r = run_one(name, "spike", seed, cfg)
        weeks = np.arange(cfg.weeks)
        ax.plot(weeks, r.demand, color=MUTED, linewidth=1.5, label="Customer demand")
        ax.plot(weeks, r.orders[1], color=COLORS[name], linewidth=2, label="Factory orders to farm")
        ax.set_title(LABELS[name], loc="left", color=INK, fontsize=11)
        ax.set_xlabel("Week", color=INK2, fontsize=9)
    axes[0].set_ylabel("Units per week (each panel has its own scale)", color=INK2, fontsize=9)
    for ax in axes:
        ax.legend(frameon=False, fontsize=8, loc="upper right", labelcolor=INK2)
    fig.suptitle("Panic buying at week 30: what the farm hears", x=0.01, ha="left",
                 color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "spike_orders.png", dpi=160)
    plt.close(fig)


def chart_plantings(df: pd.DataFrame):
    """Farm outcomes: how steady plantings are and how much crop rots."""
    fig, axes = plt.subplots(1, 2, figsize=(11, 4), facecolor=SURFACE)
    metrics = [("planting_swing", "Planting swing (std ÷ mean)"),
               ("farm_short_weeks_pct", "Weeks the farm can't fill orders (%)")]
    x = np.arange(len(DEMANDS))
    width = 0.26
    for ax, (col, title) in zip(axes, metrics):
        style(ax)
        for k, name in enumerate(SCENARIOS):
            vals = [df[(df.demand == d) & (df.scenario == name)][col].median() for d in DEMANDS]
            bars = ax.bar(x + (k - 1) * (width + 0.02), vals, width, color=COLORS[name],
                          label=LABELS[name], edgecolor=SURFACE, linewidth=2)
            ax.bar_label(bars, fmt="%.2f" if col == "planting_swing" else "%.0f",
                         fontsize=8, color=INK2, padding=2)
        ax.set_xticks(x, ["Noisy", "Step +20%", "Spike +60%"])
        ax.set_title(title, loc="left", color=INK, fontsize=11)
    axes[0].legend(frameon=False, fontsize=8, labelcolor=INK2)
    fig.suptitle("What the farmer experiences (median of seeds)", x=0.01, ha="left",
                 color=INK, fontsize=12)
    fig.tight_layout()
    fig.savefig(OUT / "farm_outcomes.png", dpi=160)
    plt.close(fig)


def chart_money(cfg: Config, seeds: int, run_seed: int = 1):
    """Left: one run's farmgate price. Right: farmer's worst season vs price chasing."""
    fig, axes = plt.subplots(1, 2, figsize=(13, 4.2), facecolor=SURFACE,
                             gridspec_kw={"width_ratios": [1.5, 1]})
    ax = axes[0]
    style(ax)
    weeks = np.arange(cfg.weeks)
    for name in SCENARIOS:
        r = run_one(name, "noisy", run_seed, cfg)
        ax.plot(weeks, r.price, color=COLORS[name], linewidth=2, label=LABELS[name])
    ax.axhline(cfg.base_price, color=AXIS, linewidth=1, linestyle="--")
    ax.set_title("Farmgate spot price, one run with noisy demand", loc="left", color=INK, fontsize=11)
    ax.set_xlabel("Week", color=INK2, fontsize=9)
    ax.set_ylabel("Price (1.0 = balanced market)", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="upper left", labelcolor=INK2)

    ax = axes[1]
    style(ax)
    responses = [0.0, 0.25, 0.5, 0.75]
    sens = price_sensitivity(cfg, seeds, responses)
    for name in SCENARIOS:
        y = sens[name]
        ax.plot(responses, y, color=COLORS[name], linewidth=2, marker="o", markersize=8,
                markeredgecolor=SURFACE, markeredgewidth=2, label=LABELS[name])
        ax.annotate(f"{y[-1]:.2f}", (responses[-1], y[-1]), xytext=(8, 0),
                    textcoords="offset points", va="center", fontsize=9, color=INK2)
    ax.axhline(0, color=AXIS, linewidth=1)
    ax.set_xticks(responses)
    ax.set_xlim(-0.05, 0.88)
    ax.set_title("Farmer's worst season (× normal profit)", loc="left", color=INK, fontsize=11)
    ax.set_xlabel("How strongly farmers plant for today's price", color=INK2, fontsize=9)
    ax.legend(frameon=False, fontsize=8, loc="lower left", labelcolor=INK2)
    fig.tight_layout()
    fig.savefig(OUT / "farm_money.png", dpi=160)
    plt.close(fig)
    return sens


def price_sensitivity(cfg: Config, seeds: int, responses: list[float]) -> dict[str, list[float]]:
    """Median worst-season income for each strategy as farmers chase price harder."""
    out = {name: [] for name in SCENARIOS}
    for resp in responses:
        for name in SCENARIOS:
            vals = []
            for seed in range(seeds):
                s = SCENARIOS[name](cfg)
                s["farm_agent"].price_response = resp
                demand = make_demand(s["cfg"], "noisy", np.random.default_rng(seed))
                r = simulate(s["cfg"], demand, s["agents"], s["farm_agent"], s["share_pos"], seed=seed)
                vals.append(summarize(r)["worst_season_income_x"])
            out[name].append(float(np.median(vals)))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=30)
    args = ap.parse_args()
    OUT.mkdir(exist_ok=True)
    cfg = Config()

    rows = []
    for kind in DEMANDS:
        for name in SCENARIOS:
            for seed in range(args.seeds):
                rows.append({"demand": kind, "scenario": name, "seed": seed,
                             **summarize(run_one(name, kind, seed, cfg))})
    df = pd.DataFrame(rows)
    df.to_csv(OUT / "runs.csv", index=False)

    summary = df.drop(columns="seed").groupby(["demand", "scenario"], sort=False).median()
    summary.to_csv(OUT / "summary.csv")
    (OUT / "summary.md").write_text(
        f"Median over {args.seeds} seeds per row.\n\n" + summary.round(2).to_markdown() + "\n")

    chart_bullwhip(df)
    chart_spike(cfg)
    chart_plantings(df)
    chart_money(cfg, args.seeds)

    cols = ["bullwhip_factory", "planting_swing", "farm_short_weeks_pct",
            "empty_shelf_weeks_pct", "chain_cost", "farm_income",
            "worst_season_income_x", "income_swing", "avg_price"]
    with pd.option_context("display.width", 250, "display.max_columns", None,
                           "display.float_format", "{:,.2f}".format):
        print(summary[cols])
    print(f"\nSaved results to {OUT.resolve()}/")


if __name__ == "__main__":
    main()
