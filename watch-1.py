import json, math, os, pathlib, re, urllib.request, urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone

# ================= НАСТРОЙКИ =================
REPO       = "ltybc111222/hzbot"
TZ_OFFSET  = 2
SUMMARY_AT = 9

CPU_HOURS_PER_SEED = 2070
WORKLOAD_BW        = 5.57
HOURS_TO_CAP       = 624
VAT                = 0.21
HISTORY_DAYS       = 120
MAX_NOTIFY         = 6

DEFAULTS = {"cost": 60, "days": 8, "equiv": 8, "ecc": 1}
# =============================================

# (regex, ядра, каналы, MT/s, пул L3 МБ, k_equiv, внутр. NUMA)
CPU_DB = [
    (r"EPYC 9\d{3}",            48, 12, 4800, 32,    0.85, False),
    (r"EPYC 7[3-5]\d{2}X",      16,  8, 3200, 96,    0.72, False),
    (r"EPYC 75\d{2}",           32,  8, 3200, 32,    0.70, False),
    (r"EPYC 74\d{2}",           24,  8, 3200, 32,    0.70, False),
    (r"EPYC 73\d{2}",           16,  8, 3200, 32,    0.70, False),
    (r"EPYC 72\d{2}",            8,  8, 3200, 32,    0.68, False),
    (r"Xeon Gold 6\d{3}",       20,  6, 2933, 27.5,  0.68, False),
    (r"Xeon Silver 4\d{3}",     12,  6, 2400, 16.5,  0.58, False),
    (r"Xeon W-2295",            18,  4, 2933, 24.75, 0.72, False),
    (r"Xeon W-21\d{2}",          8,  4, 2666, 11,    0.70, False),
    (r"Xeon E5-16\d{2}V4",       6,  4, 2400, 15,    0.58, False),
    (r"Xeon E5-16\d{2}V3",       6,  4, 2133, 15,    0.55, False),
    (r"Xeon E5-26\d{2}",        12,  4, 2133, 30,    0.52, False),
    (r"Xeon E3-12\d{2}",         4,  2, 2133,  8,    0.55, False),
    (r"XEON E-2\d{3}",           6,  2, 2666, 12,    0.65, False),
    (r"Threadripper 39\d{2}",   24,  4, 3200, 16,    0.70, True),
    (r"Threadripper 29\d{2}",   16,  4, 2933,  8,    0.55, True),
    (r"Threadripper 19\d{2}",   16,  4, 2666,  8,    0.50, True),
    (r"Ryzen 9 7950",           16,  2, 4800, 32,    0.88, False),
    (r"Ryzen 9 59\d{2}",        12,  2, 3200, 32,    0.78, False),
    (r"Ryzen 9 39\d{2}",        12,  2, 3200, 16,    0.68, False),
    (r"Ryzen 7 77\d{2}",         8,  2, 4800, 32,    0.85, False),
    (r"Ryzen 7 5[89]\d{2}",      8,  2, 3200, 32,    0.75, False),
    (r"Ryzen 7 3[78]\d{2}",      8,  2, 3200, 16,    0.68, False),
    (r"Ryzen 5 3600",            6,  2, 3200, 16,    0.65, False),
    (r"Core i9-13900",          24,  2, 5600, 36,    0.85, False),
    (r"Core i9-1[23]9\d{2}",    16,  2, 4800, 30,    0.82, False),
    (r"Core i7-\d{4}",           4,  2, 2400,  8,    0.60, False),
]

# имя, ядра, каналы, MT/s, L3, k, €/мес, €/ч, setup
CATALOG = [
    ("AX162 (EPYC 9454P)",  48, 12, 4800, 32, 0.85, 229.0, 0.3670, 79.0),
    ("AX102 (7950X3D)",     16,  2, 4800, 96, 0.88, 104.0, 0.1666, 269.0),
    ("AX52 (Ryzen 7 7700)",  8,  2, 4800, 32, 0.85,  80.0, 0.1282,  79.0),
]

FEEDS = [
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json",
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json",
]
UA      = "Mozilla/5.0 (compatible; hzbot/7.0)"
RUN_URL = "https://github.com/" + REPO + "/actions/workflows/watch.yml"
TOKEN   = os.environ["TG_TOKEN"]
CHAT    = os.environ["TG_CHAT"]
STATE   = pathlib.Path("state.json")
OLD     = pathlib.Path("seen.json")

