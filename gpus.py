"""
GPU catalog for the OLX radar.

`perf` is a relative rasterization score with **RX 6750 XT = 100**, roughly
aligned with 1080p/1440p aggregate benchmark tiers. It is a tier indicator,
not a precise benchmark.

`fair` is a *prior* for the Brazilian used-market price in R$ (mid-2026-ish).
It is only used until the database has enough observations for that model, at
which point the observed median takes over (see scraper.reference_price).

`pats` are regexes matched against a normalized title. The list is ordered
**most specific first** — the first hit wins, so "RX 6600 XT" can never be
mistaken for a plain "RX 6600".
"""

import re
import unicodedata

# perf band we care about: roughly "around an RX 6750 XT", above and below.
# ~40 (RX 580 class) up to ~120 (RX 6800 / RTX 5060 Ti class).

MODELS = [
    # ---------------------------------------------------------------- AMD ---
    dict(key="rx7700xt",  name="RX 7700 XT",   brand="AMD", vram=12, perf=115, fair=2400,
         pats=[r"\brx\s*7700\s*xt\b", r"\brx\s*7700\b"]),
    dict(key="rx6800",    name="RX 6800",      brand="AMD", vram=16, perf=120, fair=2400,
         pats=[r"\brx\s*6800\b(?!\s*xt)"]),
    dict(key="rx9060xt",  name="RX 9060 XT",   brand="AMD", vram=16, perf=108, fair=2600,
         pats=[r"\brx\s*9060\s*xt\b", r"\brx\s*9060\b"]),
    dict(key="rx6750xt",  name="RX 6750 XT",   brand="AMD", vram=12, perf=100, fair=1950,
         pats=[r"\brx\s*6750\s*xt\b", r"\brx\s*6750\b"]),
    dict(key="rx6700xt",  name="RX 6700 XT",   brand="AMD", vram=12, perf=96,  fair=1750,
         pats=[r"\brx\s*6700\s*xt\b"]),
    dict(key="rx7600xt",  name="RX 7600 XT",   brand="AMD", vram=16, perf=88,  fair=1900,
         pats=[r"\brx\s*7600\s*xt\b"]),
    dict(key="rx6700",    name="RX 6700 10GB", brand="AMD", vram=10, perf=88,  fair=1500,
         pats=[r"\brx\s*6700\b"]),
    dict(key="rx7600",    name="RX 7600",      brand="AMD", vram=8,  perf=85,  fair=1500,
         pats=[r"\brx\s*7600\b"]),
    dict(key="rx6650xt",  name="RX 6650 XT",   brand="AMD", vram=8,  perf=78,  fair=1350,
         pats=[r"\brx\s*6650\s*xt\b", r"\brx\s*6650\b"]),
    dict(key="rx6600xt",  name="RX 6600 XT",   brand="AMD", vram=8,  perf=74,  fair=1250,
         pats=[r"\brx\s*6600\s*xt\b"]),
    dict(key="rx5700xt",  name="RX 5700 XT",   brand="AMD", vram=8,  perf=72,  fair=1150,
         pats=[r"\brx\s*5700\s*xt\b"]),
    dict(key="rx6600",    name="RX 6600",      brand="AMD", vram=8,  perf=65,  fair=1050,
         pats=[r"\brx\s*6600\b"]),
    dict(key="rx5700",    name="RX 5700",      brand="AMD", vram=8,  perf=65,  fair=950,
         pats=[r"\brx\s*5700\b"]),
    dict(key="rx5600xt",  name="RX 5600 XT",   brand="AMD", vram=6,  perf=58,  fair=800,
         pats=[r"\brx\s*5600\s*xt\b", r"\brx\s*5600\b"]),
    dict(key="vega64",    name="RX Vega 64",   brand="AMD", vram=8,  perf=55,  fair=850,
         pats=[r"\bvega\s*64\b"]),
    dict(key="vega56",    name="RX Vega 56",   brand="AMD", vram=8,  perf=50,  fair=700,
         pats=[r"\bvega\s*56\b"]),
    dict(key="rx6500xt",  name="RX 6500 XT",   brand="AMD", vram=4,  perf=42,  fair=700,
         pats=[r"\brx\s*6500\s*xt\b", r"\brx\s*6500\b"]),
    dict(key="rx590",     name="RX 590",       brand="AMD", vram=8,  perf=42,  fair=550,
         pats=[r"\brx\s*590\b"]),
    dict(key="rx580",     name="RX 580 8GB",   brand="AMD", vram=8,  perf=38,  fair=450,
         pats=[r"\brx\s*580\b"]),

    # ------------------------------------------------------------- NVIDIA ---
    dict(key="rtx5060ti", name="RTX 5060 Ti",  brand="NVIDIA", vram=16, perf=110, fair=2900,
         pats=[r"\brtx\s*5060\s*ti\b"]),
    dict(key="rtx3070ti", name="RTX 3070 Ti",  brand="NVIDIA", vram=8,  perf=107, fair=2200,
         pats=[r"\brtx\s*3070\s*ti\b"]),
    dict(key="rtx3070",   name="RTX 3070",     brand="NVIDIA", vram=8,  perf=100, fair=1900,
         pats=[r"\brtx\s*3070\b"]),
    dict(key="rtx5060",   name="RTX 5060",     brand="NVIDIA", vram=8,  perf=95,  fair=2100,
         pats=[r"\brtx\s*5060\b"]),
    dict(key="rtx2080ti", name="RTX 2080 Ti",  brand="NVIDIA", vram=11, perf=105, fair=2300,
         pats=[r"\brtx\s*2080\s*ti\b"]),
    dict(key="rtx2080s",  name="RTX 2080 Super", brand="NVIDIA", vram=8, perf=95, fair=1750,
         pats=[r"\brtx\s*2080\s*super\b"]),
    dict(key="rtx4060ti", name="RTX 4060 Ti",  brand="NVIDIA", vram=8,  perf=92,  fair=2300,
         pats=[r"\brtx\s*4060\s*ti\b"]),
    dict(key="rtx2080",   name="RTX 2080",     brand="NVIDIA", vram=8,  perf=90,  fair=1600,
         pats=[r"\brtx\s*2080\b(?!\s*(?:ti|super))"]),
    dict(key="gtx1080ti", name="GTX 1080 Ti",  brand="NVIDIA", vram=11, perf=90,  fair=1300,
         pats=[r"\bgtx\s*1080\s*ti\b"]),
    dict(key="rtx3060ti", name="RTX 3060 Ti",  brand="NVIDIA", vram=8,  perf=85,  fair=1700,
         pats=[r"\brtx\s*3060\s*ti\b"]),
    dict(key="rtx2070s",  name="RTX 2070 Super", brand="NVIDIA", vram=8, perf=84, fair=1400,
         pats=[r"\brtx\s*2070\s*super\b"]),
    dict(key="rtx4060",   name="RTX 4060",     brand="NVIDIA", vram=8,  perf=80,  fair=1700,
         pats=[r"\brtx\s*4060\b"]),
    dict(key="rtx2070",   name="RTX 2070",     brand="NVIDIA", vram=8,  perf=76,  fair=1200,
         pats=[r"\brtx\s*2070\b"]),
    dict(key="gtx1080",   name="GTX 1080",     brand="NVIDIA", vram=8,  perf=72,  fair=950,
         pats=[r"\bgtx\s*1080\b"]),
    dict(key="rtx2060s",  name="RTX 2060 Super", brand="NVIDIA", vram=8, perf=72, fair=1100,
         pats=[r"\brtx\s*2060\s*super\b"]),
    dict(key="rtx2060x",  name="RTX 2060 12GB", brand="NVIDIA", vram=12, perf=70, fair=1050,
         pats=[r"\brtx\s*2060\s*12\s*gb\b"]),
    dict(key="rtx3060",   name="RTX 3060 12GB", brand="NVIDIA", vram=12, perf=68, fair=1350,
         pats=[r"\brtx\s*3060\b"]),
    dict(key="gtx1070ti", name="GTX 1070 Ti",  brand="NVIDIA", vram=8,  perf=65,  fair=800,
         pats=[r"\bgtx\s*1070\s*ti\b"]),
    dict(key="rtx2060",   name="RTX 2060",     brand="NVIDIA", vram=6,  perf=63,  fair=900,
         pats=[r"\brtx\s*2060\b"]),
    dict(key="gtx1070",   name="GTX 1070",     brand="NVIDIA", vram=8,  perf=58,  fair=700,
         pats=[r"\bgtx\s*1070\b"]),
    dict(key="gtx1660ti", name="GTX 1660 Ti",  brand="NVIDIA", vram=6,  perf=57,  fair=720,
         pats=[r"\bgtx\s*1660\s*ti\b"]),
    dict(key="gtx1660s",  name="GTX 1660 Super", brand="NVIDIA", vram=6, perf=55, fair=700,
         pats=[r"\bgtx\s*1660\s*super\b"]),
    dict(key="rtx3050",   name="RTX 3050 8GB", brand="NVIDIA", vram=8,  perf=50,  fair=900,
         pats=[r"\brtx\s*3050\b"]),
    dict(key="gtx1660",   name="GTX 1660",     brand="NVIDIA", vram=6,  perf=50,  fair=600,
         pats=[r"\bgtx\s*1660\b"]),
    dict(key="gtx1650s",  name="GTX 1650 Super", brand="NVIDIA", vram=4, perf=45, fair=500,
         pats=[r"\bgtx\s*1650\s*super\b"]),

    # --------------------------------------------------------------- INTEL --
    dict(key="arcb580",   name="Arc B580",     brand="Intel", vram=12, perf=92,  fair=1800,
         pats=[r"\barc\s*b\s*580\b", r"\bb580\b"]),
    dict(key="arca770",   name="Arc A770 16GB", brand="Intel", vram=16, perf=85, fair=1400,
         pats=[r"\barc\s*a\s*770\b", r"\ba770\b"]),
    dict(key="arca750",   name="Arc A750",     brand="Intel", vram=8,  perf=78,  fair=1000,
         pats=[r"\barc\s*a\s*750\b", r"\ba750\b"]),
]

