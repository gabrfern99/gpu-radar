#!/usr/bin/env python3
"""
GPU Radar — Flask backend for the SPA plus a small JSON API.

    python3 app.py            # http://127.0.0.1:5055

The cron job (run_scrape.sh) writes to the same SQLite file this reads, so the
page is always showing whatever the last sweep found.
"""

import json
import mimetypes
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, url_for

import db
import gpus
import imgcache
import market
import notify
import region
from config import CFG, ROOT

app = Flask(__name__)
app.json.ensure_ascii = False

# Flask caches static files for 12 hours by default, which meant an edited
# app.js kept serving stale from the browser. Never cache them; the templates
# cache-bust with ?v=<mtime> anyway.
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0

@app.template_global()
def asset(filename: str) -> str:
    """static URL with a ?v=<mtime> cache-buster, so edits always land."""
    path = Path(app.static_folder) / filename
    v = int(path.stat().st_mtime) if path.exists() else 0
    return url_for("static", filename=filename, v=v)


@app.get("/img/<path:p>")
def img_proxy(p):
    """
    Serve OLX listing photos from the local cache, fetching on miss.

    See imgcache.py for why this exists: OLX 403s hotlinked images, so the
    browser can never load them directly.
    """
    cached = imgcache.fetch(p)
    if cached is None:
        return "unavailable", 502
    resp = Response(cached.read_bytes(),
                    mimetype=mimetypes.guess_type(p)[0] or "image/webp")
    resp.headers["Cache-Control"] = "public, max-age=604800"
    return resp


_scrape_lock = threading.Lock()
_scrape_state = {"running": False, "started": None, "result": None, "error": None}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

SORTS = {
    "score":    "deal_score DESC, last_seen DESC",
    "recent":   "COALESCE(posted_at, first_seen) DESC",
    "cheap":    "price ASC",
    "power":    "perf DESC, deal_score DESC",
    "value":    "perf_per_1k DESC",
    "discount": "discount DESC",
}


def _iso_age_hours(row) -> float | None:
    stamp = row.get("posted_at") or row.get("first_seen")
    if not stamp:
        return None
    try:
        dt = datetime.fromisoformat(stamp)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).total_seconds() / 3600


def decorate(row: dict) -> dict:
    row["age_hours"] = _iso_age_hours(row)
    row["is_local"] = (row.get("city") == CFG["home_city"])
    row["is_close"] = region.is_close(row.get("city"))
    row["priority"] = row.get("model_key") in CFG["priority_models"]
    row["linux"] = gpus.LINUX_DRIVER.get(row.get("brand") or "")
    # Shortlisted cards are allowed a higher ceiling than the general budget.
    ceiling = CFG["max_price"]
    if row["priority"]:
        ceiling = max(ceiling, CFG["priority_max_price"])
    row["in_budget"] = bool(row.get("price") and row["price"] <= ceiling)
    row["ceiling"] = ceiling
    row["saving"] = ((row["reference_price"] - row["price"])
                     if row.get("reference_price") and row.get("price") else None)
    return row


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------

@app.route("/")
def index():
    """
    Renders with the default view's data already inlined, so the grid paints
    with real cards instead of flashing skeletons while the first fetch lands.
    """
    return render_template(
        "index.html", cfg=CFG, subscribe=notify.subscribe_url(),
        initial_listings=fetch_listings({"price_max": CFG["max_price"], "limit": 300}),
        initial_stats=api_stats().get_json(),
    )


# ---------------------------------------------------------------------------
# api
# ---------------------------------------------------------------------------

