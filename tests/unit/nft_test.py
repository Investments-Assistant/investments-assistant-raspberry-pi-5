"""Unit tests for local NFT risk screening."""

from __future__ import annotations

import pytest

from src.tools.nft import assess_nft_risk


@pytest.mark.unit
def test_nft_screen_marks_illiquid_collection_high_risk():
    result = assess_nft_risk(
        "Example Collection",
        floor_price_usd=100,
        floor_change_7d_pct=-30,
        volume_7d_usd=0,
        sales_7d=2,
        holders=50,
        top_holder_pct=35,
    )

    assert result["risk_level"] == "high"
    assert result["usable_for_auto_trading"] is False
    assert result["data_quality"] == "caller_supplied_snapshot_only"


@pytest.mark.unit
def test_nft_screen_rejects_negative_metrics():
    with pytest.raises(ValueError, match="volume_7d_usd"):
        assess_nft_risk("Example Collection", volume_7d_usd=-1)