KB = {"keyboard": [["Сводка", "Топ"], ["Проверить", "Статус"],
                   ["Процессоры", "Цели"]],
      "resize_keyboard": True, "is_persistent": True}
LINK = {"inline_keyboard": [[{"text": "Запустить сейчас", "url": RUN_URL}]]}
LOG = []


def log(m):
    print(m)
    LOG.append(str(m))


def now():
    return datetime.now(timezone(timedelta(hours=TZ_OFFSET)))


def today():
    return now().strftime("%Y-%m-%d")


def http(url, timeout=30):
    r = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(r, timeout=timeout).read()


def esc(t):
    return str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tg(method, **p):
    for k, v in list(p.items()):
        if isinstance(v, (dict, list)):
            p[k] = json.dumps(v)
    req = urllib.request.Request(
        "https://api.telegram.org/bot" + TOKEN + "/" + method,
        data=urllib.parse.urlencode(p).encode())
    return json.loads(urllib.request.urlopen(req, timeout=35).read())


def send(text, markup=None):
    if not text:
        return
    for i in range(0, len(text), 3900):
        try:
            tg("sendMessage", chat_id=CHAT, text=text[i:i + 3900],
               parse_mode="HTML", disable_web_page_preview="true",
               reply_markup=markup or KB)
        except Exception as e:
            log("SEND FAIL: " + str(e))


# ---------- данные ----------

def discover():
    try:
        html = http("https://www.hetzner.com/sb/").decode("utf-8", "replace")
        hits = re.findall(r'["\'](/[\w/.\-]*live_data_sb[\w.\-]*\.json)["\']', html)
        return ["https://www.hetzner.com" + p for p in dict.fromkeys(hits)]
    except Exception:
        return []


def unwrap(doc):
    if isinstance(doc, list):
        return doc
    for k, v in doc.items():
        if isinstance(v, list) and v and isinstance(v[0], dict):
            return v
    raise ValueError("ключи = " + str(list(doc)[:20]))


def fetch():
    for url in FEEDS + discover():
        try:
            out = unwrap(json.loads(http(url)))
            log("FEED OK: " + url)
            return out
        except Exception as e:
            log("FEED FAIL: " + type(e).__name__ + " " + str(e))
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


def sockets(s):
    return dig(s, "Hardware.CPU.CoreCount", 1) or 1


def ecc(s):
    return bool(dig(s, "Hardware.RAM.ecc", False))


def price(s):
    return float(dig(s, "Prices.monthly.EUR", 0) or 0)


def hourly(s):
    return float(dig(s, "Prices.hourly.EUR", 0) or 0)


def setup(s):
    return float(dig(s, "Prices.setup.EUR", 0) or 0)


def ram(s):
    v = dig(s, "Hardware.RAM.Size")
    if v:
        return float(v)
    r = dig(s, "Hardware.RAM.RealSize")
    return float(r) / 1024 if r else 0.0


def disks(s):
    d = dig(s, "Hardware.Storage.Disks", [])
    return ", ".join(str(x) for x in d) if d else "?"


# ---------- оценка ----------

def calc(cores, ch, mts, l3, k, mo, hr, su, numa=False):
    bw = ch * mts * 8 / 1000.0
    sat = min(1.0, bw / (cores * WORKLOAD_BW)) if cores else 0
    eq = cores * k * sat
    if eq <= 0:
        return None
    h = CPU_HOURS_PER_SEED / eq
    cost = min(h * hr, math.ceil(h / HOURS_TO_CAP) * mo) + su
    return dict(cores=cores, channels=ch, l3=l3, k=k, numa=numa, bw=bw,
                bw_core=bw / cores, equiv=eq, hours=h, days=h / 24.0,
                cost=cost, known=True)


def spec_of(name, sk=1):
    for pat, c, ch, mts, l3, k, numa in CPU_DB:
        if re.search(pat, name, re.I):
            return (c * sk, ch * sk, mts, l3, k, numa or sk > 1)
    return None


def score(s):
    sp = spec_of(cpu(s), sockets(s))
    if not sp:
        return None
    c, ch, mts, l3, k, numa = sp
    return calc(c, ch, mts, l3, k, price(s), hourly(s), setup(s), numa)


def catalog_best():
    out = []
    for name, c, ch, mts, l3, k, mo, hr, su in CATALOG:
        r = calc(c, ch, mts, l3, k, mo, hr, su)
        if r:
            out.append((r["cost"], name, r))
    out.sort(key=lambda x: x[0])
    return out