def fetch_listings(args) -> list[dict]:
    """Shared by /api/listings and the initial server-side render."""
    get = args.get

    def flag(name, default=False):
        v = get(name)
        return default if v is None else str(v).lower() in ("1", "true", "yes", "on")

    def num(name):
        v = get(name)
        try:
            return int(v) if v not in (None, "") else None
        except (TypeError, ValueError):
            return None

    q     = (get("q") or "").strip()
    brand = get("brand") or ""
    model = get("model") or ""
    city  = get("city") or ""
    band  = get("band") or ""
    klass = get("class") or ""
    sort  = SORTS.get(get("sort") or "score", SORTS["score"])
    limit = min(num("limit") or 300, 1000)
    price_max, price_min = num("price_max"), num("price_min")
    perf_min, max_age = num("perf_min"), num("max_age_h")

    where, params = ["model_key IS NOT NULL", "price IS NOT NULL"], []
    if not flag("include_gone"):
        where.append("gone = 0")
    if not flag("include_suspect"):
        where.append("suspect = 0")
    if not flag("include_combos"):
        where.append("kind = 'gpu'")
    if price_max is not None:
        where.append("price <= ?"); params.append(price_max)
    if price_min is not None:
        where.append("price >= ?"); params.append(price_min)
    if perf_min is not None:
        where.append("perf >= ?"); params.append(perf_min)
    if brand:
        where.append("brand = ?"); params.append(brand)
    if model:
        where.append("model_key = ?"); params.append(model)
    if city:
        where.append("city = ?"); params.append(city)
    if band:
        parts = band.split(",")
        where.append("band IN (%s)" % ",".join("?" * len(parts)))
        params += parts
    if klass:
        parts = klass.split(",")
        where.append("deal_class IN (%s)" % ",".join("?" * len(parts)))
        params += parts
    if q:
        where.append("(LOWER(title) LIKE ? OR LOWER(location) LIKE ? OR LOWER(model_name) LIKE ?)")
        params += [f"%{q.lower()}%"] * 3

    sql = f"SELECT * FROM listings WHERE {' AND '.join(where)} ORDER BY {sort} LIMIT ?"
    with db.connect() as con:
        rows = [decorate(db.row_to_dict(r)) for r in con.execute(sql, params + [limit])]

    if max_age is not None:
        rows = [r for r in rows if r["age_hours"] is not None and r["age_hours"] <= max_age]
    return rows


@app.get("/api/listings")
def api_listings():
    rows = fetch_listings(request.args)
    return jsonify({"count": len(rows), "listings": rows})


@app.get("/api/listing/<lid>")
def api_listing(lid):
    with db.connect() as con:
        row = con.execute("SELECT * FROM listings WHERE id=?", (lid,)).fetchone()
        if not row:
            return jsonify({"error": "not found"}), 404
        item = decorate(db.row_to_dict(row))
        item["history"] = [dict(r) for r in con.execute(
            "SELECT price, ts FROM price_history WHERE listing_id=? ORDER BY ts", (lid,))]
        peers = [dict(r) for r in con.execute(
            "SELECT id,title,price,url,deal_score,city,band,location,posted_at FROM listings "
            "WHERE model_key=? AND id<>? AND gone=0 AND price IS NOT NULL "
            "ORDER BY price ASC LIMIT 8", (item["model_key"], lid))]
        stats = con.execute(
            "SELECT COUNT(*) n, MIN(price) lo, MAX(price) hi, AVG(price) avg "
            "FROM listings WHERE model_key=? AND price IS NOT NULL AND kind='gpu' "
            "AND suspect=0", (item["model_key"],)).fetchone()
    item["peers"] = peers
    item["model_stats"] = dict(stats) if stats else {}
    return jsonify(item)


@app.get("/api/stats")
def api_stats():
    with db.connect() as con:
        base = "FROM listings WHERE gone=0 AND price IS NOT NULL AND model_key IS NOT NULL"
        tot = con.execute(f"SELECT COUNT(*) FROM listings").fetchone()[0]
        live = con.execute(f"SELECT COUNT(*) {base}").fetchone()[0]
        in_budget = con.execute(
            f"SELECT COUNT(*) {base} AND kind='gpu' AND suspect=0 AND price<=?",
            (CFG["max_price"],)).fetchone()[0]
        # shortlisted cards count against their own, higher ceiling
        if CFG["priority_models"]:
            qs = ",".join("?" * len(CFG["priority_models"]))
            n_priority = con.execute(
                f"SELECT COUNT(*) {base} AND kind='gpu' AND suspect=0 AND price<=? "
                f"AND model_key IN ({qs})",
                (CFG["priority_max_price"], *CFG["priority_models"])).fetchone()[0]
        else:
            n_priority = 0
        by_class = {r[0]: r[1] for r in con.execute(
            f"SELECT deal_class, COUNT(*) {base} AND kind='gpu' AND suspect=0 "
            f"GROUP BY deal_class")}
        by_brand = {r[0]: r[1] for r in con.execute(
            f"SELECT brand, COUNT(*) {base} GROUP BY brand")}
        cities = [{"city": r[0], "n": r[1]} for r in con.execute(
            f"SELECT city, COUNT(*) c {base} GROUP BY city ORDER BY c DESC") if r[0]]
        bands = {r[0]: r[1] for r in con.execute(
            f"SELECT band, COUNT(*) {base} GROUP BY band") if r[0]}
        best = con.execute(
            f"SELECT * {base} AND kind='gpu' AND suspect=0 AND price<=? "
            f"ORDER BY deal_score DESC LIMIT 1", (CFG["max_price"],)).fetchone()
        last_run = con.execute("SELECT * FROM runs ORDER BY id DESC LIMIT 1").fetchone()
        n_alerts = con.execute("SELECT COUNT(*) FROM alerts").fetchone()[0]
        mkt_sum = market.summary(con)
        models = [dict(r) for r in con.execute(f"""
            SELECT model_key, model_name, brand, perf, vram,
                   COUNT(*) n, MIN(price) lo, MAX(price) hi,
                   CAST(AVG(price) AS INT) avg, MAX(reference_price) ref
            {base} AND kind='gpu' AND suspect=0
            GROUP BY model_key ORDER BY perf DESC""")]
    return jsonify({
        "total": tot, "live": live, "in_budget": in_budget,
        "by_class": by_class, "by_brand": by_brand, "cities": cities,
        "bands": bands, "priority_count": n_priority,
        "best": decorate(db.row_to_dict(best)) if best else None,
        "last_run": dict(last_run) if last_run else None,
        "alerts": n_alerts, "models": models,
        "budget": CFG["max_price"], "priority_budget": CFG["priority_max_price"],
        "home_city": CFG["home_city"],
        "region": {"metro": region.RM_GOIANIA, "anapolis": region.ANAPOLIS,
                   "nearby": region.NEARBY if CFG["include_nearby"] else [],
                   "label": "Grande Goiânia + Anápolis"
                            + (" + ~120 km" if CFG["include_nearby"] else "")},
        "priority_models": [
            {"key": k, "name": gpus.BY_KEY[k]["name"]}
            for k in CFG["priority_models"] if k in gpus.BY_KEY],
        "brand_bonus": CFG["brand_bonus"],
        "linux_drivers": gpus.LINUX_DRIVER,
        "scrape": {k: v for k, v in _scrape_state.items() if k != "result"},
        "imgcache": imgcache.stats(),
        "market": {k: mkt_sum[k] for k in
                   ("ready", "global_ratio", "sales_seen", "fast_sales_seen", "min_sales")},
    })


