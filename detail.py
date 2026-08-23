"""
Verify a listing against its own ad page before alerting on it.

The search results only give a title, and titles lie by omission. The
highest-scoring listing this radar ever produced was an *empty cardboard box*
for an RTX 3060 — the title happened to say so, but nothing guaranteed it
would. A description says what a title will not: that the card is untested,
was mined on, is missing the fan, or that the seller is really offering the
box it came in.

So before an alert goes out, fetch that one ad page and read it. This costs at
most `max_alerts_per_run` requests per sweep and usually zero to four, because
only listings that already cleared the score threshold are ever checked.

OLX renders a schema.org Product block into every ad page, which carries the
full description, the price and the photo list — far steadier than scraping
the visible markup.
"""

import json
import re

from bs4 import BeautifulSoup

import gpus

# Description phrases that kill an alert outright, over and above whatever the
# shared classifier finds. Kept here because they only make sense in prose.
DEALBREAKERS = [
    (r"\bn[ãa]o\s+(?:foi\s+)?test", "seller says it is untested"),
    (r"\bsem\s+garantia\s+de\s+funcionamento\b", "no working guarantee"),
    (r"\bn[ãa]o\s+d[áa]\s+v[íi]deo\b|\bn[ãa]o\s+liga\b", "does not display"),
    (r"\bpara\s+retirada\s+de\s+pe[çc]as\b", "sold for parts"),
    (r"\bcom\s+defeito\b|\bdefeituosa\b", "stated defect"),
    (r"\btravando\b|\bartefatos?\b|\bglitch", "artefacts / instability"),
    (r"\bapenas\s+a\s+caixa\b|\bcaixa\s+vazia\b", "it is only the box"),
]

# Softer notes — worth telling you, not worth blocking.
CAUTIONS = [
    (r"\bminera[çc][ãa]o\b|\bminerad", "was used for mining"),
    (r"\brepasta|\btroquei\s+a\s+pasta\b", "repasted"),
    (r"\bsem\s+nota\b|\bsem\s+nota\s+fiscal\b", "no invoice"),
    (r"\bn[ãa]o\s+aceito\s+devolu", "no returns"),
    (r"\bpix\s+antecipado\b|\bpagamento\s+antecipado\b", "wants payment up front"),
    # Modded Chinese cards (Jieshuo and friends) need a patched driver. On
    # Linux that is a genuine problem, not a footnote.
    (r"\bbaixar\s+o\s+driver\b|\bdriver\s+(?:modificad|customizad|espec[íi]fic|pr[óo]prio)"
     r"|\bjieshuo\b|\bdriver\s+modded\b", "needs a custom/patched driver — check Linux support"),
    (r"\bbios\s+(?:modificad|custom)|\bflashad", "modified BIOS"),
]

_DEAL = [(re.compile(p, re.I), why) for p, why in DEALBREAKERS]
_CAUT = [(re.compile(p, re.I), why) for p, why in CAUTIONS]


def parse(html: str) -> dict | None:
    """Pull the schema.org Product block out of an ad page."""
    soup = BeautifulSoup(html, "lxml")
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = tag.string or tag.get_text() or ""
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(d, dict) or d.get("@type") != "Product":
            continue
        images = d.get("image") or []
        if isinstance(images, dict):
            images = [images]
        desc = d.get("description") or ""
        desc = re.sub(r"<br\s*/?>", "\n", desc)
        desc = re.sub(r"<[^>]+>", " ", desc)
        price = None
        offers = d.get("offers") or {}
        if isinstance(offers, dict):
            try:
                price = int(float(offers.get("price")))
            except (TypeError, ValueError):
                price = None
        return {
            "title": d.get("name") or "",
            "description": desc.strip(),
            "price": price,
            "photos": len(images),
        }
    return None


def verify(listing: dict, det: dict) -> dict:
    """
    Judge a listing against its ad page.

    Returns {"ok", "reasons", "cautions", "kind", "flags", "photos",
             "price_changed"} — `ok=False` means do not alert.
    """
    desc = det.get("description") or ""
    out = {"ok": True, "reasons": [], "cautions": [], "photos": det.get("photos", 0),
           "kind": listing.get("kind"), "flags": list(listing.get("flags") or []),
           "price_changed": None}

    if not desc:
        out["cautions"].append("ad page had no description")

    for rx, why in _DEAL:
        if rx.search(desc):
            out["ok"] = False
            out["reasons"].append(why)

    for rx, why in _CAUT:
        if rx.search(desc):
            out["cautions"].append(why)

    # Re-run the shared classifier over the prose, but only trust the verdicts
    # that prose can actually support. Deliberately NOT the combo verdict:
    # almost every description says "PC" somewhere ("testada no meu PC"), so
    # treating that as evidence of a whole machine misfires on nearly
    # everything. Whether the ad sells a system is a question about the title.
    kind, flags = gpus.inspect_text(desc)
    if kind in ("accessory", "wanted", "other"):
        out["ok"] = False
        out["reasons"].append(f"description reads as {kind}, not a card for sale")
        out["kind"] = kind

    for f in flags:
        if f not in out["flags"]:
            out["flags"].append(f)
    if "broken" in out["flags"]:
        out["ok"] = False
        out["reasons"].append("description indicates a fault")

    # A single photo on a card worth four figures is worth being told about.
    if det.get("photos", 0) <= 1:
        out["cautions"].append(f"only {det.get('photos', 0)} photo(s)")

    # The listing page is authoritative if the card went stale.
    if det.get("price") and listing.get("price") and det["price"] != listing["price"]:
        out["price_changed"] = det["price"]

    return out