def ranked(servers, need_ecc=True):
    out = []
    for s in servers:
        sc = score(s)
        if sc and (ecc(s) or not need_ecc):
            out.append((sc["cost"], s, sc))
    out.sort(key=lambda x: x[0])
    return out


def matches(s, sc, st):
    if sc is None:
        return False
    if st["ecc"] and not ecc(s):
        return False
    if ram(s) < 2 * sc["cores"] + 8:
        return False
    if sc["cost"] > st["cost"]:
        return False
    if sc["days"] > st["days"]:
        return False
    if sc["equiv"] < st["equiv"]:
        return False
    return True


# ---------- тексты ----------

def card(s, sc):
    return ("<b>" + esc(cpu(s)) + "</b>\n"
            + "EUR %.0f за прогон · %.1f сут/сид\n" % (sc["cost"], sc["days"])
            + "%dc · %.1f экв. M1 · %.1f ГБ/с на ядро\n"
              % (sc["cores"], sc["equiv"], sc["bw_core"])
            + "L3 пул %.0f МБ" % sc["l3"] + (" · NUMA" if sc["numa"] else "") + "\n"
            + "RAM %.0f GB" % ram(s) + (" ECC" if ecc(s) else "")
            + " · " + esc(disks(s)) + "\n"
            + "EUR %.2f/мес · EUR %.4f/ч · setup %.0f\n"
              % (price(s), hourly(s), setup(s))
            + "https://www.hetzner.com/sb/#search=" + sid(s))


def verdict(servers):
    r = ranked(servers)
    cat = catalog_best()
    if not cat:
        return "Каталожная база не задана."
    cb, cname, cr = cat[0]
    if not r:
        return ("<b>Вердикт: НЕ ПОКУПАТЬ</b>\nВ аукционе нечего оценивать.\n"
                + "Каталог: " + esc(cname) + " — EUR %.0f/прогон, %.1f сут"
                % (cb, cr["days"]))
    ab, asrv, asc = r[0]
    head = "ПОКУПАТЬ В АУКЦИОНЕ" if ab < cb else "НЕ ПОКУПАТЬ"
    return ("<b>Вердикт: " + head + "</b>\n"
            + "Аукцион: EUR %.0f/прогон · %.1f сут · " % (ab, asc["days"])
            + esc(cpu(asrv)) + "\n"
            + "Каталог: EUR %.0f/прогон · %.1f сут · " % (cb, cr["days"])
            + esc(cname) + "\n"
            + "Отношение: %.1fx\n" % (ab / cb if cb else 0)
            + "Разворот при аукционном лоте дешевле EUR %.0f/прогон" % cb)


