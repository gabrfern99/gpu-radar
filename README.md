# GPU Radar

A price radar for used graphics cards on [OLX](https://www.olx.com.br), scoped to
**Greater Goiânia + Anápolis (+ ~120 km)** and a **R$ 1.500** ceiling, and
weighted toward **AMD** because it feeds a Linux machine. It sweeps OLX on a
schedule, works out what each card is actually worth from the listings it has
seen, and pushes an alert to your phone when something genuinely underpriced
shows up.

Three parts:

| | |
|---|---|
| `scraper.py` | fetches, parses, scores and stores listings; fires alerts |
| `gpus.py` | model catalog, title matcher, ad classifier |
| `region.py` | which cities count, and how far out they are |
| `market.py` | infers what cards actually *sell* for, from how fast ads vanish |
| `detail.py` | reads an ad's own page before alerting on it |
| `imgcache.py` | on-disk photo cache (OLX blocks hotlinking) |
| `app.py` | Flask backend + a single-page dashboard |
| `run_scrape.sh` | cron entrypoint, `flock`-guarded |

---

## Why a score instead of a price filter

"Cheap" is not the same as "a good deal". An RX 6500 XT at R$600 is cheap and
mediocre; an RX 6650 XT at R$1.450 is neither cheap nor a bargain. So every
listing gets a **0–100 deal score** blending four things:

| weight | signal | what it measures |
|---:|---|---|
| 40% | **price** | how far under that model's market price it is |
| 28% | **power** | how capable the card is, in absolute terms |
| 17% | **value** | performance per R$1.000 |
| 15% | **recency** | how fresh the ad is — old bargains are already sold |

Plus nudges: warranty and invoice help, a mining card and a cut-down VRAM
variant hurt, your own city is worth a little, the far ring costs a little,
and a seller who has already dropped the price is a seller worth talking to.

### AMD-first, because Linux

`brand_bonus` adds **+8 to AMD** and **+3 to Intel Arc**. Both ship open,
in-tree drivers — `amdgpu`/`Xe` plus Mesa — so there is nothing to install, no
DKMS rebuild when the kernel updates, and Wayland behaves. NVIDIA is *not*
penalised; it simply earns no bonus, so it has to be a better deal on merit to
outrank a comparable Radeon. Every card carries its driver stack in the
catalog and the dashboard labels it (`◆ driver aberto` / `◇ proprietário`).

AMD also gets more search effort: extra broad sweeps (`radeon`, `placa de
video amd`, `placa de video radeon`) on top of the per-model queries.

### VRAM

Gaming first, so raster tier still leads the blend — but memory is what ages a
card out, so 12 GB is treated as the comfortable floor: **+4** at 16 GB, **+3**
at 12 GB, **+1.5** at 10 GB, and **−4** at 4 GB or less.

That uses the memory the card in *that ad* actually has, not the catalog's
nominal figure. Cut-down variants get sold under the full model's name — an
RTX 3050 6GB, or the Jieshuo RTX 3060 with 6 GB instead of 12 — and handing
those a 12 GB bonus was exactly the kind of quiet error that makes a score
untrustworthy.

### A shortlist that gets special treatment

`priority_models` (default: **RX 6750 XT** and **RX 7600 XT**) get:

- a **+6** score bonus,
- **deeper paging** — their own dedicated queries, two pages each,
- a **higher price ceiling** — `priority_max_price` (default R$1.800) instead
  of `max_price`, because a decent local 6750 XT starts around R$1.800 and
  silently dropping those on the floor was worse than telling you about them.

Set `priority_max_price` equal to `max_price` to switch that headroom off.

Scores land in five buckets — `steal` ≥ 78, `great` ≥ 64, `good` ≥ 52,
`fair` ≥ 40, `meh` below that.

### Asking price is not selling price

The reference above is the median **asking** price, and sellers ask high — so
"20% under the median ask" can still be more than anyone actually pays.

But a listing that vanishes within a few days was priced at or below what the
market bears, while one sitting untouched for a month was not. `market.py`
turns that into a **clearing price**, using data the sweep already collects —
no extra requests at all:

- Tenure is measured from OLX's own posting date, not `first_seen`. Our own
  first sighting is left-censored: an ad may have been up for weeks before the
  radar ever ran.
- Ads older than `EXPIRY_DAYS` (45) when they vanish are marked `expired` and
  excluded — OLX ads time out on their own, which says nothing about price.
- The ratio is learned **globally first**, pooled across every model, then
  overridden per model only at 10+ of that model's own sales. On synthetic
  data a 9-sample per-model estimate was still 10% off while the pooled
  estimate was within 1%, so a noisy per-model override is worse than none.
- A listing must be missing from **3 consecutive sweeps** before it counts as
  gone, and absences are ignored entirely on a sweep with more than 3 HTTP
  errors. Otherwise one failed request fabricates a sale for every listing
  that query was the only source of.

Until there are 12 observed fast sales the estimator reports "not ready" and
scoring falls back to the asking median exactly as before. Once it is ready,
the score is computed against the clearing price with a tighter band (22%
under, versus 35% under the asking median), and the dashboard shows the
learned ratio plus a **velocity curve** — how long ads last at each price
level. That is what lets an alert say *"ads at this price usually last ~29h,
move fast"*.

### The market price is learned, not hardcoded

`gpus.py` ships a *prior* for each model, but it is only a bootstrap. Once the
database holds 5+ sightings of a model, the **observed median** takes over. So
the radar calibrates itself to the local market rather than to a price list
that goes stale — and "35% under market" means under *Goiânia's* market.

This matters more than it sounds. Calibrated against national prices, every
local listing scored in the 30s and nothing ever crossed the alert line.

### Titles lie by omission

The highest-scoring listing this radar ever produced was an **empty cardboard
box** — *"CAIXA VAZIA VGA MSI RTX 3060"*, R$100, deal score 90. Six of them,
in fact, all from the same seller. Nothing was technically wrong: it is a real
ad, for a real model, at a real price. Only the price heuristic caught it, and
that was luck — an empty box asking 31% of the card's value would have cleared
the suspect threshold and alerted as a steal.

Two defences, because one is not enough:

1. **Title level** — `accessory` is now a kind of its own, alongside `combo`
   and `wanted`. Empty boxes, backplates, risers, brackets and bare coolers
   are excluded outright. (Note what is *not* in that list: "Triple Fan" and
   "Triplo Fan" are genuine card names — Red Devil Triple Fan is a real
   RX 6700 XT — so matching on `fan` would have thrown away real listings.)

2. **Description level** — `detail.py` opens the ad's own page and reads it
   before any alert is sent. OLX renders a schema.org Product block into every
   ad page with the full description, price and photo list, which is far
   steadier than scraping the visible markup.

The verifier blocks on: untested, no working guarantee, "does not display",
sold for parts, stated defect, artefacts, and "only the box". It warns without
blocking on: mining use, repasted, no invoice, no returns, payment up front,
**a modified BIOS or a card needing a patched driver** — that last one matters
on Linux, and it is exactly what one "RTX 3060" in the current data turned out
to need.

This costs at most `max_alerts_per_run` requests per sweep and usually zero to
four, because only listings that already cleared the score threshold are ever
checked. A listing its own page disqualifies is remembered and never
reconsidered.

One thing deliberately *not* carried over from the title rules: the `combo`
verdict. Nearly every description says "PC" somewhere — "testada no meu PC" —
so treating that as evidence of a whole machine misfires on almost everything.
Whether an ad sells a system is a question about its title.

### What gets thrown out

A graphics card gets named in ads that are not selling a graphics card, and
mixing them corrupts the reference price — three gaming PCs in the GTX 1070
bucket once dragged its "median" to R$3.000. So `gpus.py` classifies first:

- **`gpu`** — a bare card. Scored and alertable.
- **`combo`** — a whole PC or laptop that contains one. Shown with a badge,
  kept out of the medians, not alerted by default.
- **`accessory`** — the box, a backplate, a riser, a bare cooler. Dropped.
- **`wanted`** — someone *looking to buy* (`compro`, `procuro`). Dropped.
- **`other`** — a phone or console whose seller merely accepts a GPU in
  trade. Dropped.

Listings priced under 30% of the model's market price are flagged
**`suspect`** and hidden by default. That is not a bargain, that is a scam,
a photo of an empty box, or a typo.

### Finding the cheap-but-old listings

OLX ignores every sort value except `sf=1` (newest first) — `sf=2/3/4` are
silently the same as no sort at all. So paging a query gives recent listings,
and a genuinely cheap card posted three weeks ago sits below any page depth
worth fetching.

`ps` and `pe` (price from / price to) *do* work, so the sweep slices the
buying range into narrow bands and pages each one. Within a R$300 band there
are few enough ads that one or two pages exhaust them, whatever their age.

Measured on this region: **six bracketed requests surfaced 52 in-catalogue
cards, where the newest-first sweep needed 47 requests to find 49.** The
coverage gain is modest — about 6% of listings were genuinely invisible before
— but the efficiency gain is roughly eightfold per request, which is what
makes sweeping more often affordable.

### Geography

OLX's region filter is wrong in both directions: a "Grande Goiânia e Anápolis"
search still returned Uruaçu, Cocalzinho and Nova Glória, while hiding
everything just outside the metro. So the sweep fetches **state-wide** and
`region.py` does the geography itself, in three bands:

| band | what | scoring |
|---|---|---|
| `metro` | the 21 municipalities of the RM Goiânia — Goiânia, Aparecida, Trindade, Senador Canedo, Goianira, Inhumas… | +3 home city, +1.5 short drive |
| `anapolis` | Anápolis | — |
| `nearby` | 31 more towns within roughly 120 km — Itaberaí, Piracanjuba, Pirenópolis, Silvânia… | −2 |

Anything outside all three is dropped. Turn the outer ring off with
`include_nearby: false`, or add towns `region.py` misses via `extra_cities`.

### Photos

`img.olx.com.br` returns **403 to any request carrying a `Referer`** that is
not olx.com.br — which is every request a browser makes from this dashboard,
so every thumbnail failed. Photos are therefore fetched server-side (no
`Referer` to send) into `data/imgcache/` and served from `/img/...`. The
scraper pre-warms that cache as it sweeps, so the grid paints from local disk
in ~1 ms instead of waiting on OLX per card.

---

## Install

```bash
git clone https://github.com/gabrfern99/gpu-radar.git
cd gpu-radar
pip3 install -r requirements.txt          # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
python3 scraper.py                        # first sweep, ~3 min
python3 app.py                            # http://127.0.0.1:5055
```

The first run writes `config.json` with every setting at its default and a
freshly generated ntfy topic. **That file is gitignored — the topic is a
secret.**

### Schedule it

```cron
*/20 * * * * /path/to/gpu-radar/run_scrape.sh
@reboot      /usr/bin/python3 /path/to/gpu-radar/app.py >> /path/to/gpu-radar/logs/flask.log 2>&1
```

A sweep is ~37 requests paced 3–6.5s apart, so it takes about three minutes.
`flock` in `run_scrape.sh` means a slow sweep can never overlap the next tick.

---

## Getting alerted

Channels are pluggable and independent; a dead one never blocks the others.

**ntfy** (default, zero setup) — install the [ntfy](https://ntfy.sh) app,
subscribe to the topic in your `config.json`, and alerts arrive with the
card's photo attached and a tap-through to the ad. `steal` sends at urgent
priority. The topic string is the only credential, so treat it like a
password.

**Desktop** (default) — `notify-send` on the local session.

Also available, off until configured: a Discord/Slack **webhook**, **Telegram**
(bot token + chat id), and **email** via local `msmtp`/`sendmail`.

```bash
python3 notify.py     # fire a realistic test alert through every live channel
```

Alerting is deduped by an `alerted` flag rather than by newness, so a bargain
that was already posted when the radar first saw it still reaches you —
exactly once. `max_alerts_per_run` (default 12) caps the blast radius if you
widen the catalog or drop the threshold.

---

## The dashboard

`http://127.0.0.1:5055` — dark, filterable, and it polls itself so it is
always showing the last sweep.

- The best in-budget find is pinned at the top.
- Filter by text, brand, city, price ceiling, minimum tier and deal class;
  toggle your shortlist, open-driver-only, the far ring, whole PCs, suspects
  and expired ads.
- Sort by deal score, recency, price, raw power, value or discount.
- Click any card for the full breakdown: price history sparkline, the model's
  observed min/avg/max, and every other listing of the same card in the region.
- Filters live in the URL hash, so any view you like is a bookmark.
- `/` focuses search, `r` runs a sweep, `a` opens the alert log, `Esc` closes.

---

## Tuning

Everything is in `config.json` (or `OLXGPU_*` env vars, which win):

| key | default | |
|---|---|---|
| `max_price` | `1500` | alert ceiling, R$ |
| `priority_max_price` | `1800` | ceiling for shortlisted cards |
| `priority_models` | `["rx6750xt","rx7600xt"]` | shortlist: bonus + deeper sweeps |
| `priority_bonus` | `6` | score bonus for the shortlist |
| `brand_bonus` | `{"AMD":8,"Intel":3,"NVIDIA":0}` | open-driver preference |
| `include_nearby` | `true` | include the ~120 km ring |
| `min_price` | `250` | below this a GPU ad is almost always bogus |
| `min_perf` | `40` | ignore anything weaker than an RX 580 |
| `alert_min_score` | `60` | score needed to fire |
| `home_city` | `Goiânia` | small scoring nudge |
| `extra_cities` | `[]` | towns `region.py` does not list |
| `strict_region` | `true` | enforce the allowlist at all |
| `broad_pages` | `6` | pages of the newest-in-region sweep |
| `delay_min` / `delay_max` | `3.0` / `6.5` | request pacing, seconds |

After editing weights in `scraper.py`, the catalog in `gpus.py`, or the
classifier, recompute everything from stored data without touching OLX:

```bash
python3 scraper.py --rescore
```

Handy while iterating:

```bash
python3 scraper.py --once "rx 6750"   # one query, printed, nothing stored
python3 scraper.py --no-alert         # full sweep, send nothing
```

---

## The catalog

`gpus.py` covers 47 models spanning roughly **tier 40 to 120** — RX 580 class
up to RX 6800 / RTX 5060 Ti class — centred on the RX 6750 XT, which is the
reference point at **tier 100**. Tiers are relative raster performance, not
benchmark scores. Each entry also carries its Linux driver stack.

Title matching is deliberately fussy, because sellers are not:

- `RX 6600 XT` never collapses into `RX 6600` — the longest match at a given
  position wins.
- `RX MSI 6750 XT` still resolves, via a bare-model-number fallback tier.
- `RX 5700 XT — troco por RX 6700/6750` is filed as the **5700 XT** it
  actually is, because the card being sold is named first.
- `Semi nova` does not read as `nova`.

---

## Notes

The dashboard ships the default view's data inlined in the HTML, so the grid
paints real cards on first load instead of flashing skeletons; a hash filter
falls through to a normal fetch. Static assets are served no-cache with a
`?v=<mtime>` buster — Flask's 12-hour default had browsers running stale JS.

Pages are server-rendered, so this reads the same HTML a browser would; there
is no private API involved. Requests are paced and retried with backoff — OLX
answers `403` when pushed, and clears within seconds. Be a good citizen and
leave the delays alone.

Reference medians are computed *after* each sweep's rows land, not before —
otherwise a fresh database scores everything against the catalog priors and
the first run's alerts are meaningless.

`db.init()` creates tables, backfills columns added by later versions, and
only then builds indexes — in that order, because an index over a
newly-added column fails if it runs before the `ALTER TABLE` that adds it.
Upgrading never means dropping the database and losing the learned medians.

Prices, tiers and priors reflect the Brazilian used market and will drift.
The medians self-correct; the priors will not.

---

## Other sources: what is actually reachable

Probed directly rather than assumed:

| Source | Result |
|---|---|
| **Kabum** | `catalog/v2/products` returns JSON with `price`, `old_price`, `discount_percentage`, `available`, `is_openbox` — usable as a **new-retail anchor** |
| **Mercado Livre** | anonymous requests redirect to `/gz/account-verification`, and the public API answers `403`. The way in is their **free developer API** with your own registered app — not scraping |
| **Pichau / Terabyte** | Cloudflare JS challenge. Defeating a bot protection is a different activity from reading a public page, so: no |
| **Enjoei / Shopee** | client-rendered shells, no prices in the HTML, and negligible GPU volume anyway |
| **Facebook Marketplace** | login-gated and aggressively anti-bot |

A new-retail anchor is worth having because it bounds the whole scale: an
RX 6600 at R$1.399 used against R$2.425 new is 58% of retail, which is normal.
It is what catches a used card priced *above* new — and there are plenty.