@app.get("/api/market")
def api_market():
    """Clearing-price model: what cards actually sell for, and how fast."""
    with db.connect() as con:
        m = market.summary(con)
        m["outcomes"] = {r[0]: r[1] for r in con.execute(
            "SELECT outcome, COUNT(*) FROM listings WHERE outcome IS NOT NULL "
            "GROUP BY outcome")}
        m["pending"] = con.execute(
            "SELECT COUNT(*) FROM listings WHERE gone=0 AND missed_sweeps>0").fetchone()[0]
    return jsonify(m)


@app.get("/api/alerts")
def api_alerts():
    with db.connect() as con:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM alerts ORDER BY id DESC LIMIT 100")]
    for r in rows:
        try:
            r["channels"] = json.loads(r["channels"] or "[]")
        except json.JSONDecodeError:
            r["channels"] = []
    return jsonify({"alerts": rows})


@app.get("/api/runs")
def api_runs():
    with db.connect() as con:
        return jsonify({"runs": [dict(r) for r in con.execute(
            "SELECT * FROM runs ORDER BY id DESC LIMIT 40")]})


@app.get("/api/catalog")
def api_catalog():
    return jsonify({"models": [
        {k: m[k] for k in ("key", "name", "brand", "vram", "perf", "fair")}
        for m in gpus.MODELS], "queries": gpus.QUERIES})


@app.post("/api/scrape")
def api_scrape():
    """Kick off a sweep in a worker thread. The UI polls /api/stats for it."""
    if not _scrape_lock.acquire(blocking=False):
        return jsonify({"ok": False, "error": "a sweep is already running"}), 409

    def work():
        import scraper
        _scrape_state.update(running=True, started=db.now(), error=None, result=None)
        try:
            _scrape_state["result"] = scraper.sweep(verbose=False)
        except Exception as e:
            _scrape_state["error"] = f"{type(e).__name__}: {e}"
        finally:
            _scrape_state["running"] = False
            _scrape_lock.release()

    threading.Thread(target=work, daemon=True).start()
    return jsonify({"ok": True, "started": True})


@app.post("/api/test-alert")
def api_test_alert():
    return jsonify(notify.test_alert())


@app.get("/api/config")
def api_config():
    public = {k: v for k, v in CFG.items()
              if not k.startswith("notify_telegram") and k != "notify_webhook"}
    public["ntfy_subscribe"] = notify.subscribe_url()
    return jsonify(public)


if __name__ == "__main__":
    db.init()
    print(f"\n  GPU Radar  ->  http://{CFG['host']}:{CFG['port']}")
    print(f"  ntfy topic ->  {notify.subscribe_url()}\n")
    app.run(host=CFG["host"], port=CFG["port"], debug=False, threaded=True)
