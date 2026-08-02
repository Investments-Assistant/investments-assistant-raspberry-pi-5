"""Local NFT risk assessment from user- or source-supplied collection metrics.

The assistant deliberately does not scrape marketplaces or place NFT orders. NFT
market data is fragmented, easy to spoof, and often illiquid; this function keeps
the analysis auditable by requiring the caller to provide the observed metrics.
"""

from __future__ import annotations

import math
from typing import Any


def _number(value: Any, name: str, *, minimum: float | None = None) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric") from exc
    if not math.isfinite(parsed) or (minimum is not None and parsed < minimum):
        raise ValueError(f"{name} must be finite and at least {minimum}")
    return parsed


def assess_nft_risk(
    collection: str,
    floor_price_usd: float | None = None,
    floor_change_7d_pct: float | None = None,
    volume_7d_usd: float | None = None,
    sales_7d: int | None = None,
    holders: int | None = None,
    top_holder_pct: float | None = None,
    bid_ask_spread_pct: float | None = None,
) -> dict[str, Any]:
    """Score observable NFT liquidity/concentration risks without a live feed."""
    name = str(collection).strip()[:200]
    if not name:
        return {"error": "collection is required"}

    floor = _number(floor_price_usd, "floor_price_usd", minimum=0)
    change = _number(floor_change_7d_pct, "floor_change_7d_pct")
    volume = _number(volume_7d_usd, "volume_7d_usd", minimum=0)
    sales = _number(sales_7d, "sales_7d", minimum=0)
    holder_count = _number(holders, "holders", minimum=0)
    concentration = _number(top_holder_pct, "top_holder_pct", minimum=0)
    spread = _number(bid_ask_spread_pct, "bid_ask_spread_pct", minimum=0)

    risks: list[str] = []
    if floor is None or volume is None or sales is None:
        risks.append("insufficient liquidity data; do not infer a reliable exit price")
    if volume is not None and volume == 0:
        risks.append("no reported 7-day volume")
    if sales is not None and sales < 10:
        risks.append("very few reported sales in the last 7 days")
    if change is not None and change <= -25:
        risks.append("severe 7-day floor-price decline")
    if concentration is not None and concentration >= 20:
        risks.append("high ownership concentration in the top holder")
    if holder_count is not None and holder_count < 100:
        risks.append("small holder base")
    if spread is not None and spread >= 15:
        risks.append("wide bid/ask spread indicates poor exit liquidity")

    if not risks:
        level = "unassessed"
    elif any("severe" in risk or "no reported" in risk for risk in risks):
        level = "high"
    elif len(risks) >= 2:
        level = "elevated"
    else:
        level = "moderate"

    return {
        "collection": name,
        "risk_level": level,
        "risks": risks,
        "observed_metrics": {
            "floor_price_usd": floor,
            "floor_change_7d_pct": change,
            "volume_7d_usd": volume,
            "sales_7d": int(sales) if sales is not None else None,
            "holders": int(holder_count) if holder_count is not None else None,
            "top_holder_pct": concentration,
            "bid_ask_spread_pct": spread,
        },
        "data_quality": "caller_supplied_snapshot_only",
        "usable_for_auto_trading": False,
        "disclaimer": (
            "This is a risk screen, not a valuation or authenticity check. Verify the "
            "contract, provenance, wash trading, custody, taxes, and marketplace terms manually."
        ),
    }
