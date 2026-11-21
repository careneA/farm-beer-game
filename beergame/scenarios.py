"""The three strategies the project compares."""

from __future__ import annotations

from dataclasses import replace

from .agents import ContractFarmer, InfoSharingAgent, InfoSharingFarmer, NaiveAgent, NaiveFarmer
from .sim import STAGES, Config

DOWNSTREAM = STAGES[1:]


def baseline(cfg: Config):
    """Everyone plays alone and only sees their own incoming orders."""
    return dict(cfg=cfg, agents={s: NaiveAgent() for s in DOWNSTREAM},
                farm_agent=NaiveFarmer(), share_pos=False)


def info_sharing(cfg: Config):
    """Everyone, farm included, sees real customer demand."""
    return dict(cfg=cfg, agents={s: InfoSharingAgent() for s in DOWNSTREAM},
                farm_agent=InfoSharingFarmer(), share_pos=True)


def forward_contract(cfg: Config):
    """Information sharing plus a seasonal forward contract: the factory
    commits to a weekly quantity at planting time and takes it all."""
    return dict(cfg=replace(cfg, contract=True),
                agents={s: InfoSharingAgent() for s in DOWNSTREAM},
                farm_agent=ContractFarmer(), share_pos=True)


SCENARIOS = {
    "baseline": baseline,
    "info_sharing": info_sharing,
    "forward_contract": forward_contract,
}


# --- Contract designs ---------------------------------------------------------
# All of these share demand information, so the only thing that changes is how
# the farmer gets paid.

def _contract(rule: str):
    def make(cfg: Config):
        s = forward_contract(cfg)
        s["cfg"] = replace(s["cfg"], contract_price_rule=rule)
        return s
    return make


def government_farmgate(cfg: Config, share: float = 0.7):
    """No contract. Every unit is paid a farmgate price set each season at a
    share of last season's average market price. Buyers still pay the market
    price; the middleman keeps the difference."""
    s = info_sharing(cfg)
    s["cfg"] = replace(cfg, farmgate_share=share)
    return s


CONTRACT_DESIGNS = {
    "no_contract": info_sharing,
    "last_season_price": _contract("last_season"),
    "fixed_price": _contract("fixed"),
    "floor_plus_share": _contract("floor_share"),
    "government_farmgate": government_farmgate,
}

DESIGN_LABELS = {
    "no_contract": "No contract (spot price)",
    "last_season_price": "Contract: last season's price",
    "fixed_price": "Contract: fixed price",
    "floor_plus_share": "Contract: floor + half the upside",
    "government_farmgate": "Government farmgate (70%)",
}


# --- Negotiated contracts -----------------------------------------------------
# Same physical chain as the contract designs above; the price terms are
# bargained each season. What changes between setups is who the farmer is.

from .negotiation import FactoryPrefs, FarmerPrefs, NashMediator, Table, TimeDependentNegotiator

SMALLHOLDER = FarmerPrefs(risk_aversion=1.0, spot_discount=0.15)
COOPERATIVE = FarmerPrefs(risk_aversion=0.5, spot_discount=0.0)
FACTORY = FactoryPrefs(security_value=0.15, risk_aversion=0.5)


def negotiated(farmer: FarmerPrefs, farm_style: str, factory_style: str = "tough",
               nash: bool = False):
    def make(cfg: Config):
        s = forward_contract(cfg)
        s["cfg"] = replace(s["cfg"], contract_price_rule="negotiated",
                           farm_spot_discount=farmer.spot_discount)
        s["negotiation"] = (NashMediator(farmer, FACTORY) if nash else
                            Table(TimeDependentNegotiator(FACTORY, factory_style),
                                  TimeDependentNegotiator(farmer, farm_style)))
        return s
    return make


NEGOTIATION_SETUPS = {
    # A lone smallholder: fears a bad season, gets 15% below spot selling
    # alone, and needs a deal so concedes fast. The factory plays tough.
    "smallholder": negotiated(SMALLHOLDER, "eager"),
    # The same farmer, if it held out as long as the factory does.
    "smallholder_holds_out": negotiated(SMALLHOLDER, "tough"),
    # Farmers bargaining together: sell at full spot if talks fail, can
    # absorb a bad season, and hold out as long as the factory does.
    "cooperative": negotiated(COOPERATIVE, "tough"),
    # Benchmark: the fair split (Nash bargaining solution) for the smallholder.
    "fair_split_smallholder": negotiated(SMALLHOLDER, "eager", nash=True),
}

NEGOTIATION_LABELS = {
    "smallholder": "Smallholder, concedes fast",
    "smallholder_holds_out": "Smallholder, holds out",
    "cooperative": "Cooperative, holds out",
    "fair_split_smallholder": "Fair split (Nash), smallholder",
}
