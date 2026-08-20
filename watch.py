import json, math, os, pathlib, re, urllib.request, urllib.parse
from collections import Counter
from datetime import datetime, timedelta, timezone

# ================= НАСТРОЙКИ =================
REPO       = "ltybc111222/hzbot"
TZ_OFFSET  = 2          # Прага летом
SUMMARY_AT = 9          # час ежедневной сводки

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

# каталожная база для вердикта: имя, ядра, каналы, MT/s, L3, k, €/мес, €/ч, setup
CATALOG = [
    ("AX162 (EPYC 9454P)", 48, 12, 4800, 32, 0.85, 229.0, 0.3670, 79.0),
    ("AX102 (7950X3D)",    16,  2, 4800, 96, 0.88, 104.0, 0.1666, 269.0),
    ("AX52 (Ryzen 7 7700)", 8,  2, 4800, 32, 0.85,  80.0, 0.1282,  79.0),
]

FEEDS = [
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb.json",
    "https://www.hetzner.com/_resources/app/data/app/live_data_sb_EUR.json",
]
UA      = "Mozilla/5.0 (compatible; hzbot/7.0)"
RUN_URL = f"https://github.com/{REPO}/actions/workflows/watch.yml"
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
    return (str(t).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


def tg(method, **p):
    for k, v in list(p.items()):
        if isinstance(v, (dict, list)):
            p[k] = json.dumps(v)
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{TOKEN}/{method}",
        data=urllib.parse.urlencode(p).encode())
    return json.loads(urllib.request.urlopen(req, timeout=35).read())


def send(text, markup=None):
    for i in range(0, max(len(text), 1), 3900):
        chunk = text[i:i + 3900]
        try:
            tg("sendMessage", chat_id=CHAT, text=chunk, parse_mode="HTML",
               disable_web_page_preview="true", reply_markup=markup or KB)
        except Exception as e:
            log(f"SEND FAIL: {e}")


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
    raise ValueError(f"ключи = {list(doc)[:20]}")


def fetch():
    for url in FEEDS + discover():
        try:
            out = unwrap(json.loads(http(url)))
            log(f"FEED OK: {url}")
            return out
        except Exception as e:
            log(f"FEED FAIL: {type(e).__name__} {e}")
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


def sid(s):     return str(dig(s, "Id", ""))
def cpu(s):     return str(dig(s, "Hardware.CPU.Name", "") or "")
def sockets(s): return dig(s, "Hardware.CPU.CoreCount", 1) or 1
def ecc(s):     return bool(dig(s, "Hardware.RAM.ecc", False))
def price(s):   return float(dig(s, "Prices.monthly.EUR", 0) or 0)
def hourly(s):  return float(dig(s, "Prices.hourly.EUR", 0) or 0)
def setup(s):   return float(dig(s, "Prices.setup.EUR", 0) or 0)


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
    bw = ch * mts * 8 / 1000
    sat = min(1.0, bw / (cores * WORKLOAD_BW)) if cores else 0
    eq = cores * k * sat
    if eq <= 0:
        return None
    h = CPU_HOURS_PER_SEED / eq
    cost = min(h * hr, math.ceil(h / HOURS_TO_CAP) * mo) + su if (hr or mo) else 0
    return dict(cores=cores, channels=ch, l3=l3, k=k, numa=numa, bw=bw,
                bw_core=bw / cores, equiv=eq, hours=h, days=h / 24, cost=cost,
                known=True)


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
    return (sc is not None
            and (ecc(s) or not st["ecc"])
            and ram(s) >= 2 * sc["cores"] + 8
            and sc["cost"] <= st["cost"]
            and sc["days"] <= st["days"]
            and sc["equiv"] >= st["equiv"])


# ---------- тексты ----------

def card(s, sc):
    return (f"<b>{esc(cpu(s))}</b>\n"
            f"EUR {sc['cost']:.0f} за прогон · {sc['days']:.1f} сут/сид\n"
            f"{sc['cores']}c · {sc['equiv']:.1f} экв. M1 · "
            f"{sc['bw_core']:.1f} ГБ/с на ядро\n"
            f"L3 пул {sc['l3']:.0f} МБ{' · NUMA' if sc['numa'] else ''}\n"
            f"RAM {ram(s):.0f} GB{' ECC' if ecc(s) else ''} · {esc(disks(s))}\n"
            f"EUR {price(s):.2f}/мес · EUR {hourly(s):.4f}/ч · "
            f"setup {setup(s):.0f}\n"
            f"https://www.hetzner.com/sb/#search={sid(s)}")


