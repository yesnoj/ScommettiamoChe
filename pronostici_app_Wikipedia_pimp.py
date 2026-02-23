#!/usr/bin/env python3
"""
⚽ Pronostici Serie B & C — v7 Python/Flask
Modello Dixon-Coles + Decadimento temporale + Fattore campo
Aggiornamento da Wikipedia (it.wikipedia.org)
"""

import json, re, os, math, sys
from datetime import datetime, date, timedelta
from pathlib import Path

try:
    from flask import Flask, jsonify, request, render_template_string
except ImportError:
    print("Installo dipendenze..."); os.system(f"{sys.executable} -m pip install flask requests beautifulsoup4"); from flask import Flask, jsonify, request, render_template_string

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests"); import requests as req

try:
    from bs4 import BeautifulSoup
except ImportError:
    os.system(f"{sys.executable} -m pip install beautifulsoup4"); from bs4 import BeautifulSoup

APP_DIR = Path(__file__).parent
DATA_FILE = APP_DIR / "pronostici_data.json"
PORT = 5050

WIKI_SERIE_B = "https://it.wikipedia.org/wiki/Serie_B_2025-2026"
WIKI_SERIE_C = "https://it.wikipedia.org/wiki/Serie_C_2025-2026"

MESI_IT = {"gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,"lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12}

# ============================================================
# SCRAPING & PARSING HELPERS
# ============================================================
def fetch_page(url):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36", "Accept-Language": "it-IT,it;q=0.9"}
    try:
        r = req.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status(); r.encoding = 'utf-8'; return r.text
    except Exception as e:
        print(f"  ❌ Errore fetch {url}: {e}"); return None

def parse_italian_date(text):
    text = text.strip().rstrip('.')
    text = re.sub(r'(\d+)[ºª°]', r'\1', text)
    m = re.match(r'(\d{1,2})\s+(\w+)', text)
    if not m: return ''
    day = int(m.group(1)); ms = m.group(2).lower().rstrip('.')
    month = 0
    for abbr, num in MESI_IT.items():
        if ms.startswith(abbr): month = num; break
    if not month: return ''
    year = 2025 if month >= 7 else 2026
    try: return date(year, month, day).isoformat()
    except: return ''

