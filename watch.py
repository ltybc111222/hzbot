import json, os, pathlib, re, urllib.request, urllib.parse

# ================= НАСТРОЙКИ =================
CPU_WANTED = ["7313P", "7443P", "7513P", "7373X", "7473X", "7573X", "7302P", "7402P"]
MIN_RAM_GB    = 128
MAX_PRICE_NET = 140
REQUIRE_ECC   = True
VAT           = 0.21
# =============================================

CANDIDATES = [
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json",
    "https://www.hetzner.com/_resources/app/jsondata/live_data_sb.json",
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json",
    "https://www.hetzner.com/a_hz_serverboerse/live_data_sb_EUR.json",
]
UA = "Mozilla/5.0 (compatible; hzbot/2.0)"

TOKEN = os.environ["TG_TOKEN"]
CHAT  = os.environ["TG_CHAT"]
STATE = pathlib.Path("seen.json")


def http(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=30).read()


def send(text):
    body = urllib.parse.urlencode({
        "chat_id": CHAT, "text": text,
        "parse_mode": "HTML", "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/sendMessage", data=body)
    urllib.request.urlopen(req, timeout=30).read()


def discover():
    """Вытащить адрес фида со страницы аукциона."""
    html = http("https://www.hetzner.com/sb/").decode("utf-8", "replace")
    found = re.findall(r'["\'](/[\w/.\-]*live_data_sb[\w.\-]*\.json)["\']', html)
    return ["https://www.hetzner.com" + p for p in dict.fromkeys(found)]


def fetch():
    urls = CANDIDATES[:]
    for url in urls:
        try:
            doc = json.loads(http(url))
            print(f"FEED OK: {url}")
            return unwrap(doc)
        except Exception as e:
            print(f"FEED FAIL: {url} -> {type(e).__name__} {e}")

    print("Пробую найти адрес на странице аукциона")
    for url in discover():
        try:
            doc = json.loads(http(url))
            print(f"FEED OK (discovered): {url}")
            return unwrap(doc)
        except Exception as e:
            print(f"FEED FAIL: {url} -> {type(e).__name__} {e}")

    raise SystemExit("Ни один адрес не сработал")


def unwrap(doc):
    if isinstance(doc, list):
        return doc
    for k in ("server", "servers", "data"):
        if isinstance(doc.get(k), list):
            return doc[k]
    raise ValueError(f"ключи верхнего уровня = {list(doc)[:15]}")


def num(x, default=0.0):
    try:
        return float(str(x).replace(",", "."))
    except (TypeError, ValueError):
        return default


def dc(s):
    v = s.get("datacenter")
    if isinstance(v, list):
        return v[0] if v else "?"
    return v or "?"


def matches(s):
    cpu = str(s.get("cpu") or "")
    if not any(m.lower() in cpu.lower() for m in CPU_WANTED):
        return False
    if num(s.get("ram")) < MIN_RAM_GB:
        return False
    if num(s.get("price"), 1e9) > MAX_PRICE_NET:
        return False
    if REQUIRE_ECC and not s.get("is_ecc"):
        return False
    return True


def card(s):
    p = num(s.get("price"))
    return (
        f"<b>{s.get('cpu', '?')}</b>\n"
        f"RAM {s.get('ram_hr') or s.get('ram')} · {dc(s)}\n"
        f"Диски: {s.get('hdd_hr', '?')}\n"
        f"EUR {p:.2f} нетто · EUR {p * (1 + VAT):.2f} с НДС\n"
        f"Setup: {num(s.get('setup_price')):.2f}\n"
        f"Снижение цены через: {s.get('next_reduce_hr', '?')}\n"
        f"https://www.hetzner.com/sb/#search={s.get('key')}"
    )


def main():
    servers = fetch()

    if os.environ.get("DUMP"):
        print(json.dumps(servers[0], indent=2, ensure_ascii=False)[:3000])
        return

    first_run = not STATE.exists()
    seen = set() if first_run else set(json.loads(STATE.read_text()))

    hits = []
    for s in servers:
        sid = str(s.get("key") or s.get("id") or "")
        if not sid:
            continue
        if sid not in seen and matches(s):
            hits.append(s)
        seen.add(sid)

    if first_run:
        send(f"hzbot запущен\nЛотов в аукционе: {len(servers)}\n"
             f"Подходит сейчас: {len(hits)}")
        for s in hits[:3]:
            send(card(s))
    else:
        for s in hits:
            send(card(s))

    STATE.write_text(json.dumps(sorted(seen)))
    print(f"lots={len(servers)} hits={len(hits)} seen={len(seen)} first_run={first_run}")


main()