def verdict(servers):
    r = ranked(servers)
    cat = catalog_best()
    if not cat:
        return "Каталожная база не задана."
    cb, cname, cr = cat[0]
    if not r:
        return (f"<b>Вердикт: НЕ ПОКУПАТЬ</b>\nВ аукционе нечего оценивать.\n"
                f"Каталог: {esc(cname)} — EUR {cb:.0f}/прогон, {cr['days']:.1f} сут")
    ab, asrv, asc = r[0]
    ratio = ab / cb if cb else 0
    head = "ПОКУПАТЬ В АУКЦИОНЕ" if ab < cb else "НЕ ПОКУПАТЬ"
    return (f"<b>Вердикт: {head}</b>\n"
            f"Аукцион: EUR {ab:.0f}/прогон · {asc['days']:.1f} сут · "
            f"{esc(cpu(asrv))}\n"
            f"Каталог: EUR {cb:.0f}/прогон · {cr['days']:.1f} сут · {esc(cname)}\n"
            f"Отношение: {ratio:.1f}x\n"
            f"Разворот при аукционном лоте дешевле EUR {cb:.0f}/прогон")


def summary(servers, st, hist):
    r = ranked(servers)
    ep = sum(1 for s in servers if "epyc" in cpu(s).lower())
    prev = hist[-1] if hist else None
    d = f" ({servers and prev and len(servers) - prev['lots']:+d})" if prev else ""

    streak = 0
    for h in reversed(hist):
        if h.get("epyc", 0) == 0:
            streak += 1
        else:
            break
    if ep == 0:
        streak += 1

    med = ""
    vals = sorted(h["best"] for h in hist[-14:] if h.get("best"))
    if vals:
        med = f"\nМедиана лучшего за {len(vals)} дн: EUR {vals[len(vals)//2]:.0f}"

    lines = [f"<b>Аукцион · {now():%d.%m %H:%M}</b>",
             f"Лотов {len(servers)}{d} · ECC {sum(1 for s in servers if ecc(s))} "
             f"· оценено {len(r)}", ""]
    lines.append("<b>Лучшее по EUR/прогон</b>")
    for i, (c, s, sc) in enumerate(r[:3], 1):
        lines.append(f"{i}. EUR {c:.0f} · {sc['days']:.1f}сут · "
                     f"{esc(cpu(s))} · {ram(s):.0f}GB · {price(s):.0f}/мес")
    if not r:
        lines.append("нет оцениваемых лотов")
    lines += ["", verdict(servers), "",
              f"EPYC в аукционе: {ep} (подряд без: {streak} дн){med}"]
    return "\n".join(lines)


def status(servers, st, seen_n, hist):
    cnt = Counter(cpu(s) for s in servers)
    unk = {cpu(s) for s in servers if not spec_of(cpu(s))}
    r = ranked(servers)
    return ("<b>Статус</b>\n"
            f"Лотов {len(servers)} · в памяти {seen_n} · история {len(hist)} дн\n"
            f"ECC {sum(1 for s in servers if ecc(s))} · оценено {len(r)} · "
            f"вне справочника {len(unk)}\n\n"
            f"<b>Пороги</b>\ncost {st['cost']} · days {st['days']} · "
            f"equiv {st['equiv']} · ecc {'да' if st['ecc'] else 'нет'}\n"
            f"Меняются: /set cost 80\n\n"
            "<b>Частые CPU</b>\n" +
            "\n".join(f"{n:3d}  {esc(k)}" for k, n in cnt.most_common(8)) +
            (f"\n\n<b>Нет в справочнике</b>\n" +
             "\n".join(esc(x) for x in sorted(unk)[:8]) if unk else ""))