# On Linux the driver story is a real part of the buying decision, so carry it
# in the catalog rather than leaving it implicit.
LINUX_DRIVER = {
    "AMD":    {"stack": "amdgpu + Mesa", "open": True,
               "note": "in-kernel driver, nothing to install, Wayland fine"},
    "Intel":  {"stack": "Xe/i915 + Mesa", "open": True,
               "note": "open stack, less mature; older titles can be rough"},
    "NVIDIA": {"stack": "nvidia proprietary", "open": False,
               "note": "out-of-tree module, DKMS rebuild on kernel updates"},
}

for _m in MODELS:
    _m["linux"] = LINUX_DRIVER[_m["brand"]]

BY_KEY = {m["key"]: m for m in MODELS}

# Compiled in catalog order, so within one starting position the longer
# ("RX 6600 XT") alternative beats the shorter ("RX 6600").
_COMPILED = [(m, re.compile("|".join(m["pats"]))) for m in MODELS]

# Fallback tier: the bare model number, no brand prefix. Sellers routinely
# write "RX MSI 6750 XT" or "Placa 3060 12GB", which the branded patterns miss.
# Only consulted when nothing branded matched AND the title actually talks
# about a graphics card — otherwise a stray four-digit number turns a
# CPU-and-motherboard kit into an RX 6700.
_PREFIX_RX = re.compile(r"\\b(?:rx|rtx|gtx|arc)\\s\*")

