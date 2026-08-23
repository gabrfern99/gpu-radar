#!/usr/bin/env python3
"""
OLX graphics-card radar — fetch, parse, score, store, alert.

OLX server-renders its listing pages, so the ad cards are plain HTML
(`section.olx-adcard`). We read `sf=1` (newest first) and paginate with `o=N`.

    python3 scraper.py              # one full sweep
    python3 scraper.py --once "rx 6750"   # single query, print and exit
    python3 scraper.py --rescore    # recompute scores from stored data only
"""

import argparse
import json
import random
import re
import statistics
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import requests
from bs4 import BeautifulSoup

import db
import detail
import gpus
import imgcache
import market
import notify
import region
from config import CFG

BASE = "https://www.olx.com.br"

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,"
              "image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Referer": BASE + "/",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}

MONTHS = {"jan": 1, "fev": 2, "mar": 3, "abr": 4, "mai": 5, "jun": 6,
          "jul": 7, "ago": 8, "set": 9, "out": 10, "nov": 11, "dez": 12}

# São Paulo time — OLX stamps its listing dates in local Brazilian time.
BR_TZ = timezone(timedelta(hours=-3))


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------

class Fetcher:
    """Paced, retrying HTML fetcher. OLX answers 403 when pushed too hard."""

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)
        self.requests = 0
        self.errors = 0
        self._last = 0.0

    def _pace(self):
        wait = random.uniform(CFG["delay_min"], CFG["delay_max"])
        slept = time.monotonic() - self._last
        if slept < wait:
            time.sleep(wait - slept)
        self._last = time.monotonic()

    def get(self, url: str):
        for attempt in range(1, CFG["retries"] + 1):
            self._pace()
            self.requests += 1
            try:
                r = self.session.get(url, timeout=CFG["timeout"])
            except requests.RequestException as e:
                self.errors += 1
                log(f"  ! {type(e).__name__} on {url}")
                time.sleep(4 * attempt)
                continue
            if r.status_code == 200:
                return r.text
            self.errors += 1
            # 403/429 = rate limited. Back off hard, it clears within seconds.
            back = 8 * attempt + random.uniform(0, 4)
            log(f"  ! HTTP {r.status_code} (attempt {attempt}/{CFG['retries']}), "
                f"backing off {back:.0f}s")
            time.sleep(back)
        return None


def search_url(query: str, page: int = 1,
               price_from: int | None = None, price_to: int | None = None) -> str:
    parts = [p for p in (CFG["category_path"], CFG["region_path"]) if p]
    path = "/" + "/".join(parts) if parts else ""
    url = f"{BASE}{path}?q={quote_plus(query)}&sf=1"
    if price_from is not None:
        url += f"&ps={price_from}"
    if price_to is not None:
        url += f"&pe={price_to}"
    if page > 1:
        url += f"&o={page}"
    return url


def price_brackets() -> list[tuple[int, int]]:
    """
    Split the buying range into narrow price bands.

    Sorting is newest-first only — OLX ignores every sort value we tried except
    `sf=1` — so a cheap listing posted three weeks ago sits far below the page
    depth we fetch and is effectively invisible. Slicing by price fixes that:
    within a narrow band there are few enough ads that one or two pages
    exhaust them, whatever their age.

    Measured on this region: six bracketed requests surfaced 52 in-catalogue
    cards, where the newest-first sweep needed 47 requests to find 49.
    """
    lo, hi = CFG["min_price"], max(CFG["max_price"], CFG["priority_max_price"])
    edges = [lo, 600, 900, 1200, CFG["max_price"]]
    if hi > CFG["max_price"]:
        edges.append(hi)
    edges = sorted({e for e in edges if lo <= e <= hi})
    return [(edges[i], edges[i + 1]) for i in range(len(edges) - 1)]


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

_ID_RX = re.compile(r"-(\d{6,})(?:\?|$)")
_STATE_RX = re.compile(r"https?://([a-z]{2})\.olx\.com\.br/")
_PRICE_RX = re.compile(r"R\$\s*([\d.]+)")


