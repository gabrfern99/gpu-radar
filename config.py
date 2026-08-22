"""
Settings resolution order (last wins):
    defaults here  ->  config.json in this folder  ->  OLXGPU_* env vars
Anything in config.json is written back on first run so the file is
self-documenting once it exists.
"""

import json
import os
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DB_PATH = ROOT / "data" / "olx.db"
CONFIG_PATH = ROOT / "config.json"

DEFAULTS = {
    # ---------------------------------------------------------- what to buy
    "max_price": 1500,          # R$ — hard ceiling for an alert
    "min_price": 250,           # below this a GPU listing is almost always bogus
    "min_perf": 40,             # ignore anything weaker than ~RX 580
    "alert_min_score": 60,      # deal_score needed to fire an alert
    "alert_suspect": False,     # also alert on suspiciously-cheap listings
    "alert_combos": False,      # also alert on whole-PC listings
    "home_city": "Goiânia",     # closest city — small scoring nudge
    "extra_cities": [],         # add cities region.py does not list, e.g. ["Itumbiara"]
    "include_nearby": True,     # include the ~120 km second ring (region.NEARBY)
    "strict_region": True,      # drop anything outside the allowlist entirely

    # ---------------------------------------------------------- preferences
    # You run Linux, so AMD's in-kernel/Mesa driver is worth real points:
    # nothing to install, no DKMS rebuild on kernel updates, Wayland just
    # works. Intel Arc is open too but the stack is less mature. NVIDIA is
    # not penalised — it simply earns no bonus.
    "brand_bonus": {"AMD": 8, "Intel": 3, "NVIDIA": 0},

    # Cards you specifically want. They get a score bonus and are swept more
    # deeply than the rest of the catalog.
    "priority_models": ["rx6750xt", "rx7600xt"],
    "priority_bonus": 6,
    # Shortlisted cards get a little headroom over max_price, because a good
    # RX 6750 XT locally starts around R$1800 and you asked to hear about
    # those specifically. Set this equal to max_price to switch it off.
    "priority_max_price": 1800,

    # ---------------------------------------------------------- how to fetch
    # Scope is Greater Goiânia + Anápolis. OLX's region filter gets us most of
    # the way; region.py's city allowlist enforces the rest (see strict_region).
    # No category filter: the local pool is small and plenty of cards get
    # posted outside "informatica/placas-de-video", so we cast wide and let
    # the title matcher decide.
    # State-wide fetch, filtered locally by region.py. OLX's own
    # "grande-goiania-e-anapolis" filter leaks distant cities *and* hides the
    # nearby ring, so we do the geography ourselves.
    "region_path": "estado-go",
    "category_path": "",
    "broad_pages": 6,           # pages of the newest-in-region sweep
    "targeted_pages": 1,        # pages per model query
    "delay_min": 3.0,           # polite pacing between requests (seconds)
    "delay_max": 6.5,
    "timeout": 30,
    "retries": 3,
    "stale_days": 45,           # listings unseen this long are pruned

    # -------------------------------------------------------------- alerting
    # ntfy is the zero-setup default: install the ntfy app, subscribe to the
    # topic below, and pushes land on your phone. The topic is your password —
    # it is generated once and kept private in config.json.
    "notify_ntfy": True,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",           # auto-generated on first run
    "notify_desktop": True,     # notify-send on the local session
    "notify_webhook": "",       # Discord/Slack-compatible incoming webhook
    "notify_email": "",         # sent via the local msmtp/sendmail
    "notify_telegram_token": "",
    "notify_telegram_chat": "",
    "max_alerts_per_run": 12,

    # ------------------------------------------------------------------- web
    "host": "127.0.0.1",
    "port": 5055,
}


def _load_file() -> dict:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            print(f"[config] {CONFIG_PATH.name} is not valid JSON ({e}); ignoring it")
    return {}


def _coerce(default, raw: str):
    if isinstance(default, bool):
        return raw.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(default, int):
        return int(float(raw))
    if isinstance(default, float):
        return float(raw)
    if isinstance(default, list):
        return [x.strip() for x in raw.split(",") if x.strip()]
    if isinstance(default, dict):
        return json.loads(raw)
    return raw


def load() -> dict:
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in _load_file().items() if k in DEFAULTS})
    for key, default in DEFAULTS.items():
        env = os.environ.get("OLXGPU_" + key.upper())
        if env is not None:
            try:
                cfg[key] = _coerce(default, env)
            except ValueError:
                print(f"[config] ignoring bad env value for {key}: {env!r}")
    if not cfg["ntfy_topic"]:
        cfg["ntfy_topic"] = "gpuradar-" + secrets.token_urlsafe(9).replace("-", "").replace("_", "")
    save(cfg)
    return cfg


def save(cfg: dict) -> None:
    """Persist the resolved settings so config.json always shows every knob."""
    body = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(body, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(CONFIG_PATH)


CFG = load()