# Words that mean "this ad is about a graphics card", required before the
# bare-number fallback is allowed to fire.
_GPU_CONTEXT = re.compile(
    r"\bplaca\s*de\s*v[i]deo\b|\bvga\b|\bgpu\b|\bgeforce\b|\bradeon\b"
    r"|\bnvidia\b|\bamd\b|\bplaca\s*v[i]deo\b|\bgraphics\b"
    r"|\b(?:asus|sapphire|powercolor|xfx|gigabyte|msi|zotac|galax|evga|pny|"
    r"inno3d|palit|gainward|colorful|asrock|biostar)\b")
_NUMBERED = [
    (m, re.compile("|".join(_PREFIX_RX.sub("", p) for p in m["pats"])))
    for m in MODELS
]

# Search terms sent to OLX. Deliberately coarse: OLX fuzzy-matches, so
# "rx 6600" also surfaces 6600 XT / 6650 XT listings, and the title matcher
# above sorts out what each result actually is.
# Broad sweeps, newest-first. The AMD-specific ones are deliberately generic
# so oddly-titled Radeon ads still surface.
BROAD_QUERIES = ["placa de video", "placa de video gamer"]
AMD_QUERIES = ["radeon", "placa de video amd", "placa de video radeon"]

# Cards worth searching harder for (see config.priority_models).
PRIORITY_QUERIES = ["rx 6750 xt", "rx 7600 xt"]

QUERIES = [
    # --- AMD first: the open amdgpu/Mesa stack is the reason to prefer them.
    "rx 6750", "rx 6700", "rx 6650", "rx 6600", "rx 7600", "rx 7700 xt",
    "rx 6800", "rx 9060", "rx 5700", "rx 5600 xt", "rx 6500 xt",
    "rx 590", "rx 580", "rx vega",
    # --- Intel Arc: also open-source drivers.
    "arc a750", "arc a770", "arc b580",
    # --- NVIDIA: proprietary driver, still worth watching for a real steal.
    "rtx 3070", "rtx 3060", "rtx 3050", "rtx 2080", "rtx 2070", "rtx 2060",
    "rtx 4060", "rtx 5060", "gtx 1080", "gtx 1070", "gtx 1660", "gtx 1650 super",
]

