#!/usr/bin/env python3
"""
⚽ Grid Search — NuovoMetodo (v11) su dati Wikipedia
========================================================
Scansiona una griglia di parametri del NuovoMetodo e identifica
le configurazioni migliori per Brier, LogLoss e ROI.

Osservazione chiave: nella formula NM, solo il RAPPORTO w_rec/w_prev
conta (non i loro valori assoluti), perché entrambi moltiplicano
numeratore e denominatore. Quindi w_rec è fissato a 1.0 e varia
solo w_prev. Questo elimina un'intera dimensione dalla ricerca.

Ulteriore deduplica: quando n_prev=0 oppure w_prev=0, la finestra
precedente è ignorata → tutte queste combinazioni collassano in una.

Parametri cercati (modificabili nel dict GRID in fondo a questa sezione):
  n_rec:  numero partite recenti (finestra ad alto peso)
  n_prev: numero partite precedenti (finestra a basso peso)
  w_prev: peso relativo della finestra precedente (w_rec = 1.0)
  bonus:  bonus prestazione graduato (0 = conteggio binario puro)

Output in ./gridsearch_output_wiki/:
  ├── serieB_all.csv              tutte le config testate, Serie B
  ├── serieCa_all.csv             tutte le config testate, Girone A
  ├── serieCb_all.csv             tutte le config testate, Girone B
  ├── serieCc_all.csv             tutte le config testate, Girone C
  └── gridsearch_report.txt       top-N per metrica + robustezza parametri
"""

import json, re, os, math, sys, csv, time
from datetime import date
from pathlib import Path
from collections import defaultdict, Counter
from itertools import product

# ── Dipendenze ────────────────────────────────────────────────
for pkg, imp in [("requests", "requests"), ("beautifulsoup4", "bs4")]:
    try:
        __import__(imp)
    except ImportError:
        os.system(f"{sys.executable} -m pip install {pkg} -q")

import requests as req
from bs4 import BeautifulSoup

# ═════════════════════════════════════════════════════════════
# GRIGLIA PARAMETRI — modifica qui per allargare/restringere
# ═════════════════════════════════════════════════════════════
GRID = {
    "n_rec":  [2, 3, 4, 5, 6, 7, 8],
    "n_prev": [0, 1, 2, 3, 4, 5, 6, 8],
    "w_prev": [0.0, 0.2, 0.4, 0.6, 0.8, 1.0],   # w_rec è fisso = 1.0
    "bonus":  [0.00, 0.05, 0.10, 0.15, 0.20, 0.25, 0.35, 0.50],
}

TOP_N = 10                       # quanti mostrare nei ranking
MIN_MATCHES_PER_TEAM = 3
ODD = 1.85
WIKI_SERIE_B = "https://it.wikipedia.org/wiki/Serie_B_2025-2026"
WIKI_SERIE_C = "https://it.wikipedia.org/wiki/Serie_C_2025-2026"
OUT_DIR = Path("./gridsearch_output_wiki")
OUT_DIR.mkdir(exist_ok=True)

MESI_IT = {
    "gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,
    "lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12,
}

# ─────────────────────────────────────────────────────────────
# FETCH + PARSER (allineato a pronostici_app_v11.py)
# ─────────────────────────────────────────────────────────────
def fetch_page(url):
    import urllib3
    urllib3.disable_warnings()
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    }
    try:
        r = req.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status(); r.encoding = "utf-8"; return r.text
    except Exception as e:
        print(f"  ❌ {url}: {e}"); return None

def _parse_italian_date(text):
    text = text.strip().rstrip(".")
    text = re.sub(r"(\d+)[ºª°]", r"\1", text)
    m = re.match(r"(\d{1,2})\s+(\w+)", text)
    if not m: return ""
    day, ms = int(m.group(1)), m.group(2).lower().rstrip(".")
    month = next((v for k, v in MESI_IT.items() if ms.startswith(k)), 0)
    if not month: return ""
    year = 2025 if month >= 7 else 2026
    try: return date(year, month, day).isoformat()
    except Exception: return ""

def _is_time_or_dash(t):
    t = t.strip()
    return t == "-" or bool(re.match(r"^\d{1,2}:\d{2}$", t))

