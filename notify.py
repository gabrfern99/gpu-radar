"""
Alerting. Every channel is optional and failures never abort a sweep.

ntfy (default)
    Install the ntfy app (Android/iOS/web), subscribe to the topic printed by
    `python3 scraper.py` or shown in the web UI, and pushes arrive on your
    phone with the card's photo attached and a tap-through to the OLX ad.
    The topic string is the only secret — keep config.json private.

desktop     notify-send on the local X/Wayland session
webhook     Discord (…/api/webhooks/…) or Slack incoming webhook
email       handed to the local msmtp/sendmail
telegram    bot token + chat id
"""

import html
import json
import shutil
import subprocess
import urllib.parse
from datetime import datetime, timezone

import requests

import db
from config import CFG

DISABLED = False           # set by scraper --no-alert

CLASS_EMOJI = {"steal": "🔥", "great": "⚡", "good": "✅", "fair": "•", "meh": "·"}
CLASS_LABEL = {"steal": "STEAL", "great": "GREAT DEAL", "good": "GOOD",
               "fair": "FAIR", "meh": "MEH"}


def brl(v) -> str:
    if v is None:
        return "—"
    return "R$ " + f"{int(v):,}".replace(",", ".")


def headline(c: dict) -> str:
    return f"{CLASS_EMOJI.get(c['deal_class'], '•')} {c['model_name']} — {brl(c['price'])}"


def body(c: dict) -> str:
    off = int(round(c["discount"] * 100))
    lines = [
        c["title"][:110],
        f"{brl(c['price'])}  ({off:+d}% vs {brl(c.get('clearing_price') or c['reference_price'])}"
        f" {'real sale price' if c.get('clearing_price') else 'typical ask'})",
        f"{c['model_name']} · {c['vram']}GB · tier {int(c['perf'])} "
        f"· {c['perf_per_1k']:.0f} pts/R$1k",
        f"score {c['deal_score']:.0f}/100 — {CLASS_LABEL.get(c['deal_class'], '')}",
        f"{c.get('city') or c.get('location') or '?'} · {c.get('date_text') or ''}",
    ]
    extra = [f for f in c.get("flags", []) if f in
             ("mining", "new", "used", "warranty", "invoice", "bundle")]
    if extra:
        lines.append("flags: " + ", ".join(extra))
    if c.get("suspect"):
        lines.append("⚠ suspiciously cheap — verify before paying anything")
    if c.get("kind") == "combo":
        lines.append("ℹ this ad is a whole PC, not just the card")
    if c.get("expected_hours"):
        h = c["expected_hours"]
        when = f"{h:.0f}h" if h < 48 else f"{h/24:.0f} days"
        lines.append(f"⏱ ads at this price usually last ~{when} — move fast")
    if c.get("_cautions"):
        lines.append("⚠ " + "; ".join(c["_cautions"][:3]))
    if c.get("photo_count") is not None:
        lines.append(f"{c['photo_count']} photo(s) on the ad")
    if c.get("_reason"):
        lines.append(f"trigger: {c['_reason']}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# channels — each returns True on success
# ---------------------------------------------------------------------------

def _ntfy(c: dict) -> bool:
    topic = CFG["ntfy_topic"]
    if not topic:
        return False
    prio = {"steal": "urgent", "great": "high"}.get(c["deal_class"], "default")
    tags = {"steal": "fire,money_with_wings", "great": "zap"}.get(c["deal_class"], "computer")
    hdr = {
        "Title": headline(c).encode("utf-8"),
        "Priority": prio,
        "Tags": tags,
        "Click": c["url"],
        "Actions": f"view, Open on OLX, {c['url']}, clear=true".encode("utf-8"),
        "Markdown": "no",
    }
    if c.get("image"):
        hdr["Attach"] = c["image"]
    r = requests.post(f"{CFG['ntfy_server'].rstrip('/')}/{topic}",
                      data=body(c).encode("utf-8"), headers=hdr, timeout=15)
    r.raise_for_status()
    return True


def _desktop(c: dict) -> bool:
    if not shutil.which("notify-send"):
        return False
    urgency = "critical" if c["deal_class"] == "steal" else "normal"
    subprocess.run(
        ["notify-send", "-u", urgency, "-a", "GPU Radar",
         "-h", f"string:desktop-entry:gpuradar",
         headline(c), html.escape(body(c))],
        check=True, timeout=15,
    )
    return True


def _webhook(c: dict) -> bool:
    url = CFG["notify_webhook"]
    if not url:
        return False
    colour = {"steal": 0xFF3B30, "great": 0xFF9500,
              "good": 0x34C759}.get(c["deal_class"], 0x8E8E93)
    if "slack.com" in url:
        payload = {"text": f"*{headline(c)}*\n{body(c)}\n{c['url']}"}
    else:  # Discord
        payload = {"embeds": [{
            "title": headline(c)[:250],
            "url": c["url"],
            "description": body(c)[:2000],
            "color": colour,
            "thumbnail": {"url": c["image"]} if c.get("image") else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }]}
        payload["embeds"][0] = {k: v for k, v in payload["embeds"][0].items() if v is not None}
    r = requests.post(url, json=payload, timeout=15)
    r.raise_for_status()
    return True


def _telegram(c: dict) -> bool:
    token, chat = CFG["notify_telegram_token"], CFG["notify_telegram_chat"]
    if not (token and chat):
        return False
    text = f"*{headline(c)}*\n{body(c)}\n{c['url']}"
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat, "text": text,
                            "parse_mode": "Markdown",
                            "disable_web_page_preview": False},
                      timeout=15)
    r.raise_for_status()
    return True


