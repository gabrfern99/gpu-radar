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

OLX_REGION_PATH = "estado-go/grande-goiania-e-anapolis"

# Região Metropolitana de Goiânia
RM_GOIANIA = [
    "Abadia de Goiás", "Aparecida de Goiânia", "Aragoiânia", "Bela Vista de Goiás",
    "Bonfinópolis", "Brazabrantes", "Caldazinha", "Caturaí", "Damolândia",
    "Goianápolis", "Goiânia", "Goianira", "Guapó", "Hidrolândia", "Inhumas",
    "Nerópolis", "Nova Veneza", "Santo Antônio de Goiás", "Senador Canedo",
    "Terezópolis de Goiás", "Trindade",
]

ANAPOLIS = ["Anápolis"]

ALLOWED = RM_GOIANIA + ANAPOLIS

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


def city_of(location: str) -> str | None:
    """
    OLX renders locations as "City, Neighborhood" or just "City".
    Returns the canonical city name when it is one we cover, else None.
    """
    if not location:
        return None
    return _CANON.get(fold(location.split(",")[0]))


def in_region(location: str, extra: list[str] | None = None) -> bool:
    if not location:
        return False
    head = fold(location.split(",")[0])
    if head in _ALLOWED_FOLDED:
        return True
    return head in {fold(c) for c in (extra or [])}


def is_close(city: str | None) -> bool:
    return bool(city) and fold(city) in _CLOSE_FOLDED
