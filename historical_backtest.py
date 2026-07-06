#!/usr/bin/env python3
"""
⚽ Historical Backtest — NuovoMetodo su 10 stagioni precedenti
================================================================
Scarica i risultati storici da Wikipedia per Serie B (10 stagioni)
e Serie C (fino a 8 stagioni — Serie C esiste con l'attuale
struttura a 3 gironi solo dal 2017-2018; prima era "Lega Pro").

Per ogni combinazione (stagione, campionato):
  - walk-forward su tutte le partite della stagione
  - grid search sulla griglia di parametri
  - calcolo metriche (Brier, LogLoss, ROI@60%, ROI@70%)

Aggregazione:
  Per ogni configurazione, calcola media e deviazione standard
  delle metriche ATTRAVERSO le stagioni. Le config vincenti sono
  quelle con Brier medio basso E stdev bassa (robuste, non fortunate).

Output in ./historical_output/:
  ├── raw/                             risultati per (stagione, campionato, config)
  │   ├── serieB_2024-2025.csv
  │   ├── serieCa_2023-2024.csv
  │   └── ...
  ├── aggregated_serieB.csv            aggregato per Serie B
  ├── aggregated_serieC.csv            aggregato per Serie C (tutti i gironi)
  ├── aggregated_global.csv            aggregato globale
  └── historical_report.txt            top-N per stagione e globali

Cache download: ./historical_cache/ (JSON per stagione).
Prima esecuzione ~2-5min (download). Successive: parte da cache.
"""

import json, re, os, math, sys, csv, time
from datetime import date
from pathlib import Path
from collections import defaultdict
from itertools import product
from statistics import mean, stdev

# ── Dipendenze ────────────────────────────────────────────────
for pkg, imp in [("requests", "requests"), ("beautifulsoup4", "bs4")]:
    try:
        __import__(imp)
    except ImportError:
        os.system(f"{sys.executable} -m pip install {pkg} -q")

import requests as req
from bs4 import BeautifulSoup

# ═════════════════════════════════════════════════════════════
# CONFIG — modificabile in cima al file
# ═════════════════════════════════════════════════════════════

# Stagioni da testare. Rimuovi/aggiungi come vuoi.
SERIE_B_SEASONS = [
    "2024-2025", "2023-2024", "2022-2023", "2021-2022", "2020-2021",
    "2019-2020", "2018-2019", "2017-2018", "2016-2017", "2015-2016",
]

# Serie C esiste con struttura a 3 gironi solo dal 2017-2018.
# Prima era "Lega Pro" con struttura diversa (2 divisioni), parser non compatibile.
SERIE_C_SEASONS = [
    "2024-2025", "2023-2024", "2022-2023", "2021-2022",
    "2020-2021", "2019-2020", "2018-2019", "2017-2018",
]

# Griglia parametri: raffinata rispetto alla zona vincente della scorsa grid search.
# Per allargare, aggiungi valori nelle liste (ogni raddoppio ~raddoppia il tempo).
GRID = {
    "n_rec":  [2, 3, 4, 5],
    "n_prev": [4, 6, 8],
    "w_prev": [0.4, 0.6, 0.8, 1.0],       # w_rec fisso = 1.0
    "bonus":  [0.00, 0.05, 0.10, 0.20],
}

TOP_N = 15                       # config da mostrare nel report
MIN_MATCHES_PER_TEAM = 3
MIN_SEASON_MATCHES = 200         # sotto questa soglia la stagione viene scartata
ODD = 1.85
SCRIPT_DIR = Path(__file__).parent
CACHE_DIR = SCRIPT_DIR / "historical_cache"
OUT_DIR = SCRIPT_DIR / "historical_output"
RAW_DIR = OUT_DIR / "raw"
CACHE_DIR.mkdir(exist_ok=True)
OUT_DIR.mkdir(exist_ok=True)
RAW_DIR.mkdir(exist_ok=True)

MESI_IT = {
    "gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,
    "lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12,
}

# ─────────────────────────────────────────────────────────────
# FETCH HTTP + PARSER WIKIPEDIA
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
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        print(f"    ❌ {url}: {e}")
        return None


