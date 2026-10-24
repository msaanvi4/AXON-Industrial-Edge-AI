"""Transparent cost calculations; catalogue records are supplied by the operator."""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def estimate_cost(line_items: list[dict], *, currency: str = "INR", tax_rate: float | None = None, labour_hours: float | None = None, labour_rate: float | None = None) -> dict:
    normalized, missing = [], []
    subtotal = Decimal("0")
    for item in line_items:
        quantity, unit_price = item.get("quantity"), item.get("unit_price")
        if quantity is None or unit_price is None:
            missing.append(item.get("description", "unnamed line item")); continue
        amount = Decimal(str(quantity)) * Decimal(str(unit_price)); subtotal += amount
        normalized.append({"description": item.get("description", "unnamed line item"), "quantity": quantity, "unit_price": float(unit_price), "amount": float(amount), "source_status": item.get("source_status", "user_supplied")})
    if labour_hours is not None and labour_rate is not None:
        labour = Decimal(str(labour_hours)) * Decimal(str(labour_rate)); subtotal += labour
        normalized.append({"description": "labour", "quantity": labour_hours, "unit_price": labour_rate, "amount": float(labour), "source_status": "user_supplied"})
    elif labour_hours is not None or labour_rate is not None: missing.append("labour hours and rate")
    tax = subtotal * Decimal(str(tax_rate)) if tax_rate is not None else Decimal("0")
    total = subtotal + tax
    return {"currency": currency, "line_items": normalized, "parts_and_labour_subtotal": float(subtotal.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "tax": float(tax.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)), "total": float(total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)) if not missing else None, "estimate_status": "incomplete" if missing else "user_supplied_estimate", "missing_inputs": missing, "disclaimer": "This is not a supplier quote and does not establish part compatibility."}
