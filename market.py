"""
Clearing-price inference — what cards actually *sell* for, not what sellers ask.

The reference price in scraper.py is the median **asking** price. Sellers ask
high, so "20% under the median ask" can still be above what anyone pays. But a
listing that disappears within a few days was priced at or below what the
market will bear, while one sitting untouched for a month was not. That
difference is the real market, and it costs no extra requests — the sweep is
already recording first_seen / posted_at / gone_at for every ad.

Two things are estimated here:

  clearing ratio    how far below the asking median a card has to be priced
                    before it actually moves. Learned globally across every
                    model first (far more samples, far more stable), then
                    refined per model once a model has enough of its own sales.

  velocity curve    given a price relative to the asking median, roughly how
                    long the ad lasts. This is what lets an alert say "at this
                    price it will be gone by morning".

Both degrade gracefully: with too little data they report `None` and the
caller keeps using the asking median exactly as before.
"""

import statistics
from datetime import datetime, timedelta, timezone

# Sales needed before we trust a global clearing ratio at all.
MIN_SALES_GLOBAL = 12

# Sales of one specific model before its own ratio overrides the global one.
# Kept high deliberately: on synthetic data a 9-sample per-model estimate was
# still 10% off the truth, while the pooled global estimate was within 1%.
# A noisy per-model override is worse than the global number.
MIN_SALES_PER_MODEL = 10

# Only look at recent history — the used-GPU market moves.
WINDOW_DAYS = 120

# Ratio bands used for the velocity curve, as multiples of the asking median.
BANDS = [(0.00, 0.70), (0.70, 0.85), (0.85, 1.00), (1.00, 1.20), (1.20, 9.99)]


def _asking_medians(con, window_days: int = WINDOW_DAYS) -> dict:
    """Median asking price per model, over everything we have seen recently."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=window_days)).isoformat()
    buckets: dict[str, list[int]] = {}
    for r in con.execute(
        "SELECT model_key, price FROM listings "
        "WHERE model_key IS NOT NULL AND price IS NOT NULL AND kind='gpu' "
        "AND suspect=0 AND last_seen >= ?", (cutoff,)
    ):
        buckets.setdefault(r["model_key"], []).append(r["price"])
    return {k: statistics.median(v) for k, v in buckets.items() if v}


def observations(con) -> list[dict]:
    """
    One row per resolved listing: how it was priced relative to its model's
    asking median, how long it lasted, and whether it sold.

    `expired` listings are excluded — after ~45 days an OLX ad times out on its
    own, so its disappearance says nothing about price.
    """
    medians = _asking_medians(con)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)).isoformat()
    out = []
    for r in con.execute(
        "SELECT model_key, price, tenure_hours, outcome, gone_at "
        "FROM listings WHERE kind='gpu' AND suspect=0 AND price IS NOT NULL "
        "AND model_key IS NOT NULL AND outcome IN ('sold','sold_fast') "
        "AND gone_at >= ?", (cutoff,)
    ):
        med = medians.get(r["model_key"])
        if not med:
            continue
        out.append({
            "model_key": r["model_key"],
            "price": r["price"],
            "ratio": r["price"] / med,
            "hours": r["tenure_hours"],
            "fast": r["outcome"] == "sold_fast",
        })
    return out


def clearing_ratios(con) -> dict:
    """
    {"global": float|None, "by_model": {key: float}, "n": int, "n_fast": int}

    The ratio is the median price-to-asking-median of cards that *sold fast*.
    Those are the ones that were priced right, so their level is the clearing
    price. Falling back to all sales (not just fast ones) would drag the
    estimate back toward the asking price we are trying to correct for.
    """
    obs = observations(con)
    fast = [o for o in obs if o["fast"]]
    result = {"global": None, "by_model": {}, "n": len(obs), "n_fast": len(fast)}

    pool = fast if len(fast) >= MIN_SALES_GLOBAL else None
    if pool is None and len(obs) >= MIN_SALES_GLOBAL:
        pool = obs                     # better than nothing, just less sharp
    if not pool:
        return result

    result["global"] = round(statistics.median(o["ratio"] for o in pool), 4)

    per: dict[str, list[float]] = {}
    for o in (fast or obs):
        per.setdefault(o["model_key"], []).append(o["ratio"])
    result["by_model"] = {
        k: round(statistics.median(v), 4)
        for k, v in per.items() if len(v) >= MIN_SALES_PER_MODEL
    }
    return result


def velocity_curve(con) -> list[dict]:
    """
    Median hours-on-market per price band. Answers "how long do I have?".
    Only resolved listings can contribute, so this fills in over time.
    """
    obs = [o for o in observations(con) if o["hours"] is not None]
    curve = []
    for lo, hi in BANDS:
        band = [o["hours"] for o in obs if lo <= o["ratio"] < hi]
        curve.append({
            "from": lo, "to": hi, "n": len(band),
            "median_hours": round(statistics.median(band), 1) if band else None,
        })
    return curve


def summary(con) -> dict:
    """Everything the scorer and the dashboard need, in one query pass."""
    ratios = clearing_ratios(con)
    medians = _asking_medians(con)
    clearing = {}
    if ratios["global"] is not None:
        for key, med in medians.items():
            r = ratios["by_model"].get(key, ratios["global"])
            clearing[key] = int(round(med * r))
    return {
        "ready": ratios["global"] is not None,
        "global_ratio": ratios["global"],
        "by_model_ratio": ratios["by_model"],
        "sales_seen": ratios["n"],
        "fast_sales_seen": ratios["n_fast"],
        "asking_medians": {k: int(v) for k, v in medians.items()},
        "clearing_prices": clearing,
        "velocity": velocity_curve(con),
        "min_sales": MIN_SALES_GLOBAL,
    }


def expected_hours(summary_: dict, price: int, asking_median: int | None) -> float | None:
    """Roughly how long an ad at this price should last, from the curve."""
    if not asking_median:
        return None
    ratio = price / asking_median
    for band in summary_.get("velocity", []):
        if band["from"] <= ratio < band["to"] and band["n"] >= 3:
            return band["median_hours"]
    return None
