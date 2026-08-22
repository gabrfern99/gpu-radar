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
import gpus
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


def search_url(query: str, page: int = 1) -> str:
    parts = [p for p in (CFG["category_path"], CFG["region_path"]) if p]
    path = "/" + "/".join(parts) if parts else ""
    url = f"{BASE}{path}?q={quote_plus(query)}&sf=1"
    if page > 1:
        url += f"&o={page}"
    return url


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


def score(rec: dict, ref: int, now_utc: datetime) -> dict:
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
    discount = (ref - price) / ref if ref and price else 0.0
    perf_per_1k = perf / (price / 1000) if price else 0.0

    price_c = _clamp(discount / 0.35)              # 35% under market = full marks
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
    if rec.get("city") == CFG["home_city"]:
        s += 3                                     # in your own city, go see it today
    elif region.is_close(rec.get("city")):
        s += 1.5                                   # short drive
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

    s = round(_clamp(s, 0, 100), 1)
    cls = ("steal" if s >= 78 else "great" if s >= 64 else
           "good" if s >= 52 else "fair" if s >= 40 else "meh")
    return {
        "reference_price": ref,
        "discount": round(discount, 4),
        "perf_per_1k": round(perf_per_1k, 1),
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


def log(msg: str):
    stamp = datetime.now().strftime("%H:%M:%S")
    print(f"{_LOG_PREFIX} {stamp} {msg}", flush=True)


def plan() -> list[tuple[str, int]]:
    """(query, page) pairs for one sweep: a broad newest-first pass over
    everything the region lists as a graphics card, then one targeted query
    per model family to catch oddly-titled ads."""
    jobs = [("placa de video", p) for p in range(1, CFG["broad_pages"] + 1)]
    jobs += [("placa de video gamer", p) for p in range(1, 3)]
    for q in gpus.QUERIES:
        for p in range(1, CFG["targeted_pages"] + 1):
            jobs.append((q, p))
    return jobs


def sweep(verbose=True) -> dict:
    db.init()
    started = db.now()
    fetcher = Fetcher()
    now_utc = datetime.now(timezone.utc)

    jobs = plan()
    log(f"sweep starting — {len(jobs)} requests planned")

    # url-id -> card, deduped across queries
    cards: dict[str, dict] = {}
    total_cards = 0
    for i, (query, page) in enumerate(jobs, 1):
        url = search_url(query, page)
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
            log(f"  [{i}/{len(jobs)}] {query!r} p{page}: {len(found)} cards, {fresh} new to sweep")
        if not found and page > 1:
            continue

    log(f"fetched {total_cards} cards, {len(cards)} unique listings")

    # ---- classify -------------------------------------------------------
    keep = []
    out_of_region = 0
    for c in cards.values():
        # OLX pads regional results with far-off cities, so gate on the
        # allowlist before doing any further work.
        c["city"] = region.city_of(c["location"])
        if CFG["strict_region"] and not region.in_region(c["location"], CFG["extra_cities"]):
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
                 vram=model["vram"], perf=model["perf"], kind=kind, flags=flags)
        c["_posted_dt"] = parse_date(c["date_text"])
        c["posted_at"] = c["_posted_dt"].astimezone(timezone.utc).isoformat(
            timespec="seconds") if c["_posted_dt"] else None
        keep.append(c)

    log(f"{out_of_region} listings dropped as outside Greater Goiânia + Anápolis")
    log(f"{len(keep)} listings matched the GPU catalog")

    # ---- store ----------------------------------------------------------
    stats = {"new": 0, "price_drops": 0, "alerted": 0}
    to_alert = []
    with db.connect() as con:
        refs = reference_prices(con)
        existing = {r["id"]: db.row_to_dict(r) for r in con.execute(
            "SELECT id, price, alerted, first_seen, seen_count, price_drops, initial_price "
            "FROM listings")}

        for c in keep:
            ref, ref_src = refs[c["model_key"]]
            prev = existing.get(c["id"])
            c["price_drops"] = prev["price_drops"] if prev else 0
            dropped = bool(prev and prev["price"] and c["price"] < prev["price"])
            if dropped:
                c["price_drops"] += 1
                stats["price_drops"] += 1

            c["suspect"] = int(is_suspect(c["price"], ref, c["flags"]))
            c.update(score(c, ref, now_utc))
            c["ref_source"] = ref_src

            is_new = prev is None
            if is_new:
                stats["new"] += 1

            con.execute("""
                INSERT INTO listings (id,url,title,price,image,location,city,state,date_text,
                    posted_at,model_key,model_name,brand,vram,perf,kind,flags,
                    reference_price,ref_source,discount,perf_per_1k,deal_score,deal_class,
                    suspect,first_seen,last_seen,seen_count,price_drops,initial_price,
                    gone,alerted,via_query)
                VALUES (:id,:url,:title,:price,:image,:location,:city,:state,:date_text,
                    :posted_at,:model_key,:model_name,:brand,:vram,:perf,:kind,:flags,
                    :reference_price,:ref_source,:discount,:perf_per_1k,:deal_score,:deal_class,
                    :suspect,:ts,:ts,1,:price_drops,:price,0,0,:via_query)
                ON CONFLICT(id) DO UPDATE SET
                    price=excluded.price, title=excluded.title, url=excluded.url,
                    image=COALESCE(excluded.image, listings.image),
                    location=excluded.location, city=excluded.city, state=excluded.state,
                    date_text=excluded.date_text, posted_at=excluded.posted_at,
                    reference_price=excluded.reference_price, ref_source=excluded.ref_source,
                    discount=excluded.discount, perf_per_1k=excluded.perf_per_1k,
                    deal_score=excluded.deal_score, deal_class=excluded.deal_class,
                    suspect=excluded.suspect, flags=excluded.flags, kind=excluded.kind,
                    last_seen=excluded.last_seen, seen_count=listings.seen_count+1,
                    price_drops=excluded.price_drops, gone=0
            """, {**c, "flags": json.dumps(c["flags"]), "ts": started})

            if is_new or dropped:
                con.execute("INSERT INTO price_history (listing_id,price,ts) VALUES (?,?,?)",
                            (c["id"], c["price"], started))

            already = prev["alerted"] if prev else 0
            if not already and wants_alert(c):
                # Alerting is deduped by the `alerted` flag rather than by
                # newness, so a bargain that was already posted when the radar
                # first saw it still reaches you — exactly once.
                c["_reason"] = ("price drop" if dropped
                                else "new listing" if is_new
                                else "standing bargain")
                to_alert.append(c)

        # anything we did not see this sweep is probably sold or expired
        con.execute("UPDATE listings SET gone=1 WHERE last_seen < ?", (started,))
        pruned = db.prune(con, CFG["stale_days"])
        if pruned:
            log(f"pruned {pruned} stale listings")

    # ---- alert ----------------------------------------------------------
    to_alert.sort(key=lambda c: -c["deal_score"])
    to_alert = to_alert[:CFG["max_alerts_per_run"]]
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