# ---------------------------------------------------------------------------
# title analysis
# ---------------------------------------------------------------------------

_SPLIT_MODEL = re.compile(r"\b(rx|rtx|gtx|gt)\s*(\d{3,4})\b")

def normalize(title: str) -> str:
    """lowercase, de-accent, glue 'rtx3060' -> 'rtx 3060', collapse spaces."""
    t = unicodedata.normalize("NFKD", title or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\b(rx|rtx|gtx|gt)(\d{3,4})\b", r"\1 \2", t)
    t = re.sub(r"\b(\d{3,4})(xt|ti)\b", r"\1 \2", t)
    return re.sub(r"\s+", " ", t).strip()


def _hits(normalized: str, table):
    out = []
    for model, rx in table:
        for m in rx.finditer(normalized):
            # earliest mention first, then the longest (most specific) match
            out.append((m.start(), -(m.end() - m.start()), model))
    return out


def match_model(title: str):
    """
    Return the catalog entry for `title`, or None.

    The card being sold is almost always named first, so we pick the
    earliest mention. That keeps "RX 5700 XT - troco por RX 6700/6750"
    filed as the 5700 XT it actually is.
    """
    n = normalize(title)
    hits = _hits(n, _COMPILED)
    if not hits and _GPU_CONTEXT.search(n):
        hits = _hits(n, _NUMBERED)
    if not hits:
        return None
    hits.sort(key=lambda h: (h[0], h[1]))
    return hits[0][2]


# ---------------------------------------------------------------------------
# what kind of ad is this?
# ---------------------------------------------------------------------------
#
# A graphics card gets named in three very different kinds of ad, and mixing
# them corrupts the market reference price — three gaming PCs in the GTX 1070
# bucket once dragged its "median" up to R$3000. So classify first:
#
#   gpu     a bare card for sale            -> priced, scored, alerted
#   combo   a whole PC/laptop containing it -> shown with a badge, kept out
#                                              of the medians
#   wanted  someone looking to buy one      -> dropped
#   other   a phone/console whose seller     -> dropped
#           merely accepts a GPU in trade

# A CPU in the title means we are looking at a whole machine.
CPU_PAT = (r"\bryzen\b|\bcore\s*i\s*[3579]\b|\bi[3579]\b|\bxeon\b|\bpentium\b"
           r"|\bceleron\b|\bathlon\b|\br[357]\s*\d{4}\b|\bfx\s*\d{4}\b")

# Whole-system words. Deliberately excludes GPU brand lines that would
# otherwise read as laptops — TUF, ROG, Aorus, Strix and Ventus are all
# graphics-card families.
SYSTEM_PAT = (r"\bpc\b|\bcpu\b|\bcomputador\b|\bdesktop\b|\bworkstation\b"
              r"|\bnote\b|\bnotebook\b|\blaptop\b|\ball\s*in\s*one\b|\baio\b"
              r"|\bmonitores\b|\bgabinete\s*completo\b|\bsetup\s*completo\b"
              r"|\bprocessador\b|\bplaca\s*mae\b|\bkit\s*(?:processador|placa|upgrade)\b"
              r"|\bmemoria\s*ram\b|\bpente\s*de\s*memoria\b"
              r"|\bdell\s*(?:g\d|xps|inspiron|optiplex|vostro|latitude|precision|alienware)\b"
              r"|\blegion\b|\bloq\b|\bideapad\b|\bnitro\s*5\b|\bvictus\b|\baspire\b"
              r"|\bvivobook\b|\bthinkpad\b|\bmacbook\b|\bpredator\b|\bkatana\b")

# Selling the packaging, a bracket, or a cooler — not the card. These name a
# GPU model in full and read exactly like a bargain on price alone.
ACCESSORY_PAT = (r"\bcaixa\s*vazia\b|\bs[o]\s*(?:a\s*)?caixa\b"
                 r"|\bapenas\s*(?:a\s*)?caixa\b|\bsomente\s*(?:a\s*)?caixa\b"
                 r"|\bcaixa\s*(?:da|do)\s*(?:rx|rtx|gtx|placa)\b"
                 r"|\bsem\s*a\s*placa\b|\bvendo\s*(?:a\s*)?caixa\b"
                 r"|\bbackplate\b|\briser\b|\bsuporte\s*(?:para|de)\s*(?:placa|gpu|vga)\b"
                 r"|\bapenas\s*o\s*cooler\b|\bs[o]\s*o\s*cooler\b"
                 r"|\bcooler\s*(?:da|do|para)\s*(?:rx|rtx|gtx|placa)\b"
                 r"|\bwaterblock\b|\bmanual\b|\badesivo\b")

# The seller's actual product is something else; the GPU is only a trade offer.
OTHER_PAT = (r"\biphone\b|\bplaystation\b|\bps[45]\b|\bxbox\b|\bnintendo\b"
             r"|\bswitch\s*(?:oled|lite)\b|\bsmartphone\b|\bcelular\b"
             r"|\bgalaxy\s*s\s*\d|\bredmi\b|\bmoto\s*g\b|\bsmart\s*tv\b")

WANTED_PAT = r"\bprocuro\b|\bcompro\b|\bquero\s*comprar\b|\bpago\s*ate\b|\bpreciso\s*de\b"

FLAG_PATS = [
    (r"\bmineracao\b|\bminerada\b|\bminerado\b|\bmining\b|\bmineirada\b", "mining"),
    (r"\bdefeito\b|\bqueimada\b|\bnao\s*funciona\b|\bsucata\b|\bpara\s*reparo\b"
     r"|\bconserto\b|\bpara\s*conserto\b|\bcom\s*problema\b|\bsem\s*imagem\b"
     r"|\bartefato", "broken"),
    (r"(?<!semi )\bnova\b|(?<!semi )\bnovo\b|\blacrad|\bna\s*caixa\b|\bselad", "new"),
    (r"\bsemi\s*nova?\b|\busada?\b|\bseminova?\b", "used"),
    (r"\bgarantia\b", "warranty"),
    (r"\bnota\s*fiscal\b|\bcom\s*nf\b", "invoice"),
    (r"\btroco\b|\btroca\b|\baceito\s*troca", "trade"),
    (r"\baluguel\b|\balugo\b", "rental"),
    (r"\bwater\s*cooler\b|\bwatercooler\b|\bfonte\b", "bundle"),
]

_ACCESSORY = re.compile(ACCESSORY_PAT)
_CPU = re.compile(CPU_PAT)
_SYSTEM = re.compile(SYSTEM_PAT)
_OTHER = re.compile(OTHER_PAT)
_WANTED = re.compile(WANTED_PAT)
_FLAGS = [(re.compile(p), k) for p, k in FLAG_PATS]

# "12gb", "12 gb", "12g" — used to spot cut-down variants like an RTX 3050 6GB
# being sold under the same name as the 8GB card.
_VRAM = re.compile(r"\b(\d{1,2})\s*g(?:b)?\b")


def title_vram(normalized: str) -> int | None:
    """Smallest plausible VRAM figure stated in the title, if any."""
    vals = [int(v) for v in _VRAM.findall(normalized) if 1 <= int(v) <= 32]
    return min(vals) if vals else None


def classify(normalized: str) -> str:
    if _ACCESSORY.search(normalized):
        return "accessory"
    if _WANTED.search(normalized):
        return "wanted"
    if _OTHER.search(normalized):
        return "other"
    if _CPU.search(normalized) or _SYSTEM.search(normalized):
        return "combo"
    return "gpu"


def effective_vram(title: str, model: dict) -> int:
    """
    The memory the card in *this ad* actually has.

    Cut-down variants get sold under the full model's name — an RTX 3050 6GB,
    or the Jieshuo RTX 3060 with 6GB instead of 12. Scoring those on the
    catalog's nominal figure would hand them a memory bonus they have not
    earned, so believe the smaller number when the title states one.
    """
    tv = title_vram(normalize(title))
    if tv and tv < model["vram"]:
        return tv
    return model["vram"]


def inspect_text(text: str):
    """
    (kind, flags) for a free-text blob — a listing's full description.

    Same rules as analyze(), minus the model matching: a description mentions
    other cards in passing ("melhor que a 3060") and we do not want that to
    override the model the title established.
    """
    n = normalize(text)
    return classify(n), [k for rx, k in _FLAGS if rx.search(n)]


def analyze(title: str):
    """(model|None, kind, [flags]) for a listing title."""
    n = normalize(title)
    model = match_model(title)
    kind = classify(n)
    flags = [k for rx, k in _FLAGS if rx.search(n)]

    # A card advertised with less memory than the model normally ships is a
    # cut-down variant (RTX 3050 6GB, the Jieshuo RTX 3060 6GB) and is worth
    # meaningfully less than its name suggests.
    if model:
        tv = title_vram(n)
        if tv and tv < model["vram"]:
            flags.append("less_vram")
    return model, kind, flags
