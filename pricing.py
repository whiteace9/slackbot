"""
Pricing engine for Airbnb turnover cleaning quotes.
Ported from the interactive quoting tool.
"""

# ── Zip code → market tier mapping ──────────────────────
ZIP_HIGH4 = [
    "100","101","102","103","104","105","106","107","108","109",
    "110","111","112","113","114","115","116","117","118","119",
    "120","121","900","901","902","903","904","941","942","943",
    "944","945","946","947","948","021","022","023","024","980","981","982",
]
ZIP_HIGH3 = [
    "200","201","202","203","204","205","206","207","208","209",
    "600","601","602","603","330","331","332","333","920","921","922",
    "800","801","802","970","971","972",
]
ZIP_MODERATE = [
    "956","957","958","787","786","372","373","850","851","852","853",
    "273","274","275","276","303","304","305","770","771","772","773","774","775",
]

TIER_LABELS = {
    1: "Lower Cost Market",
    2: "Moderate Cost Market",
    3: "High Cost Market",
    4: "Very High Cost Market",
}

SUPERVISOR_OVERHEAD_PCT = 0.12
MIN_MARGIN = 0.25

SUPPLY_COSTS = {
    "0-1": 8, "1-1": 10, "2-1": 12, "2-2": 13,
    "3-2": 15, "3-3": 16, "4-2": 18, "4-3": 20,
}

UNIT_LABELS = {
    "0-1": "Studio / 1BA",
    "1-1": "1BR / 1BA",
    "2-1": "2BR / 1BA",
    "2-2": "2BR / 2BA",
    "3-2": "3BR / 2BA",
    "3-3": "3BR / 3BA",
    "4-2": "4BR / 2BA",
    "4-3": "4BR / 3BA",
}

VALID_CONFIGS = list(UNIT_LABELS.keys())


def get_market_tier(zip_code: str) -> int | None:
    """Determine market cost tier (1-4) from zip code prefix."""
    if not zip_code or len(zip_code) < 3:
        return None
    prefix = zip_code[:3]
    if prefix in ZIP_HIGH4:
        return 4
    if prefix in ZIP_HIGH3:
        return 3
    if prefix in ZIP_MODERATE:
        return 2
    return 1


def get_tier_multiplier(tier: int) -> float:
    return {1: 0.75, 2: 1.0, 3: 1.25, 4: 1.55}.get(tier, 1.0)


def get_base_rates(tier: int) -> dict:
    """Get cleaner payout ranges [low, mid, high] by unit config for a given tier."""
    m = get_tier_multiplier(tier)
    return {
        "0-1": [round(50 * m), round(60 * m), round(70 * m)],
        "1-1": [round(65 * m), round(80 * m), round(95 * m)],
        "2-1": [round(85 * m), round(100 * m), round(120 * m)],
        "2-2": [round(95 * m), round(115 * m), round(135 * m)],
        "3-2": [round(115 * m), round(140 * m), round(165 * m)],
        "3-3": [round(130 * m), round(155 * m), round(185 * m)],
        "4-2": [round(145 * m), round(175 * m), round(205 * m)],
        "4-3": [round(160 * m), round(195 * m), round(225 * m)],
    }


def get_addon_rates(tier: int) -> dict:
    """Get add-on service rates for a given tier."""
    m = {1: 0.8, 2: 1.0, 3: 1.2, 4: 1.45}.get(tier, 1.0)
    return {
        "deep_clean": {"label": "Deep Clean (Oven & Fridge)", "rate": round(35 * m)},
        "laundry": {"label": "Laundry (Linens & Towels)", "rate": round(25 * m)},
    }


def calculate_unit_price(
    tier: int,
    beds: str,
    baths: str,
    deep_clean: bool = False,
    laundry: bool = False,
    override_base: float | None = None,
    override_deep: float | None = None,
    override_laundry: float | None = None,
) -> dict:
    """Calculate full pricing breakdown for a single unit type."""
    key = f"{beds}-{baths}"
    rates = get_base_rates(tier)
    rate_range = rates.get(key, rates["1-1"])
    addons = get_addon_rates(tier)
    supply = SUPPLY_COSTS.get(key, 10)

    # Cleaner payouts
    cleaner_base = override_base if override_base is not None else rate_range[1]  # mid default
    cleaner_deep = 0
    if deep_clean:
        cleaner_deep = override_deep if override_deep is not None else addons["deep_clean"]["rate"]
    cleaner_laundry = 0
    if laundry:
        cleaner_laundry = override_laundry if override_laundry is not None else addons["laundry"]["rate"]

    cleaner_total = cleaner_base + cleaner_deep + cleaner_laundry
    supervisor_cost = cleaner_total * SUPERVISOR_OVERHEAD_PCT
    total_cost = cleaner_total + supervisor_cost + supply

    # Price to client (minimum margin)
    price = int(-(-total_cost // (1 - MIN_MARGIN)))  # ceiling division
    while (price - total_cost) / price < MIN_MARGIN:
        price += 1

    margin = (price - total_cost) / price * 100

    addon_labels = []
    if deep_clean:
        addon_labels.append("Deep Clean")
    if laundry:
        addon_labels.append("Laundry")

    return {
        "label": UNIT_LABELS.get(key, f"{beds}BR / {baths}BA"),
        "key": key,
        "cleaner_base": cleaner_base,
        "cleaner_deep": cleaner_deep,
        "cleaner_laundry": cleaner_laundry,
        "cleaner_total": cleaner_total,
        "supervisor_cost": supervisor_cost,
        "supply_cost": supply,
        "total_cost": total_cost,
        "unit_price": price,
        "margin": round(margin, 1),
        "payout_range": [rate_range[0] + cleaner_deep + cleaner_laundry,
                         rate_range[2] + cleaner_deep + cleaner_laundry],
        "addons": addon_labels,
        "addon_total": cleaner_deep + cleaner_laundry,
    }


def calculate_quote(units: list, discount_pct: float = 0) -> dict:
    """
    Calculate full quote from a list of unit configs.
    
    Each unit in the list should be a dict with:
        - pricing: dict (output from calculate_unit_price)
        - qty: int
    """
    subtotal = 0
    line_items = []
    
    for u in units:
        p = u["pricing"]
        qty = u["qty"]
        line_total = p["unit_price"] * qty
        subtotal += line_total
        line_items.append({
            **p,
            "qty": qty,
            "line_total": line_total,
        })

    discount_amt = subtotal * (discount_pct / 100)
    final_total = subtotal - discount_amt
    total_units = sum(u["qty"] for u in units)

    # Check margins after discount
    worst_margin = 100
    for item in line_items:
        discounted_price = item["unit_price"] * (1 - discount_pct / 100)
        if discounted_price > 0:
            m = (discounted_price - item["total_cost"]) / discounted_price * 100
            worst_margin = min(worst_margin, m)

    return {
        "line_items": line_items,
        "total_units": total_units,
        "subtotal": subtotal,
        "discount_pct": discount_pct,
        "discount_amt": discount_amt,
        "final_total": final_total,
        "worst_margin": round(worst_margin, 1),
        "margin_ok": worst_margin >= 25,
    }