def _is_score(t): return bool(re.match(r'^\d+\s*[-\u2013]\s*\d+$', t.strip()))
def _is_time_or_dash(t): t=t.strip(); return t == '-' or bool(re.match(r'^\d{1,2}:\d{2}$', t))
def _parse_score(t):
    m = re.match(r'^(\d+)\s*[-\u2013]\s*(\d+)$', t.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None
def _is_match_name(t):
    t = t.strip()
    if _is_score(t) or _is_time_or_dash(t): return False
    if not re.search(r'[A-Za-z\u00C0-\u00FF]', t): return False
    if not re.search(r'[-\u2013\u2014]', t): return False
    return True
def _split_match_name(text):
    parts = re.split(r'[-\u2013\u2014]', text)
    if len(parts) < 2: return None, None
    if len(parts) == 2: return parts[0].strip().replace('*','').strip(), parts[1].strip().replace('*','').strip()
    return '-'.join(parts[:-1]).strip().replace('*','').strip(), parts[-1].strip().replace('*','').strip()

# ============================================================
# SERIE B PARSER
# ============================================================
def parse_wiki_serie_b(html):
    soup = BeautifulSoup(html, 'html.parser') if isinstance(html, str) else html
    giornate = {}
    for table in soup.find_all('table'):
        rows = table.find_all('tr'); current_g = 0; current_date = ''
        for row in rows:
            tds = row.find_all('td'); ths = row.find_all('th')
            if ths and not tds: continue
            if len(tds) > 10: continue
            if len(tds) == 3:
                mid = tds[1].get_text(strip=True)
                gm = re.match(r'^(\d+)[\u00AAa\u00B0]\s*giornata$', mid, re.IGNORECASE)
                if gm:
                    current_g = int(gm.group(1))
                    if current_g not in giornate: giornate[current_g] = {'results':[],'fixtures':[]}
                    current_date = ''; continue
            if current_g == 0: continue
            if len(tds) == 3:
                pd = parse_italian_date(tds[0].get_text(strip=True))
                if pd: current_date = pd
                match_text = tds[1].get_text(strip=True); score_text = tds[2].get_text(strip=True)
            elif len(tds) == 2:
                match_text = tds[0].get_text(strip=True); score_text = tds[1].get_text(strip=True)
            else: continue
            home, away = _split_match_name(match_text)
            if not home or not away: continue
            sc = _parse_score(score_text.strip())
            if sc:
                existing = [(r['home'],r['away']) for r in giornate[current_g]['results']]
                if (home,away) not in existing:
                    giornate[current_g]['results'].append({'date':current_date,'home':home,'away':away,'hg':sc[0],'ag':sc[1]})
            elif _is_time_or_dash(score_text.strip()):
                existing = [(f['home'],f['away']) for f in giornate[current_g]['fixtures']]
                if (home,away) not in existing:
                    giornate[current_g]['fixtures'].append({'date':current_date,'home':home,'away':away})
    return giornate

# ============================================================
# SERIE C PARSER — formato andata/ritorno combinato
# Ogni riga contiene 2 partite: andata (sx) e ritorno (dx)
# Colonne: [data_and] [ris_and] [Squadra1-Squadra2] [ris_rit] [data_rit]
# Con rowspan su entrambe le colonne data
# Girone A/B: tabelle in "Calendario"; Girone C: in "Risultati"
# ============================================================
def _is_date_text(text):
    return bool(re.match(r'^\d{1,2}[ºª°]?\s+\w{3}', text.strip()))

def parse_wiki_serie_c_girone(soup, girone_letter):
    """Parse un girone Serie C dalla pagina Wikipedia completa.
    Restituisce dict {giornata_num: {'results':[], 'fixtures':[]}}"""
    
    # 1) Trova la sezione del girone
    girone_section = None
    for h2 in soup.find_all('h2'):
        if f'Girone {girone_letter}' in h2.get_text():
            girone_section = h2.parent.parent  # section > div.mw-heading > h2
            break
    if not girone_section:
        return {}
    
    # 2) Trova tabelle in Calendario o Risultati (Girone C usa Risultati)
    all_tables = []
    for child in girone_section.children:
        if not hasattr(child, 'name') or child.name != 'section':
            continue
        heading = child.find(['h3', 'h4'])
        if not heading:
            continue
        h_text = heading.get_text(strip=True)
        if 'Calendario' in h_text or 'Risultati' in h_text:
            tables = child.find_all('table')
            if len(tables) >= 5:  # sezione con molte tabelle = dati partite
                all_tables.extend(tables)
    
    if not all_tables:
        return {}
    
    giornate = {}
    seen_results = set()   # (home, away, date) per dedup
    seen_fixtures = set()  # (home, away, 'a'|'r') per dedup
    
    for table in all_tables:
        current_g_andata = 0
        current_g_ritorno = 0
        andata_date_rs = 0     # rowspan rimanente per data andata
        ritorno_date_rs = 0    # rowspan rimanente per data ritorno
        current_andata_date = ""
        current_ritorno_date = ""
        
        for row in table.find_all('tr'):
            tds = row.find_all('td')
            n = len(tds)
            
            if n > 10 or n == 0:
                continue  # riga collassata o vuota
            
            # Controlla header giornata: 3 TD con "Nª giornata" al centro
            if n == 3:
                mid = tds[1].get_text(strip=True)
                gm = re.match(r'^(\d+)[ªa°]\s*giornata$', mid, re.IGNORECASE)
                if gm:
                    g_num = int(gm.group(1))
                    # Estrai numero ritorno dal terzo TD
                    rit_text = tds[2].get_text(strip=True)
                    rm = re.search(r'(\d+)[ªa°]', rit_text)
                    current_g_andata = g_num
                    current_g_ritorno = int(rm.group(1)) if rm else g_num + 19
                    if current_g_andata not in giornate:
                        giornate[current_g_andata] = {'results': [], 'fixtures': []}
                    if current_g_ritorno not in giornate:
                        giornate[current_g_ritorno] = {'results': [], 'fixtures': []}
                    # Reset rowspan per nuova giornata
                    andata_date_rs = 0
                    ritorno_date_rs = 0
                    continue
            
            if current_g_andata == 0:
                continue  # non siamo ancora in una giornata
            
            # Riga dati — parsing sequenziale basato su rowspan
            idx = 0
            
            # Colonna data andata (se rowspan scaduto)
            if andata_date_rs <= 0:
                if idx < n and _is_date_text(tds[idx].get_text(strip=True)):
                    current_andata_date = parse_italian_date(tds[idx].get_text(strip=True))
                    andata_date_rs = int(tds[idx].get('rowspan', 1))
                    idx += 1
                elif n < 3:
                    continue  # riga troppo corta
            
            remaining = n - idx
            if remaining < 3:
                continue  # servono almeno: ris_and, squadre, ris_rit
            
            andata_score_text = tds[idx].get_text(strip=True); idx += 1
            teams_text = tds[idx].get_text(strip=True); idx += 1
            ritorno_score_text = tds[idx].get_text(strip=True); idx += 1
            
            # Colonna data ritorno (se rowspan scaduto e c'è un TD in più)
            if ritorno_date_rs <= 0 and idx < n:
                current_ritorno_date = parse_italian_date(tds[idx].get_text(strip=True))
                ritorno_date_rs = int(tds[idx].get('rowspan', 1))
            
            andata_date_rs -= 1
            ritorno_date_rs -= 1
            
            # Parse squadre
            home, away = _split_match_name(teams_text)
            if not home or not away:
                continue
            
            # ANDATA: home gioca in casa
            sc_a = _parse_score(andata_score_text)
            if sc_a:
                key = (current_andata_date, home, away)
                if key not in seen_results:
                    giornate[current_g_andata]['results'].append({
                        'date': current_andata_date, 'home': home, 'away': away,
                        'hg': sc_a[0], 'ag': sc_a[1]
                    })
                    seen_results.add(key)
            elif _is_time_or_dash(andata_score_text):
                key = (home, away, 'a')
                if key not in seen_fixtures:
                    giornate[current_g_andata]['fixtures'].append({
                        'date': current_andata_date, 'home': home, 'away': away
                    })
                    seen_fixtures.add(key)
            
            # RITORNO: away gioca in casa (invertito!)
            sc_r = _parse_score(ritorno_score_text)
            if sc_r:
                key = (current_ritorno_date, away, home)
                if key not in seen_results:
                    giornate[current_g_ritorno]['results'].append({
                        'date': current_ritorno_date, 'home': away, 'away': home,
                        'hg': sc_r[0], 'ag': sc_r[1]
                    })
                    seen_results.add(key)
            elif _is_time_or_dash(ritorno_score_text):
                key = (away, home, 'r')
                if key not in seen_fixtures:
                    giornate[current_g_ritorno]['fixtures'].append({
                        'date': current_ritorno_date, 'home': away, 'away': home
                    })
                    seen_fixtures.add(key)
    
    return giornate

# ============================================================
# SHARED HELPERS
# ============================================================
def find_next_giornata(giornate_data):
    """Trova la prossima giornata da giocare.
    Priorità: giornata con più fixtures (almeno 5), altrimenti la prima con fixtures."""
    candidates = []
    for g in sorted(giornate_data.keys()):
        nf = len(giornate_data[g].get('fixtures', []))
        if nf > 0:
            candidates.append((g, nf))
    if not candidates:
        return max(giornate_data.keys()) + 1 if giornate_data else 1
    # Trova la prima giornata con >=5 fixtures (giornata "piena")
    for g, nf in candidates:
        if nf >= 5:
            return g
    # Se nessuna ha >=5, prendi quella con più fixtures
    return max(candidates, key=lambda x: x[1])[0]

def process_giornate(giornate_data, data_key, data):
    all_results = []; results_by_g = {}
    for g_num, g_data in giornate_data.items():
        results_by_g[str(g_num)] = g_data["results"]; all_results.extend(g_data["results"])
    if all_results:
        data[data_key]["results_by_giornata"] = results_by_g
        data[data_key]["results"] = all_results
        next_g = find_next_giornata(giornate_data)
        data[data_key]["next_giornata"] = next_g
        data[data_key]["next_fixtures"] = giornate_data.get(next_g, {}).get("fixtures", [])
        return len(all_results), len(giornate_data), next_g, len(data[data_key]["next_fixtures"])
    return 0, 0, 0, 0

# ============================================================
# DATA MANAGEMENT
# ============================================================
def get_default_data():
    return {"version":7,"source":"wikipedia","updatedAt":date.today().isoformat(),
        "serieB":{"results_by_giornata":{},"results":[],"next_giornata":0,"next_fixtures":[]},
        "serieCa":{"results_by_giornata":{},"results":[],"next_giornata":0,"next_fixtures":[]},
        "serieCb":{"results_by_giornata":{},"results":[],"next_giornata":0,"next_fixtures":[]},
        "serieCc":{"results_by_giornata":{},"results":[],"next_giornata":0,"next_fixtures":[]}}

def _normalize_result(r):
    if isinstance(r, list): return {"date":r[0],"home":r[1],"away":r[2],"hg":int(r[3]),"ag":int(r[4])}
    return r
def _normalize_fixture(f):
    if "home" not in f and "h" in f: return {"home":f["h"],"away":f["a"],"date":f.get("date","")}
    return f

def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f: data = json.load(f)
            if data.get("version", 6) < 7:
                for key in ["serieB","serieCa","serieCb","serieCc"]:
                    if key in data:
                        league = data[key]
                        if "results" in league and league["results"] and isinstance(league["results"][0], list):
                            league["results"] = [_normalize_result(r) for r in league["results"]]
                        if "fixtures" in league and "next_fixtures" not in league:
                            old_fix = league.pop("fixtures", [])
                            league["next_fixtures"] = [{"date":f.get("date",""),"home":f.get("h",""),"away":f.get("a","")} for f in old_fix]
                        league.setdefault("results_by_giornata", {}); league.setdefault("next_giornata", 0); league.setdefault("next_fixtures", [])
                data["version"] = 7; save_data(data)
            return data
        except Exception as e: print(f"  ⚠️ Errore caricamento: {e}")
    return get_default_data()

def save_data(data):
    data["updatedAt"] = date.today().isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f: json.dump(data, f, ensure_ascii=False, indent=1)

# ============================================================
# POISSON MODEL — Dixon-Coles + Decadimento + Fattore Campo
# ============================================================
DECAY_XI = 0.005    # emivita ~139 giorni (ln2/0.005)
RHO_DEFAULT = -0.13 # fallback rho

def poisson_pmf(lam, k):
    if lam <= 0: return 1.0 if k == 0 else 0.0
    return (lam**k) * math.exp(-lam) / math.factorial(k)

# --- Strato 1: Statistiche con decadimento temporale ---
def calc_stats(results):
    """Statistiche pesate: partite recenti contano di più (e^{-ξ·giorni})."""
    today = date.today(); stats = {}
    for r in results:
        r = _normalize_result(r); home, away, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        try:
            days = max((today - date.fromisoformat(r["date"])).days, 0)
            w = math.exp(-DECAY_XI * days)
        except: w = 0.5
        for t in [home, away]:
            if t not in stats:
                stats[t] = {"hgf_w":0,"hga_w":0,"hw":0,"agf_w":0,"aga_w":0,"aw":0,
                            "hg":0,"ag":0,"home_gf":0,"home_ga":0,"away_gf":0,"away_ga":0}
        stats[home]["hgf_w"]+=hg*w; stats[home]["hga_w"]+=ag*w; stats[home]["hw"]+=w; stats[home]["hg"]+=1
        stats[home]["home_gf"]+=hg; stats[home]["home_ga"]+=ag
        stats[away]["agf_w"]+=ag*w; stats[away]["aga_w"]+=hg*w; stats[away]["aw"]+=w; stats[away]["ag"]+=1
        stats[away]["away_gf"]+=ag; stats[away]["away_ga"]+=hg
    return stats

def league_avg(stats):
    tg = sum(t["hgf_w"] for t in stats.values()); tw = sum(t["hw"] for t in stats.values())
    return tg/tw if tw > 0 else 1.2

# --- Strato 2: Fattore campo per squadra ---
def calc_home_factors(stats, avg):
    """Rapporto gol casa/trasferta della squadra vs media lega, con shrinkage."""
    th = sum(t["home_gf"] for t in stats.values()); ta = sum(t["away_gf"] for t in stats.values())
    thm = sum(t["hg"] for t in stats.values()); tam = sum(t["ag"] for t in stats.values())
    lhr = (th/thm) / (ta/tam) if thm > 0 and tam > 0 and ta > 0 else 1.2
    factors = {}
    for team, s in stats.items():
        if s["hg"] >= 3 and s["ag"] >= 3:
            thr = s["home_gf"]/s["hg"]; tar = s["away_gf"]/s["ag"] if s["ag"] > 0 else ta/tam if tam > 0 else 1.0
            raw = (thr/tar)/lhr if tar > 0 and lhr > 0 else 1.0
            factors[team] = 0.7 * raw + 0.3  # shrinkage verso 1.0
        else: factors[team] = 1.0
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
    if len(results) < 30: return RHO_DEFAULT
    obs = {(0,0):0,(1,1):0}; exp = {(0,0):0.0,(1,1):0.0}; n = 0
    for r in results:
        hs = stats.get(r["home"]); as_ = stats.get(r["away"])
        if not hs or not as_ or hs["hw"]==0 or as_["aw"]==0: continue
        lh = (hs["hgf_w"]/hs["hw"])*(as_["aga_w"]/as_["aw"])/avg if avg>0 else 1.0
        la = (as_["agf_w"]/as_["aw"])*(hs["hga_w"]/hs["hw"])/avg if avg>0 else 1.0
        k = (min(r["hg"],2), min(r["ag"],2))
        if k in obs: obs[k] += 1; exp[k] += poisson_pmf(lh, k[0]) * poisson_pmf(la, k[1])
        n += 1
    if n < 30: return RHO_DEFAULT
    rhos = []
    for k in [(0,0),(1,1)]:
        if exp[k] > 0:
            rhos.append(-(obs[k]/(exp[k]*n) - 1) * 0.3)
    rho = sum(rhos)/len(rhos) if rhos else RHO_DEFAULT
    return round(max(-0.25, min(0.05, rho)), 4)

def dc_prob_matrix(lam_h, lam_a, rho, mg=8):
    """Matrice probabilità congiunta con correzione Dixon-Coles."""
    return [[max(poisson_pmf(lam_h,i)*poisson_pmf(lam_a,j)*dc_tau(i,j,lam_h,lam_a,rho),0) for j in range(mg+1)] for i in range(mg+1)]

def prob_home_over_05(lam_h, lam_a, rho):
    p = dc_prob_matrix(lam_h, lam_a, rho)
    return min(max(1 - sum(p[0][j] for j in range(len(p[0]))), 0.01), 0.99)

def prob_away_under_15(lam_h, lam_a, rho):
    p = dc_prob_matrix(lam_h, lam_a, rho)
    return min(max(sum(p[i][0]+p[i][1] for i in range(len(p))), 0.01), 0.99)

# --- Filtro risultati (invariato) ---
def filter_results(results, range_type, custom_n=10):
    results = [_normalize_result(r) for r in results]
    if range_type == "all": return results
    now = date.today()
    if range_type == "2026": return [r for r in results if r["date"] >= "2026-01-01"]
    if range_type == "last30d": return [r for r in results if r["date"] >= (now - timedelta(days=30)).isoformat()]
    if range_type == "last60d": return [r for r in results if r["date"] >= (now - timedelta(days=60)).isoformat()]
    if range_type.startswith("last") or range_type == "custom":
        n = custom_n if range_type == "custom" else int(range_type.replace("last",""))
        sorted_r = sorted(results, key=lambda x: x["date"], reverse=True); tc={}; kept=[]
        for r in sorted_r:
            h,a = r["home"],r["away"]; ch=tc.get(h,0); ca=tc.get(a,0)
            if ch<n or ca<n:
                kept.append(r)
                if ch<n: tc[h]=ch+1
                if ca<n: tc[a]=ca+1
        return kept
    return results

# --- Predizioni complete ---
def predict_serie_b(results, fixtures, range_type="all", custom_n=10):
    filtered = filter_results(results, range_type, custom_n)
    stats = calc_stats(filtered); avg = league_avg(stats)
    hf = calc_home_factors(stats, avg); rho = estimate_rho(filtered, stats, avg)
    preds = []
    for fix in fixtures:
        fix = _normalize_fixture(fix); h,a = fix["home"],fix["away"]
        hs = stats.get(h); as_ = stats.get(a)
        if not hs or not as_: continue
        hA = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
        aD = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
        aA = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
        hD = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
        home_f = hf.get(h, 1.0)
        lam_h = (hA*aD)/avg * home_f; lam_a = (aA*hD)/avg / home_f
        prob = prob_home_over_05(lam_h, lam_a, rho)
        preds.append({"h":h,"a":a,"prob":round(prob,4),"lam":round(lam_h,2),"lam_a":round(lam_a,2),
            "hA":round(hA,2),"aD":round(aD,2),"hG":hs["hg"],"aG":as_["ag"],
            "hf":round(home_f,2),"rho":round(rho,3),"date":fix.get("date","")})
    preds.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions":preds, "total":len(filtered), "avg":round(avg,2), "rho":round(rho,3)}

def predict_serie_c(results, fixtures, range_type="all", custom_n=10):
    filtered = filter_results(results, range_type, custom_n)
    stats = calc_stats(filtered); avg = league_avg(stats)
    hf = calc_home_factors(stats, avg); rho = estimate_rho(filtered, stats, avg)
    preds = []
    for fix in fixtures:
        fix = _normalize_fixture(fix); h,a = fix["home"],fix["away"]
        hs = stats.get(h); as_ = stats.get(a)
        if not hs or not as_: continue
        hA = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
        aD = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
        aA = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
        hD = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
        home_f = hf.get(h, 1.0)
        lam_h = (hA*aD)/avg * home_f; lam_a = (aA*hD)/avg / home_f
        prob = prob_away_under_15(lam_h, lam_a, rho)
        preds.append({"h":h,"a":a,"prob":round(prob,4),"lam":round(lam_a,2),"lam_h":round(lam_h,2),
            "aA":round(aA,2),"hD":round(hD,2),"hG":hs["hg"],"aG":as_["ag"],
            "hf":round(home_f,2),"rho":round(rho,3),"date":fix.get("date","")})
    preds.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions":preds, "total":len(filtered), "avg":round(avg,2), "rho":round(rho,3)}

def calc_standings(results):
    st = {}
    for r in results:
        r = _normalize_result(r); h,a,hg,ag = r["home"],r["away"],r["hg"],r["ag"]
        for t in [h,a]:
            if t not in st: st[t] = {"g":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"pts":0}
        st[h]["g"]+=1; st[a]["g"]+=1; st[h]["gf"]+=hg; st[h]["ga"]+=ag; st[a]["gf"]+=ag; st[a]["ga"]+=hg
        if hg>ag: st[h]["w"]+=1; st[h]["pts"]+=3; st[a]["l"]+=1
        elif hg<ag: st[a]["w"]+=1; st[a]["pts"]+=3; st[h]["l"]+=1
        else: st[h]["d"]+=1; st[a]["d"]+=1; st[h]["pts"]+=1; st[a]["pts"]+=1
    return sorted(st.items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"]-x[1]["ga"])))

# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__)

@app.route("/")
def index(): return render_template_string(HTML_TEMPLATE)

@app.route("/api/data")
def api_data(): return jsonify(load_data())

@app.route("/api/predict")
def api_predict():
    data = load_data(); rt = request.args.get("range","all"); cn = int(request.args.get("customN",10))
    rB = predict_serie_b(data["serieB"].get("results",[]), data["serieB"].get("next_fixtures",[]), rt, cn)
    rB["next_giornata"] = data["serieB"].get("next_giornata",0)
    allC = {}
    for key, gir in [("serieCa","A"),("serieCb","B"),("serieCc","C")]:
        res = predict_serie_c(data[key].get("results",[]), data[key].get("next_fixtures",[]), rt, cn)
        res["girone"]=gir; res["next_giornata"]=data[key].get("next_giornata",0)
        for p in res["predictions"]: p["gir"]=gir
        allC[gir]=res
    return jsonify({"serieB":rB, "serieC":allC})

@app.route("/api/standings")
def api_standings():
    data = load_data()
    return jsonify({k: calc_standings(data[k].get("results",[])) for k in ["serieB","serieCa","serieCb","serieCc"]})

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    data = load_data()
    if data.get("version") != 7: data = get_default_data()
    errors = []; log = []
    print("📥 Scarico Serie B da Wikipedia...")
    html_b = fetch_page(WIKI_SERIE_B)
    if html_b is None: errors.append("Errore scaricamento Serie B")
    else:
        giornate_b = parse_wiki_serie_b(html_b)
        nr, ng, nxg, nf = process_giornate(giornate_b, "serieB", data)
        if nr > 0: log.append(f"Serie B: {nr} risultati in {ng} giornate → prossima: {nxg}ª ({nf} partite)")
        else: log.append("Serie B: ⚠️ nessun risultato"); errors.append("Nessun risultato Serie B")
    print("📥 Scarico Serie C da Wikipedia...")
    html_c = fetch_page(WIKI_SERIE_C)
    if html_c is None: errors.append("Errore scaricamento Serie C")
    else:
        soup_c = BeautifulSoup(html_c, 'html.parser')
        for gl, dk in [("A","serieCa"),("B","serieCb"),("C","serieCc")]:
            print(f"  📋 Parsing Girone {gl}...")
            giornate = parse_wiki_serie_c_girone(soup_c, gl)
            if not giornate:
                log.append(f"Serie C Gir.{gl}: ⚠️ sezione non trovata"); errors.append(f"Girone {gl} non trovato"); continue
            nr, ng, nxg, nf = process_giornate(giornate, dk, data)
            if nr > 0: log.append(f"Serie C Gir.{gl}: {nr} ris. in {ng} giornate → prossima: {nxg}ª ({nf} partite)")
            else: log.append(f"Serie C Gir.{gl}: ⚠️ nessun risultato")
    save_data(data)
    counts = {k: len(data[k].get("results",[])) for k in ["serieB","serieCa","serieCb","serieCc"]}
    total = sum(counts.values())
    msg = f"⚠️ {'; '.join(errors)}" if errors else f"✅ {total} risultati scaricati da Wikipedia"
    return jsonify({"success":len(errors)==0, "total":total, "counts":counts,
        "next_giornata":{k:data[k].get("next_giornata",0) for k in ["serieB","serieCa","serieCb","serieCc"]},
        "errors":errors, "log":log, "message":msg})

@app.route("/api/export")
def api_export(): return jsonify(load_data())
@app.route("/api/import", methods=["POST"])
def api_import():
    data = request.json
    if data and "serieB" in data: save_data(data); return jsonify({"success":True})
    return jsonify({"success":False, "error":"Formato non valido"})
@app.route("/api/reset", methods=["POST"])
def api_reset(): save_data(get_default_data()); return jsonify({"success":True})
@app.route("/api/status")
def api_status():
    data = load_data(); counts = {k: len(data[k].get("results",[])) for k in ["serieB","serieCa","serieCb","serieCc"]}
    return jsonify({"updatedAt":data.get("updatedAt","N/A"), "total":sum(counts.values()), "counts":counts,
        "next_giornata":{k:data[k].get("next_giornata",0) for k in ["serieB","serieCa","serieCb","serieCc"]},
        "next_fixtures":{k:len(data[k].get("next_fixtures",[])) for k in ["serieB","serieCa","serieCb","serieCc"]}})

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
:root{--bg:#0a0e17;--card:#111827;--card2:#1a2235;--accent:#22d3ee;--accent2:#a78bfa;--green:#34d399;--red:#f87171;--orange:#fbbf24;--text:#e2e8f0;--text2:#94a3b8;--border:#1e293b;--glow:0 0 20px rgba(34,211,238,0.15)}
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
.custom-n{display:none;align-items:center;gap:8px;margin-top:8px}.custom-n.show{display:flex}
.custom-n input{width:55px;padding:5px 8px;border-radius:6px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-size:0.82rem;text-align:center}
.custom-n label{font-size:0.75rem;color:var(--text2)}
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;margin:14px 0}
.match-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;transition:all .3s;position:relative;overflow:hidden}
.match-card:hover{border-color:var(--accent);box-shadow:var(--glow);transform:translateY(-2px)}
.match-card .rank{position:absolute;top:10px;right:12px;font-family:'Space Mono',monospace;font-size:0.62rem;color:var(--text2);background:var(--card2);padding:2px 7px;border-radius:6px}
.match-card .teams{font-size:0.95rem;font-weight:600;margin-bottom:8px;line-height:1.4}
.match-card .vs{color:var(--text2);font-weight:400;font-size:0.78rem;margin:0 4px}
.match-card .match-date{font-size:0.68rem;color:var(--text2);font-family:'Space Mono',monospace;margin-bottom:6px}
.prob-container{margin:8px 0}.prob-label{display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:3px}.prob-label .type{color:var(--text2)}.prob-label .pct{font-family:'Space Mono',monospace;font-weight:700}
.prob-bar{height:7px;background:var(--card2);border-radius:4px;overflow:hidden}.prob-fill{height:100%;border-radius:4px;transition:width .6s ease}.prob-fill.high{background:linear-gradient(90deg,var(--green),#10b981)}.prob-fill.mid{background:linear-gradient(90deg,var(--orange),#f59e0b)}.prob-fill.low{background:linear-gradient(90deg,var(--red),#ef4444)}
.detail-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:0.7rem}.detail-stat{background:var(--card2);border-radius:6px;padding:5px 9px}.detail-stat .dl{color:var(--text2);font-size:0.62rem;text-transform:uppercase;letter-spacing:0.5px}.detail-stat .dv{font-family:'Space Mono',monospace;font-weight:700;color:var(--text);margin-top:1px}
.stitle{font-family:'Space Mono',monospace;font-size:1.05rem;font-weight:700;margin:24px 0 6px;display:flex;align-items:center;gap:10px}.stitle .dot{width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ssub{font-size:0.75rem;color:var(--text2);margin-bottom:12px}
.giornata-badge{display:inline-block;background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border:1px solid var(--accent);border-radius:8px;padding:4px 12px;font-family:'Space Mono',monospace;font-size:0.78rem;color:var(--accent);margin-bottom:8px}
.btn{padding:8px 18px;border-radius:8px;border:none;font-weight:600;font-size:0.78rem;cursor:pointer;transition:all .2s}
.btn-u{background:linear-gradient(135deg,rgba(34,211,238,0.2),rgba(167,139,250,0.2));color:var(--accent);border:1px solid var(--accent);font-size:0.82rem;padding:10px 22px}
.btn-g{background:rgba(52,211,153,0.15);color:var(--green);border:1px solid rgba(52,211,153,0.3)}.btn-s{background:var(--card2);color:var(--text2);border:1px solid var(--border)}.btn-d{background:rgba(248,113,113,0.15);color:var(--red);border:1px solid rgba(248,113,113,0.3)}
.btn:hover{opacity:0.85;transform:translateY(-1px)}.btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.dm-bar{display:flex;gap:8px;justify-content:center;margin:8px 0;flex-wrap:wrap}.dm-bar .btn{font-size:0.72rem;padding:6px 14px}
.st{width:100%;border-collapse:collapse;font-size:0.75rem;margin:10px 0;table-layout:fixed}.st col.c-rank{width:36px}.st col.c-team{width:auto}.st col.c-num{width:42px}.st col.c-pts{width:42px}.st th{padding:7px 8px;text-align:center;border-bottom:1px solid var(--border);color:var(--accent);font-family:'Space Mono',monospace;font-size:0.68rem;text-transform:uppercase}.st td{padding:6px 8px;text-align:center;border-bottom:1px solid var(--border);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.st td:nth-child(2){text-align:left;font-weight:600}.st tr:hover{background:rgba(34,211,238,0.04)}
.empty{text-align:center;padding:36px;color:var(--text2);font-size:0.82rem}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:12px 24px;font-size:0.82rem;color:var(--accent);box-shadow:0 8px 30px rgba(0,0,0,0.4);transition:transform .3s ease;z-index:999;pointer-events:none}.toast.show{transform:translateX(-50%) translateY(0)}
.scrape-log{background:var(--card2);border-radius:8px;padding:10px;margin-top:8px;font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--text2);max-height:250px;overflow-y:auto;display:none}.scrape-log.show{display:block}
.section-sep{border:none;border-top:1px solid var(--border);margin:20px 0}
@media(max-width:600px){.container{padding:10px}.cards{grid-template-columns:1fr}.range-grid{gap:4px}.range-btn{padding:5px 9px;font-size:0.7rem}.match-card{padding:12px}.dm-bar{flex-direction:column;align-items:stretch}}
</style>
</head>
<body>
<div class="container">
<div class="header"><h1>⚽ Pronostici Serie B & C</h1><p>Dixon-Coles + Decadimento temporale + Fattore campo — v7 · Dati da Wikipedia</p></div>
<div class="data-status" id="dataStatus"><span class="status-dot empty"></span><span class="status-text">Caricamento...</span></div>
<div style="text-align:center;margin:14px 0"><button class="btn btn-u" onclick="doScrape()" id="scrapeBtn">🔄 Aggiorna da Wikipedia</button></div>
<div class="scrape-log" id="scrapeLog"></div>
<div class="dm-bar"><button class="btn btn-g" onclick="doExport()">📤 Esporta</button><button class="btn btn-s" onclick="document.getElementById('importFile').click()">📥 Importa</button><button class="btn btn-d" onclick="doReset()">🗑️ Reset</button><input type="file" id="importFile" accept=".json" style="display:none" onchange="doImport(event)"></div>
<div class="tabs"><div class="tab active" onclick="switchTab('predictions',this)">📊 Pronostici</div><div class="tab" onclick="switchTab('standings',this)">🏆 Classifiche</div></div>
<div class="range-panel"><h3>📐 Range Statistiche</h3><div class="range-grid"><button class="range-btn active" onclick="setRange('all',this)">Tutta la stagione</button><button class="range-btn" onclick="setRange('last5',this)">Ultime 5</button><button class="range-btn" onclick="setRange('last8',this)">Ultime 8</button><button class="range-btn" onclick="setRange('last10',this)">Ultime 10</button><button class="range-btn" onclick="setRange('last15',this)">Ultime 15</button><button class="range-btn" onclick="setRange('2026',this)">Solo 2026</button><button class="range-btn" onclick="setRange('last30d',this)">Ultimi 30gg</button><button class="range-btn" onclick="setRange('last60d',this)">Ultimi 60gg</button><button class="range-btn" onclick="setRange('custom',this)">Personalizzato</button></div><div class="custom-n" id="customN"><label>Ultime</label><input type="number" id="customNval" value="12" min="3" max="38" onchange="recalc()"><label>partite per squadra</label></div></div>
<div id="tab_predictions">
<div class="stitle"><span class="dot"></span>Serie B — CASA OVER 0.5</div><div id="giornataB"></div><div class="ssub" id="statsB"></div><div class="cards" id="cardsB"></div>
<hr class="section-sep">
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone A — OSPITE UNDER 1.5</div><div id="giornataCa"></div><div class="ssub" id="statsCa"></div><div class="cards" id="cardsCa"></div>
<hr class="section-sep">
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone B — OSPITE UNDER 1.5</div><div id="giornataCb"></div><div class="ssub" id="statsCb"></div><div class="cards" id="cardsCb"></div>
<hr class="section-sep">
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone C — OSPITE UNDER 1.5</div><div id="giornataCc"></div><div class="ssub" id="statsCc"></div><div class="cards" id="cardsCc"></div>
</div>
<div id="tab_standings" style="display:none"><div class="stitle"><span class="dot"></span>Classifica Serie B</div><div style="overflow-x:auto"><table class="st" id="tableB"></table></div><div id="tablesC"></div></div>
</div>
<div class="toast" id="toast"></div>
<script>
let curRange='all';
function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)}
function pc(p){return p>=0.75?'high':p>=0.55?'mid':'low'}
function pcol(p){return p>=0.75?'#34d399':p>=0.55?'#fbbf24':'#f87171'}
async function updateStatus(){const r=await fetch('/api/status');const d=await r.json();const dot=d.total>0?'ok':'empty';const el=document.getElementById('dataStatus');const ng=d.next_giornata||{};el.innerHTML=`<span class="status-dot ${dot}"></span><span class="status-text">📊 <b>${d.total}</b> risultati · Agg: <b>${d.updatedAt}</b> · Prossime: B=${ng.serieB||'?'}ª CA=${ng.serieCa||'?'}ª CB=${ng.serieCb||'?'}ª CC=${ng.serieCc||'?'}ª</span>`}
async function doScrape(){const btn=document.getElementById('scrapeBtn');const log=document.getElementById('scrapeLog');btn.disabled=true;btn.textContent='⏳ Scaricamento da Wikipedia...';log.classList.add('show');log.innerHTML='Connessione a it.wikipedia.org...<br>';const dot=document.querySelector('.status-dot');if(dot)dot.className='status-dot loading';try{const r=await fetch('/api/scrape',{method:'POST'});const d=await r.json();log.innerHTML+=d.message+'<br>';if(d.log)log.innerHTML+=d.log.map(l=>'  '+l).join('<br>')+'<br>';if(d.errors&&d.errors.length)log.innerHTML+='<br>Errori: '+d.errors.join(', ')+'<br>';toast(d.message);await updateStatus();recalc()}catch(e){log.innerHTML+='❌ '+e.message+'<br>';toast('❌ Errore')}btn.disabled=false;btn.textContent='🔄 Aggiorna da Wikipedia';setTimeout(()=>log.classList.remove('show'),8000)}
function fmtDate(d){if(!d)return'';const[y,m,dd]=d.split('-');const days=['Dom','Lun','Mar','Mer','Gio','Ven','Sab'];const dt=new Date(y,m-1,dd);return days[dt.getDay()]+' '+dd+'/'+m+'/'+y}
function crdB(i,p){const dt=p.date?`<div class="match-date">📅 ${fmtDate(p.date)}</div>`:'';return`<div class="match-card"><div class="rank">#${i+1}</div>${dt}<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div><div class="prob-container"><div class="prob-label"><span class="type">CASA OVER 0.5</span><span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div><div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div><div class="detail-stats"><div class="detail-stat"><div class="dl">λ Casa / Osp</div><div class="dv">${p.lam} / ${p.lam_a||'—'}</div></div><div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.hA} / ${p.aD}</div></div><div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div><div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div></div></div>`}
function crdC(i,p){const dt=p.date?`<div class="match-date">📅 ${fmtDate(p.date)}</div>`:'';return`<div class="match-card"><div class="rank">#${i+1}</div>${dt}<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div><div class="prob-container"><div class="prob-label"><span class="type">OSPITE UNDER 1.5</span><span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div><div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div><div class="detail-stats"><div class="detail-stat"><div class="dl">λ Osp / Casa</div><div class="dv">${p.lam} / ${p.lam_h||'—'}</div></div><div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.aA} / ${p.hD}</div></div><div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div><div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div></div></div>`}
async function recalc(){const cn=document.getElementById('customNval');const n=cn?cn.value:10;const r=await fetch(`/api/predict?range=${curRange}&customN=${n}`);const d=await r.json();const bG=d.serieB.next_giornata;document.getElementById('giornataB').innerHTML=bG?`<span class="giornata-badge">${bG}ª Giornata</span>`:'';document.getElementById('statsB').innerHTML=`📊 <b>${d.serieB.total}</b> partite · Media gol: <b>${d.serieB.avg}</b>`;document.getElementById('cardsB').innerHTML=d.serieB.predictions.length?d.serieB.predictions.map((p,i)=>crdB(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄</div>';['A','B','C'].forEach(gir=>{const s=gir.toLowerCase();const g=d.serieC[gir];if(!g)return;const cG=g.next_giornata;document.getElementById('giornataC'+s).innerHTML=cG?`<span class="giornata-badge">${cG}ª Giornata</span>`:'';document.getElementById('statsC'+s).innerHTML=`📊 <b>${g.total}</b> partite · Media gol: <b>${g.avg}</b>`;document.getElementById('cardsC'+s).innerHTML=g.predictions.length?g.predictions.map((p,i)=>crdC(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄</div>'})}
async function renderStandings(){const r=await fetch('/api/standings');const d=await r.json();const cg='<colgroup><col class="c-rank"><col class="c-team"><col class="c-num"><col class="c-num"><col class="c-num"><col class="c-num"><col class="c-num"><col class="c-num"><col class="c-pts"></colgroup>';function tbl(rows){if(!rows.length)return cg+'<tbody><tr><td colspan="9" style="text-align:center;color:var(--text2)">Nessun dato</td></tr></tbody>';return cg+`<thead><tr><th>#</th><th>Squadra</th><th>G</th><th>V</th><th>P</th><th>S</th><th>GF</th><th>GS</th><th>Pt</th></tr></thead><tbody>`+rows.map(([n,s],i)=>`<tr><td>${i+1}</td><td>${n}</td><td>${s.g}</td><td>${s.w}</td><td>${s.d}</td><td>${s.l}</td><td>${s.gf}</td><td>${s.ga}</td><td><b>${s.pts}</b></td></tr>`).join('')+'</tbody>'}document.getElementById('tableB').innerHTML=tbl(d.serieB);let h='';[{k:'serieCa',n:'A'},{k:'serieCb',n:'B'},{k:'serieCc',n:'C'}].forEach(g=>{h+=`<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C — Girone ${g.n}</div><div style="overflow-x:auto"><table class="st">${tbl(d[g.k])}</table></div>`});document.getElementById('tablesC').innerHTML=h}
async function doExport(){const r=await fetch('/api/export');const d=await r.json();const b=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download=`pronostici_v7_${d.updatedAt||'export'}.json`;a.click();URL.revokeObjectURL(u);toast('📤 Esportato!')}
async function doImport(ev){const f=ev.target.files[0];if(!f)return;const reader=new FileReader();reader.onload=async function(e){try{const d=JSON.parse(e.target.result);const r=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});const res=await r.json();if(res.success){updateStatus();recalc();toast('📥 Importato!')}else toast('⚠️ '+res.error)}catch(err){toast('⚠️ Errore')}};reader.readAsText(f);ev.target.value=''}
async function doReset(){if(!confirm('Cancellare tutti i dati?'))return;await fetch('/api/reset',{method:'POST'});updateStatus();recalc();toast('🗑️ Reset!')}
function setRange(r,el){curRange=r;document.querySelectorAll('.range-btn').forEach(b=>b.classList.remove('active'));el.classList.add('active');document.getElementById('customN').classList.toggle('show',r==='custom');recalc()}
function switchTab(tab,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));el.classList.add('active');['predictions','standings'].forEach(t=>{document.getElementById('tab_'+t).style.display=t===tab?'':'none'});if(tab==='standings')renderStandings()}
updateStatus();recalc();
</script>
</body>
</html>"""

if __name__ == "__main__":
    import threading
    if not DATA_FILE.exists():
        save_data(get_default_data()); print("📂 Primo avvio — premi 🔄 per scaricare i dati da Wikipedia")
    else:
        d = load_data(); total = sum(len(d[k].get("results",[])) for k in ["serieB","serieCa","serieCb","serieCc"]); print(f"📂 Dati caricati: {total} risultati")
    def run_server():
        import logging; logging.getLogger('werkzeug').setLevel(logging.ERROR); app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)
    server = threading.Thread(target=run_server, daemon=True); server.start()
    try:
        import webview; print("⚽ Avvio finestra desktop..."); webview.create_window("⚽ Pronostici Serie B & C — v7", f"http://127.0.0.1:{PORT}", width=1100, height=800, min_size=(600,500)); webview.start()
    except ImportError:
        print(f"\n⚠️  pywebview non trovato → pip install pywebview"); print(f"    Oppure apri: http://localhost:{PORT}\n")
        import webbrowser; webbrowser.open(f"http://localhost:{PORT}")
        try: server.join()
        except KeyboardInterrupt: pass