def parse_price(text: str):
    if not text:
        return None
    m = _PRICE_RX.search(text)
    if not m:
        return None
    try:
        return int(m.group(1).replace(".", ""))
    except ValueError:
        return None


def parse_date(text: str, ref: datetime | None = None):
    """'Hoje, 10:27' / 'Ontem, 19:00' / '26 de jul, 19:39' -> aware datetime."""
    if not text:
        return None
    ref = ref or datetime.now(BR_TZ)
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c)).lower().strip()
    hh, mm = 12, 0
    tm = re.search(r"(\d{1,2}):(\d{2})", t)
    if tm:
        hh, mm = int(tm.group(1)), int(tm.group(2))
    if "hoje" in t:
        d = ref
    elif "ontem" in t:
        d = ref - timedelta(days=1)
    else:
        dm = re.search(r"(\d{1,2})\s*de\s*([a-z]{3})", t)
        if not dm:
            return None
        day, mon = int(dm.group(1)), MONTHS.get(dm.group(2))
        if not mon:
            return None
        year = ref.year if mon <= ref.month else ref.year - 1
        try:
            d = ref.replace(year=year, month=mon, day=day)
        except ValueError:
            return None
    try:
        return d.replace(hour=hh, minute=mm, second=0, microsecond=0)
    except ValueError:
        return None


def _txt(node):
    return node.get_text(" ", strip=True) if node else ""


def parse_cards(html: str) -> list[dict]:
    """Extract every ad card on a listing page."""
    soup = BeautifulSoup(html, "lxml")
    out = []
    for card in soup.select("section.olx-adcard"):
        link = card.select_one('a[data-testid="adcard-link"]') or card.select_one("a.olx-adcard__link")
        if not link or not link.get("href"):
            continue
        url = link["href"].split("?")[0]
        m = _ID_RX.search(url + "?")
        if not m:
            continue
        st = _STATE_RX.match(url)
        img = card.select_one(".olx-adcard__media img")
        out.append({
            "id": m.group(1),
            "url": url,
            "title": link.get("title") or _txt(card.select_one(".olx-adcard__title")),
            "price": parse_price(_txt(card.select_one(".olx-adcard__price"))),
            "image": (img.get("src") if img else None),
            "location": _txt(card.select_one(".olx-adcard__location")),
            "state": st.group(1) if st else None,
            "date_text": _txt(card.select_one(".olx-adcard__date")),
            "city": None,   # filled in by classify(), which knows the allowlist
        })
    return out


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------

def _clamp(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, x))