def _email(c: dict) -> bool:
    to = CFG["notify_email"]
    if not to:
        return False
    binary = shutil.which("msmtp") or shutil.which("sendmail")
    if not binary:
        return False
    msg = (f"To: {to}\nFrom: GPU Radar <gpuradar@localhost>\n"
           f"Subject: {headline(c)}\nContent-Type: text/plain; charset=utf-8\n\n"
           f"{body(c)}\n\n{c['url']}\n")
    args = [binary, to] if binary.endswith("msmtp") else [binary, "-t"]
    subprocess.run(args, input=msg.encode("utf-8"), check=True, timeout=30)
    return True


CHANNELS = [
    ("ntfy", _ntfy, "notify_ntfy"),
    ("desktop", _desktop, "notify_desktop"),
    ("webhook", _webhook, None),
    ("telegram", _telegram, None),
    ("email", _email, None),
]


# ---------------------------------------------------------------------------

def send_one(c: dict) -> list[str]:
    """Push one listing to every enabled channel. Returns channels that worked."""
    ok = []
    for name, fn, toggle in CHANNELS:
        if toggle and not CFG.get(toggle):
            continue
        try:
            if fn(c):
                ok.append(name)
        except Exception as e:                      # a dead channel must not
            print(f"[notify] {name} failed: {type(e).__name__}: {e}")   # stop the rest
    return ok


def send_batch(listings: list[dict]) -> int:
    """Alert on each listing, record it, and mark it so it never repeats."""
    if DISABLED:
        print(f"[notify] suppressed {len(listings)} alert(s) (--no-alert)")
        return 0
    sent = 0
    with db.connect() as con:
        for c in listings:
            channels = send_one(c)
            con.execute("""INSERT INTO alerts (listing_id,ts,deal_score,reason,channels,
                                               title,price,url)
                           VALUES (?,?,?,?,?,?,?,?)""",
                        (c["id"], db.now(), c["deal_score"], c.get("_reason", ""),
                         json.dumps(channels), c["title"], c["price"], c["url"]))
            if channels:
                con.execute("UPDATE listings SET alerted=1 WHERE id=?", (c["id"],))
                sent += 1
                print(f"[notify] {headline(c)} -> {', '.join(channels)}")
            else:
                print(f"[notify] no channel accepted {c['id']}")
    return sent


def subscribe_url() -> str:
    return f"{CFG['ntfy_server'].rstrip('/')}/{CFG['ntfy_topic']}"


def test_alert() -> dict:
    """Fire a fake but realistic alert through every channel."""
    demo = {
        "id": "test", "url": "https://www.olx.com.br/", "title":
        "TESTE — RX 6750 XT 12GB, radar de GPU funcionando",
        "price": 1290, "image": None, "location": "Goiânia, Setor Bueno",
        "city": CFG["home_city"], "date_text": "Hoje, agora",
        "model_name": "RX 6750 XT", "vram": 12, "perf": 100,
        "reference_price": 1950, "discount": 0.338, "perf_per_1k": 77.5,
        "deal_score": 84.0, "deal_class": "steal", "flags": ["warranty"],
        "kind": "gpu", "suspect": 0, "_reason": "manual test",
    }
    channels = send_one(demo)
    return {"channels": channels, "ntfy_topic": CFG["ntfy_topic"],
            "subscribe": subscribe_url()}


if __name__ == "__main__":
    print(json.dumps(test_alert(), indent=2, ensure_ascii=False))