def cpu_list(servers, flt=""):
    sel = [s for s in servers if flt.lower() in cpu(s).lower()] if flt else servers
    if not sel:
        return f"Нет лотов по запросу «{esc(flt)}»"
    if not flt:
        cnt = Counter(cpu(s) for s in sel)
        out = [f"<b>Процессоры в аукционе ({len(cnt)} моделей)</b>",
               "Подробнее: /cpu xeon\n"]
        for name, n in cnt.most_common(30):
            sp = spec_of(name)
            tag = f"{sp[0]}c/{sp[1]}ch" if sp else "нет в справочнике"
            out.append(f"{n:3d}  {esc(name)}  · {tag}")
        return "\n".join(out)
    out = [f"<b>«{esc(flt)}» — {len(sel)} лотов</b>\n"]
    rows = ranked(sel, need_ecc=False)
    for c, s, sc in rows[:15]:
        out.append(f"EUR {c:.0f}/прогон · {sc['days']:.1f}сут · "
                   f"{sc['equiv']:.1f}eq\n{esc(cpu(s))} · {ram(s):.0f}GB"
                   f"{' ECC' if ecc(s) else ''} · {price(s):.0f}/мес\n"
                   f"https://www.hetzner.com/sb/#search={sid(s)}\n")
    for s in sel:
        if not spec_of(cpu(s)):
            out.append(f"⧗ {esc(cpu(s))} — нет в справочнике")
            break
    return "\n".join(out)


def why(servers, wid, st):
    tgt = next((s for s in servers if sid(s) == wid), None)
    if not tgt:
        return f"Лот {esc(wid)} не найден в текущей выдаче"
    sc = score(tgt)
    if not sc:
        return (f"<b>{esc(cpu(tgt))}</b> · id {wid}\n"
                f"Нет в справочнике CPU — оценить невозможно.\n"
                f"Добавьте модель в CPU_DB.")
    need_ram = 2 * sc["cores"] + 8
    rows = [
        ("ECC", "да" if ecc(tgt) else "нет", "да" if st["ecc"] else "любое",
         ecc(tgt) or not st["ecc"]),
        ("RAM", f"{ram(tgt):.0f}", f">={need_ram:.0f}", ram(tgt) >= need_ram),
        ("EUR/прогон", f"{sc['cost']:.0f}", f"<={st['cost']}",
         sc["cost"] <= st["cost"]),
        ("Суток", f"{sc['days']:.1f}", f"<={st['days']}", sc["days"] <= st["days"]),
        ("Экв. ядер", f"{sc['equiv']:.1f}", f">={st['equiv']}",
         sc["equiv"] >= st["equiv"]),
    ]
    body = "\n".join(f"{n:12s} {v:>8s}  {t:>8s}  {'OK' if ok else 'нет'}"
                     for n, v, t, ok in rows)
    bad = [n for n, _, _, ok in rows if not ok]
    tail = ("Проходит по всем критериям." if not bad
            else f"Не проходит: {', '.join(bad)}")
    return (f"<b>{esc(cpu(tgt))}</b> · id {wid}\n"
            f"{sc['cores']}c · {sc['channels']}ch · {sc['bw_core']:.1f} ГБ/с на ядро "
            f"· L3 пул {sc['l3']:.0f} МБ\n\n<code>{body}</code>\n\n{tail}")


def db_text():
    out = ["<b>Справочник CPU</b>", "ядра/каналы/L3 пул/k_equiv", ""]
    for pat, c, ch, mts, l3, k, numa in CPU_DB:
        out.append(f"{esc(pat):24s} {c:3d}c {ch:2d}ch {l3:5.1f}МБ k={k:.2f}"
                   + (" NUMA" if numa else ""))
    out += ["", "Все k_equiv оценочные, ни один не измерен.",
            "До замера зондом рейтинг — ранжирование догадок.", "",
            "<b>Каталог для вердикта</b>"]
    for c, name, r in catalog_best():
        out.append(f"EUR {c:.0f}/прогон · {r['days']:.1f}сут · {esc(name)}")
    return "\n".join(out)


def targets_text(servers, tg_list):
    if not tg_list:
        return ("Целей нет.\nДобавить: /watch EPYC 73 120\n"
                "(подстрока в имени CPU и потолок EUR/прогон)")
    out = ["<b>Цели</b>\n"]
    for i, t in enumerate(tg_list, 1):
        hits = [s for s in servers if t["q"].lower() in cpu(s).lower()]
        good = []
        for s in hits:
            sc = score(s)
            if sc and sc["cost"] <= t["max"]:
                good.append((sc["cost"], s))
        good.sort(key=lambda x: x[0])
        out.append(f"{i}. «{esc(t['q'])}» до EUR {t['max']}\n"
                   f"   наблюдение {t.get('days', 0)} дн · всего появлений "
                   f"{t.get('hits', 0)}\n"
                   f"   сейчас: {len(good)}"
                   + (f" · лучший EUR {good[0][0]:.0f}" if good else ""))
        if t.get("closest"):
            out.append(f"   ближайшее: {esc(t['closest'])}")
    out.append("\nУдалить: /untarget 1")
    return "\n".join(out)