def wants_alert(c: dict) -> bool:
    if c["price"] > CFG["max_price"] or c["price"] < CFG["min_price"]:
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
        for r in con.execute("SELECT * FROM listings").fetchall():
            rec = db.row_to_dict(r)
            model = gpus.BY_KEY.get(rec["model_key"] or "")
            if not model or not rec["price"]:
                continue
            # Re-derive kind and flags from the stored title so edits to the
            # classifier take effect without hitting OLX again.
            _m, kind, flags = gpus.analyze(rec["title"])
            rec["kind"], rec["flags"], rec["perf"] = kind, flags, model["perf"]
            rec["_posted_dt"] = (datetime.fromisoformat(rec["posted_at"])
                                 if rec["posted_at"] else None)
            ref, src = refs[model["key"]]
            rec["suspect"] = int(is_suspect(rec["price"], ref, rec["flags"]))
            s = score(rec, ref, now_utc)
            con.execute("""UPDATE listings SET reference_price=?, ref_source=?, discount=?,
                           perf_per_1k=?, deal_score=?, deal_class=?, suspect=?,
                           model_name=?, brand=?, vram=?, perf=?, kind=?, flags=?
                           WHERE id=?""",
                        (s["reference_price"], src, s["discount"], s["perf_per_1k"],
                         s["deal_score"], s["deal_class"], rec["suspect"],
                         model["name"], model["brand"], model["vram"], model["perf"],
                         kind, json.dumps(flags), rec["id"]))
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