def _parse_italian_date(text, season):
    """Parsa 'DD mese' → data ISO, con anno dedotto dalla stagione."""
    text = text.strip().rstrip(".")
    text = re.sub(r"(\d+)[ºª°]", r"\1", text)
    m = re.match(r"(\d{1,2})\s+(\w+)", text)
    if not m: return ""
    day, ms = int(m.group(1)), m.group(2).lower().rstrip(".")
    month = next((v for k, v in MESI_IT.items() if ms.startswith(k)), 0)
    if not month: return ""
    y1, y2 = season.split("-")
    year = int(y1) if month >= 7 else int(y2)
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


def wiki_parse_serie_b(html, season):
    """Parser Serie B per una singola stagione. Ritorna lista di risultati.
    Usa il formato per-giornata standard (funziona su 2023-24, 2024-25+)."""
    if not html: return []
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
                pd = _parse_italian_date(tds[0].get_text(strip=True), season)
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


# ─────────────────────────────────────────────────────────────
# Fallback: parser Calendario per pagine Serie B più vecchie
# (2015-2022 circa). La sezione Calendario è racchiusa in blocchi
# mw-collapsible collassati; ogni giornata è header + tabella.
# ─────────────────────────────────────────────────────────────
def _parse_calendar_date(text, season):
    """Parsa una data in vari formati usati nel Calendario Wikipedia."""
    text = text.strip()
    if not text:
        return ""
    y1, y2 = season.split("-")
    # "26 settembre" o "26 set."
    d = _parse_italian_date(text, season)
    if d:
        return d
    # "26.09." o "26.09.2020"
    m = re.match(r"^(\d{1,2})\.(\d{1,2})\.?(?:(\d{2,4}))?$", text)
    if m:
        try:
            day = int(m.group(1)); month = int(m.group(2))
            if m.group(3):
                yy = int(m.group(3))
                year = yy + 2000 if yy < 100 else yy
            else:
                year = int(y1) if month >= 7 else int(y2)
            return date(year, month, day).isoformat()
        except Exception:
            return ""
    # "DD/MM/YYYY" o "DD-MM-YYYY"
    m = re.match(r"^(\d{1,2})[-/](\d{1,2})[-/](\d{2,4})$", text)
    if m:
        try:
            yy = int(m.group(3))
            year = yy + 2000 if yy < 100 else yy
            return date(year, int(m.group(2)), int(m.group(1))).isoformat()
        except Exception:
            return ""
    # ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    return ""


def _parse_calendar_table(table, season, results, seen):
    """Estrae le righe di una tabella-calendario cercando pattern
    (data | teams | score) o varianti. Aggiunge a `results`/`seen`."""
    for row in table.find_all("tr"):
        cells = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
        if len(cells) < 2:
            continue
        # Trova la cella con il punteggio (formato H-A)
        score = None
        score_idx = None
        for i, cell in enumerate(cells):
            sc = _parse_score(cell)
            if sc:
                score = sc; score_idx = i; break
        if not score:
            continue
        # Trova la cella "team - team" prima del punteggio
        teams = None
        for i in range(score_idx - 1, -1, -1):
            txt = cells[i]
            if not re.search(r"[-–—]", txt):
                continue
            # Escludi celle che sono chiaramente date (contengono / o : e numeri)
            if re.match(r"^\d{1,2}[/.-]\d{1,2}[/.-]?\d*$", txt.strip()):
                continue
            parts = re.split(r"\s*[-–—]\s*", txt, maxsplit=1)
            if len(parts) != 2:
                continue
            h_raw, a_raw = parts[0].strip(), parts[1].strip()
            # Rimuovi eventuali orari agganciati
            h_raw = re.sub(r"\s*\bore\s+\d+[:.]\d+.*$", "", h_raw, flags=re.IGNORECASE).strip()
            a_raw = re.sub(r"^\s*\d+[:.]\d+\s+", "", a_raw).strip()
            h_raw = h_raw.replace("*", "").strip()
            a_raw = a_raw.replace("*", "").strip()
            # Sanity check: nomi ragionevoli (almeno 3 char, non solo numeri)
            if len(h_raw) < 3 or len(a_raw) < 3:
                continue
            if h_raw.isdigit() or a_raw.isdigit():
                continue
            teams = (h_raw, a_raw)
            break
        if not teams:
            continue
        # Cerca una data tra le celle precedenti
        match_date = ""
        for cell in cells[:score_idx]:
            d = _parse_calendar_date(cell, season)
            if d:
                match_date = d; break
        home, away = teams
        key = (match_date, home, away)
        if key in seen:
            continue
        seen.add(key)
        results.append({"date": match_date, "home": home, "away": away,
                        "hg": score[0], "ag": score[1]})


