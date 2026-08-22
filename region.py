"""
Geographic scope: Greater Goiânia + Anápolis.

OLX's own region filter (`estado-go/grande-goiania-e-anapolis`) narrows the
result set but still pads it with listings from far-off cities — Uruaçu,
Cocalzinho and Nova Glória all showed up in testing. So we filter a second
time against this explicit allowlist: the 21 municipalities of the Região
Metropolitana de Goiânia, plus Anápolis.

Widen it without touching this file by adding "extra_cities" to config.json.
"""

import re
import unicodedata

# We fetch the whole state and filter locally. OLX's own
# "grande-goiania-e-anapolis" filter both leaks distant cities *and* hides the
# second ring below, so it is the wrong tool in both directions — doing the
# geography ourselves is the only way to control the radius.
OLX_REGION_PATH = "estado-go"

# Região Metropolitana de Goiânia
RM_GOIANIA = [
    "Abadia de Goiás", "Aparecida de Goiânia", "Aragoiânia", "Bela Vista de Goiás",
    "Bonfinópolis", "Brazabrantes", "Caldazinha", "Caturaí", "Damolândia",
    "Goianápolis", "Goiânia", "Goianira", "Guapó", "Hidrolândia", "Inhumas",
    "Nerópolis", "Nova Veneza", "Santo Antônio de Goiás", "Senador Canedo",
    "Terezópolis de Goiás", "Trindade",
]

ANAPOLIS = ["Anápolis"]

# Second ring: everything else within roughly 120 km of Goiânia. Worth a drive
# for a real bargain, but not a casual trip — scored slightly lower for it.
NEARBY = [
    "Anicuns", "Avelinópolis", "Campestre de Goiás", "Cezarina", "Cromínia",
    "Firminópolis", "Gameleira de Goiás", "Heitoraí", "Itaberaí", "Itaguari",
    "Itaguaru", "Jaraguá", "Leopoldo de Bulhões", "Nazário", "Orizona",
    "Ouro Verde de Goiás", "Palmeiras de Goiás", "Petrolina de Goiás",
    "Piracanjuba", "Pirenópolis", "Pontalina", "Professor Jamil",
    "Santa Bárbara de Goiás", "São Francisco de Goiás", "São Luís de Montes Belos",
    "Silvânia", "Taquaral de Goiás", "Turvânia", "Uruana", "Varjão",
    "Vianópolis",
]

ALLOWED = RM_GOIANIA + ANAPOLIS + NEARBY

# Close enough to go see it after work and be home for dinner — this is a
# small scoring nudge, not a filter.
CLOSE = ["Goiânia", "Aparecida de Goiânia", "Senador Canedo", "Trindade",
         "Goianira", "Abadia de Goiás"]


def fold(s: str) -> str:
    """Lowercase, de-accent, squeeze whitespace — for comparing city names."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return re.sub(r"\s+", " ", s).strip()


_ALLOWED_FOLDED = {fold(c) for c in ALLOWED}
_CLOSE_FOLDED = {fold(c) for c in CLOSE}
_CANON = {fold(c): c for c in ALLOWED}
_METRO_FOLDED = {fold(c) for c in RM_GOIANIA}
_ANAPOLIS_FOLDED = {fold(c) for c in ANAPOLIS}
_NEARBY_FOLDED = {fold(c) for c in NEARBY}


def city_of(location: str, extra: list[str] | None = None) -> str | None:
    """
    OLX renders locations as "City, Neighborhood" or just "City".
    Returns the canonical city name when it is one we cover, else None.
    """
    if not location:
        return None
    head = location.split(",")[0].strip()
    canon = _CANON.get(fold(head))
    if canon:
        return canon
    # a city added via config.extra_cities keeps whatever spelling OLX used
    if extra and fold(head) in {fold(c) for c in extra}:
        return head
    return None


def band_of(location_or_city: str) -> str | None:
    """
    How far out a listing is: "metro" (RM Goiânia), "anapolis", or "nearby"
    (the ~120 km second ring). None means outside everything we cover.
    """
    if not location_or_city:
        return None
    head = fold(location_or_city.split(",")[0])
    if head in _METRO_FOLDED:
        return "metro"
    if head in _ANAPOLIS_FOLDED:
        return "anapolis"
    if head in _NEARBY_FOLDED:
        return "nearby"
    return None


def in_region(location: str, extra: list[str] | None = None,
              include_nearby: bool = True) -> bool:
    if not location:
        return False
    head = fold(location.split(",")[0])
    if head in _METRO_FOLDED or head in _ANAPOLIS_FOLDED:
        return True
    if include_nearby and head in _NEARBY_FOLDED:
        return True
    return head in {fold(c) for c in (extra or [])}


def is_close(city: str | None) -> bool:
    return bool(city) and fold(city) in _CLOSE_FOLDED