def reference_prices(con) -> dict:
    """
    Per-model reference price: the median of real GPU listings seen in the last
    90 days, once we have at least 5 of them. Until then we fall back to the
    catalog prior. This makes the radar self-calibrating — as the local market
    moves, so does what counts as a bargain.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    buckets: dict[str, list[int]] = {}
    for r in con.execute(
        "SELECT model_key, price FROM listings "
        "WHERE model_key IS NOT NULL AND price IS NOT NULL AND kind='gpu' "
        "AND suspect=0 AND last_seen >= ?", (cutoff,)
    ):
        buckets.setdefault(r["model_key"], []).append(r["price"])

    refs = {}
    for m in gpus.MODELS:
        seen = buckets.get(m["key"], [])
        if len(seen) >= 5:
            refs[m["key"]] = (int(statistics.median(seen)), f"median of {len(seen)}")
        else:
            refs[m["key"]] = (m["fair"], "catalog prior")
    return refs


def score(rec: dict, ref: int, now_utc: datetime, mkt: dict | None = None) -> dict:
    """
    Blend four signals into a 0-100 deal score:
      price   how far under the model's reference price it is  (40%)
      power   how capable the card is in absolute terms        (28%)
      value   performance per R$1000                           (17%)
      recency how fresh the ad is — old bargains are sold      (15%)

    The bands below are calibrated to what R$1500 can actually reach in
    Greater Goiânia: tier ~85 is the practical ceiling here, and 35% under
    the model's market median is a genuine steal. Calibrating against a
    national market instead left every local listing scoring in the 30s and
    nothing ever crossing the alert line.
    """
    price, perf = rec["price"], rec["perf"]
    perf_per_1k = perf / (price / 1000) if price else 0.0

    # Score against the inferred *clearing* price once market.py has enough
    # observed sales to estimate one — that is what cards actually go for, and
    # it sits well below the asking median. Until then, fall back to the
    # asking median exactly as before.
    mkt = mkt or {}
    clearing = mkt.get("clearing_prices", {}).get(rec.get("model_key")) if mkt.get("ready") else None
    if clearing:
        basis, band, basis_name = clearing, 0.22, "clearing price"
    else:
        basis, band, basis_name = ref, 0.35, None      # caller keeps its label

    discount = (basis - price) / basis if basis and price else 0.0
    price_c = _clamp(discount / band)
    power_c = _clamp((perf - 45) / (85 - 45))      # RX 580 tier -> RX 6650 XT tier
    value_c = _clamp((perf_per_1k - 35) / (90 - 35))

    posted = rec.get("_posted_dt")
    if posted:
        age_h = (now_utc - posted.astimezone(timezone.utc)).total_seconds() / 3600
    else:
        age_h = 999
    recency_c = (1.0 if age_h <= 6 else 0.85 if age_h <= 24 else
                 0.6 if age_h <= 72 else 0.35 if age_h <= 168 else 0.15)

    s = 100 * (0.40 * price_c + 0.28 * power_c + 0.17 * value_c + 0.15 * recency_c)

    # nudges
    # You run Linux: AMD's in-kernel amdgpu/Mesa driver is worth real points,
    # Intel Arc's open stack a little, and NVIDIA simply earns none.
    s += CFG["brand_bonus"].get(rec.get("brand", ""), 0)

    if rec.get("model_key") in CFG["priority_models"]:
        s += CFG["priority_bonus"]                 # a card you actually want

    band = rec.get("band")
    if rec.get("city") == CFG["home_city"]:
        s += 3                                     # in your own city, go see it today
    elif region.is_close(rec.get("city")):
        s += 1.5                                   # short drive
    elif band == "nearby":
        s -= 2                                     # ~120 km out; worth it only
                                                   # for a real bargain
    fl = rec.get("flags", [])
    if "warranty" in fl or "invoice" in fl:
        s += 2
    if "mining" in fl:
        s -= 8
    if "less_vram" in fl:
        s -= 12                                    # cut-down variant sold under
                                                   # the full model's name
    if "broken" in fl:
        s -= 30
    if rec.get("kind") == "combo":
        s -= 6                                     # a whole PC is not a bare card
    if rec.get("price_drops"):
        s += 2 * min(rec["price_drops"], 3)        # seller is motivated

    # Gaming first, so raster tier still leads the blend — but VRAM has become
    # the thing that ages a card out, and 12 GB is the comfortable floor now.
    vram = rec.get("real_vram") or rec.get("vram") or 8
    if vram >= 16:
        s += 4
    elif vram >= 12:
        s += 3
    elif vram >= 10:
        s += 1.5
    elif vram <= 4:
        s -= 4                                     # 4 GB is a real limitation

    s = round(_clamp(s, 0, 100), 1)
    cls = ("steal" if s >= 78 else "great" if s >= 64 else
           "good" if s >= 52 else "fair" if s >= 40 else "meh")
    return {
        "reference_price": ref,               # asking median, kept for display
        "clearing_price": clearing,
        "basis_name": basis_name,
        "discount": round(discount, 4),       # against whichever basis was used
        "perf_per_1k": round(perf_per_1k, 1),
        "expected_hours": market.expected_hours(mkt, price, ref),
        "deal_score": s,
        "deal_class": cls,
    }


def is_suspect(price: int, ref: int, flags: list) -> bool:
    """Too-good-to-be-true: usually a scam, a photo of a box, or a typo."""
    if not price or not ref:
        return False
    if "broken" in flags or "rental" in flags:
        return True
    return price < 0.30 * ref or price < CFG["min_price"]


# ---------------------------------------------------------------------------
# the sweep
# ---------------------------------------------------------------------------

_LOG_PREFIX = "[radar]"

# How many consecutive sweeps a listing must be missing before we accept it is
# actually gone. At one sweep every 20 minutes this is ~1 hour of absence.
MISSES_TO_CONFIRM = 3

# If a sweep hit more errors than this, its silence means nothing.
MAX_ERRORS_TO_TRUST_ABSENCE = 3

# OLX ads expire on their own after about two months. A listing that vanishes
# after that long probably timed out rather than sold, so it must not pollute
# the clearing-price estimate.
EXPIRY_DAYS = 45

# A card that clears this fast was priced at or below what the market will pay.
FAST_SALE_HOURS = 72


def log(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{_LOG_PREFIX} {stamp} {msg}", flush=True)


def plan() -> list[tuple[str, int]]:
    """(query, page) pairs for one sweep: a broad newest-first pass over
    everything the region lists as a graphics card, then one targeted query
    per model family to catch oddly-titled ads."""
    # Bracketed passes first: these are the ones that see cheap-but-old ads.
    jobs = []
    for lo, hi in price_brackets():
        for page in (1, 2):
            jobs.append(("placa de video", page, lo, hi))

    # Then the plain newest-first pass, which catches anything titled oddly
    # enough to miss the bracketed query, and anything with no price band.
    jobs += [("placa de video", p) for p in range(1, min(CFG["broad_pages"], 3) + 1)]
    jobs += [("placa de video gamer", p) for p in range(1, 3)]

    # AMD gets extra breadth — it is the stack you actually want on Linux.
    for q in gpus.AMD_QUERIES:
        jobs += [(q, p) for p in range(1, 3)]

    # The cards on your shortlist get paged deeper than the rest.
    for q in gpus.PRIORITY_QUERIES:
        jobs += [(q, p) for p in range(1, 3)]

    for q in gpus.QUERIES:
        jobs += [(q, p) for p in range(1, CFG["targeted_pages"] + 1)]

    # normalise every job to (query, page, price_from, price_to)
    norm = [j if len(j) == 4 else (j[0], j[1], None, None) for j in jobs]
    seen, out = set(), []
    for j in norm:
        if j not in seen:
            seen.add(j); out.append(j)
    return out


def sweep(verbose=True) -> dict:
    db.init()
    started = db.now()
    fetcher = Fetcher()
    now_utc = datetime.now(timezone.utc)

    jobs = plan()
    log(f"sweep starting — {len(jobs)} requests planned "
        f"({len(price_brackets())} price brackets)")

    # url-id -> card, deduped across queries
    cards: dict[str, dict] = {}
    total_cards = 0
    for i, (query, page, pfrom, pto) in enumerate(jobs, 1):
        url = search_url(query, page, pfrom, pto)
        html = fetcher.get(url)
        if not html:
            log(f"  [{i}/{len(jobs)}] {query!r} p{page}: FAILED")
            continue
        found = parse_cards(html)
        total_cards += len(found)
        fresh = 0
        for c in found:
            if c["id"] not in cards:
                c["via_query"] = query
                cards[c["id"]] = c
                fresh += 1
        if verbose:
            band = f" R${pfrom}-{pto}" if pfrom is not None else ""
            log(f"  [{i}/{len(jobs)}] {query!r}{band} p{page}: "
                f"{len(found)} cards, {fresh} new to sweep")
        if not found and page > 1:
            continue

    log(f"fetched {total_cards} cards, {len(cards)} unique listings")

    # ---- classify -------------------------------------------------------
    keep = []
    out_of_region = 0
    for c in cards.values():
        # OLX pads regional results with far-off cities, so gate on the
        # allowlist before doing any further work.
        c["city"] = region.city_of(c["location"], CFG["extra_cities"])
        c["band"] = region.band_of(c["location"]) or ("extra" if c["city"] else None)
        if CFG["strict_region"] and not region.in_region(
                c["location"], CFG["extra_cities"], CFG["include_nearby"]):
            out_of_region += 1
            continue
        model, kind, flags = gpus.analyze(c["title"])
        if not model or model["perf"] < CFG["min_perf"]:
            continue
        if kind in ("wanted", "other"):
            continue   # a buy request, or a phone whose seller takes GPUs in trade
        if not c["price"]:
            continue
        c.update(model_key=model["key"], model_name=model["name"], brand=model["brand"],
                 vram=model["vram"], perf=model["perf"], kind=kind, flags=flags,
                 real_vram=gpus.effective_vram(c["title"], model))
        c["_posted_dt"] = parse_date(c["date_text"])
        c["posted_at"] = c["_posted_dt"].astimezone(timezone.utc).isoformat(
            timespec="seconds") if c["_posted_dt"] else None
        keep.append(c)

    log(f"{out_of_region} listings dropped as outside the covered region")
    log(f"{len(keep)} listings matched the GPU catalog")

    # ---- store ----------------------------------------------------------
    #
    # Two passes on purpose. The market reference for a model is the median of
    # what we have seen, so it has to be computed *after* this sweep's rows
    # land — otherwise a fresh database scores everything against the stale
    # catalog priors and the first run's alerts are meaningless.
    stats = {"new": 0, "price_drops": 0, "alerted": 0}
    to_alert = []
    with db.connect() as con:
        existing = {r["id"]: db.row_to_dict(r) for r in con.execute(
            "SELECT id, price, alerted, price_drops, verify_ok FROM listings")}

        # pass 1 — upsert the raw facts
        for c in keep:
            prev = existing.get(c["id"])
            c["price_drops"] = prev["price_drops"] if prev else 0
            c["_dropped"] = bool(prev and prev["price"] and c["price"] < prev["price"])
            if c["_dropped"]:
                c["price_drops"] += 1
                stats["price_drops"] += 1
            c["_is_new"] = prev is None
            if c["_is_new"]:
                stats["new"] += 1

            con.execute("""
                INSERT INTO listings (id,url,title,price,image,location,city,band,state,date_text,
                    posted_at,model_key,model_name,brand,vram,perf,kind,flags,
                    first_seen,last_seen,seen_count,price_drops,initial_price,
                    gone,alerted,via_query)
                VALUES (:id,:url,:title,:price,:image,:location,:city,:band,:state,:date_text,
                    :posted_at,:model_key,:model_name,:brand,:vram,:perf,:kind,:flags,
                    :ts,:ts,1,:price_drops,:price,0,0,:via_query)
                ON CONFLICT(id) DO UPDATE SET
                    price=excluded.price, title=excluded.title, url=excluded.url,
                    image=COALESCE(excluded.image, listings.image),
                    location=excluded.location, city=excluded.city,
                    band=excluded.band, state=excluded.state,
                    date_text=excluded.date_text, posted_at=excluded.posted_at,
                    flags=excluded.flags, kind=excluded.kind,
                    model_key=excluded.model_key, model_name=excluded.model_name,
                    brand=excluded.brand, vram=excluded.vram, perf=excluded.perf,
                    last_seen=excluded.last_seen, seen_count=listings.seen_count+1,
                    price_drops=excluded.price_drops,
                    gone=0, missed_sweeps=0, gone_at=NULL, outcome=NULL
            """, {**c, "flags": json.dumps(c["flags"]), "ts": started})

            if c["_is_new"] or c["_dropped"]:
                con.execute("INSERT INTO price_history (listing_id,price,ts) VALUES (?,?,?)",
                            (c["id"], c["price"], started))

        # pass 2 — now that the rows are in, the medians are current
        refs = reference_prices(con)
        mkt = market.summary(con)
        if mkt["ready"]:
            log(f"clearing-price model active: cards sell at "
                f"{mkt['global_ratio']:.2f}x the asking median "
                f"({mkt['fast_sales_seen']} fast sales observed)")
        else:
            log(f"clearing-price model still learning "
                f"({mkt['fast_sales_seen']}/{mkt['min_sales']} fast sales) — "
                f"scoring against asking medians")

        for c in keep:
            ref, ref_src = refs[c["model_key"]]
            c["suspect"] = int(is_suspect(c["price"], ref, c["flags"]))
            c.update(score(c, ref, now_utc, mkt))
            c["ref_source"] = c.pop("basis_name") or ref_src
            con.execute("""UPDATE listings SET reference_price=?, ref_source=?, discount=?,
                           perf_per_1k=?, deal_score=?, deal_class=?, suspect=?,
                           clearing_price=?, expected_hours=? WHERE id=?""",
                        (c["reference_price"], c["ref_source"], c["discount"],
                         c["perf_per_1k"], c["deal_score"], c["deal_class"], c["suspect"],
                         c["clearing_price"], c["expected_hours"], c["id"]))

            prev = existing.get(c["id"])
            if (prev and prev.get("verify_ok") == 0):
                continue          # its own ad page already disqualified it
            if not (prev["alerted"] if prev else 0) and wants_alert(c):
                # Deduped by the `alerted` flag rather than by newness, so a
                # bargain that was already posted when the radar first saw it
                # still reaches you — exactly once.
                c["_reason"] = ("price drop" if c["_dropped"]
                                else "new listing" if c["_is_new"]
                                else "standing bargain")
                to_alert.append(c)

        # Anything not seen this sweep *might* be gone — but a single failed
        # request would otherwise fake a sale for every listing that query was
        # the only source of. So count consecutive misses and only call it gone
        # after MISSES_TO_CONFIRM of them, and only if the sweep went well
        # enough to trust its absence.
        con.execute("UPDATE listings SET missed_sweeps=0 WHERE last_seen >= ?", (started,))
        if fetcher.errors <= MAX_ERRORS_TO_TRUST_ABSENCE:
            con.execute("UPDATE listings SET missed_sweeps=missed_sweeps+1 "
                        "WHERE last_seen < ?", (started,))
            con.execute(f"UPDATE listings SET gone=1, gone_at=COALESCE(gone_at, ?) "
                        f"WHERE missed_sweeps >= {MISSES_TO_CONFIRM} AND gone=0",
                        (started,))
            classify_outcomes(con)
        else:
            log(f"{fetcher.errors} http errors — not trusting absences this sweep")
        pruned = db.prune(con, CFG["stale_days"])
        if pruned:
            log(f"pruned {pruned} stale listings")

    # ---- photos ---------------------------------------------------------
    # Pull the thumbnails now so the dashboard paints them from disk instead
    # of waiting on a round trip to OLX for each card on first view.
    got, missed = imgcache.warm(c.get("image") for c in keep)
    if got or missed:
        log(f"cached {got} new photos" + (f", {missed} failed" if missed else ""))

    # Listings churn, so drop photos nothing points at any more.
    with db.connect() as con:
        live = [r[0] for r in con.execute(
            "SELECT image FROM listings WHERE image IS NOT NULL")]
    gone_imgs, freed = imgcache.prune(live)
    if gone_imgs:
        log(f"pruned {gone_imgs} orphaned photos ({freed // 1024} KB)")

    # ---- alert ----------------------------------------------------------
    to_alert.sort(key=lambda c: -c["deal_score"])
    to_alert = to_alert[:CFG["max_alerts_per_run"]]
    if to_alert and CFG["verify_before_alert"]:
        to_alert = verify_candidates(fetcher, to_alert)
    if to_alert:
        log(f"alerting on {len(to_alert)} listing(s)")
        stats["alerted"] = notify.send_batch(to_alert)

    with db.connect() as con:
        con.execute("""INSERT INTO runs (started,finished,requests,http_errors,cards,
                       matched,new,price_drops,alerted)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (started, db.now(), fetcher.requests, fetcher.errors, total_cards,
                     len(keep), stats["new"], stats["price_drops"], stats["alerted"]))

    log(f"done — {stats['new']} new, {stats['price_drops']} price drops, "
        f"{stats['alerted']} alerts, {fetcher.errors} http errors")
    return {"requests": fetcher.requests, "errors": fetcher.errors,
            "cards": total_cards, "matched": len(keep), **stats}


