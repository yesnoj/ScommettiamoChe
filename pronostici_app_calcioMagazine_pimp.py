#!/usr/bin/env python3
"""
⚽ Pronostici Serie B & C — v7 Python/Flask
Modello Dixon-Coles + Decadimento temporale + Fattore campo
Aggiornamento da calciomagazine.net (tabelle risultati)
"""

import json, re, os, math, sys
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    from flask import Flask, jsonify, request, render_template_string
except ImportError:
    print("Installo Flask..."); os.system(f"{sys.executable} -m pip install flask requests"); from flask import Flask, jsonify, request, render_template_string

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests"); import requests as req

# ============================================================
# CONFIG
# ============================================================
APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "pronostici_data.json"
PORT = 5050

# calciomagazine.net: pagine risultati e calendario separate
LEAGUES = {
    "serieB": {
        "name": "Serie B",
        "results_url": "https://www.calciomagazine.net/risultati-serie-b-120385.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-b-99638.html",
    },
    "serieCa": {
        "name": "Serie C Girone A",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-a-120404.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-c-girone-a-99207.html",
        "calendar_url_alt": "https://www.calciomagazine.net/calendario-serie-c-girone-a-99200.html",
    },
    "serieCb": {
        "name": "Serie C Girone B",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-b-120417.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-c-girone-b-99208.html",
    },
    "serieCc": {
        "name": "Serie C Girone C",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-c-120418.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-c-girone-c-99209.html",
    },
}

# ============================================================
# SCRAPING & PARSING (calciomagazine.net)
# ============================================================
def fetch_page(url):
    """Fetch a page from calciomagazine.net."""
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                       "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    }
    try:
        r = req.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status()
        r.encoding = 'utf-8'
        return r.text
    except Exception as e:
        print(f"  ❌ Errore fetch {url}: {e}")
        return None


def extract_text_content(html):
    """Extract clean text from HTML, preserving newlines at block boundaries."""
    if not html:
        return ""
    # Replace <br>, </p>, </div>, </li> etc. with newlines
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</(?:p|div|li|h[1-6]|tr)>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    # Clean up whitespace
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r'\n[ \t]+', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    # Decode HTML entities
    import html as html_mod
    text = html_mod.unescape(text)
    return text.strip()


def infer_year(month, day):
    """Infer year for a DD.MM. date in Serie B/C 2025-2026 season.
    Season runs Aug 2025 - May 2026."""
    if month >= 7:  # Jul-Dec → 2025
        return 2025
    else:  # Jan-Jun → 2026
        return 2026


