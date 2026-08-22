"""
On-disk cache for OLX listing photos.

img.olx.com.br returns 403 to any request carrying a Referer that is not
olx.com.br — which is every request a browser makes from this dashboard. So
the photos are fetched server-side (where we simply send no Referer) and
served back from `/img/...` by app.py.

The scraper pre-warms this cache while it sweeps, so by the time you open the
page every thumbnail is already on disk and paints instantly instead of
waiting on a round trip to OLX.
"""

import hashlib
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "data" / "imgcache"
HOST = "https://img.olx.com.br/"

# Only this host, and only image paths. This must never become an open proxy.
PATH_OK = re.compile(r"^[A-Za-z0-9_\-./]{4,180}\.(?:webp|jpg|jpeg|png)$")

HEADERS = {"Accept": "image/avif,image/webp,*/*"}   # deliberately no Referer


def rel_of(url: str) -> str | None:
    """'https://img.olx.com.br/thumbs.../x.webp' -> 'thumbs.../x.webp'"""
    if not url or not url.startswith(HOST):
        return None
    rel = url[len(HOST):].split("?")[0]
    return rel if PATH_OK.match(rel) and ".." not in rel else None


def path_for(rel: str) -> Path:
    return CACHE / (hashlib.sha256(rel.encode()).hexdigest() + Path(rel).suffix)


def fetch(rel: str, timeout: int = 20) -> Path | None:
    """Return the cached file for `rel`, downloading it if needed."""
    if not PATH_OK.match(rel) or ".." in rel:
        return None
    dest = path_for(rel)
    if dest.exists() and dest.stat().st_size > 0:
        return dest
    try:
        r = requests.get(HOST + rel, headers=HEADERS, timeout=timeout)
    except requests.RequestException:
        return None
    if r.status_code != 200 or not r.content:
        return None
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(r.content)
    tmp.replace(dest)
    return dest


def warm(urls, workers: int = 6) -> tuple[int, int]:
    """
    Pre-download any photo we do not already hold. Returns (fetched, failed).
    Thumbnails are ~20 KB, so a few in parallel is plenty polite.
    """
    todo = []
    for u in urls:
        rel = rel_of(u or "")
        if rel and not path_for(rel).exists():
            todo.append(rel)
    if not todo:
        return 0, 0
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for res in pool.map(fetch, todo):
            ok += bool(res)
    return ok, len(todo) - ok


def prune(keep_urls) -> tuple[int, int]:
    """
    Drop cached photos no live listing points at any more. Listings churn, so
    without this the cache grows forever. Returns (removed, bytes_freed).
    """
    if not CACHE.exists():
        return 0, 0
    keep = {path_for(rel).name for rel in
            (rel_of(u or "") for u in keep_urls) if rel}
    removed = freed = 0
    for f in CACHE.iterdir():
        if not f.is_file():
            continue
        if f.name.endswith(".part") or f.name not in keep:
            freed += f.stat().st_size
            f.unlink(missing_ok=True)
            removed += 1
    return removed, freed


def stats() -> dict:
    if not CACHE.exists():
        return {"files": 0, "bytes": 0}
    files = [f for f in CACHE.iterdir() if f.is_file() and not f.name.endswith(".part")]
    return {"files": len(files), "bytes": sum(f.stat().st_size for f in files)}