def classify_outcomes(con) -> None:
    """
    Label every confirmed-gone listing with how long it lasted and what that
    most likely means. `tenure_hours` is measured from OLX's own posting date
    where we have it — our own first_seen is left-censored, since a listing may
    have been up for weeks before this radar ever ran.
    """
    rows = con.execute(
        "SELECT id, posted_at, first_seen, gone_at, last_seen FROM listings "
        "WHERE gone=1 AND outcome IS NULL").fetchall()
    for r in rows:
        start = r["posted_at"] or r["first_seen"]
        end = r["gone_at"] or r["last_seen"]
        if not (start and end):
            continue
        try:
            t0, t1 = datetime.fromisoformat(start), datetime.fromisoformat(end)
        except ValueError:
            continue
        if t0.tzinfo is None:
            t0 = t0.replace(tzinfo=timezone.utc)
        if t1.tzinfo is None:
            t1 = t1.replace(tzinfo=timezone.utc)
        hours = max(0.0, (t1 - t0).total_seconds() / 3600)
        outcome = ("expired" if hours > EXPIRY_DAYS * 24
                   else "sold_fast" if hours <= FAST_SALE_HOURS
                   else "sold")
        # gone_at must be persisted, not just read: market.observations()
        # filters on it, so leaving it NULL silently drops the observation.
        con.execute("UPDATE listings SET tenure_hours=?, outcome=?, "
                    "gone_at=COALESCE(gone_at, ?) WHERE id=?",
                    (round(hours, 1), outcome, end, r["id"]))