def parse_results_page(html):
    """Parse results from a calciomagazine.net risultati page.
    
    Primary format (text-based):
        27ª Giornata
        16.02. ore 20:30 Pineto-Ternana 0 : 3
        16.02. ore 20:30 Vis Pesaro-Campobasso 1 : 2
    
    Alternative format (table-based, dates like "28 set."):
        7ª Giornata
        28 set. 0-0 Audace Cerignola-Catania 0-0
    
    Returns: dict of {giornata_num: [list of result dicts]}
    """
    text = extract_text_content(html)
    giornate = {}
    current_giornata = 0
    
    giornata_re = re.compile(r'(\d+)[ªa°]\s*Giornata', re.IGNORECASE)
    
    # Primary pattern: DD.MM. ore HH:MM Team1-Team2 G1 : G2
    result_re = re.compile(
        r'(\d{2})\.(\d{2})\.\s*ore\s*\d{1,2}:\d{2}\s+'
        r'(.+?)\s*[-–]\s*(.+?)\s+'
        r'(\d+)\s*:\s*(\d+)'
    )
    
    # Alternative pattern: DD.MM. Team1-Team2 G1 : G2 (without "ore")
    result_re_alt = re.compile(
        r'(\d{2})\.(\d{2})\.\s+'
        r'(.+?)\s*[-–]\s*(.+?)\s+'
        r'(\d+)\s*:\s*(\d+)'
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Skip postponed/canceled matches
        if 'annullata' in line.lower() or 'rinviata' in line.lower():
            continue
        
        # Check for giornata header
        gm = giornata_re.search(line)
        if gm:
            current_giornata = int(gm.group(1))
            if current_giornata not in giornate:
                giornate[current_giornata] = []
            continue
        
        # Try primary pattern first, then alternative
        rm = result_re.search(line) or result_re_alt.search(line)
        if rm and current_giornata > 0:
            day_n = int(rm.group(1))
            month = int(rm.group(2))
            home = rm.group(3).strip()
            away = rm.group(4).strip()
            hg = int(rm.group(5))
            ag = int(rm.group(6))
            
            year = infer_year(month, day_n)
            try:
                d = date(year, month, day_n)
                date_str = d.isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            
            giornate[current_giornata].append({
                "date": date_str,
                "home": home,
                "away": away,
                "hg": hg,
                "ag": ag,
            })
    
    return giornate


def parse_calendar_page(html):
    """Parse calendar/fixtures from a calciomagazine.net calendario page.
    
    Format per giornata:
        **28ª Giornata**
        * Domenica 22.02.2026 ore 18:30 Arezzo-Campobasso
        * Domenica 22.02.2026 ore 18:30 Bra-Pontedera
        ...
    
    Also handles format: Venerdì 22.08.25 ore 21:15 Team1-Team2
    
    Returns: dict of {giornata_num: [list of fixture dicts]}
        fixture dict: {"date": "YYYY-MM-DD", "home": str, "away": str}
    """
    text = extract_text_content(html)
    giornate = {}
    current_giornata = 0
    
    giornata_re = re.compile(r'(\d+)[ªa°]\s*Giornata', re.IGNORECASE)
    
    # Pattern for calendar line: Weekday DD.MM.YYYY ore HH:MM Team1-Team2
    # Date can be DD.MM.YYYY or DD.MM.YY
    fixture_re = re.compile(
        r'(?:luned[ìi]|marted[ìi]|mercoled[ìi]|gioved[ìi]|venerd[ìi]|sabato|domenica)\s+'
        r'(\d{2})\.(\d{2})\.(\d{2,4})\s+ore\s*\d{1,2}:\d{2}\s+'
        r'(.+?)\s*[-–]\s*(.+?)(?:\s*$)',
        re.IGNORECASE
    )
    
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        gm = giornata_re.search(line)
        if gm:
            current_giornata = int(gm.group(1))
            if current_giornata not in giornate:
                giornate[current_giornata] = []
            # Don't continue — the fixture might be on same line after giornata
        
        fm = fixture_re.search(line)
        if fm and current_giornata > 0:
            day_n = int(fm.group(1))
            month = int(fm.group(2))
            year_raw = fm.group(3)
            year = int(year_raw)
            if year < 100:
                year += 2000
            
            home = fm.group(4).strip()
            away = fm.group(5).strip()
            # Clean away team (remove trailing markdown artifacts)
            away = re.sub(r'\s*\*+$', '', away).strip()
            
            try:
                d = date(year, month, day_n)
                date_str = d.isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            
            giornate[current_giornata].append({
                "date": date_str,
                "home": home,
                "away": away,
            })
    
    return giornate


def find_next_giornata(results_by_giornata, calendar_by_giornata):
    """Find the next unplayed (or partially played) giornata.
    
    Strategy:
    1. If we have calendar data: find first giornata with more calendar entries than results
    2. If calendar is empty: find first gap in results giornate, or last+1
    """
    if calendar_by_giornata:
        all_giornate = sorted(set(list(results_by_giornata.keys()) + list(calendar_by_giornata.keys())))
        
        for g in all_giornate:
            cal_matches = calendar_by_giornata.get(g, [])
            res_matches = results_by_giornata.get(g, [])
            
            if not cal_matches:
                continue
            
            # If this giornata has fewer results than calendar entries, it's incomplete
            if len(res_matches) < len(cal_matches):
                return g
        
        # All complete — return next after last
        if all_giornate:
            return max(all_giornate) + 1
    
    # Fallback: use only results data
    if results_by_giornata:
        played = sorted(results_by_giornata.keys())
        # Serie B has 38 giornate, Serie C has 38 giornate
        max_g = 38
        for g in range(1, max_g + 1):
            if g not in results_by_giornata or len(results_by_giornata[g]) == 0:
                return g
        return played[-1] + 1 if played else 1
    
    return 1


# ============================================================
# DATA MANAGEMENT
# ============================================================
def get_default_data():
    """Return empty data structure."""
    return {
        "version": 7,
        "updatedAt": date.today().isoformat(),
        "serieB":  {"results_by_giornata": {}, "calendar_by_giornata": {}, "results": [], "next_giornata": 0, "next_fixtures": []},
        "serieCa": {"results_by_giornata": {}, "calendar_by_giornata": {}, "results": [], "next_giornata": 0, "next_fixtures": []},
        "serieCb": {"results_by_giornata": {}, "calendar_by_giornata": {}, "results": [], "next_giornata": 0, "next_fixtures": []},
        "serieCc": {"results_by_giornata": {}, "calendar_by_giornata": {}, "results": [], "next_giornata": 0, "next_fixtures": []},
    }

def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            # Auto-migrate v6 data to v7 format
            if data.get("version", 6) < 7:
                print("  📦 Migrazione dati v6 → v7...")
                for key in ["serieB", "serieCa", "serieCb", "serieCc"]:
                    if key in data:
                        league = data[key]
                        # Normalize results from list to dict format
                        if "results" in league and league["results"]:
                            if isinstance(league["results"][0], list):
                                league["results"] = [_normalize_result(r) for r in league["results"]]
                        # Migrate fixtures → next_fixtures
                        if "fixtures" in league and "next_fixtures" not in league:
                            old_fix = league.pop("fixtures", [])
                            league["next_fixtures"] = [
                                {"date": f.get("date",""), "home": f.get("h",""), "away": f.get("a","")}
                                for f in old_fix
                            ]
                        league.setdefault("results_by_giornata", {})
                        league.setdefault("calendar_by_giornata", {})
                        league.setdefault("next_giornata", 0)
                        league.setdefault("next_fixtures", [])
                data["version"] = 7
                save_data(data)
            return data
        except Exception as e:
            print(f"  ⚠️ Errore caricamento dati: {e}")
    return get_default_data()

def save_data(data):
    data["updatedAt"] = date.today().isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

# ============================================================
# POISSON MODEL — Dixon-Coles + Decadimento + Fattore Campo
# ============================================================
DECAY_XI = 0.005    # emivita ~139 giorni (ln2/0.005)
RHO_DEFAULT = -0.13 # fallback rho

def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def _normalize_result(r):
    """Normalize a result to dict format, handling both v6 lists and v7 dicts."""
    if isinstance(r, list):
        return {"date": r[0], "home": r[1], "away": r[2], "hg": int(r[3]), "ag": int(r[4])}
    return r

def _normalize_fixture(f):
    """Normalize a fixture dict (v6 uses h/a, v7 uses home/away)."""
    if "home" not in f and "h" in f:
        return {"home": f["h"], "away": f["a"], "date": f.get("date", "")}
    return f

# --- Strato 1: Statistiche con decadimento temporale ---
def calc_stats(results):
    """Statistiche pesate: partite recenti contano di più (e^{-ξ·giorni})."""
    today = date.today()
    stats = {}
    for r in results:
        r = _normalize_result(r)
        home, away, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        try:
            days = max((today - date.fromisoformat(r["date"])).days, 0)
            w = math.exp(-DECAY_XI * days)
        except:
            w = 0.5
        for t in [home, away]:
            if t not in stats:
                stats[t] = {"hgf_w":0,"hga_w":0,"hw":0,"agf_w":0,"aga_w":0,"aw":0,
                            "hg":0,"ag":0,"home_gf":0,"home_ga":0,"away_gf":0,"away_ga":0}
        stats[home]["hgf_w"] += hg * w
        stats[home]["hga_w"] += ag * w
        stats[home]["hw"] += w
        stats[home]["hg"] += 1
        stats[home]["home_gf"] += hg
        stats[home]["home_ga"] += ag
        stats[away]["agf_w"] += ag * w
        stats[away]["aga_w"] += hg * w
        stats[away]["aw"] += w
        stats[away]["ag"] += 1
        stats[away]["away_gf"] += ag
        stats[away]["away_ga"] += hg
    return stats

def league_avg(stats):
    """Media gol lega pesata."""
    total_goals = sum(t["hgf_w"] for t in stats.values())
    total_weight = sum(t["hw"] for t in stats.values())
    return total_goals / total_weight if total_weight > 0 else 1.2

# --- Strato 2: Fattore campo per squadra ---
def calc_home_factors(stats, avg):
    """Rapporto gol casa/trasferta della squadra vs media lega, con shrinkage."""
    th = sum(t["home_gf"] for t in stats.values())
    ta = sum(t["away_gf"] for t in stats.values())
    thm = sum(t["hg"] for t in stats.values())
    tam = sum(t["ag"] for t in stats.values())
    lhr = (th/thm) / (ta/tam) if thm > 0 and tam > 0 and ta > 0 else 1.2
    factors = {}
    for team, s in stats.items():
        if s["hg"] >= 3 and s["ag"] >= 3:
            thr = s["home_gf"] / s["hg"]
            tar = s["away_gf"] / s["ag"] if s["ag"] > 0 else ta/tam if tam > 0 else 1.0
            raw = (thr / tar) / lhr if tar > 0 and lhr > 0 else 1.0
            factors[team] = 0.7 * raw + 0.3  # shrinkage verso 1.0
        else:
            factors[team] = 1.0
    return factors

# --- Strato 3: Dixon-Coles ---
def dc_tau(hg, ag, lam_h, lam_a, rho):
    """Correzione Dixon-Coles per risultati bassi."""
    if hg == 0 and ag == 0: return 1 - lam_h * lam_a * rho
    if hg == 0 and ag == 1: return 1 + lam_h * rho
    if hg == 1 and ag == 0: return 1 + lam_a * rho
    if hg == 1 and ag == 1: return 1 - rho
    return 1.0

def estimate_rho(results, stats, avg):
    """Stima rho confrontando frequenza osservata vs attesa di 0-0 e 1-1."""
    results = [_normalize_result(r) for r in results]
    if len(results) < 30:
        return RHO_DEFAULT
    obs = {(0,0): 0, (1,1): 0}
    exp = {(0,0): 0.0, (1,1): 0.0}
    n = 0
    for r in results:
        hs = stats.get(r["home"])
        as_ = stats.get(r["away"])
        if not hs or not as_ or hs["hw"] == 0 or as_["aw"] == 0:
            continue
        lh = (hs["hgf_w"]/hs["hw"]) * (as_["aga_w"]/as_["aw"]) / avg if avg > 0 else 1.0
        la = (as_["agf_w"]/as_["aw"]) * (hs["hga_w"]/hs["hw"]) / avg if avg > 0 else 1.0
        k = (min(r["hg"], 2), min(r["ag"], 2))
        if k in obs:
            obs[k] += 1
            exp[k] += poisson_pmf(lh, k[0]) * poisson_pmf(la, k[1])
        n += 1
    if n < 30:
        return RHO_DEFAULT
    rhos = []
    for k in [(0,0), (1,1)]:
        if exp[k] > 0:
            rhos.append(-(obs[k] / (exp[k] * n) - 1) * 0.3)
    rho = sum(rhos) / len(rhos) if rhos else RHO_DEFAULT
    return round(max(-0.25, min(0.05, rho)), 4)

def dc_prob_matrix(lam_h, lam_a, rho, mg=8):
    """Matrice probabilità congiunta con correzione Dixon-Coles."""
    return [[max(poisson_pmf(lam_h, i) * poisson_pmf(lam_a, j) * dc_tau(i, j, lam_h, lam_a, rho), 0)
             for j in range(mg + 1)] for i in range(mg + 1)]

def prob_home_over_05(lam_h, lam_a, rho):
    p = dc_prob_matrix(lam_h, lam_a, rho)
    return min(max(1 - sum(p[0][j] for j in range(len(p[0]))), 0.01), 0.99)

def prob_away_under_15(lam_h, lam_a, rho):
    p = dc_prob_matrix(lam_h, lam_a, rho)
    return min(max(sum(p[i][0] + p[i][1] for i in range(len(p))), 0.01), 0.99)

# --- Filtro risultati ---
def filter_results(results, range_type, custom_n=10):
    """Filter results by range."""
    results = [_normalize_result(r) for r in results]
    if range_type == "all":
        return results
    now = date.today()
    if range_type == "2026":
        return [r for r in results if r["date"] >= "2026-01-01"]
    if range_type == "last30d":
        cutoff = (now - timedelta(days=30)).isoformat()
        return [r for r in results if r["date"] >= cutoff]
    if range_type == "last60d":
        cutoff = (now - timedelta(days=60)).isoformat()
        return [r for r in results if r["date"] >= cutoff]
    if range_type.startswith("last") or range_type == "custom":
        n = custom_n if range_type == "custom" else int(range_type.replace("last", ""))
        sorted_r = sorted(results, key=lambda x: x["date"], reverse=True)
        team_count = {}
        kept = []
        for r in sorted_r:
            h, a = r["home"], r["away"]
            ch = team_count.get(h, 0)
            ca = team_count.get(a, 0)
            if ch < n or ca < n:
                kept.append(r)
                if ch < n: team_count[h] = ch + 1
                if ca < n: team_count[a] = ca + 1
        return kept
    return results

# --- Predizioni complete ---
def predict_serie_b(results, fixtures, range_type="all", custom_n=10):
    """Serie B: Casa Over 0.5 — Dixon-Coles + Decadimento + Fattore Campo."""
    filtered = filter_results(results, range_type, custom_n)
    stats = calc_stats(filtered)
    avg = league_avg(stats)
    hf = calc_home_factors(stats, avg)
    rho = estimate_rho(filtered, stats, avg)
    predictions = []
    
    for fix in fixtures:
        fix = _normalize_fixture(fix)
        h, a = fix["home"], fix["away"]
        hs = stats.get(h)
        as_ = stats.get(a)
        if not hs or not as_:
            continue
        hA = hs["hgf_w"] / hs["hw"] if hs["hw"] > 0 else avg
        aD = as_["aga_w"] / as_["aw"] if as_["aw"] > 0 else avg
        aA = as_["agf_w"] / as_["aw"] if as_["aw"] > 0 else avg * 0.8
        hD = hs["hga_w"] / hs["hw"] if hs["hw"] > 0 else avg
        home_f = hf.get(h, 1.0)
        lam_h = (hA * aD) / avg * home_f
        lam_a = (aA * hD) / avg / home_f
        prob = prob_home_over_05(lam_h, lam_a, rho)
        predictions.append({
            "h": h, "a": a, "prob": round(prob, 4), "lam": round(lam_h, 2), "lam_a": round(lam_a, 2),
            "hA": round(hA, 2), "aD": round(aD, 2), "hG": hs["hg"], "aG": as_["ag"],
            "hf": round(home_f, 2), "rho": round(rho, 3), "date": fix.get("date", "")
        })
    
    predictions.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": predictions, "total": len(filtered), "avg": round(avg, 2), "rho": round(rho, 3)}

def predict_serie_c(results, fixtures, range_type="all", custom_n=10):
    """Serie C: Ospite Under 1.5 — Dixon-Coles + Decadimento + Fattore Campo."""
    filtered = filter_results(results, range_type, custom_n)
    stats = calc_stats(filtered)
    avg = league_avg(stats)
    hf = calc_home_factors(stats, avg)
    rho = estimate_rho(filtered, stats, avg)
    predictions = []
    
    for fix in fixtures:
        fix = _normalize_fixture(fix)
        h, a = fix["home"], fix["away"]
        hs = stats.get(h)
        as_ = stats.get(a)
        if not hs or not as_:
            continue
        hA = hs["hgf_w"] / hs["hw"] if hs["hw"] > 0 else avg
        aD = as_["aga_w"] / as_["aw"] if as_["aw"] > 0 else avg
        aA = as_["agf_w"] / as_["aw"] if as_["aw"] > 0 else avg * 0.8
        hD = hs["hga_w"] / hs["hw"] if hs["hw"] > 0 else avg
        home_f = hf.get(h, 1.0)
        lam_h = (hA * aD) / avg * home_f
        lam_a = (aA * hD) / avg / home_f
        prob = prob_away_under_15(lam_h, lam_a, rho)
        predictions.append({
            "h": h, "a": a, "prob": round(prob, 4), "lam": round(lam_a, 2), "lam_h": round(lam_h, 2),
            "aA": round(aA, 2), "hD": round(hD, 2), "hG": hs["hg"], "aG": as_["ag"],
            "hf": round(home_f, 2), "rho": round(rho, 3), "date": fix.get("date", "")
        })
    
    predictions.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": predictions, "total": len(filtered), "avg": round(avg, 2), "rho": round(rho, 3)}

def calc_standings(results):
    """Calculate league standings from results list (handles both formats)."""
    st = {}
    for r in results:
        r = _normalize_result(r)
        h, a, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        for t in [h, a]:
            if t not in st:
                st[t] = {"g": 0, "w": 0, "d": 0, "l": 0, "gf": 0, "ga": 0, "pts": 0}
        st[h]["g"] += 1; st[a]["g"] += 1
        st[h]["gf"] += hg; st[h]["ga"] += ag
        st[a]["gf"] += ag; st[a]["ga"] += hg
        if hg > ag:
            st[h]["w"] += 1; st[h]["pts"] += 3; st[a]["l"] += 1
        elif hg < ag:
            st[a]["w"] += 1; st[a]["pts"] += 3; st[h]["l"] += 1
        else:
            st[h]["d"] += 1; st[a]["d"] += 1; st[h]["pts"] += 1; st[a]["pts"] += 1
    
    return sorted(st.items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"] - x[1]["ga"])))

# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__)

@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route("/api/data")
def api_data():
    return jsonify(load_data())

@app.route("/api/predict")
def api_predict():
    data = load_data()
    range_type = request.args.get("range", "all")
    custom_n = int(request.args.get("customN", 10))
    
    # Serie B
    rB = predict_serie_b(
        data["serieB"].get("results", []),
        data["serieB"].get("next_fixtures", []),
        range_type, custom_n
    )
    rB["next_giornata"] = data["serieB"].get("next_giornata", 0)
    
    # Serie C — each girone separately
    allC = {}
    for key, gir in [("serieCa", "A"), ("serieCb", "B"), ("serieCc", "C")]:
        res = predict_serie_c(
            data[key].get("results", []),
            data[key].get("next_fixtures", []),
            range_type, custom_n
        )
        res["girone"] = gir
        res["next_giornata"] = data[key].get("next_giornata", 0)
        for p in res["predictions"]:
            p["gir"] = gir
        allC[gir] = res
    
    return jsonify({"serieB": rB, "serieC": allC})

@app.route("/api/standings")
def api_standings():
    data = load_data()
    return jsonify({
        "serieB": calc_standings(data["serieB"].get("results", [])),
        "serieCa": calc_standings(data["serieCa"].get("results", [])),
        "serieCb": calc_standings(data["serieCb"].get("results", [])),
        "serieCc": calc_standings(data["serieCc"].get("results", [])),
    })

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Scrape all leagues from calciomagazine.net."""
    data = load_data()
    if data.get("version") != 7:
        data = get_default_data()
    
    errors = []
    log = []
    
    for key, cfg in LEAGUES.items():
        name = cfg["name"]
        
        # --- Fetch results page ---
        html_r = fetch_page(cfg["results_url"])
        if html_r is None:
            errors.append(f"Errore scaricamento risultati {name}")
        else:
            results_by_g = parse_results_page(html_r)
            all_results = []
            for g_num, matches in results_by_g.items():
                all_results.extend(matches)
            
            # Only update if we got meaningful data (safeguard against broken parsing)
            if len(all_results) > 0:
                data[key]["results_by_giornata"] = {str(k): v for k, v in results_by_g.items()}
                data[key]["results"] = all_results
                total_r = len(all_results)
                n_giornate = len(results_by_g)
                log.append(f"{name}: {total_r} risultati in {n_giornate} giornate")
            else:
                prev_count = len(data[key].get("results", []))
                log.append(f"{name}: ⚠️ nessun risultato trovato (mantengo {prev_count} precedenti)")
                if prev_count == 0:
                    errors.append(f"Nessun risultato trovato per {name}")
        
        # --- Fetch calendar page (with fallback URL) ---
        html_c = fetch_page(cfg["calendar_url"])
        if html_c is None and "calendar_url_alt" in cfg:
            log.append(f"  → Provo URL alternativo per calendario {name}...")
            html_c = fetch_page(cfg["calendar_url_alt"])
        if html_c is None:
            errors.append(f"Errore scaricamento calendario {name}")
        else:
            calendar_by_g = parse_calendar_page(html_c)
            data[key]["calendar_by_giornata"] = {str(k): v for k, v in calendar_by_g.items()}
            
            # Find next giornata
            results_by_g = {int(k): v for k, v in data[key].get("results_by_giornata", {}).items()}
            next_g = find_next_giornata(results_by_g, calendar_by_g)
            data[key]["next_giornata"] = next_g
            data[key]["next_fixtures"] = calendar_by_g.get(next_g, [])
            
            n_fix = len(data[key]["next_fixtures"])
            log.append(f"  → Prossima giornata: {next_g}ª ({n_fix} partite)")
    
    save_data(data)
    
    counts = {k: len(data[k].get("results", [])) for k in LEAGUES}
    next_g = {k: data[k].get("next_giornata", 0) for k in LEAGUES}
    next_fix = {k: len(data[k].get("next_fixtures", [])) for k in LEAGUES}
    total = sum(counts.values())
    
    if errors:
        msg = f"⚠️ {'; '.join(errors)}"
    else:
        msg = f"✅ {total} risultati scaricati da calciomagazine.net"
    
    return jsonify({
        "success": len(errors) == 0,
        "total": total,
        "counts": counts,
        "next_giornata": next_g,
        "next_fixtures": next_fix,
        "errors": errors,
        "log": log,
        "message": msg
    })

@app.route("/api/export")
def api_export():
    return jsonify(load_data())

@app.route("/api/import", methods=["POST"])
def api_import():
    data = request.json
    if data and "serieB" in data:
        save_data(data)
        return jsonify({"success": True})
    return jsonify({"success": False, "error": "Formato non valido"})

@app.route("/api/reset", methods=["POST"])
def api_reset():
    save_data(get_default_data())
    return jsonify({"success": True})

@app.route("/api/status")
def api_status():
    data = load_data()
    counts = {k: len(data[k].get("results", [])) for k in LEAGUES}
    next_g = {k: data[k].get("next_giornata", 0) for k in LEAGUES}
    next_fix = {k: len(data[k].get("next_fixtures", [])) for k in LEAGUES}
    return jsonify({
        "updatedAt": data.get("updatedAt", "N/A"),
        "total": sum(counts.values()),
        "counts": counts,
        "next_giornata": next_g,
        "next_fixtures": next_fix,
    })


# ============================================================
# HTML TEMPLATE
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚽ Pronostici Serie B & C — v7</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@400;500;600;700&family=Space+Mono:wght@400;700&display=swap" rel="stylesheet">
<style>
*{margin:0;padding:0;box-sizing:border-box}
:root{
--bg:#0a0e17;--card:#111827;--card2:#1a2235;--accent:#22d3ee;--accent2:#a78bfa;
--green:#34d399;--red:#f87171;--orange:#fbbf24;--text:#e2e8f0;--text2:#94a3b8;
--border:#1e293b;--glow:0 0 20px rgba(34,211,238,0.15);
}
body{font-family:'DM Sans',sans-serif;background:var(--bg);color:var(--text);min-height:100vh;line-height:1.5}
.container{max-width:1200px;margin:0 auto;padding:16px}
.header{text-align:center;padding:32px 16px 20px;position:relative;overflow:hidden}
.header::before{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);width:600px;height:200px;background:radial-gradient(ellipse,rgba(34,211,238,0.08),transparent 70%);pointer-events:none}
.header h1{font-family:'Space Mono',monospace;font-size:clamp(1.3rem,3.5vw,2.1rem);font-weight:700;background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent;letter-spacing:-0.5px}
.header p{color:var(--text2);font-size:0.82rem;margin-top:4px}
.data-status{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;margin:10px 0 6px;font-size:0.72rem;font-family:'Space Mono',monospace}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.status-dot.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
.status-dot.empty{background:var(--orange);box-shadow:0 0 6px var(--orange)}
.status-dot.loading{background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:0.4}}
.status-text{color:var(--text2)}.status-text b{color:var(--accent)}
.tabs{display:flex;gap:6px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.tab{padding:9px 20px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--text2);cursor:pointer;font-size:0.82rem;font-weight:600;transition:all .2s}
.tab.active,.tab:hover{background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border-color:var(--accent);color:var(--accent);box-shadow:var(--glow)}
.range-panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin:12px 0}
.range-panel h3{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--accent);margin-bottom:10px;letter-spacing:1px;text-transform:uppercase}
.range-grid{display:flex;flex-wrap:wrap;gap:6px}
.range-btn{padding:6px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card2);color:var(--text2);cursor:pointer;font-size:0.75rem;font-weight:500;transition:all .2s}
.range-btn.active,.range-btn:hover{background:rgba(34,211,238,0.12);border-color:var(--accent);color:var(--accent)}
.custom-n{display:none;align-items:center;gap:8px;margin-top:8px}
.custom-n.show{display:flex}
.custom-n input{width:55px;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:0.82rem;text-align:center}
.custom-n label{font-size:0.75rem;color:var(--text2)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;margin:14px 0}
.match-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;transition:all .3s;position:relative;overflow:hidden}
.match-card:hover{border-color:var(--accent);box-shadow:var(--glow);transform:translateY(-2px)}
.match-card .rank{position:absolute;top:10px;right:12px;font-family:'Space Mono',monospace;font-size:0.62rem;color:var(--text2);background:var(--card2);padding:2px 7px;border-radius:6px}
.match-card .teams{font-size:0.95rem;font-weight:600;margin-bottom:8px;line-height:1.4}
.match-card .vs{color:var(--text2);font-weight:400;font-size:0.78rem;margin:0 4px}
.match-card .girone-tag{font-size:0.62rem;color:var(--accent2);font-family:'Space Mono',monospace;margin-bottom:4px;background:rgba(167,139,250,0.1);display:inline-block;padding:2px 8px;border-radius:4px}
.match-card .match-date{font-size:0.68rem;color:var(--text2);font-family:'Space Mono',monospace;margin-bottom:6px;display:flex;align-items:center;gap:5px}
.prob-container{margin:8px 0}
.prob-label{display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:3px}
.prob-label .type{color:var(--text2)}
.prob-label .pct{font-family:'Space Mono',monospace;font-weight:700}
.prob-bar{height:7px;background:var(--card2);border-radius:4px;overflow:hidden}
.prob-fill{height:100%;border-radius:4px;transition:width .6s ease}
.prob-fill.high{background:linear-gradient(90deg,var(--green),#10b981)}
.prob-fill.mid{background:linear-gradient(90deg,var(--orange),#f59e0b)}
.prob-fill.low{background:linear-gradient(90deg,var(--red),#ef4444)}
.detail-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:0.7rem}
.detail-stat{background:var(--card2);border-radius:6px;padding:5px 9px}
.detail-stat .dl{color:var(--text2);font-size:0.62rem;text-transform:uppercase;letter-spacing:0.5px}
.detail-stat .dv{font-family:'Space Mono',monospace;font-weight:700;color:var(--text);margin-top:1px}
.stitle{font-family:'Space Mono',monospace;font-size:1.05rem;font-weight:700;margin:24px 0 6px;display:flex;align-items:center;gap:10px}
.stitle .dot{width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ssub{font-size:0.75rem;color:var(--text2);margin-bottom:12px}
.giornata-badge{display:inline-block;background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border:1px solid var(--accent);border-radius:8px;padding:4px 12px;font-family:'Space Mono',monospace;font-size:0.78rem;color:var(--accent);margin-bottom:8px}
.btn{padding:8px 18px;border-radius:8px;border:none;font-weight:600;font-size:0.78rem;cursor:pointer;transition:all .2s}
.btn-p{background:var(--accent);color:var(--bg)}
.btn-s{background:var(--card2);color:var(--text2);border:1px solid var(--border)}
.btn-d{background:rgba(248,113,113,0.15);color:var(--red);border:1px solid rgba(248,113,113,0.3)}
.btn-g{background:rgba(52,211,153,0.15);color:var(--green);border:1px solid rgba(52,211,153,0.3)}
.btn-u{background:linear-gradient(135deg,rgba(34,211,238,0.2),rgba(167,139,250,0.2));color:var(--accent);border:1px solid var(--accent);font-size:0.82rem;padding:10px 22px}
.btn:hover{opacity:0.85;transform:translateY(-1px)}
.btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.dm-bar{display:flex;gap:8px;justify-content:center;margin:8px 0;flex-wrap:wrap}
.dm-bar .btn{font-size:0.72rem;padding:6px 14px}
.st{width:100%;border-collapse:collapse;font-size:0.75rem;margin:10px 0}
.st th{padding:7px 8px;text-align:center;border-bottom:1px solid var(--border);color:var(--accent);font-family:'Space Mono',monospace;font-size:0.68rem;text-transform:uppercase}
.st td{padding:6px 8px;text-align:center;border-bottom:1px solid var(--border)}
.st td:nth-child(2){text-align:left;font-weight:600}
.st tr:hover{background:rgba(34,211,238,0.04)}
.empty{text-align:center;padding:36px;color:var(--text2);font-size:0.82rem}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:12px 24px;font-size:0.82rem;color:var(--accent);box-shadow:0 8px 30px rgba(0,0,0,0.4);transition:transform .3s ease;z-index:999;pointer-events:none}
.toast.show{transform:translateX(-50%) translateY(0)}
.scrape-log{background:var(--card2);border-radius:8px;padding:10px;margin-top:8px;font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--text2);max-height:250px;overflow-y:auto;display:none}
.scrape-log.show{display:block}
.section-sep{border:none;border-top:1px solid var(--border);margin:20px 0}
@media(max-width:600px){
.container{padding:10px}.cards{grid-template-columns:1fr}.range-grid{gap:4px}
.range-btn{padding:5px 9px;font-size:0.7rem}.match-card{padding:12px}
.dm-bar{flex-direction:column;align-items:stretch}
}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>⚽ Pronostici Serie B & C</h1>
<p>Dixon-Coles + Decadimento temporale + Fattore campo — v7 · Dati da calciomagazine.net</p>
</div>

<div class="data-status" id="dataStatus">
<span class="status-dot empty"></span>
<span class="status-text">Caricamento...</span>
</div>

<div style="text-align:center;margin:14px 0">
<button class="btn btn-u" onclick="doScrape()" id="scrapeBtn">🔄 Aggiorna da calciomagazine.net</button>
</div>
<div class="scrape-log" id="scrapeLog"></div>

<div class="dm-bar">
<button class="btn btn-g" onclick="doExport()">📤 Esporta JSON</button>
<button class="btn btn-s" onclick="document.getElementById('importFile').click()">📥 Importa JSON</button>
<button class="btn btn-d" onclick="doReset()">🗑️ Reset</button>
<input type="file" id="importFile" accept=".json" style="display:none" onchange="doImport(event)">
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab('predictions',this)">📊 Pronostici</div>
<div class="tab" onclick="switchTab('standings',this)">🏆 Classifiche</div>
</div>

<div class="range-panel" id="rangePanel">
<h3>📐 Range Statistiche</h3>
<div class="range-grid">
<button class="range-btn active" onclick="setRange('all',this)">Tutta la stagione</button>
<button class="range-btn" onclick="setRange('last5',this)">Ultime 5</button>
<button class="range-btn" onclick="setRange('last8',this)">Ultime 8</button>
<button class="range-btn" onclick="setRange('last10',this)">Ultime 10</button>
<button class="range-btn" onclick="setRange('last15',this)">Ultime 15</button>
<button class="range-btn" onclick="setRange('2026',this)">Solo 2026</button>
<button class="range-btn" onclick="setRange('last30d',this)">Ultimi 30gg</button>
<button class="range-btn" onclick="setRange('last60d',this)">Ultimi 60gg</button>
<button class="range-btn" onclick="setRange('custom',this)">Personalizzato</button>
</div>
<div class="custom-n" id="customN">
<label>Ultime</label>
<input type="number" id="customNval" value="12" min="3" max="38" onchange="recalc()">
<label>partite per squadra</label>
</div>
</div>

<div id="tab_predictions">
<!-- Serie B -->
<div class="stitle"><span class="dot"></span>Serie B — CASA OVER 0.5</div>
<div id="giornataB"></div>
<div class="ssub" id="statsB"></div>
<div class="cards" id="cardsB"></div>

<hr class="section-sep">

<!-- Serie C Girone A -->
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone A — OSPITE UNDER 1.5</div>
<div id="giornataCa"></div>
<div class="ssub" id="statsCa"></div>
<div class="cards" id="cardsCa"></div>

<hr class="section-sep">

<!-- Serie C Girone B -->
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone B — OSPITE UNDER 1.5</div>
<div id="giornataCb"></div>
<div class="ssub" id="statsCb"></div>
<div class="cards" id="cardsCb"></div>

<hr class="section-sep">

<!-- Serie C Girone C -->
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone C — OSPITE UNDER 1.5</div>
<div id="giornataCc"></div>
<div class="ssub" id="statsCc"></div>
<div class="cards" id="cardsCc"></div>
</div>

<div id="tab_standings" style="display:none">
<div class="stitle"><span class="dot"></span>Classifica Serie B</div>
<div style="overflow-x:auto"><table class="st" id="tableB"></table></div>
<div id="tablesC"></div>
</div>

</div>

<div class="toast" id="toast"></div>

<script>
let curRange='all';

function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)}
function pc(p){return p>=0.75?'high':p>=0.55?'mid':'low'}
function pcol(p){return p>=0.75?'#34d399':p>=0.55?'#fbbf24':'#f87171'}

// ============================================================
// STATUS
// ============================================================
async function updateStatus(){
    const r=await fetch('/api/status');const d=await r.json();
    const dot=d.total>0?'ok':'empty';
    const el=document.getElementById('dataStatus');
    const ng=d.next_giornata||{};
    const nf=d.next_fixtures||{};
    el.innerHTML=`<span class="status-dot ${dot}"></span><span class="status-text">📊 <b>${d.total}</b> risultati · Agg: <b>${d.updatedAt}</b> · Prossime: B=${ng.serieB||'?'}ª CA=${ng.serieCa||'?'}ª CB=${ng.serieCb||'?'}ª CC=${ng.serieCc||'?'}ª</span>`;
}

// ============================================================
// SCRAPE
// ============================================================
async function doScrape(){
    const btn=document.getElementById('scrapeBtn');
    const log=document.getElementById('scrapeLog');
    btn.disabled=true;btn.textContent='⏳ Scaricamento in corso...';
    log.classList.add('show');log.innerHTML='Connessione a calciomagazine.net...<br>';
    
    const dot=document.querySelector('.status-dot');
    if(dot){dot.className='status-dot loading'}
    
    try{
        const r=await fetch('/api/scrape',{method:'POST'});
        const d=await r.json();
        log.innerHTML+=d.message+'<br>';
        if(d.log){log.innerHTML+=d.log.map(l=>'  '+l).join('<br>')+'<br>'}
        if(d.errors&&d.errors.length>0){log.innerHTML+='<br>Errori: '+d.errors.join(', ')+'<br>'}
        toast(d.message);
        await updateStatus();
        recalc();
    }catch(e){
        log.innerHTML+='❌ Errore: '+e.message+'<br>';
        toast('❌ Errore di connessione');
    }
    btn.disabled=false;btn.textContent='🔄 Aggiorna da calciomagazine.net';
    setTimeout(()=>log.classList.remove('show'),8000);
}

// ============================================================
// PREDICTIONS
// ============================================================
function fmtDate(d){
    if(!d)return'';
    const[y,m,dd]=d.split('-');
    const days=['Dom','Lun','Mar','Mer','Gio','Ven','Sab'];
    const dt=new Date(y,m-1,dd);
    return days[dt.getDay()]+' '+dd+'/'+m+'/'+y;
}

function crdB(i,p){
const dateHtml=p.date?`<div class="match-date">📅 ${fmtDate(p.date)}</div>`:'';
return`<div class="match-card"><div class="rank">#${i+1}</div>
${dateHtml}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container"><div class="prob-label"><span class="type">CASA OVER 0.5</span>
<span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
<div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
<div class="detail-stats">
<div class="detail-stat"><div class="dl">λ Casa / Osp</div><div class="dv">${p.lam} / ${p.lam_a||'—'}</div></div>
<div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.hA} / ${p.aD}</div></div>
<div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div>
<div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div>
</div></div>`}

function crdC(i,p){
const dateHtml=p.date?`<div class="match-date">📅 ${fmtDate(p.date)}</div>`:'';
return`<div class="match-card"><div class="rank">#${i+1}</div>
${dateHtml}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container"><div class="prob-label"><span class="type">OSPITE UNDER 1.5</span>
<span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
<div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
<div class="detail-stats">
<div class="detail-stat"><div class="dl">λ Osp / Casa</div><div class="dv">${p.lam} / ${p.lam_h||'—'}</div></div>
<div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.aA} / ${p.hD}</div></div>
<div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div>
<div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div>
</div></div>`}

async function recalc(){
    const cn=document.getElementById('customNval');
    const n=cn?cn.value:10;
    const r=await fetch(`/api/predict?range=${curRange}&customN=${n}`);
    const d=await r.json();
    
    // Serie B
    const bG=d.serieB.next_giornata;
    document.getElementById('giornataB').innerHTML=bG?`<span class="giornata-badge">${bG}ª Giornata</span>`:'';
    document.getElementById('statsB').innerHTML=`📊 <b>${d.serieB.total}</b> partite analizzate · Media gol: <b>${d.serieB.avg}</b>`;
    document.getElementById('cardsB').innerHTML=d.serieB.predictions.length?
        d.serieB.predictions.map((p,i)=>crdB(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';
    
    // Serie C per girone
    ['A','B','C'].forEach(gir=>{
        const key=gir==='A'?'serieCa':gir==='B'?'serieCb':'serieCc';
        const suffix=gir.toLowerCase();
        const gData=d.serieC[gir];
        if(!gData)return;
        
        const cG=gData.next_giornata;
        document.getElementById('giornataC'+suffix[0]).innerHTML=cG?`<span class="giornata-badge">${cG}ª Giornata</span>`:'';
        document.getElementById('statsC'+suffix[0]).innerHTML=`📊 <b>${gData.total}</b> partite analizzate · Media gol: <b>${gData.avg}</b>`;
        document.getElementById('cardsC'+suffix[0]).innerHTML=gData.predictions.length?
            gData.predictions.map((p,i)=>crdC(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';
    });
}

// ============================================================
// STANDINGS
// ============================================================
async function renderStandings(){
    const r=await fetch('/api/standings');
    const d=await r.json();
    
    function tblH(rows){
        if(!rows.length)return'<tbody><tr><td colspan="9" style="text-align:center;color:var(--text2)">Nessun dato</td></tr></tbody>';
        return`<thead><tr><th>#</th><th>Squadra</th><th>G</th><th>V</th><th>P</th><th>S</th><th>GF</th><th>GS</th><th>Pt</th></tr></thead><tbody>`+
        rows.map(([n,s],i)=>`<tr><td>${i+1}</td><td>${n}</td><td>${s.g}</td><td>${s.w}</td><td>${s.d}</td><td>${s.l}</td><td>${s.gf}</td><td>${s.ga}</td><td><b>${s.pts}</b></td></tr>`).join('')+'</tbody>'}
    
    document.getElementById('tableB').innerHTML=tblH(d.serieB);
    let html='';
    [{k:'serieCa',n:'A'},{k:'serieCb',n:'B'},{k:'serieCc',n:'C'}].forEach(g=>{
        html+=`<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C — Girone ${g.n}</div>
        <div style="overflow-x:auto"><table class="st">${tblH(d[g.k])}</table></div>`;
    });
    document.getElementById('tablesC').innerHTML=html;
}

// ============================================================
// EXPORT / IMPORT / RESET
// ============================================================
async function doExport(){
    const r=await fetch('/api/export');const d=await r.json();
    const b=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
    const u=URL.createObjectURL(b);const a=document.createElement('a');
    a.href=u;a.download=`pronostici_v7_${d.updatedAt||'export'}.json`;a.click();URL.revokeObjectURL(u);
    toast('📤 Dati esportati!');
}

async function doImport(ev){
    const f=ev.target.files[0];if(!f)return;
    const reader=new FileReader();
    reader.onload=async function(e){
        try{const d=JSON.parse(e.target.result);
        const r=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
        const res=await r.json();
        if(res.success){updateStatus();recalc();toast('📥 Dati importati!')}
        else{toast('⚠️ '+res.error)}}
        catch(err){toast('⚠️ Errore parsing')}};
    reader.readAsText(f);ev.target.value='';
}

async function doReset(){
    if(!confirm('⚠️ Cancellare tutti i dati?'))return;
    await fetch('/api/reset',{method:'POST'});updateStatus();recalc();toast('🗑️ Dati cancellati');
}

// ============================================================
// UI
// ============================================================
function setRange(r,el){curRange=r;document.querySelectorAll('.range-btn').forEach(b=>b.classList.remove('active'));el.classList.add('active');document.getElementById('customN').classList.toggle('show',r==='custom');recalc()}
function switchTab(tab,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));el.classList.add('active');
['predictions','standings'].forEach(t=>{document.getElementById('tab_'+t).style.display=t===tab?'':'none'});
if(tab==='standings')renderStandings()}

// ============================================================
// INIT
// ============================================================
updateStatus();recalc();
</script>
</body>
</html>"""


# ============================================================
# MAIN
# ============================================================
if __name__ == "__main__":
    import threading

    # Init data
    if not DATA_FILE.exists():
        save_data(get_default_data())
        print("📂 Primo avvio — premi 🔄 nell'interfaccia per scaricare i dati")
    else:
        d = load_data()
        total = sum(len(d[k].get("results", [])) for k in LEAGUES)
        print(f"📂 Dati caricati: {total} risultati")

    # Start Flask in background thread
    def run_server():
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)

    server = threading.Thread(target=run_server, daemon=True)
    server.start()

    # Open desktop window with pywebview
    try:
        import webview
        print("⚽ Avvio finestra desktop...")
        webview.create_window(
            "⚽ Pronostici Serie B & C — v7",
            f"http://127.0.0.1:{PORT}",
            width=1100,
            height=800,
            min_size=(600, 500),
        )
        webview.start()
    except ImportError:
        print(f"\n⚠️  pywebview non trovato. Installalo con: pip install pywebview")
        print(f"    Oppure apri manualmente: http://localhost:{PORT}\n")
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")
        try:
            server.join()
        except KeyboardInterrupt:
            pass