def summary(servers, st, hist):
    r = ranked(servers)
    ep = sum(1 for s in servers if "epyc" in cpu(s).lower())
    prev = hist[-1] if hist else None
    delta = ""
    if prev and prev.get("lots"):
        delta = " (%+d)" % (len(servers) - prev["lots"])

    streak = 1 if ep == 0 else 0
    if ep == 0:
        for h in reversed(hist):
            if h.get("epyc", 0) == 0:
                streak += 1
            else:
                break

    med = ""
    vals = sorted(h["best"] for h in hist[-14:] if h.get("best"))
    if vals:
        med = "\nМедиана лучшего за %d дн: EUR %.0f" % (
            len(vals), vals[len(vals) // 2])

    lines = ["<b>Аукцион · " + now().strftime("%d.%m %H:%M") + "</b>",
             "Лотов %d%s · ECC %d · оценено %d"
             % (len(servers), delta,
                sum(1 for s in servers if ecc(s)), len(r)),
             "", "<b>Лучшее по EUR/прогон</b>"]
    if r:
        for i, (c, s, sc) in enumerate(r[:3], 1):
            lines.append("%d. EUR %.0f · %.1fсут · %s · %.0fGB · %.0f/мес"
                         % (i, c, sc["days"], esc(cpu(s)), ram(s), price(s)))
    else:
        lines.append("нет оцениваемых лотов")
    lines += ["", verdict(servers), "",
              "EPYC в аукционе: %d (подряд без: %d дн)%s" % (ep, streak, med)]
    return "\n".join(lines)


def status(servers, st, seen_n, hist):
    cnt = Counter(cpu(s) for s in servers)
    unk = sorted({cpu(s) for s in servers if not spec_of(cpu(s))})
    r = ranked(servers)
    out = ["<b>Статус</b>",
           "Лотов %d · в памяти %d · история %d дн"
           % (len(servers), seen_n, len(hist)),
           "ECC %d · оценено %d · вне справочника %d"
           % (sum(1 for s in servers if ecc(s)), len(r), len(unk)),
           "", "<b>Пороги</b>",
           "cost %g · days %g · equiv %g · ecc %s"
           % (st["cost"], st["days"], st["equiv"], "да" if st["ecc"] else "нет"),
           "Меняются: /set cost 80", "", "<b>Частые CPU</b>"]
    for k, n in cnt.most_common(8):
        out.append("%3d  %s" % (n, esc(k)))
    if unk:
        out += ["", "<b>Нет в справочнике</b>"] + [esc(x) for x in unk[:8]]
    return "\n".join(out)


def cpu_list(servers, flt=""):
    if not flt:
        cnt = Counter(cpu(s) for s in servers)
        out = ["<b>Процессоры в аукционе (%d моделей)</b>" % len(cnt),
               "Подробнее: /cpu xeon", ""]
        for name, n in cnt.most_common(30):
            sp = spec_of(name)
            tag = "%dc/%dch" % (sp[0], sp[1]) if sp else "нет в справочнике"
            out.append("%3d  %s  · %s" % (n, esc(name), tag))
        return "\n".join(out)

    sel = [s for s in servers if flt.lower() in cpu(s).lower()]
    if not sel:
        return "Нет лотов по запросу «" + esc(flt) + "»"
    out = ["<b>«%s» — %d лотов</b>" % (esc(flt), len(sel)), ""]
    for c, s, sc in ranked(sel, need_ecc=False)[:15]:
        out.append("EUR %.0f/прогон · %.1fсут · %.1feq\n%s · %.0fGB%s · %.0f/мес\n"
                   "https://www.hetzner.com/sb/#search=%s\n"
                   % (c, sc["days"], sc["equiv"], esc(cpu(s)), ram(s),
                      " ECC" if ecc(s) else "", price(s), sid(s)))
    unk = sorted({cpu(s) for s in sel if not spec_of(cpu(s))})
    for u in unk[:5]:
        out.append("[нет в справочнике] " + esc(u))
    return "\n".join(out)


def why(servers, wid, st):
    tgt = None
    for s in servers:
        if sid(s) == wid:
            tgt = s
            break
    if tgt is None:
        return "Лот " + esc(wid) + " не найден в текущей выдаче"
    sc = score(tgt)
    if sc is None:
        return ("<b>" + esc(cpu(tgt)) + "</b> · id " + wid + "\n"
                "Нет в справочнике CPU — оценить невозможно.")
    need = 2 * sc["cores"] + 8
    rows = [
        ("ECC", "да" if ecc(tgt) else "нет",
         "да" if st["ecc"] else "любое", (not st["ecc"]) or ecc(tgt)),
        ("RAM", "%.0f" % ram(tgt), ">=%.0f" % need, ram(tgt) >= need),
        ("EUR/прогон", "%.0f" % sc["cost"], "<=%g" % st["cost"],
         sc["cost"] <= st["cost"]),
        ("Суток", "%.1f" % sc["days"], "<=%g" % st["days"],
         sc["days"] <= st["days"]),
        ("Экв. ядер", "%.1f" % sc["equiv"], ">=%g" % st["equiv"],
         sc["equiv"] >= st["equiv"]),
    ]
    body = "\n".join("%-11s %8s %8s  %s" % (n, v, t, "OK" if ok else "нет")
                     for n, v, t, ok in rows)
    bad = [n for n, _, _, ok in rows if not ok]
    tail = ("Проходит по всем критериям." if not bad
            else "Не проходит: " + ", ".join(bad))
    return ("<b>" + esc(cpu(tgt)) + "</b> · id " + wid + "\n"
            + "%dc · %dch · %.1f ГБ/с на ядро · L3 пул %.0f МБ\n\n"
              % (sc["cores"], sc["channels"], sc["bw_core"], sc["l3"])
            + "<code>" + esc(body) + "</code>\n\n" + tail)


def db_text():
    out = ["<b>Справочник CPU</b>", "ядра / каналы / L3 пул / k_equiv", ""]
    for pat, c, ch, mts, l3, k, numa in CPU_DB:
        out.append("%-22s %3dc %2dch %5.1fМБ k=%.2f%s"
                   % (esc(pat), c, ch, l3, k, " NUMA" if numa else ""))
    out += ["", "Все k_equiv оценочные, ни один не измерен.",
            "До замера зондом рейтинг — ранжирование догадок.",
            "", "<b>Каталог для вердикта</b>"]
    for c, name, r in catalog_best():
        out.append("EUR %.0f/прогон · %.1fсут · %s" % (c, r["days"], esc(name)))
    return "\n".join(out)


def targets_text(servers, tg_list):
    if not tg_list:
        return ("Целей нет.\nДобавить: /watch EPYC 73 120\n"
                "(подстрока в имени CPU и потолок EUR/прогон)")
    out = ["<b>Цели</b>", ""]
    for i, t in enumerate(tg_list, 1):
        good = []
        for s in servers:
            if t["q"].lower() in cpu(s).lower():
                sc = score(s)
                if sc and sc["cost"] <= t["max"]:
                    good.append((sc["cost"], s))
        good.sort(key=lambda x: x[0])
        out.append("%d. «%s» до EUR %g" % (i, esc(t["q"]), t["max"]))
        out.append("   наблюдение %d дн · всего появлений %d"
                   % (t.get("days", 0), t.get("hits", 0)))
        line = "   сейчас: %d" % len(good)
        if good:
            line += " · лучший EUR %.0f" % good[0][0]
        out.append(line)
        if t.get("closest"):
            out.append("   ближайшее: " + esc(t["closest"]))
    out += ["", "Удалить: /untarget 1"]
    return "\n".join(out)


HELP = ("Сводка — рынок и вердикт\n"
        "Топ — лучшие по EUR/прогон\n"
        "Проверить — что проходит пороги\n"
        "Статус — цифры и настройки\n"
        "Процессоры — /cpu, /cpu xeon\n"
        "Цели — /watch EPYC 73 120, /targets\n"
        "/why 3054238 — разбор лота\n"
        "/db — справочник CPU\n"
        "/set cost 80 — пороги\n"
        "Лог — вывод запуска")


# ---------- команды ----------

def handle(state, servers):
    st = state["settings"]
    hist = state["history"]
    try:
        raw = http("https://api.telegram.org/bot" + TOKEN
                   + "/getUpdates?offset=" + str(state.get("offset", 0))
                   + "&timeout=0", timeout=20)
        ups = json.loads(raw).get("result", [])
    except Exception as e:
        log("getUpdates FAIL: " + str(e))
        return

    for u in ups:
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(dig(msg, "chat.id", "")) != str(CHAT):
            continue
        raw_t = (msg.get("text") or "").strip()
        t = raw_t.lower()
        arg = raw_t.split(None, 1)[1].strip() if " " in raw_t else ""
        log("CMD " + raw_t)

        if t in ("сводка", "/daily", "/start"):
            send(summary(servers, st, hist), LINK)
        elif t.startswith(("топ", "/best", "/top")):
            n = int(arg) if arg.isdigit() else 10
            r = ranked(servers)
            out = ["<b>Топ-%d по EUR/прогон</b> (из %d с ECC)" % (n, len(r)), ""]
            for c, s, sc in r[:n]:
                out.append("EUR %.0f · %.1fсут · %.1feq · %.1fГБ/с/c\n"
                           "%s · %.0fGB · %.0f/мес\n"
                           "https://www.hetzner.com/sb/#search=%s\n"
                           % (c, sc["days"], sc["equiv"], sc["bw_core"],
                              esc(cpu(s)), ram(s), price(s), sid(s)))
            send("\n".join(out))
        elif t in ("проверить", "/check"):
            hit = [x for x in ranked(servers) if matches(x[1], x[2], st)]
            send("Проверено %d лотов.\nПроходят порог: %d"
                 % (len(servers), len(hit)), LINK)
            for c, s, sc in hit[:MAX_NOTIFY]:
                send(card(s, sc))
        elif t in ("статус", "/status"):
            send(status(servers, st, len(state["seen"]), hist))
        elif t.startswith(("процессоры", "/cpu")):
            send(cpu_list(servers, arg))
        elif t.startswith("/why"):
            send(why(servers, arg, st))
        elif t in ("/db", "справочник"):
            send(db_text())
        elif t.startswith("/set"):
            p = arg.split()
            if len(p) == 2 and p[0] in st:
                try:
                    st[p[0]] = int(float(p[1])) if p[0] == "ecc" else float(p[1])
                    send("%s = %g" % (p[0], st[p[0]]))
                except ValueError:
                    send("Не число")
            else:
                send("/set cost|days|equiv|ecc <число>\nСейчас: "
                     + " · ".join("%s=%g" % (k, v) for k, v in st.items()))
        elif t.startswith("/watch"):
            p = arg.rsplit(None, 1)
            if len(p) == 2 and p[1].replace(".", "", 1).isdigit():
                state["targets"].append({"q": p[0], "max": float(p[1]),
                                         "since": today(), "days": 0, "hits": 0})
                send("Цель добавлена: «" + esc(p[0]) + "» до EUR " + p[1])
            else:
                send("/watch EPYC 73 120")
        elif t in ("цели", "/targets"):
            send(targets_text(servers, state["targets"]))
        elif t.startswith("/untarget"):
            try:
                state["targets"].pop(int(arg) - 1)
                send("Удалено")
            except Exception:
                send("Номер не найден")
        elif t in ("лог", "/log"):
            send("<b>Лог</b>\n<code>" + esc("\n".join(LOG[-40:])) + "</code>")
        elif t in ("/help", "помощь"):
            send(HELP, LINK)


# ---------- состояние ----------

def load():
    s = {}
    if STATE.exists():
        try:
            s = json.loads(STATE.read_text())
        except Exception:
            s = {}
    elif OLD.exists():
        try:
            s = {"seen": json.loads(OLD.read_text())}
        except Exception:
            s = {}
    s.setdefault("seen", [])
    s.setdefault("offset", 0)
    s.setdefault("history", [])
    s.setdefault("targets", [])
    s.setdefault("last_summary", "")
    st = s.setdefault("settings", {})
    for k, v in DEFAULTS.items():
        st.setdefault(k, v)
    return s


def main():
    state = load()
    st = state["settings"]
    try:
        servers = fetch()
    except SystemExit as e:
        send("hzbot: не удалось получить данные\n" + esc(str(e)))
        STATE.write_text(json.dumps(state, ensure_ascii=False))
        raise
    if not servers:
        raise SystemExit("Пустой список")

    log("lots=" + str(len(servers)))

    prev = set(state["seen"])
    first = len(prev) <= 1
    seen = set()
    hits = []
    for s in servers:
        i = sid(s)
        if not i:
            continue
        seen.add(i)
        if i in prev:
            continue
        sc = score(s)
        if matches(s, sc, st):
            hits.append((sc["cost"], s, sc))
    hits.sort(key=lambda x: x[0])

    if first:
        try:
            tg("setMyCommands", commands=[
                {"command": "daily",   "description": "Сводка и вердикт"},
                {"command": "best",    "description": "Топ по EUR/прогон"},
                {"command": "check",   "description": "Что проходит пороги"},
                {"command": "status",  "description": "Цифры и настройки"},
                {"command": "cpu",     "description": "Процессоры в аукционе"},
                {"command": "why",     "description": "Разбор лота по id"},
                {"command": "db",      "description": "Справочник CPU"},
                {"command": "targets", "description": "Цели наблюдения"},
                {"command": "help",    "description": "Команды"},
            ])
        except Exception as e:
            log("setMyCommands FAIL: " + str(e))
        send("hzbot запущен", LINK)
        send(summary(servers, st, state["history"]))

    for c, s, sc in hits[:MAX_NOTIFY]:
        send(card(s, sc))
    if len(hits) > MAX_NOTIFY:
        send("Ещё %d лотов не показаны" % (len(hits) - MAX_NOTIFY))

    if state["last_summary"] != today() and now().hour >= SUMMARY_AT:
        r = ranked(servers)
        if not first:
            send(summary(servers, st, state["history"]), LINK)
        state["history"].append({
            "date": today(),
            "lots": len(servers),
            "ecc": sum(1 for s in servers if ecc(s)),
            "epyc": sum(1 for s in servers if "epyc" in cpu(s).lower()),
            "best": round(r[0][0], 1) if r else None,
            "best_cpu": cpu(r[0][1]) if r else None,
        })
        state["history"] = state["history"][-HISTORY_DAYS:]
        for t in state["targets"]:
            t["days"] = t.get("days", 0) + 1
            found = [s for s in servers if t["q"].lower() in cpu(s).lower()]
            if found:
                t["hits"] = t.get("hits", 0) + len(found)
                t["closest"] = cpu(found[0]) + " · " + today()
        state["last_summary"] = today()

    handle(state, servers)

    state["seen"] = sorted(seen)
    STATE.write_text(json.dumps(state, ensure_ascii=False))
    log("hits=%d seen=%d" % (len(hits), len(seen)))


main()
