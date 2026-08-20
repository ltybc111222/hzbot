import json, os, pathlib, urllib.request, urllib.parse

# ================= НАСТРОЙКИ =================
FEED = "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json"

CPU_WANTED = ["7313P", "7443P", "7513P", "7373X", "7473X", "7573X", "7302P", "7402P"]
MIN_RAM_GB     = 128
MAX_PRICE_NET  = 140      # евро/мес БЕЗ НДС
REQUIRE_ECC    = True
VAT            = 0.21
# =============================================

TOKEN = os.environ["TG_TOKEN"]
CHAT  = os.environ["TG_CHAT"]
STATE = pathlib.Path("seen.json")


def send(text):
    body = urllib.parse.urlencode({
        "chat_id": CHAT, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=body)
    urllib.request.urlopen(req, timeout=30).read()


def fetch():
    req = urllib.request.Request(FEED, headers={"User-Agent": "hzbot/1.0"})
    doc = json.loads(urllib.request.urlopen(req, timeout=30).read())
    if isinstance(doc, list):
        return doc
    for k in ("server", "servers", "data"):
        if k in doc:
            return doc[k]
    raise SystemExit(f"SCHEMA: ключи верхнего уровня = {list(doc)[:15]}")


def g(s, *names, default=None):
    for n in names:
        v = s.get(n)
        if v not in (None, ""):
            return v
    return default


def num(x, default=0.0):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return default


def matches(s):
    cpu = str(g(s, "cpu", "cpu_name", "cpu_description", default=""))
    if not any(m.lower() in cpu.lower() for m in CPU_WANTED):
        return False
    if num(g(s, "ram_size", "ram_gb", default=0)) < MIN_RAM_GB:
        return False
    if num(g(s, "price", "price_net", default=1e9), 1e9) > MAX_PRICE_NET:
        return False
    if REQUIRE_ECC and "ecc" not in json.dumps(s, ensure_ascii=False).lower():
        return False
    return True


def card(sid, s):
    p = num(g(s, "price", "price_net", default=0))
    return (
        f"🟢 <b>{g(s, 'cpu', 'cpu_name', default='?')}</b>\n"
        f"RAM {g(s, 'ram_size', 'ram_gb', default='?')} GB · {g(s, 'datacenter', 'dc', default='?')}\n"
        f"€{p:.2f} нетто · €{p * (1 + VAT):.2f} с НДС\n"
        f"https://www.hetzner.com/sb/#search={sid}"
    )


def main():
    servers = fetch()

    if os.environ.get("DUMP"):
        print(json.dumps(servers[0], indent=2, ensure_ascii=False)[:3000])
        return

    first_run = not STATE.exists()
    seen = set(json.loads(STATE.read_text())) if not first_run else set()

    hits = []
    for s in servers:
        sid = str(g(s, "id", "key", default=""))
        if not sid:
            continue
        if sid not in seen and matches(s):
            hits.append((sid, s))
        seen.add(sid)

    if first_run:
        send(f"✅ hzbot запущен\nЛотов в аукционе: {len(servers)}\nПодходит сейчас: {len(hits)}")
        for sid, s in hits[:3]:
            send(card(sid, s))
    else:
        for sid, s in hits:
            send(card(sid, s))

    STATE.write_text(json.dumps(sorted(seen)))
    print(f"lots={len(servers)} hits={len(hits)} seen={len(seen)} first_run={first_run}")


main()
