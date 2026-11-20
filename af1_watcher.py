#!/usr/bin/env python3
"""
GTA VI x Nike x Cactus Jack AF1 '07 RS drop watcher.

Polls shop.travisscott.com and alerts you the moment the AF1 raffle
("ENTER DRAW") opens or the shoes become available. Alerts:
  - Mac pop-up with an "Open Shop" button (click -> opens the site)
  - Push notification to your phone via the free ntfy app (tap -> opens the site)
  - Optional iMessage to yourself with the link
Also watches the Nike SNKRS feed and alerts when the same AF1s post there,
when a drop time is set, and when the drop/draw goes live (link opens SNKRS).

Usage:
  python3 af1_watcher.py            # run forever (checks every ~60s)
  python3 af1_watcher.py --once     # check once and print status
  python3 af1_watcher.py --test     # send a test alert right now
"""
import json
import os
import random
import re
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timezone
from html import unescape

SHOP_URL = "https://shop.travisscott.com/?mc_cid=ae18f1dfe8&mc_eid=67bf0f9c2b"
CHECK_URL = "https://shop.travisscott.com/"
HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "config.json")
STATE_PATH = os.path.join(HERE, "state.json")

INTERVAL = 60            # seconds between checks (+ random jitter)
REMIND_EVERY = 600       # once live, re-alert every 10 min...
MAX_REMINDERS = 6        # ...up to 6 times

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def log(msg):
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}", flush=True)


def load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


# ---------------------------------------------------------------- checking

