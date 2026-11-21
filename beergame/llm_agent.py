"""A Beer Game player that asks Claude what to order.

Drop it in place of any rule-based player:

    from beergame.llm_agent import ClaudeAgent
    agents["retailer"] = ClaudeAgent("retailer")

Every week costs one API call per Claude player, so start with one player
and a short game (see run_llm_trial.py).
"""

from __future__ import annotations

import json

import anthropic

from .sim import Observation

SYSTEM = """You are playing the Beer Game, a supply chain simulation, as the {stage}.
The chain is: farm -> factory -> distributor -> wholesaler -> retailer -> customers.
Each week you receive an order from your downstream customer, ship what you can,
and place one order with your upstream supplier. Goods you order arrive about
{lead} weeks later. Unfilled orders become backlog.
Costs: 0.5 per unit of inventory per week, 1.0 per unit of backlog per week.
Your goal is to keep total cost low for the whole chain, not just for you.
Reply with JSON only."""

ORDER_SCHEMA = {
    "type": "object",
    "properties": {
        "order": {"type": "integer", "description": "Units to order from your supplier this week"},
        "reasoning": {"type": "string", "description": "One short sentence"},
    },
    "required": ["order", "reasoning"],
    "additionalProperties": False,
}


class ClaudeAgent:
    def __init__(self, stage: str, model: str = "claude-opus-5-5", history_weeks: int = 8,
                 client: anthropic.Anthropic | None = None):
        self.stage = stage
        self.model = model
        self.history_weeks = history_weeks
        self.client = client or anthropic.Anthropic()
        self.log: list[dict] = []     # every decision with its reasoning, for analysis

    def _prompt(self, obs: Observation) -> str:
        lines = [
            f"Week {obs.week}.",
            f"Inventory on hand: {obs.inventory:.0f}",
            f"Backlog you owe downstream: {obs.backlog:.0f}",
            f"Order you received this week: {obs.incoming_order:.0f}",
            f"Already on order from your supplier, not yet received: {obs.supply_line:.0f}",
            f"Orders you received in recent weeks (oldest first): "
            f"{[round(x) for x in obs.order_history[-self.history_weeks:]]}",
        ]
        if obs.pos_history is not None:
            lines.append(f"Real customer demand in recent weeks (shared by the retailer): "
                         f"{[round(x) for x in obs.pos_history[-self.history_weeks:]]}")
        lines.append("How many units do you order from your supplier this week?")
        return "\n".join(lines)

    def order(self, obs: Observation) -> float:
        response = self.client.beta.messages.create(
            model=self.model,
            max_tokens=2000,
            system=SYSTEM.format(stage=self.stage, lead=obs.lead_time),
            messages=[{"role": "user", "content": self._prompt(obs)}],
            output_config={"effort": "low",
                           "format": {"type": "json_schema", "schema": ORDER_SCHEMA}},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            # Should not happen for this task; fall back to passing the order on.
            decision = {"order": obs.incoming_order, "reasoning": "refused; passed order on"}
        else:
            text = next(b.text for b in response.content if b.type == "text")
            decision = json.loads(text)
        self.log.append({"week": obs.week, **decision})
        return float(decision["order"])
