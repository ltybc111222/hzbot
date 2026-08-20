import json, os, pathlib, re, urllib.request, urllib.parse
from collections import Counter

# ================= НАСТРОЙКИ =================
CPU_WANTED    = ["EPYC"]
MIN_RAM_GB    = 128
MAX_PRICE_NET = 200
REQUIRE_ECC   = True
VAT           = 0.21
MAX_NOTIFY    = 10
# =============================================

CANDIDATES = [
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json",
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json",
]
UA = "Mozilla/5.0 (compatible; hzbot/4.0)"

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
    hits = re.findall(r'["\'](/[\w/.\-]*live_data_sb[\w.\-]*\.json)["\']', html)
    return ["https://www.hetzner.com" + p for p in dict.fromkeys(hits)]


def unwrap(doc):
    if isinstance(doc, list):
        return doc
    for k, v in doc.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    raise ValueError(f"ключи = {list(doc)[:20]}")


def fetch():
    for url in CANDIDATES + discover():
        try:
            out = unwrap(json.loads(http(url)))
            print(f"FEED OK: {url}")
            return out
        except Exception as e:
            print(f"FEED FAIL: {url} -> {type(e).__name__} {e}")
    raise SystemExit("Ни один адрес не сработал")


def dig(s, path, default=None):
    cur = s
    for part in path.split("."):
        if not isinstance(cur, dict):
            return default
        cur = cur.get(part)
        if cur is None:
            return default
    return cur


def sid(s):
    return str(dig(s, "Id", ""))


def cpu(s):
    return str(dig(s, "Hardware.CPU.Name", "") or "")


def cores(s):
    return dig(s, "Hardware.CPU.CoreCount", 0) or 0


def ram(s):
    v = dig(s, "Hardware.RAM.Size")
    if v:
        return float(v)
    real = dig(s, "Hardware.RAM.RealSize")
    return float(real) / 1024 if real else 0.0


def ecc(s):
    return bool(dig(s, "Hardware.RAM.ecc", False))


def price(s):
    return float(dig(s, "Prices.monthly.EUR", 0) or 0)


def hourly(s):
    return float(dig(s, "Prices.hourly.EUR", 0) or 0)


def setup(s):
    return float(dig(s, "Prices.setup.EUR", 0) or 0)


def disks(s):
    d = dig(s, "Hardware.Storage.Disks", [])
    return ", ".join(str(x) for x in d) if d else "?"


def specials(s):
    v = dig(s, "Details.Specials", [])
    return ", ".join(str(x) for x in v) if v else ""


def matches(s):
    if not any(m.lower() in cpu(s).lower() for m in CPU_WANTED):
        return False
    if ram(s) < MIN_RAM_GB:
        return False
    if price(s) > MAX_PRICE_NET:
        return False
    if REQUIRE_ECC and not ecc(s):
        return False
    return True


def card(s):
    p = price(s)
    return (
        f"<b>{cpu(s) or '?'}</b>  ({cores(s)} cores)\n"
        f"RAM {ram(s):.0f} GB{' ECC' if ecc(s) else ''}\n"
        f"Диски: {disks(s)}\n"
        f"EUR {p:.2f} нетто · EUR {p * (1 + VAT):.2f} с НДС\n"
        f"Час: EUR {hourly(s):.4f} · Setup: EUR {setup(s):.2f}\n"
        f"{specials(s)}\n"
        f"https://www.hetzner.com/sb/#search={sid(s)}"
    )


def diagnose(servers):
    top = Counter(cpu(s) for s in servers).most_common(12)
    print("TOP CPU:")
    for name, n in top:
        print(f"  {n:3d}  {name}")

    big = [s for s in servers if ram(s) >= 64 and ecc(s)]
    print(f"ECC+64GB: {len(big)}")
    for s in sorted(big, key=lambda x: -ram(x))[:10]:
        print(f"  {cpu(s)} | {cores(s)}c | ram={ram(s):.0f} | "
              f"EUR {price(s):.2f} | id={sid(s)}")


def main():
    servers = fetch()
    if not servers:
        raise SystemExit("Пустой список")

    diagnose(servers)
    if os.environ.get("DUMP"):
        return

    prev = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    quiet = len(prev) <= 1

    seen, hits = set(), []
    for s in servers:
        i = sid(s)
        if not i:
            continue
        seen.add(i)
        if i not in prev and matches(s):
            hits.append(s)

    if quiet:
        send(f"hzbot запущен\nЛотов: {len(servers)}\nПодходит сейчас: {len(hits)}")

    for s in hits[:MAX_NOTIFY]:
        send(card(s))
    if len(hits) > MAX_NOTIFY:
        send(f"Ещё {len(hits) - MAX_NOTIFY} лотов не показаны")

    STATE.write_text(json.dumps(sorted(seen)))
    print(f"lots={len(servers)} hits={len(hits)} seen={len(seen)} quiet={quiet}")


main()