def fetch_page():
    req = urllib.request.Request(CHECK_URL, headers={
        "User-Agent": UA, "Accept": "text/html", "Cache-Control": "no-cache"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", "replace")


RAFFLE_RE = re.compile(r"<script[^>]*data-raffle-data[^>]*>(.*?)</script>", re.S)
PRODUCT_RE = re.compile(r"<script[^>]*js-product-json[^>]*>(.*?)</script>", re.S)


def find_af1s(html):
    """Return {handle: info} for every Air Force 1 listed on the page."""
    found = {}

    for block in PRODUCT_RE.findall(html):
        try:
            p = json.loads(block)
        except Exception:
            continue
        if "AIR FORCE" in p.get("title", "").upper():
            found[p["handle"]] = {
                "title": p["title"],
                "type": p.get("type", ""),
                "available": bool(p.get("available")),
                "raffle": None,
            }

    for block in RAFFLE_RE.findall(html):
        status = re.search(r'"raffleStatus"\s*:\s*"([^"]*)"', block)
        handle = re.search(r'"handle"\s*:\s*"([^"]*)"', block)
        title = re.search(r'"title"\s*:\s*"([^"]*)"', block)
        if not (status and handle and title):
            continue
        if "AIR FORCE" not in title.group(1).upper():
            continue
        info = found.setdefault(handle.group(1), {
            "title": title.group(1), "type": "", "available": False})
        info["raffle"] = status.group(1).lower()

    # Colorway name (GRADIENT CAMO / WHITE / BLACK) from the handle
    for h, info in found.items():
        name = h.replace("air-force-1-07-rs-", "").replace("-placeholder", "").replace("-copy", "")
        info["colorway"] = unescape(name.replace("-", " ").upper())
    return found


def is_live(info):
    return (info.get("raffle") not in (None, "closed")
            or info.get("available")
            or (info.get("type") and info["type"].upper() != "PLACEHOLDER"))


# ---------------------------------------------------------------- alerts

def mac_alert(title, body, url=SHOP_URL):
    """Pop-up with an 'Open Shop' button that opens the site, plus a sound."""
    subprocess.run(["afplay", "/System/Library/Sounds/Glass.aiff"], check=False)
    script = (
        f'set r to display alert {json.dumps(title)} message {json.dumps(body)} '
        f'buttons {{"Later", "Open"}} default button "Open" giving up after 3600\n'
        f'if button returned of r is "Open" then open location {json.dumps(url)}'
    )
    # Run in background so the watcher keeps going while the pop-up is open.
    subprocess.Popen(["osascript", "-e", script])


def phone_push(cfg, title, body, url=SHOP_URL):
    topic = cfg.get("ntfy_topic")
    if not topic:
        return
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=body.encode("utf-8"), method="POST",
        headers={"Title": title.encode("ascii", "ignore").decode(),
                 "Click": url, "Priority": "urgent", "Tags": "rotating_light,athletic_shoe",
                 "Actions": f"view, Open, {url}"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:
        log(f"ntfy push failed: {e}")


def imessage(cfg, text):
    to = cfg.get("imessage_to")
    if not to:
        return
    script = (
        'tell application "Messages"\n'
        '  set svc to 1st account whose service type = iMessage\n'
        f'  send {json.dumps(text)} to participant {json.dumps(to)} of svc\n'
        'end tell')
    subprocess.run(["osascript", "-e", script], check=False)


def alert_all(cfg, title, body, url=SHOP_URL):
    log(f"ALERT: {title} - {body}")
    if cfg.get("mac_popup", True):
        mac_alert(title, body, url)
    phone_push(cfg, title, body, url)
    imessage(cfg, f"{title}\n{body}\n{url}")


# ---------------------------------------------------------------- main loop

def check_shop(cfg, state):
    af1s = find_af1s(fetch_page())
    if not af1s:
        log("No AF1 listings found on page (layout may have changed).")
        return
    live = {h: i for h, i in af1s.items() if is_live(i)}
    summary = ", ".join(f"{i['colorway']}={i.get('raffle') or ('available' if i['available'] else 'n/a')}"
                        for i in af1s.values())
    log(f"Checked: {summary}")

    new_handles = set(af1s) - set(state.get("known", []))
    if state.get("known") and new_handles:
        alert_all(cfg, "New AF1 listing on Travis Scott shop",
                  "New: " + ", ".join(af1s[h]["colorway"] for h in new_handles))
    state["known"] = sorted(af1s)

    if live:
        now = time.time()
        first = not state.get("live_since")
        due = now - state.get("last_alert", 0) >= REMIND_EVERY
        if first or (due and state.get("reminders", 0) < MAX_REMINDERS):
            colors = ", ".join(i["colorway"] for i in live.values())
            alert_all(cfg, "GTA x Cactus Jack AF1s ARE LIVE",
                      f"{colors} - draw is open. Tap to enter now!")
            state["reminders"] = 0 if first else state.get("reminders", 0) + 1
            state["last_alert"] = now
            state.setdefault("live_since", now)
    else:
        state.pop("live_since", None)
        state["reminders"] = 0


# ---------------------------------------------------------------- SNKRS

SNKRS_FEED = ("https://api.nike.com/product_feed/threads/v3/?count=50&anchor={anchor}"
              "&filter=marketplace(US)&filter=language(en)"
              "&filter=channelId(010794e5-35fe-4e32-aaff-cd2c74f89d61)"
              "&filter=exclusiveAccess(true,false)")
SNKRS_PAGES = 3                     # newest 150 SNKRS posts
COLLAB_WORDS = ("CACTUS JACK", "TRAVIS SCOTT", "GTA", "GRAND THEFT AUTO")


def fetch_json(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def is_target_shoe(title, slug):
    t, s = title.upper(), slug.lower()
    shoe = "AIR FORCE 1" in t or "AF1" in t or "air-force-1" in s or "af1" in s
    collab = any(w in t for w in COLLAB_WORDS) or any(
        w in s for w in ("cactus-jack", "travis", "gta", "grand-theft"))
    return shoe and collab


def parse_time(iso):
    try:
        return datetime.strptime(iso[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except Exception:
        return None


def nice_time(dt):
    try:
        from zoneinfo import ZoneInfo
        return dt.astimezone(ZoneInfo("America/New_York")).strftime("%a %b %-d, %-I:%M %p ET")
    except Exception:
        return dt.strftime("%a %b %d, %H:%M UTC")


def find_snkrs_af1s():
    found = {}
    for page in range(SNKRS_PAGES):
        data = fetch_json(SNKRS_FEED.format(anchor=page * 50))
        for obj in data.get("objects", []):
            slug = (obj.get("publishedContent", {}).get("properties", {})
                    .get("seo", {}).get("slug") or "")
            for p in obj.get("productInfo") or []:
                pc = p.get("productContent", {})
                title = pc.get("fullTitle") or pc.get("title") or ""
                if not is_target_shoe(title, slug):
                    continue
                lv = p.get("launchView") or {}
                start = lv.get("startEntryDate") or lv.get("startSellDate")
                key = p.get("merchProduct", {}).get("styleColor") or f"{slug}:{pc.get('colorDescription')}"
                found[key] = {
                    "title": title,
                    "color": pc.get("colorDescription") or "",
                    "method": {"DAN": "draw", "LEO": "first-come"}.get(lv.get("method"), lv.get("method") or ""),
                    "start": start,
                    "url": f"https://www.nike.com/launch/t/{slug}",
                }
        if not data.get("pages", {}).get("next"):
            break
    return found


def check_snkrs(cfg, state):
    shoes = find_snkrs_af1s()
    seen = state.setdefault("snkrs", {})
    log(f"SNKRS: {len(shoes)} matching AF1 listing(s)" +
        ("".join(f"\n    {i['color']} | {i['method'] or 'no type'} | {i['start'] or 'no date'}"
                 for i in shoes.values())))
    now = datetime.now(timezone.utc)
    for key, i in shoes.items():
        prev = seen.get(key)
        start = parse_time(i["start"]) if i["start"] else None
        when = f" Drops {nice_time(start)} ({i['method']})." if start else ""
        if prev is None:
            alert_all(cfg, "GTA x Cactus Jack AF1 is on SNKRS",
                      f"{i['color']} just posted on SNKRS.{when} Tap to open.", i["url"])
            seen[key] = {"start": i["start"], "live_alerted": False}
        elif prev.get("start") != i["start"] and start:
            alert_all(cfg, "SNKRS AF1 drop date set",
                      f"{i['color']}:{when} Tap to open.", i["url"])
            prev["start"], prev["live_alerted"] = i["start"], False
        rec = seen[key]
        if start and now >= start and not rec.get("live_alerted"):
            what = "Draw is OPEN - enter now!" if i["method"] == "draw" else "LIVE NOW - go!"
            alert_all(cfg, "SNKRS AF1 LIVE", f"{i['color']}: {what}", i["url"])
            rec["live_alerted"] = True


def check_once(cfg, state):
    errors = []
    for name, fn in (("Shop", check_shop), ("SNKRS", check_snkrs)):
        try:
            fn(cfg, state)
        except Exception as e:
            log(f"{name} check failed: {e}")
            errors.append(e)
    if len(errors) == 2:
        raise errors[0]


def main():
    cfg = load_json(CONFIG_PATH, {})
    if os.environ.get("NTFY_TOPIC"):          # cloud (GitHub) mode
        cfg["ntfy_topic"] = os.environ["NTFY_TOPIC"]
        cfg["mac_popup"] = False
    if "--test" in sys.argv:
        alert_all(cfg, "TEST: AF1 watcher works", "Tap to open the Travis Scott shop.")
        time.sleep(2)
        return
    state = load_json(STATE_PATH, {})
    if "--once" in sys.argv:
        check_once(cfg, state)
        save_json(STATE_PATH, state)
        return

    log("AF1 watcher started.")
    failures = 0
    while True:
        try:
            check_once(cfg, state)
            save_json(STATE_PATH, state)
            failures = 0
        except Exception as e:
            failures += 1
            log(f"Check failed ({failures}): {e}")
        time.sleep(INTERVAL + random.uniform(0, 20) + min(failures, 10) * 30)


if __name__ == "__main__":
    main()
