import hashlib, json, os, pathlib, re, urllib.request, urllib.parse

# ================= НАСТРОЙКИ =================
CPU_WANTED    = ["EPYC 7", "EPYC 9"]
MIN_RAM_GB    = 128
MAX_PRICE_NET = 200
REQUIRE_ECC   = True
VAT           = 0.21
MAX_NOTIFY    = 10
# =============================================

CANDIDATES = [
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json",
    "https://www.hetzner.com/_resources/app/jsondata/live_data_sb.json",
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json",
]
UA = "Mozilla/5.0 (compatible; hzbot/3.0)"

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
    html = http("https://www.hetzner.com/sb/").decode("utf-8", "replace")
    found = re.findall(r'["\'](/[\w/.\-]*live_data_sb[\w.\-]*\.json)["\']', html)
    return ["https://www.hetzner.com" + p for p in dict.fromkeys(found)]


def unwrap(doc):
    if isinstance(doc, list):
        return doc
    for k in ("server", "servers", "data", "products", "offers"):
        v = doc.get(k)
        if isinstance(v, list) and v:
            return v
    for k, v in doc.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            print(f"unwrap: использую ключ '{k}'")
            return v
    raise ValueError(f"ключи верхнего уровня = {list(doc)[:20]}")


def fetch():
    for url in CANDIDATES + discover():
        try:
            out = unwrap(json.loads(http(url)))
            print(f"FEED OK: {url}")
            return out
        except Exception as e:
            print(f"FEED FAIL: {url} -> {type(e).__name__} {e}")
    raise SystemExit("Ни один адрес не сработал")


def pick(s, *names, default=None):
    for n in names:
        v = s.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def num(x, default=0.0):
    try:
        return float(re.sub(r"[^\d.,\-]", "", str(x)).replace(",", "."))
    except (TypeError, ValueError):
        return default


def flat(v):
    if isinstance(v, list):
        return " ".join(str(x) for x in v)
    return str(v) if v is not None else "?"


def f_cpu(s):
    return str(pick(s, "cpu", "cpu_name", "cpu_description", "processor", default=""))


def f_ram(s):
    return num(pick(s, "ram", "ram_size", "memory", "ram_gb", default=0))


def f_price(s):
    return num(pick(s, "price", "price_net", "cost", "amount", default=0))


def f_ecc(s):
    v = pick(s, "is_ecc", "ecc")
    if isinstance(v, bool):
        return v
    sp = flat(pick(s, "specials", "features", default="")).lower()
    return "ecc" in sp


def f_id(s):
    v = pick(s, "key", "id", "server_id", "sb_id", "auction_id")
    if v is not None:
        return str(v)
    seed = f"{f_cpu(s)}|{f_ram(s)}|{flat(pick(s, 'hdd_hr', 'hdd', default=''))}" \
           f"|{flat(pick(s, 'datacenter', 'dc', default=''))}"
    return "h" + hashlib.md5(seed.encode()).hexdigest()[:12]


def matches(s):
    cpu = f_cpu(s)
    if not any(m.lower() in cpu.lower() for m in CPU_WANTED):
        return False
    if f_ram(s) < MIN_RAM_GB:
        return False
    if f_price(s) > MAX_PRICE_NET:
        return False
    if REQUIRE_ECC and not f_ecc(s):
        return False
    return True


def card(s):
    p = f_price(s)
    return (
        f"<b>{f_cpu(s) or '?'}</b>\n"
        f"RAM {f_ram(s):.0f} GB · {flat(pick(s, 'datacenter', 'dc', default='?'))}\n"
        f"Диски: {flat(pick(s, 'hdd_hr', 'hdd', 'disks', default='?'))}\n"
        f"EUR {p:.2f} нетто · EUR {p * (1 + VAT):.2f} с НДС\n"
        f"Setup: {num(pick(s, 'setup_price', default=0)):.2f}\n"
        f"Снижение через: {flat(pick(s, 'next_reduce_hr', default='?'))}\n"
        f"https://www.hetzner.com/sb/#search={f_id(s)}"
    )


def diagnose(servers):
    first = servers[0]
    print("KEYS:", list(first.keys()))
    print("SAMPLE:", json.dumps(first, ensure_ascii=False)[:900])
    epyc = [s for s in servers if "epyc" in f_cpu(s).lower()]
    print(f"EPYC={len(epyc)} ECC={sum(1 for s in servers if f_ecc(s))}")
    for s in sorted(epyc, key=lambda x: -f_ram(x))[:10]:
        print(f"  {f_cpu(s)} | ram={f_ram(s):.0f} | price={f_price(s):.2f} "
              f"| ecc={f_ecc(s)} | id={f_id(s)}")


def main():
    servers = fetch()
    if not servers:
        raise SystemExit("Пустой список лотов")

    diagnose(servers)
    if os.environ.get("DUMP"):
        return

    prev = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    quiet = len(prev) == 0

    seen, hits = set(), []
    for s in servers:
        sid = f_id(s)
        seen.add(sid)
        if sid not in prev and matches(s):
            hits.append(s)

    if quiet:
        send(f"hzbot запущен\nЛотов: {len(servers)}\nПодходит сейчас: {len(hits)}")

    for s in hits[:MAX_NOTIFY]:
        send(card(s))
    if len(hits) > MAX_NOTIFY:
        send(f"Ещё {len(hits) - MAX_NOTIFY} подходящих лотов не показаны")

    STATE.write_text(json.dumps(sorted(seen)))
    print(f"lots={len(servers)} hits={len(hits)} seen={len(seen)} quiet={quiet}")


main()