# ---------- команды ----------

def handle(state, servers):
    st, hist = state["settings"], state["history"]
    try:
        raw = http(f"https://api.telegram.org/bot{TOKEN}/getUpdates"
                   f"?offset={state.get('offset',0)}&timeout=0", timeout=20)
        ups = json.loads(raw).get("result", [])
    except Exception as e:
        log(f"getUpdates FAIL: {e}")
        return

    for u in ups:
        state["offset"] = u["update_id"] + 1
        msg = u.get("message") or {}
        if str(dig(msg, "chat.id", "")) != str(CHAT):
            continue
        raw_t = (msg.get("text") or "").strip()
        t = raw_t.lower()
        arg = raw_t.split(maxsplit=1)[1] if " " in raw_t else ""
        log(f"CMD {raw_t}")

        if t in ("сводка", "/daily", "/start"):
            send(summary(servers, st, hist), LINK)
        elif t.startswith(("топ", "/best", "/top")):
            n = int(arg) if arg.isdigit() else 10
            r = ranked(servers)
            out = [f"<b>Топ-{n} по EUR/прогон</b> (из {len(r)} с ECC)\n"]
            for c, s, sc in r[:n]:
                out.append(f"EUR {c:.0f} · {sc['days']:.1f}сут · "
                           f"{sc['equiv']:.1f}eq · {sc['bw_core']:.1f}ГБ/с/c\n"
                           f"{esc(cpu(s))} · {ram(s):.0f}GB · {price(s):.0f}/мес\n"
                           f"https://www.hetzner.com/sb/#search={sid(s)}\n")
            send("\n".join(out))
        elif t in ("проверить", "/check"):
            hit = [x for x in ranked(servers) if matches(x[1], x[2], st)]
            send(f"Проверено {len(servers)} лотов.\n"
                 f"Проходят порог: {len(hit)}", LINK)
            for _, s, sc in hit[:MAX_NOTIFY]:
                send(card(s, sc))
        elif t in ("статус", "/status"):
            send(status(servers, st, len(state["seen"]), hist))
        elif t.startswith(("процессоры", "/cpu")):
            send(cpu_list(servers, arg))
        elif t.startswith("/why"):
            send(why(servers, arg.strip(), st))
        elif t in ("/db", "справочник"):
            send(db_text())
        elif t.startswith("/set"):
            p = arg.split()
            if len(p) == 2 and p[0] in st:
                try:
                    st[p[0]] = float(p[1]) if p[0] != "ecc" else int(float(p[1]))
                    send(f"{p[0]} = {st[p[0]]:g}")
                except ValueError:
                    send("Не число")
            else:
                send(f"/set cost|days|equiv|ecc <число>\nСейчас: "
                     + " · ".join(f"{k}={v:g}" for k, v in st.items()))
        elif t.startswith("/watch"):
            p = arg.rsplit(maxsplit=1)
            if len(p) == 2 and p[1].replace(".", "").isdigit():
                state["targets"].append({"q": p[0], "max": float(p[1]),
                                         "since": today(), "days": 0, "hits": 0})
                send(f"Цель добавлена: «{esc(p[0])}» до EUR {p[1]}")
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
            send("Сводка — рынок и вердикт\nТоп — лучшие по EUR/прогон\n"
                 "Проверить — что проходит пороги\nСтатус — цифры и настройки\n"
                 "Процессоры — /cpu, /cpu xeon\nЦели — /watch, /targets\n"
                 "/why &lt;id&gt; — разбор лота\n/db — справочник\n"
                 "/set cost 80 — пороги", LINK)


# ---------- состояние ----------

def load():
    if STATE.exists():
        s = json.loads(STATE.read_text())
    elif OLD.exists():
        s = {"seen": json.loads(OLD.read_text())}
    else:
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
    servers = fetch()
    if not servers:
        raise SystemExit("Пустой с