def verify_candidates(fetcher: "Fetcher", candidates: list[dict]) -> list[dict]:
    """
    Open each candidate's own ad page and keep only the ones that survive.

    This is the one place the radar spends a request per listing, and it is
    worth it: it is bounded by max_alerts_per_run, it only ever looks at ads
    that already cleared the score threshold, and it is the only way to catch
    what a title omits.
    """
    kept = []
    for c in candidates:
        html = fetcher.get(c["url"])
        if not html:
            log(f"  verify: could not open {c['id']}, alerting unverified")
            kept.append(c)
            continue
        det = detail.parse(html)
        if not det:
            log(f"  verify: no product data on {c['id']}, alerting unverified")
            kept.append(c)
            continue

        v = detail.verify(c, det)
        note = "; ".join(v["reasons"] + [f"({x})" for x in v["cautions"]])[:400]
        c["photo_count"] = v["photos"]
        c["description"] = det["description"][:4000]
        c["kind"], c["flags"] = v["kind"], v["flags"]
        if v["price_changed"]:
            log(f"  verify: {c['id']} price is actually {v['price_changed']}")
            c["price"] = v["price_changed"]

        with db.connect() as con:
            con.execute("""UPDATE listings SET description=?, photo_count=?, verify_ok=?,
                           verify_note=?, verified_at=?, kind=?, flags=?, price=?
                           WHERE id=?""",
                        (c["description"], c["photo_count"], int(v["ok"]), note,
                         db.now(), c["kind"], json.dumps(c["flags"]), c["price"], c["id"]))

        if v["ok"]:
            c["_cautions"] = v["cautions"]
            kept.append(c)
            if v["cautions"]:
                log(f"  verify: {c['id']} ok — note: {'; '.join(v['cautions'])}")
        else:
            log(f"  verify: REJECTED {c['id']} — {'; '.join(v['reasons'])}")
    return kept