def wiki_parse_serie_b_calendario(html, season):
    """Parser fallback: cerca la sezione Calendario nelle pagine Serie B
    più vecchie (struttura mw-collapsible per giornata).

    Prova 3 strategie di individuazione degli header 'N ª giornata':
    1) elementi <caption> dentro tabelle
    2) heading (h2-h5) seguiti da tabelle
    3) div/span con classi tipiche di blocchi collassabili"""
    if not html:
        return []
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen = set()
    giornata_re = re.compile(r"^\s*(\d+)[\u00AAa\u00B0]\s*giornata\b", re.IGNORECASE)

    # Strategia 1: <caption>...N ª giornata...</caption>
    for cap in soup.find_all("caption"):
        if giornata_re.match(cap.get_text(strip=True)):
            table = cap.parent
            if table and table.name == "table":
                _parse_calendar_table(table, season, results, seen)

    # Strategia 2: heading seguiti dalla prima tabella successiva
    for h in soup.find_all(["h2", "h3", "h4", "h5"]):
        if giornata_re.match(h.get_text(strip=True)):
            nxt = h.find_next("table")
            if nxt:
                _parse_calendar_table(nxt, season, results, seen)

    # Strategia 3: div/span di blocchi collassabili
    collapsible_cls = re.compile(r"NavHead|mw-collapsible-header|toggler|NavFrame")
    for div in soup.find_all(["div", "span"], class_=collapsible_cls):
        if giornata_re.match(div.get_text(strip=True)):
            container = div.parent
            if not container:
                continue
            for table in container.find_all("table"):
                _parse_calendar_table(table, season, results, seen)

    return results


def wiki_parse_serie_c_girone(soup, girone_letter, season):
    """Parser Serie C per una singola stagione, singolo girone.
    Applica il fix inversione ritorno per il formato compresso andata/ritorno."""
    girone_section = None
    for h2 in soup.find_all("h2"):
        if f"Girone {girone_letter}" in h2.get_text():
            girone_section = h2.parent.parent
            break
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
                    date_a = _parse_italian_date(tds[idx].get_text(strip=True), season)
                    rs_a = int(tds[idx].get("rowspan", 1)); idx += 1
                elif n < 3: continue
            rem = n - idx
            if rem < 3: continue
            score_a = tds[idx].get_text(strip=True); idx += 1
            teams_text = tds[idx].get_text(strip=True); idx += 1
            score_r = tds[idx].get_text(strip=True); idx += 1
            if rs_r <= 0 and idx < n:
                date_r = _parse_italian_date(tds[idx].get_text(strip=True), season)
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