def _parse_score(t):
    m = re.match(r"^(\d+)\s*[-\u2013]\s*(\d+)$", t.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None

def _split_match_name(text):
    parts = re.split(r"[-\u2013\u2014]", text)
    if len(parts) < 2: return None, None
    if len(parts) == 2:
        return parts[0].strip().replace("*",""), parts[1].strip().replace("*","")
    return "-".join(parts[:-1]).strip().replace("*",""), parts[-1].strip().replace("*","")

def _is_date_text(text):
    return bool(re.match(r"^\d{1,2}[ºª°]?\s+\w{3}", text.strip()))

def wiki_parse_serie_b(html):
    soup = BeautifulSoup(html, "html.parser")
    results = []; seen = set()
    for table in soup.find_all("table"):
        current_g, current_date = 0, ""
        for row in table.find_all("tr"):
            tds = row.find_all("td")
            if not tds or len(tds) > 10: continue
            if len(tds) == 3:
                mid = tds[1].get_text(strip=True)
                gm = re.match(r"^(\d+)[\u00AAa\u00B0]\s*giornata$", mid, re.IGNORECASE)
                if gm:
                    current_g = int(gm.group(1)); current_date = ""; continue
            if current_g == 0: continue
            if len(tds) == 3:
                pd = _parse_italian_date(tds[0].get_text(strip=True))
                if pd: current_date = pd
                match_text = tds[1].get_text(strip=True)
                score_text = tds[2].get_text(strip=True)
            elif len(tds) == 2:
                match_text = tds[0].get_text(strip=True)
                score_text = tds[1].get_text(strip=True)
            else: continue
            home, away = _split_match_name(match_text)
            if not home or not away: continue
            sc = _parse_score(score_text.strip())
            if sc:
                key = (current_date, home, away)
                if key not in seen:
                    results.append({"date": current_date, "home": home, "away": away,
                                    "hg": sc[0], "ag": sc[1]})
                    seen.add(key)
    return results

def wiki_parse_serie_c_girone(soup, girone_letter):
    girone_section = None
    for h2 in soup.find_all("h2"):
        if f"Girone {girone_letter}" in h2.get_text():
            girone_section = h2.parent.parent; break
    if not girone_section: return []
    all_tables = []
    for child in girone_section.children:
        if not hasattr(child, "name") or child.name != "section": continue
        heading = child.find(["h3", "h4"])
        if not heading: continue
        if "Calendario" in heading.get_text() or "Risultati" in heading.get_text():
            tbls = child.find_all("table")
            if len(tbls) >= 5: all_tables.extend(tbls)
    if not all_tables: return []
    results = []; seen = set()
    g_re = re.compile(r"^(\d+)[ªa°]\s*giornata$", re.IGNORECASE)
    for table in all_tables:
        cur_g_a = cur_g_r = 0; rs_a = rs_r = 0; date_a = date_r = ""
        for row in table.find_all("tr"):
            tds = row.find_all("td"); n = len(tds)
            if n == 0 or n > 10: continue
            if n == 3:
                mid = tds[1].get_text(strip=True)
                gm = g_re.match(mid)
                if gm:
                    cur_g_a = int(gm.group(1))
                    rit_m = re.search(r"(\d+)[ªa°]", tds[2].get_text(strip=True))
                    cur_g_r = int(rit_m.group(1)) if rit_m else cur_g_a + 19
                    rs_a = rs_r = 0; continue
            if cur_g_a == 0: continue
            idx = 0
            if rs_a <= 0:
                if idx < n and _is_date_text(tds[idx].get_text(strip=True)):
                    date_a = _parse_italian_date(tds[idx].get_text(strip=True))
                    rs_a = int(tds[idx].get("rowspan", 1)); idx += 1
                elif n < 3: continue
            rem = n - idx
            if rem < 3: continue
            score_a = tds[idx].get_text(strip=True); idx += 1
            teams_text = tds[idx].get_text(strip=True); idx += 1
            score_r = tds[idx].get_text(strip=True); idx += 1
            if rs_r <= 0 and idx < n:
                date_r = _parse_italian_date(tds[idx].get_text(strip=True))
                rs_r = int(tds[idx].get("rowspan", 1))
            rs_a -= 1; rs_r -= 1
            home, away = _split_match_name(teams_text)
            if not home or not away: continue
            # ANDATA
            sc = _parse_score(score_a)
            if sc:
                key = (date_a, home, away)
                if key not in seen:
                    results.append({"date": date_a, "home": home, "away": away,
                                    "hg": sc[0], "ag": sc[1]})
                    seen.add(key)
            # RITORNO (inverti punteggio — fix v10)
            sc = _parse_score(score_r)
            if sc:
                key = (date_r, away, home)
                if key not in seen:
                    results.append({"date": date_r, "home": away, "away": home,
                                    "hg": sc[1], "ag": sc[0]})
                    seen.add(key)
    return results

def scrape_wikipedia():
    print("\n📥 Scarico dati da Wikipedia...")
    data = {}
    print("  → Serie B...", end=" ", flush=True)
    html_b = fetch_page(WIKI_SERIE_B)
    if html_b:
        r = wiki_parse_serie_b(html_b); print(f"{len(r)} risultati"); data["Serie B"] = r
    else:
        print("FALLITO"); data["Serie B"] = []
    print("  → Serie C...", end=" ", flush=True)
    html_c = fetch_page(WIKI_SERIE_C)
    if html_c:
        soup_c = BeautifulSoup(html_c, "html.parser")
        for gl in ["A","B","C"]:
            data[f"Serie C Girone {gl}"] = wiki_parse_serie_c_girone(soup_c, gl)
        tot = sum(len(data[f"Serie C Girone {gl}"]) for gl in ["A","B","C"])
        print(f"{tot} risultati totali (3 gironi)")
    else:
        print("FALLITO")
        for gl in ["A","B","C"]: data[f"Serie C Girone {gl}"] = []
    return data

def load_from_json():
    for candidate in [Path("pronostici_data.json"),
                      Path(__file__).parent / "pronostici_data.json"]:
        if candidate.exists():
            print(f"\n  ⚠️  Uso dati locali: {candidate.resolve()}")
            with open(candidate, "r", encoding="utf-8") as f:
                jdata = json.load(f)
            mapping = {"Serie B":"serieB","Serie C Girone A":"serieCa",
                       "Serie C Girone B":"serieCb","Serie C Girone C":"serieCc"}
            result = {}
            for name, key in mapping.items():
                raw = jdata.get(key, {}).get("results", [])
                norm = []
                for r in raw:
                    if isinstance(r, list):
                        norm.append({"date": r[0], "home": r[1], "away": r[2],
                                     "hg": int(r[3]), "ag": int(r[4])})
                    else: norm.append(r)
                result[name] = norm
            total = sum(len(v) for v in result.values())
            print(f"  Caricati {total} risultati dal JSON locale")
            return result
    return None

# ─────────────────────────────────────────────────────────────
# NUOVOMETODO + WALK-FORWARD (allineato a pronostici_app_v11.py)
# ─────────────────────────────────────────────────────────────
def _nm(matches_asc, score_fn, n_recent, n_prev, w_recent, w_prev, max_score):
    n = len(matches_asc)
    if n == 0: return None
    if n < n_recent:
        total = sum(score_fn(m) for m in matches_asc)
        return total / (n * max_score) if max_score > 0 else None
    last = matches_asc[-n_recent:]
    prev_start = max(0, n - n_recent - n_prev)
    prev = matches_asc[prev_start:n - n_recent] if n_recent < n else []
    actual_prev = min(len(prev), n_prev)
    prev = prev[-actual_prev:] if actual_prev > 0 else []
    s_last = sum(score_fn(m) for m in last)
    s_prev = sum(score_fn(m) for m in prev)
    norm = max_score * (w_recent * len(last) + w_prev * len(prev))
    return (s_last * w_recent + s_prev * w_prev) / norm if norm > 0 else None

def _mk_score(bonus):
    def sc_over(g):
        if g == 0: return 0.0
        if g == 1: return 1.0
        return 1.0 + bonus
    def sc_under(g):
        if g == 0: return 1.0 + bonus
        if g == 1: return 1.0
        return 0.0
    return sc_over, sc_under

def walk_forward(sorted_results, bet_type, cfg):
    """bet_type: 'B' (casa over 0.5) | 'C' (ospite under 1.5).
    sorted_results: partite già ordinate ASCENDENTE per data."""
    home_hist = defaultdict(list)
    away_hist = defaultdict(list)
    sc_over, sc_under = _mk_score(cfg["bonus"])
    max_score = 1.0 + cfg["bonus"]
    n_rec  = cfg["n_rec"]
    n_prev = cfg["n_prev"]
    w_rec  = 1.0
    w_prev = cfg["w_prev"]

    preds = []
    for m in sorted_results:
        h, a, hg, ag = m["home"], m["away"], m["hg"], m["ag"]
        h_hist = home_hist[h]
        a_hist = away_hist[a]
        if len(h_hist) >= MIN_MATCHES_PER_TEAM and len(a_hist) >= MIN_MATCHES_PER_TEAM:
            if bet_type == "B":
                p_h = _nm(h_hist, lambda x: sc_over(x[0]),  n_rec, n_prev, w_rec, w_prev, max_score)
                p_a = _nm(a_hist, lambda x: sc_over(x[0]),  n_rec, n_prev, w_rec, w_prev, max_score)
                outcome = 1 if hg >= 1 else 0
            else:
                p_h = _nm(h_hist, lambda x: sc_under(x[1]), n_rec, n_prev, w_rec, w_prev, max_score)
                p_a = _nm(a_hist, lambda x: sc_under(x[1]), n_rec, n_prev, w_rec, w_prev, max_score)
                outcome = 1 if ag <= 1 else 0
            if p_h is not None and p_a is not None:
                preds.append({"prob": p_h * p_a, "outcome": outcome})
        home_hist[h].append((hg, ag))
        away_hist[a].append((hg, ag))
    return preds

# ─────────────────────────────────────────────────────────────
# METRICHE
# ─────────────────────────────────────────────────────────────
def brier(preds):
    if not preds: return float("nan")
    return sum((p["prob"] - p["outcome"]) ** 2 for p in preds) / len(preds)

def logloss(preds, eps=1e-7):
    if not preds: return float("nan")
    return -sum(p["outcome"] * math.log(p["prob"] + eps) +
                (1 - p["outcome"]) * math.log(1 - p["prob"] + eps)
                for p in preds) / len(preds)

def acc_roi_at(preds, thr, odd=ODD):
    sub = [p for p in preds if p["prob"] >= thr]
    if not sub: return 0.0, 0.0, 0
    wins = sum(1 for p in sub if p["outcome"] == 1)
    inv  = len(sub)
    gain = wins * odd
    return wins / inv, (gain - inv) / inv * 100, inv

# ─────────────────────────────────────────────────────────────
# GRID GENERATION (con dedup)
# ─────────────────────────────────────────────────────────────
def generate_configs(grid):
    """Genera tutte le combinazioni uniche della griglia.
    Caso speciale: se n_prev=0 o w_prev=0, la finestra precedente
    è inattiva → canonicalizzo a (n_prev=0, w_prev=0)."""
    seen = set()
    configs = []
    for n_rec, n_prev, w_prev, bonus in product(
        grid["n_rec"], grid["n_prev"], grid["w_prev"], grid["bonus"]
    ):
        # Canonicalizzazione
        if n_prev == 0 or w_prev == 0:
            n_prev_c, w_prev_c = 0, 0.0
        else:
            n_prev_c, w_prev_c = n_prev, w_prev
        key = (n_rec, n_prev_c, round(w_prev_c, 3), round(bonus, 3))
        if key in seen: continue
        seen.add(key)
        configs.append({
            "n_rec": n_rec, "n_prev": n_prev_c,
            "w_prev": w_prev_c, "bonus": bonus,
        })
    return configs

# ─────────────────────────────────────────────────────────────
# GRID SEARCH SU UN CAMPIONATO
# ─────────────────────────────────────────────────────────────
def grid_search_league(results, bet_type, configs, league_name):
    """Per ogni config, esegue walk_forward e calcola metriche."""
    sorted_r = sorted(results, key=lambda x: x.get("date", ""))
    rows = []
    n_total = len(configs)
    t0 = time.time()
    for i, cfg in enumerate(configs, 1):
        preds = walk_forward(sorted_r, bet_type, cfg)
        if not preds:
            rows.append({**cfg, "n": 0, "brier": float("nan"), "logloss": float("nan"),
                         "acc60": 0, "roi60": 0, "n60": 0,
                         "acc70": 0, "roi70": 0, "n70": 0})
            continue
        bs = brier(preds); ll = logloss(preds)
        a60, r60, n60 = acc_roi_at(preds, 0.60)
        a70, r70, n70 = acc_roi_at(preds, 0.70)
        rows.append({
            **cfg, "n": len(preds), "brier": bs, "logloss": ll,
            "acc60": a60, "roi60": r60, "n60": n60,
            "acc70": a70, "roi70": r70, "n70": n70,
        })
        if i % 200 == 0 or i == n_total:
            dt = time.time() - t0
            eta = dt / i * (n_total - i)
            print(f"    {league_name}: {i}/{n_total} ({dt:.0f}s, ETA {eta:.0f}s)")
    return rows

# ─────────────────────────────────────────────────────────────
# CSV OUTPUT
# ─────────────────────────────────────────────────────────────
def write_csv(rows, path):
    if not rows: return
    keys = ["n_rec","n_prev","w_prev","bonus","n",
            "brier","logloss","acc60","roi60","n60","acc70","roi70","n70"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in keys}
            # Arrotonda i float per leggibilità
            for k in ["brier","logloss","acc60","roi60","acc70","roi70"]:
                v = out[k]
                if isinstance(v, float) and not math.isnan(v):
                    out[k] = round(v, 4)
            out["w_prev"] = round(out["w_prev"], 2)
            out["bonus"]  = round(out["bonus"],  2)
            w.writerow(out)

# ─────────────────────────────────────────────────────────────
# REPORT
# ─────────────────────────────────────────────────────────────
SEP = "═" * 78

def top_by(rows, metric, ascending, top_n=TOP_N, min_n=50):
    """Top-N per una metrica. min_n: ignora configs con troppe poche previsioni."""
    valid = [r for r in rows if r["n"] >= min_n and not math.isnan(r.get(metric, float("nan")))]
    return sorted(valid, key=lambda r: r[metric], reverse=not ascending)[:top_n]

def param_frequency(top_rows):
    """Quanti dei top X hanno ciascun valore di ogni parametro."""
    counts = {
        "n_rec":  Counter(r["n_rec"]  for r in top_rows),
        "n_prev": Counter(r["n_prev"] for r in top_rows),
        "w_prev": Counter(round(r["w_prev"], 2) for r in top_rows),
        "bonus":  Counter(round(r["bonus"],  2) for r in top_rows),
    }
    return counts

def format_config(r):
    return f"n_rec={r['n_rec']} n_prev={r['n_prev']} w_prev={r['w_prev']:.2f} bonus={r['bonus']:.2f}"

def write_report(all_results, source_label, total_configs, n_unique, f=None):
    def w(s=""):
        print(s)
        if f: f.write(s + "\n")

    w(SEP)
    w("  GRID SEARCH — NuovoMetodo (v11)  —  Serie B & C 2025-2026")
    w(f"  Fonte: {source_label}  |  Quota simulata: {ODD}  |  Min. previsioni per classifica: 50")
    w(f"  Config generate: {total_configs}  |  Config uniche dopo dedup: {n_unique}")
    w(f"  Griglia: n_rec {GRID['n_rec']}  n_prev {GRID['n_prev']}")
    w(f"           w_prev {GRID['w_prev']}  bonus {GRID['bonus']}")
    w(f"  Nota: w_rec è fisso a 1.0 — solo il rapporto w_rec/w_prev conta.")
    w(f"  Generato: {date.today().isoformat()}")
    w(SEP)

    for league_name, bet_type, rows in all_results:
        label = "CASA OVER 0.5" if bet_type == "B" else "OSPITE UNDER 1.5"
        n_pred_sample = rows[0]["n"] if rows and rows[0]["n"] > 0 else 0
        w(); w("━" * 78)
        w(f"  {league_name}  —  {label}  ({n_pred_sample} previsioni walk-forward)")
        w("━" * 78)

        # --- Top per Brier (minore = meglio) ---
        w()
        w(f"  TOP-{TOP_N} per BRIER SCORE (↓ meglio)")
        w(f"  {'#':>3}  {'Brier':>6}  {'LL':>6}  {'Acc60%':>7}  {'ROI60%':>7}  {'N60':>4}  Config")
        w("  " + "─" * 74)
        for i, r in enumerate(top_by(rows, "brier", ascending=True), 1):
            w(f"  {i:>3}  {r['brier']:>6.4f}  {r['logloss']:>6.4f}  "
              f"{r['acc60']*100:>6.1f}%  {r['roi60']:>+6.1f}%  {r['n60']:>4}  {format_config(r)}")

        # --- Top per LogLoss ---
        w()
        w(f"  TOP-{TOP_N} per LOG-LOSS (↓ meglio)")
        w(f"  {'#':>3}  {'LL':>6}  {'Brier':>6}  {'Acc60%':>7}  {'ROI60%':>7}  {'N60':>4}  Config")
        w("  " + "─" * 74)
        for i, r in enumerate(top_by(rows, "logloss", ascending=True), 1):
            w(f"  {i:>3}  {r['logloss']:>6.4f}  {r['brier']:>6.4f}  "
              f"{r['acc60']*100:>6.1f}%  {r['roi60']:>+6.1f}%  {r['n60']:>4}  {format_config(r)}")

        # --- Top per ROI@60% (maggiore = meglio, con volume minimo) ---
        w()
        w(f"  TOP-{TOP_N} per ROI@60%  (min 30 scommesse)")
        w(f"  {'#':>3}  {'ROI60':>7}  {'N60':>4}  {'Acc60':>7}  {'Brier':>6}  Config")
        w("  " + "─" * 74)
        valid_roi = [r for r in rows if r["n60"] >= 30 and r["n"] >= 50]
        for i, r in enumerate(sorted(valid_roi, key=lambda x: -x["roi60"])[:TOP_N], 1):
            w(f"  {i:>3}  {r['roi60']:>+6.1f}%  {r['n60']:>4}  "
              f"{r['acc60']*100:>6.1f}%  {r['brier']:>6.4f}  {format_config(r)}")

        # --- Top per ROI@70% ---
        w()
        w(f"  TOP-{TOP_N} per ROI@70%  (min 20 scommesse)")
        w(f"  {'#':>3}  {'ROI70':>7}  {'N70':>4}  {'Acc70':>7}  {'Brier':>6}  Config")
        w("  " + "─" * 74)
        valid_roi = [r for r in rows if r["n70"] >= 20 and r["n"] >= 50]
        for i, r in enumerate(sorted(valid_roi, key=lambda x: -x["roi70"])[:TOP_N], 1):
            w(f"  {i:>3}  {r['roi70']:>+6.1f}%  {r['n70']:>4}  "
              f"{r['acc70']*100:>6.1f}%  {r['brier']:>6.4f}  {format_config(r)}")

        # --- Analisi robustezza parametri ---
        top_brier = top_by(rows, "brier", ascending=True, top_n=20)
        if top_brier:
            freq = param_frequency(top_brier)
            w()
            w("  Robustezza parametri — frequenza nei top 20 per Brier")
            for pname in ["n_rec", "n_prev", "w_prev", "bonus"]:
                items = sorted(freq[pname].items(), key=lambda x: -x[1])
                line = "  " + f"{pname:<8}".ljust(10) + "  "
                line += "  ".join(f"{v}→{c}" for v, c in items[:6])
                w(line)

    # --- Config migliori GLOBALMENTE (media Brier pesata su tutti i campionati) ---
    w(); w(SEP)
    w(f"  TOP-{TOP_N} CONFIGURAZIONI GLOBALI  (Brier medio pesato su tutti i campionati)")
    w(SEP)
    # Aggrega per chiave di config
    agg = {}
    for _, _, rows in all_results:
        for r in rows:
            if r["n"] < 50 or math.isnan(r["brier"]): continue
            key = (r["n_rec"], r["n_prev"], round(r["w_prev"], 2), round(r["bonus"], 2))
            if key not in agg:
                agg[key] = {"n_rec": r["n_rec"], "n_prev": r["n_prev"],
                            "w_prev": r["w_prev"], "bonus": r["bonus"],
                            "tot_n": 0, "weighted_brier": 0.0, "weighted_ll": 0.0,
                            "weighted_roi60": 0.0, "weighted_roi70": 0.0,
                            "tot_n60": 0, "tot_n70": 0, "tot_w60": 0, "tot_w70": 0}
            agg[key]["tot_n"] += r["n"]
            agg[key]["weighted_brier"] += r["brier"] * r["n"]
            agg[key]["weighted_ll"]    += r["logloss"] * r["n"]
            agg[key]["tot_n60"] += r["n60"]
            agg[key]["tot_n70"] += r["n70"]
            # ROI pesato sul numero di scommesse
            agg[key]["weighted_roi60"] += r["roi60"] * r["n60"]
            agg[key]["weighted_roi70"] += r["roi70"] * r["n70"]

    global_ranking = []
    for k, a in agg.items():
        if a["tot_n"] == 0: continue
        global_ranking.append({
            **a,
            "mean_brier": a["weighted_brier"] / a["tot_n"],
            "mean_ll":    a["weighted_ll"]    / a["tot_n"],
            "mean_roi60": a["weighted_roi60"] / a["tot_n60"] if a["tot_n60"] > 0 else 0,
            "mean_roi70": a["weighted_roi70"] / a["tot_n70"] if a["tot_n70"] > 0 else 0,
        })
    global_ranking.sort(key=lambda x: x["mean_brier"])

    w()
    w(f"  {'#':>3}  {'Brier':>6}  {'LL':>6}  {'ROI60':>7}  {'ROI70':>7}  Config")
    w("  " + "─" * 74)
    for i, r in enumerate(global_ranking[:TOP_N], 1):
        w(f"  {i:>3}  {r['mean_brier']:>6.4f}  {r['mean_ll']:>6.4f}  "
          f"{r['mean_roi60']:>+6.1f}%  {r['mean_roi70']:>+6.1f}%  {format_config(r)}")
    w()

# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print(SEP)
    print("  ⚽ GRID SEARCH — NuovoMetodo (v11) — Wikipedia")
    print(SEP)

    # 1. Genera griglia
    total = (len(GRID["n_rec"]) * len(GRID["n_prev"]) *
             len(GRID["w_prev"]) * len(GRID["bonus"]))
    configs = generate_configs(GRID)
    print(f"\n  Griglia: {total} combinazioni grezze  →  {len(configs)} uniche dopo dedup")

    # 2. Scarica dati
    raw = scrape_wikipedia()
    total_scraped = sum(len(v) for v in raw.values())
    if total_scraped == 0:
        fallback = load_from_json()
        if fallback:
            raw = fallback; source_label = "JSON locale (fallback)"
        else:
            print("\n❌ Nessuna fonte dati disponibile.")
            return
    else:
        source_label = "Wikipedia it."
    print(f"\n  Totale risultati: {sum(len(v) for v in raw.values())}")

    # 3. Grid search per ogni campionato
    print(f"\n{SEP}")
    print(f"  GRID SEARCH  ({len(configs)} configs × 4 campionati = {len(configs)*4} walk-forward)")
    print(SEP)
    all_results = []
    for league_name, bet_type, file_key in [
        ("Serie B",          "B", "serieB"),
        ("Serie C Girone A", "C", "serieCa"),
        ("Serie C Girone B", "C", "serieCb"),
        ("Serie C Girone C", "C", "serieCc"),
    ]:
        results = raw.get(league_name, [])
        print(f"\n  ► {league_name}  ({len(results)} risultati)")
        rows = grid_search_league(results, bet_type, configs, league_name)
        all_results.append((league_name, bet_type, rows))
        # Salva CSV immediatamente
        csv_path = OUT_DIR / f"{file_key}_all.csv"
        write_csv(rows, csv_path)
        print(f"    CSV → {csv_path.resolve()}")

    # 4. Report testuale
    print(f"\n{SEP}")
    print("  REPORT")
    print(SEP)
    rep_path = OUT_DIR / "gridsearch_report.txt"
    with open(rep_path, "w", encoding="utf-8") as f:
        write_report(all_results, source_label, total, len(configs), f)
    print(f"\n  Report  → {rep_path.resolve()}")
    print()

if __name__ == "__main__":
    main()