def wants_alert(c: dict) -> bool:
    ceiling = CFG["max_price"]
    if c.get("model_key") in CFG["priority_models"]:
        ceiling = max(ceiling, CFG["priority_max_price"])
    if c["price"] > ceiling or c["price"] < CFG["min_price"]:
        return False
    if c["deal_score"] < CFG["alert_min_score"]:
        return False
    if c["suspect"] and not CFG["alert_suspect"]:
        return False
    if c["kind"] == "combo" and not CFG["alert_combos"]:
        return False
    if "broken" in c["flags"]:
        return False
    return True


def rescore(passes: int = 2) -> int:
    """
    Recompute every stored listing's score — use after editing the catalog,
    the classifier or the weights, without re-hitting OLX.

    Runs twice by default: the first pass re-classifies whole-PC listings out
    of the `gpu` bucket, the second then computes medians from a clean set.
    """
    n = 0
    for _ in range(passes):
        n = _rescore_once()
    return n


def _rescore_once() -> int:
    db.init()
    now_utc = datetime.now(timezone.utc)
    n = 0
    with db.connect() as con:
        refs = reference_prices(con)
        mkt = market.summary(con)
        for r in con.execute("SELECT * FROM listings").fetchall():
            rec = db.row_to_dict(r)
            model = gpus.BY_KEY.get(rec["model_key"] or "")
            if not model or not rec["price"]:
                continue
            # Re-derive kind and flags from the stored title so edits to the
            # classifier take effect without hitting OLX again.
            _m, kind, flags = gpus.analyze(rec["title"])
            rec["kind"], rec["flags"], rec["perf"] = kind, flags, model["perf"]
            rec["brand"], rec["model_key"] = model["brand"], model["key"]
            rec["real_vram"] = gpus.effective_vram(rec["title"], model)
            rec["band"] = region.band_of(rec.get("city") or rec.get("location") or "")
            rec["_posted_dt"] = (datetime.fromisoformat(rec["posted_at"])
                                 if rec["posted_at"] else None)
            ref, src = refs[model["key"]]
            rec["suspect"] = int(is_suspect(rec["price"], ref, rec["flags"]))
            s = score(rec, ref, now_utc, mkt)
            con.execute("""UPDATE listings SET reference_price=?, ref_source=?, discount=?,
                           perf_per_1k=?, deal_score=?, deal_class=?, suspect=?,
                           model_name=?, brand=?, vram=?, perf=?, kind=?, flags=?,
                           band=COALESCE(?, band),
                           clearing_price=?, expected_hours=?
                           WHERE id=?""",
                        (s["reference_price"], s["basis_name"] or src,
                         s["discount"], s["perf_per_1k"],
                         s["deal_score"], s["deal_class"], rec["suspect"],
                         model["name"], model["brand"], model["vram"], model["perf"],
                         kind, json.dumps(flags), rec["band"],
                         s["clearing_price"], s["expected_hours"], rec["id"]))
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description="OLX graphics-card radar")
    ap.add_argument("--once", metavar="QUERY", help="fetch one query and print it")
    ap.add_argument("--rescore", action="store_true", help="recompute scores, no fetching")
    ap.add_argument("--quiet", action="store_true", help="less per-request logging")
    ap.add_argument("--no-alert", action="store_true", help="scrape but send nothing")
    args = ap.parse_args()

    if args.rescore:
        print(f"rescored {rescore()} listings")
        return
    if args.once:
        html = Fetcher().get(search_url(args.once))
        if not html:
            sys.exit("fetch failed")
        for c in parse_cards(html):
            model, kind, flags = gpus.analyze(c["title"])
            city = region.city_of(c["location"])
            inreg = "  " if city else "xx"
            print(f"{inreg} {(c['price'] or 0):>6}  {(model['name'] if model else '—'):<16} "
                  f"{kind:<6} {(city or c['location'] or '?')[:22]:<24} "
                  f"{c['date_text']:<17} {c['title'][:44]}")
        return
    if args.no_alert:
        notify.DISABLED = True
    sweep(verbose=not args.quiet)


if __name__ == "__main__":
    main()