# ─────────────────────────────────────────────────────────────
# CACHE LAYER
# ─────────────────────────────────────────────────────────────
def cached_or_fetch(cache_key, fetch_fn):
    """Cache JSON layer: se cache_key.json esiste, la carica; altrimenti fetch e salva."""
    cache_path = CACHE_DIR / f"{cache_key}.json"
    if cache_path.exists():
        try:
            with open(cache_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    data = fetch_fn()
    if data is not None:
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        except Exception as e:
            print(f"    ⚠️ Impossibile salvare cache {cache_key}: {e}")
    return data


def get_serie_b(season):
    def fetch():
        url = f"https://it.wikipedia.org/wiki/Serie_B_{season}"
        html = fetch_page(url)
        if not html:
            return []
        # Tentativo 1: parser standard (formato per-giornata visibile)
        r_std = wiki_parse_serie_b(html, season)
        if len(r_std) >= 200:
            return r_std
        # Tentativo 2: parser Calendario (mw-collapsible / vecchi formati)
        r_cal = wiki_parse_serie_b_calendario(html, season)
        # Restituisco il risultato con più partite
        return r_cal if len(r_cal) > len(r_std) else r_std
    return cached_or_fetch(f"serieB_{season}", fetch)


def get_serie_c(season):
    """Ritorna dict {girone: [risultati]}."""
    def fetch():
        url = f"https://it.wikipedia.org/wiki/Serie_C_{season}"
        html = fetch_page(url)
        if not html: return {}
        soup = BeautifulSoup(html, "html.parser")
        out = {}
        for gl in ["A", "B", "C"]:
            out[gl] = wiki_parse_serie_c_girone(soup, gl, season)
        return out
    return cached_or_fetch(f"serieC_{season}", fetch) or {}


# ─────────────────────────────────────────────────────────────
# NUOVOMETODO + WALK-FORWARD
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
    """bet_type: 'B' (casa over 0.5) | 'C' (ospite under 1.5)."""
    home_hist = defaultdict(list)
    away_hist = defaultdict(list)
    sc_over, sc_under = _mk_score(cfg["bonus"])
    max_score = 1.0 + cfg["bonus"]
    n_rec, n_prev = cfg["n_rec"], cfg["n_prev"]
    w_rec, w_prev = 1.0, cfg["w_prev"]

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
    inv = len(sub)
    gain = wins * odd
    return wins / inv, (gain - inv) / inv * 100, inv


# ─────────────────────────────────────────────────────────────
# GRID GENERATION
# ─────────────────────────────────────────────────────────────
def generate_configs(grid):
    """Configs uniche, con dedup su (n_prev=0 o w_prev=0) → canonicale."""
    seen = set()
    configs = []
    for n_rec, n_prev, w_prev, bonus in product(
        grid["n_rec"], grid["n_prev"], grid["w_prev"], grid["bonus"]
    ):
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


def config_key(cfg):
    """Chiave univoca per identificare una config nell'aggregazione."""
    return (cfg["n_rec"], cfg["n_prev"], round(cfg["w_prev"], 2), round(cfg["bonus"], 2))


def format_config(r):
    return f"n_rec={r['n_rec']} n_prev={r['n_prev']} w_prev={r['w_prev']:.2f} bonus={r['bonus']:.2f}"


# ─────────────────────────────────────────────────────────────
# GRID SEARCH SU UN DATASET
# ─────────────────────────────────────────────────────────────
def grid_search_dataset(results, bet_type, configs):
    """Ritorna lista di dict con metriche per ogni config."""
    sorted_r = sorted(results, key=lambda x: x.get("date", ""))
    rows = []
    for cfg in configs:
        preds = walk_forward(sorted_r, bet_type, cfg)
        if not preds:
            rows.append({**cfg, "n": 0, "brier": float("nan"), "logloss": float("nan"),
                         "roi60": 0, "n60": 0, "roi70": 0, "n70": 0})
            continue
        bs = brier(preds); ll = logloss(preds)
        _, r60, n60 = acc_roi_at(preds, 0.60)
        _, r70, n70 = acc_roi_at(preds, 0.70)
        rows.append({
            **cfg, "n": len(preds), "brier": bs, "logloss": ll,
            "roi60": r60, "n60": n60, "roi70": r70, "n70": n70,
        })
    return rows


# ─────────────────────────────────────────────────────────────
# DOWNLOAD DATI STORICI
# ─────────────────────────────────────────────────────────────
def download_all_seasons():
    """Scarica (o carica da cache) tutte le stagioni configurate.
    Ritorna dict {(campionato_key, season): risultati}.
    Scarta le stagioni con meno di MIN_SEASON_MATCHES partite."""
    dataset = {}
    skipped = []
    print(f"\n📥 Scarico dati storici...")
    print(f"   Cache dir: {CACHE_DIR.resolve()}")
    print(f"   Soglia scarto: <{MIN_SEASON_MATCHES} partite per stagione")

    # Serie B
    print(f"\n  === Serie B ({len(SERIE_B_SEASONS)} stagioni) ===")
    for season in SERIE_B_SEASONS:
        results = get_serie_b(season)
        n = len(results) if results else 0
        if n >= MIN_SEASON_MATCHES:
            print(f"    ✓ Serie B {season}: {n} risultati")
            dataset[("Serie B", season)] = results
        else:
            print(f"    ✗ Serie B {season}: {n} risultati (scartata, sotto soglia)")
            skipped.append(("Serie B", season, n))

    # Serie C — 3 gironi per stagione
    print(f"\n  === Serie C ({len(SERIE_C_SEASONS)} stagioni × 3 gironi) ===")
    for season in SERIE_C_SEASONS:
        c_data = get_serie_c(season)
        for gl in ["A", "B", "C"]:
            results = c_data.get(gl, [])
            n = len(results) if results else 0
            if n >= MIN_SEASON_MATCHES:
                print(f"    ✓ Serie C Gir.{gl} {season}: {n} risultati")
                dataset[(f"Serie C Girone {gl}", season)] = results
            else:
                print(f"    ✗ Serie C Gir.{gl} {season}: {n} risultati (scartata)")
                skipped.append((f"Serie C Girone {gl}", season, n))

    if skipped:
        print(f"\n  ⚠️  {len(skipped)} unità scartate per dati insufficienti "
              f"(HTML Wikipedia probabilmente diverso in quelle stagioni)")
    return dataset


# ─────────────────────────────────────────────────────────────
# AGGREGAZIONE ATTRAVERSO LE STAGIONI
# ─────────────────────────────────────────────────────────────
def aggregate_configs(per_season_rows_by_campionato, campionato_filter=None):
    """Per ogni chiave di config, calcola media e stdev delle metriche
    attraverso le stagioni del campionato (o dei campionati) selezionato.

    per_season_rows_by_campionato: dict {(campionato, season): [rows_per_config]}
    campionato_filter: se passato, filtra solo quel campionato (str) o subset.

    Ritorna lista di dict con: cfg params, n_seasons, mean_brier, std_brier, ..."""
    # Raccogli tutte le metriche per chiave config
    metrics_by_key = defaultdict(lambda: {
        "cfg": None, "briers": [], "lls": [], "roi60s": [], "roi70s": [],
        "tot_n": 0, "tot_n60": 0, "tot_n70": 0,
    })
    for (campionato, season), rows in per_season_rows_by_campionato.items():
        if campionato_filter:
            if isinstance(campionato_filter, str):
                if campionato != campionato_filter: continue
            else:
                if campionato not in campionato_filter: continue
        for r in rows:
            if r["n"] == 0 or math.isnan(r["brier"]): continue
            k = config_key(r)
            m = metrics_by_key[k]
            m["cfg"] = {"n_rec": r["n_rec"], "n_prev": r["n_prev"],
                        "w_prev": r["w_prev"], "bonus": r["bonus"]}
            m["briers"].append(r["brier"])
            m["lls"].append(r["logloss"])
            m["tot_n"] += r["n"]
            if r["n60"] > 0:
                m["roi60s"].append(r["roi60"])
                m["tot_n60"] += r["n60"]
            if r["n70"] > 0:
                m["roi70s"].append(r["roi70"])
                m["tot_n70"] += r["n70"]

    agg = []
    for k, m in metrics_by_key.items():
        if not m["briers"]: continue
        agg.append({
            **m["cfg"],
            "n_seasons":    len(m["briers"]),
            "tot_n":        m["tot_n"],
            "mean_brier":   mean(m["briers"]),
            "std_brier":    stdev(m["briers"]) if len(m["briers"]) > 1 else 0.0,
            "mean_ll":      mean(m["lls"]),
            "std_ll":       stdev(m["lls"]) if len(m["lls"]) > 1 else 0.0,
            "mean_roi60":   mean(m["roi60s"]) if m["roi60s"] else 0.0,
            "mean_roi70":   mean(m["roi70s"]) if m["roi70s"] else 0.0,
            "tot_n60":      m["tot_n60"],
            "tot_n70":      m["tot_n70"],
        })
    return agg


# ─────────────────────────────────────────────────────────────
# CSV OUTPUT
# ─────────────────────────────────────────────────────────────
def write_raw_csv(rows, path):
    if not rows: return
    keys = ["n_rec","n_prev","w_prev","bonus","n","brier","logloss",
            "roi60","n60","roi70","n70"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in rows:
            out = {k: r.get(k, "") for k in keys}
            for k in ["brier","logloss","roi60","roi70"]:
                v = out[k]
                if isinstance(v, float) and not math.isnan(v):
                    out[k] = round(v, 4)
            out["w_prev"] = round(out["w_prev"], 2)
            out["bonus"]  = round(out["bonus"],  2)
            w.writerow(out)


def write_agg_csv(rows, path):
    if not rows: return
    keys = ["n_rec","n_prev","w_prev","bonus","n_seasons","tot_n",
            "mean_brier","std_brier","mean_ll","std_ll",
            "mean_roi60","tot_n60","mean_roi70","tot_n70"]
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        for r in sorted(rows, key=lambda x: x["mean_brier"]):
            out = {k: r.get(k, "") for k in keys}
            for k in ["mean_brier","std_brier","mean_ll","std_ll","mean_roi60","mean_roi70"]:
                v = out[k]
                if isinstance(v, float):
                    out[k] = round(v, 4)
            out["w_prev"] = round(out["w_prev"], 2)
            out["bonus"]  = round(out["bonus"],  2)
            w.writerow(out)


# ─────────────────────────────────────────────────────────────
# REPORT TESTUALE
# ─────────────────────────────────────────────────────────────
SEP = "═" * 82

def write_report(dataset_info, per_season_by_campionato,
                 agg_serieB, agg_serieC, agg_global, n_configs, f=None):
    def w(s=""):
        print(s)
        if f: f.write(s + "\n")

    w(SEP)
    w("  BACKTEST STORICO — NuovoMetodo (v11)  —  10 stagioni")
    w(f"  Fonte: Wikipedia it.  |  Quota simulata: {ODD}  |  Config uniche: {n_configs}")
    w(f"  Griglia: n_rec {GRID['n_rec']}  n_prev {GRID['n_prev']}")
    w(f"           w_prev {GRID['w_prev']}  bonus {GRID['bonus']}")
    w(f"  Nota: w_rec fisso = 1.0 (solo il rapporto conta).")
    w(f"  Generato: {date.today().isoformat()}")
    w(SEP)

    # Riepilogo dataset scaricati
    w()
    w("  DATASET SCARICATI")
    w("  " + "─" * 78)
    w(f"  {'Campionato':<22}  {'Stagioni':>10}  {'Partite tot':>12}")
    for camp in sorted(set(k[0] for k in dataset_info)):
        seasons = [s for (c, s) in dataset_info if c == camp]
        tot_n = sum(dataset_info[(camp, s)] for s in seasons)
        w(f"  {camp:<22}  {len(seasons):>10}  {tot_n:>12}")

    # ── SERIE B ────────────────────────────────────────────────
    w(); w("━" * 82)
    w("  SERIE B  —  CASA OVER 0.5  (aggregato su tutte le stagioni)")
    w("━" * 82)
    if agg_serieB:
        agg_sorted = sorted(agg_serieB, key=lambda x: x["mean_brier"])
        w()
        w(f"  TOP-{TOP_N} per BRIER MEDIO (↓ meglio)")
        w(f"  {'#':>3}  {'MeanBr':>7}  {'StdBr':>6}  {'MeanLL':>7}  "
          f"{'ROI60':>7}  {'ROI70':>7}  {'Stag':>4}  Config")
        w("  " + "─" * 78)
        for i, r in enumerate(agg_sorted[:TOP_N], 1):
            w(f"  {i:>3}  {r['mean_brier']:>7.4f}  {r['std_brier']:>6.4f}  "
              f"{r['mean_ll']:>7.4f}  {r['mean_roi60']:>+6.1f}%  {r['mean_roi70']:>+6.1f}%  "
              f"{r['n_seasons']:>4}  {format_config(r)}")

        # Top ROI (min volume ragionevole)
        roi_sorted = sorted([r for r in agg_serieB if r["tot_n60"] >= 50],
                            key=lambda x: -x["mean_roi60"])
        if roi_sorted:
            w()
            w(f"  TOP-{TOP_N} per ROI@60% MEDIO (min. 50 scommesse totali)")
            w(f"  {'#':>3}  {'ROI60':>7}  {'TotN60':>7}  {'MeanBr':>7}  "
              f"{'Stag':>4}  Config")
            w("  " + "─" * 78)
            for i, r in enumerate(roi_sorted[:TOP_N], 1):
                w(f"  {i:>3}  {r['mean_roi60']:>+6.1f}%  {r['tot_n60']:>7}  "
                  f"{r['mean_brier']:>7.4f}  {r['n_seasons']:>4}  {format_config(r)}")

    # ── SERIE C ────────────────────────────────────────────────
    w(); w("━" * 82)
    w("  SERIE C  —  OSPITE UNDER 1.5  (aggregato su tutte le stagioni × 3 gironi)")
    w("━" * 82)
    if agg_serieC:
        agg_sorted = sorted(agg_serieC, key=lambda x: x["mean_brier"])
        w()
        w(f"  TOP-{TOP_N} per BRIER MEDIO (↓ meglio)")
        w(f"  {'#':>3}  {'MeanBr':>7}  {'StdBr':>6}  {'MeanLL':>7}  "
          f"{'ROI60':>7}  {'ROI70':>7}  {'Stag':>4}  Config")
        w("  " + "─" * 78)
        for i, r in enumerate(agg_sorted[:TOP_N], 1):
            w(f"  {i:>3}  {r['mean_brier']:>7.4f}  {r['std_brier']:>6.4f}  "
              f"{r['mean_ll']:>7.4f}  {r['mean_roi60']:>+6.1f}%  {r['mean_roi70']:>+6.1f}%  "
              f"{r['n_seasons']:>4}  {format_config(r)}")

        roi_sorted = sorted([r for r in agg_serieC if r["tot_n60"] >= 50],
                            key=lambda x: -x["mean_roi60"])
        if roi_sorted:
            w()
            w(f"  TOP-{TOP_N} per ROI@60% MEDIO (min. 50 scommesse totali)")
            w(f"  {'#':>3}  {'ROI60':>7}  {'TotN60':>7}  {'MeanBr':>7}  "
              f"{'Stag':>4}  Config")
            w("  " + "─" * 78)
            for i, r in enumerate(roi_sorted[:TOP_N], 1):
                w(f"  {i:>3}  {r['mean_roi60']:>+6.1f}%  {r['tot_n60']:>7}  "
                  f"{r['mean_brier']:>7.4f}  {r['n_seasons']:>4}  {format_config(r)}")

    # ── GLOBALE ────────────────────────────────────────────────
    w(); w(SEP)
    w("  CLASSIFICA GLOBALE  —  Serie B + Serie C combinati")
    w(SEP)
    if agg_global:
        agg_sorted = sorted(agg_global, key=lambda x: x["mean_brier"])
        w()
        w(f"  TOP-{TOP_N} per BRIER MEDIO GLOBALE (↓ meglio)")
        w(f"  {'#':>3}  {'MeanBr':>7}  {'StdBr':>6}  {'MeanLL':>7}  "
          f"{'ROI60':>7}  {'ROI70':>7}  {'Stag':>4}  Config")
        w("  " + "─" * 78)
        for i, r in enumerate(agg_sorted[:TOP_N], 1):
            w(f"  {i:>3}  {r['mean_brier']:>7.4f}  {r['std_brier']:>6.4f}  "
              f"{r['mean_ll']:>7.4f}  {r['mean_roi60']:>+6.1f}%  {r['mean_roi70']:>+6.1f}%  "
              f"{r['n_seasons']:>4}  {format_config(r)}")

    # ── ROBUSTEZZA: config più stabili nel tempo ───────────────
    w(); w(SEP)
    w("  CONFIGURAZIONI PIÙ ROBUSTE (Brier basso E deviazione bassa)")
    w("  Filtra le config con Brier medio nel top-30%, poi ordina per stdev crescente")
    w(SEP)
    if agg_global:
        thr = sorted(agg_global, key=lambda x: x["mean_brier"])[int(len(agg_global) * 0.3)]["mean_brier"]
        stable = [r for r in agg_global if r["mean_brier"] <= thr]
        stable.sort(key=lambda x: x["std_brier"])
        w()
        w(f"  {'#':>3}  {'StdBr':>6}  {'MeanBr':>7}  {'Stag':>4}  Config")
        w("  " + "─" * 60)
        for i, r in enumerate(stable[:TOP_N], 1):
            w(f"  {i:>3}  {r['std_brier']:>6.4f}  {r['mean_brier']:>7.4f}  "
              f"{r['n_seasons']:>4}  {format_config(r)}")
    w()


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print(SEP)
    print("  ⚽ HISTORICAL BACKTEST — NuovoMetodo (v11) — 10 stagioni")
    print(SEP)

    # 1. Genera griglia
    total_grid = (len(GRID["n_rec"]) * len(GRID["n_prev"]) *
                  len(GRID["w_prev"]) * len(GRID["bonus"]))
    configs = generate_configs(GRID)
    print(f"\n  Griglia: {total_grid} raw → {len(configs)} uniche dopo dedup")

    # 2. Download
    t0 = time.time()
    dataset = download_all_seasons()
    print(f"\n  Dataset totale: {len(dataset)} unità (stagione × campionato)")
    print(f"  Tempo download: {time.time() - t0:.1f}s")
    if not dataset:
        print("\n  ❌ Nessun dato disponibile. Verifica connessione o cache.")
        return

    # 3. Grid search per ogni (campionato, stagione)
    print(f"\n{SEP}")
    print(f"  GRID SEARCH  ({len(configs)} configs × {len(dataset)} unità = "
          f"{len(configs) * len(dataset)} walk-forward)")
    print(SEP)

    per_season_by_campionato = {}  # {(campionato, season): [rows]}
    dataset_info = {}              # {(campionato, season): n_matches}
    t0 = time.time()
    total_units = len(dataset)
    for idx, ((campionato, season), results) in enumerate(dataset.items(), 1):
        bet_type = "B" if campionato == "Serie B" else "C"
        rows = grid_search_dataset(results, bet_type, configs)
        per_season_by_campionato[(campionato, season)] = rows
        dataset_info[(campionato, season)] = len(results)

        # Salva CSV raw
        safe_key = campionato.replace(" ", "_").replace("Serie_C_Girone_", "serieC")
        if campionato == "Serie B": safe_key = "serieB"
        csv_path = RAW_DIR / f"{safe_key}_{season}.csv"
        write_raw_csv(rows, csv_path)

        elapsed = time.time() - t0
        eta = elapsed / idx * (total_units - idx)
        n_preds = max((r['n'] for r in rows if r['n'] > 0), default=0)
        print(f"  {idx:>2}/{total_units}  {campionato} {season}  "
              f"({len(results)} partite, "
              f"~{n_preds:>3} previsioni)  "
              f"[{elapsed:.0f}s, ETA {eta:.0f}s]")

    # 4. Aggregazione
    print(f"\n{SEP}")
    print("  AGGREGAZIONE")
    print(SEP)

    agg_serieB = aggregate_configs(per_season_by_campionato, campionato_filter="Serie B")
    agg_serieC = aggregate_configs(per_season_by_campionato,
                                    campionato_filter={"Serie C Girone A",
                                                       "Serie C Girone B",
                                                       "Serie C Girone C"})
    agg_global = aggregate_configs(per_season_by_campionato)
    print(f"  Serie B: {len(agg_serieB)} config valutate")
    print(f"  Serie C: {len(agg_serieC)} config valutate")
    print(f"  Globale: {len(agg_global)} config valutate")

    # Salva CSV aggregati
    write_agg_csv(agg_serieB, OUT_DIR / "aggregated_serieB.csv")
    write_agg_csv(agg_serieC, OUT_DIR / "aggregated_serieC.csv")
    write_agg_csv(agg_global, OUT_DIR / "aggregated_global.csv")

    # 5. Report
    rep_path = OUT_DIR / "historical_report.txt"
    with open(rep_path, "w", encoding="utf-8") as f:
        write_report(dataset_info, per_season_by_campionato,
                     agg_serieB, agg_serieC, agg_global, len(configs), f)

    print(f"\n  Report → {rep_path.resolve()}")
    print(f"  CSV    → {OUT_DIR.resolve()}")
    print()


if __name__ == "__main__":
    main()
