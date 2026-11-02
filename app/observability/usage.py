"""Tính chi phí ước tính theo model tầng ngoài (USD / 1 triệu token)."""

from __future__ import annotations

from decimal import Decimal

# (input_per_million, output_per_million). Local và rules = 0.
EXTERNAL_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    "claude-haiku-4-5": (Decimal("1.00"), Decimal("5.00")),
    "claude-haiku-5-5": (Decimal("0.10"), Decimal("0.50")),
    "claude-sonnet-5-5": (Decimal("2.00"), Decimal("10.00")),
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-3.8-flash": (Decimal("0.75"), Decimal("3.75")),
}

DEFAULT_PRICING = (Decimal("0.30"), Decimal("2.50"))


def estimate_cost_usd(model: str | None, input_tokens: int | None, output_tokens: int | None) -> Decimal:
    if not model or not (input_tokens or output_tokens):
        return Decimal("0")
    price_in, price_out = EXTERNAL_PRICING.get(model.lower(), DEFAULT_PRICING)
    total = (Decimal(input_tokens or 0) * price_in + Decimal(output_tokens or 0) * price_out) / Decimal(1_000_000)
    return total.quantize(Decimal("0.000001"))
