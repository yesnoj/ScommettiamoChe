#!/usr/bin/env python3
"""
⚽ Pronostici Serie B & C — v11 Python/Flask
Modello Dixon-Coles + Decadimento temporale + Fattore campo
Miglioramenti v10:
  • Decay più rapido (emivita 70gg invece di 139gg)
  • Shrinkage adattivo fattore campo (proporzionale ai dati disponibili)
  • Prior stagionale squadre con pochi dati (< 8 partite)
  • Calibrazione isotonica (temperature scaling, T configurabile)

Fonti dati selezionabili:
  • calciomagazine.net  (parser testo)
  • Wikipedia it.       (parser HTML/BeautifulSoup)
"""

import json, re, os, math, sys
from datetime import datetime, date, timedelta
from pathlib import Path

# ── Fix encoding Windows per exe PyInstaller (--noconsole) ──────────────────
# Con --noconsole sys.stdout/stderr sono None e Windows usa cp1252 di default.
# Redireziona su file di log UTF-8 per evitare crash su emoji/caratteri speciali.
if getattr(sys, 'frozen', False):
    _base = Path(sys.executable).parent
    _log  = open(_base / 'pronostici.log', 'w', encoding='utf-8', buffering=1)
    sys.stdout = _log
    sys.stderr = _log

# ── Dipendenze ────────────────────────────────────────────────
try:
    from flask import Flask, jsonify, request, render_template_string
except ImportError:
    print("Installo Flask...")
    os.system(f"{sys.executable} -m pip install flask requests beautifulsoup4")
    from flask import Flask, jsonify, request, render_template_string

try:
    import requests as req
except ImportError:
    os.system(f"{sys.executable} -m pip install requests")
    import requests as req

try:
    from bs4 import BeautifulSoup
except ImportError:
    os.system(f"{sys.executable} -m pip install beautifulsoup4")
    from bs4 import BeautifulSoup

# ============================================================
# CONFIG
# ============================================================
def get_base_dir():
    """Ritorna la cartella base corretta sia in sviluppo che dentro un exe PyInstaller."""
    if getattr(sys, 'frozen', False):  # siamo dentro un exe compilato
        return Path(sys.executable).parent
    return Path(__file__).parent

APP_DIR   = get_base_dir()
DATA_FILE = APP_DIR / "pronostici_data.json"
PORT      = 5050

# ── Fonti calciomagazine.net ──────────────────────────────────
CM_LEAGUES = {
    "serieB": {
        "name": "Serie B",
        "results_url":  "https://www.calciomagazine.net/risultati-serie-b-120385.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-b-99638.html",
    },
    "serieCa": {
        "name": "Serie C Girone A",
        "results_url":      "https://www.calciomagazine.net/risultati-serie-c-girone-a-120404.html",
        "calendar_url":     "https://www.calciomagazine.net/calendario-serie-c-girone-a-99207.html",
        "calendar_url_alt": "https://www.calciomagazine.net/calendario-serie-c-girone-a-99200.html",
    },
    "serieCb": {
        "name": "Serie C Girone B",
        "results_url":  "https://www.calciomagazine.net/risultati-serie-c-girone-b-120417.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-c-girone-b-99208.html",
    },
    "serieCc": {
        "name": "Serie C Girone C",
        "results_url":  "https://www.calciomagazine.net/risultati-serie-c-girone-c-120418.html",
        "calendar_url": "https://www.calciomagazine.net/calendario-serie-c-girone-c-99209.html",
    },
}

# ── Fonti Wikipedia ───────────────────────────────────────────
WIKI_SERIE_B = "https://it.wikipedia.org/wiki/Serie_B_2025-2026"
WIKI_SERIE_C = "https://it.wikipedia.org/wiki/Serie_C_2025-2026"

MESI_IT = {
    "gen":1,"feb":2,"mar":3,"apr":4,"mag":5,"giu":6,
    "lug":7,"ago":8,"set":9,"ott":10,"nov":11,"dic":12,
}


# ============================================================
# RETE — fetch comune
# ============================================================
def fetch_page(url):
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "it-IT,it;q=0.9",
    }
    try:
        r = req.get(url, headers=headers, timeout=30, verify=False)
        r.raise_for_status()
        r.encoding = "utf-8"
        return r.text
    except Exception as e:
        print(f"  ❌ Errore fetch {url}: {e}")
        return None


# ============================================================
# PARSER — calciomagazine.net
# ============================================================
def _extract_text(html):
    if not html:
        return ""
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(?:p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    import html as html_mod
    return html_mod.unescape(text).strip()


def _infer_year(month, day):
    """Stagione 2025-2026: lug-dic → 2025, gen-giu → 2026."""
    return 2025 if month >= 7 else 2026


def cm_parse_results(html):
    """Risultati da calciomagazine.net → {giornata: [result_dict]}"""
    text = _extract_text(html)
    giornate = {}
    current_g = 0
    g_re  = re.compile(r"(\d+)[ªa°]\s*Giornata", re.IGNORECASE)
    r_re  = re.compile(
        r"(\d{2})\.(\d{2})\.\s*ore\s*(\d{1,2}:\d{2})\s+"
        r"(.+?)\s*[-–]\s*(.+?)\s+(\d+)\s*:\s*(\d+)"
    )
    r_alt = re.compile(
        r"(\d{2})\.(\d{2})\.\s+(.+?)\s*[-–]\s*(.+?)\s+(\d+)\s*:\s*(\d+)"
    )
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        if "annullata" in line.lower() or "rinviata" in line.lower():
            continue
        gm = g_re.search(line)
        if gm:
            current_g = int(gm.group(1))
            giornate.setdefault(current_g, [])
            continue
        rm = r_re.search(line)
        if rm and current_g > 0:
            day_n, month = int(rm.group(1)), int(rm.group(2))
            time_str     = rm.group(3)
            home, away   = rm.group(4).strip(), rm.group(5).strip()
            hg, ag       = int(rm.group(6)), int(rm.group(7))
            year         = _infer_year(month, day_n)
            try:
                date_str = date(year, month, day_n).isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            giornate[current_g].append(
                {"date": date_str, "time": time_str, "home": home, "away": away, "hg": hg, "ag": ag}
            )
            continue
        rm = r_alt.search(line)
        if rm and current_g > 0:
            day_n, month = int(rm.group(1)), int(rm.group(2))
            home, away   = rm.group(3).strip(), rm.group(4).strip()
            hg, ag       = int(rm.group(5)), int(rm.group(6))
            year         = _infer_year(month, day_n)
            try:
                date_str = date(year, month, day_n).isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            giornate[current_g].append(
                {"date": date_str, "home": home, "away": away, "hg": hg, "ag": ag}
            )
    return giornate


def cm_parse_calendar(html):
    """Calendario da calciomagazine.net → {giornata: [fixture_dict]}"""
    text = _extract_text(html)
    giornate = {}
    current_g = 0
    g_re = re.compile(r"(\d+)[ªa°]\s*Giornata", re.IGNORECASE)
    f_re = re.compile(
        r"(?:luned[ìi]|marted[ìi]|mercoled[ìi]|gioved[ìi]|venerd[ìi]|sabato|domenica)\s+"
        r"(\d{2})\.(\d{2})\.(\d{2,4})\s+ore\s*(\d{1,2}:\d{2})\s+"
        r"(.+?)\s*[-–]\s*(.+?)(?:\s*$)",
        re.IGNORECASE,
    )
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            continue
        gm = g_re.search(line)
        if gm:
            current_g = int(gm.group(1))
            giornate.setdefault(current_g, [])
        fm = f_re.search(line)
        if fm and current_g > 0:
            day_n, month = int(fm.group(1)), int(fm.group(2))
            year_raw = int(fm.group(3))
            year     = year_raw + 2000 if year_raw < 100 else year_raw
            time_str = fm.group(4)
            home     = fm.group(5).strip()
            away     = re.sub(r"\s*\*+$", "", fm.group(6)).strip()
            try:
                date_str = date(year, month, day_n).isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            giornate[current_g].append({"date": date_str, "time": time_str, "home": home, "away": away})
    return giornate


def _fixtures_are_future(fixtures):
    """Ritorna True se almeno un fixture ha data >= oggi (o data sconosciuta)."""
    today = date.today().isoformat()
    for f in fixtures:
        d = f.get("date", "")
        if not d or d >= today:
            return True
    return False

def _filter_future_fixtures(fixtures):
    """Ritorna solo i fixture con data >= oggi (o senza data)."""
    today = date.today().isoformat()
    return [f for f in fixtures if not f.get("date") or f["date"] >= today]

def cm_find_next_giornata(results_by_g, calendar_by_g):
    if calendar_by_g:
        all_g = sorted(set(list(results_by_g) + list(calendar_by_g)))
        for g in all_g:
            cal = calendar_by_g.get(g, [])
            res = results_by_g.get(g, [])
            # Giornata incompleta E con almeno un fixture futuro
            if len(res) < len(cal) and _fixtures_are_future(cal):
                return g
        return (max(all_g) + 1) if all_g else 1
    if results_by_g:
        played = sorted(results_by_g)
        for g in range(1, 39):
            if g not in results_by_g or not results_by_g[g]:
                return g
        return played[-1] + 1
    return 1


def scrape_calciomagazine(data):
    """Scarica tutti i campionati da calciomagazine.net e aggiorna data."""
    errors, log = [], []
    custom_urls = data.get("urls", {}).get("calciomagazine", {})
    for key, cfg in CM_LEAGUES.items():
        name = cfg["name"]
        cu = custom_urls.get(key, {})
        res_url = cu.get("results_url", cfg["results_url"])
        cal_url = cu.get("calendar_url", cfg["calendar_url"])
        cal_alt = cu.get("calendar_url_alt", cfg.get("calendar_url_alt"))
        # Risultati
        html_r = fetch_page(res_url)
        if html_r is None:
            errors.append(f"Errore risultati {name}")
        else:
            rbg = cm_parse_results(html_r)
            all_r = [m for g in rbg.values() for m in g]
            if all_r:
                data[key]["results_by_giornata"] = {str(k): v for k, v in rbg.items()}
                data[key]["results"] = all_r
                log.append(f"{name}: {len(all_r)} risultati in {len(rbg)} giornate")
            else:
                prev = len(data[key].get("results", []))
                log.append(f"{name}: ⚠️ nessun risultato (mantengo {prev} precedenti)")
                if prev == 0:
                    errors.append(f"Nessun risultato {name}")
        # Calendario
        html_c = fetch_page(cal_url)
        if html_c is None and cal_alt:
            log.append(f"  → URL alternativo per {name}...")
            html_c = fetch_page(cal_alt)
        if html_c is None:
            errors.append(f"Errore calendario {name}")
        else:
            cbg = cm_parse_calendar(html_c)
            data[key]["calendar_by_giornata"] = {str(k): v for k, v in cbg.items()}
            rbg_int = {int(k): v for k, v in data[key].get("results_by_giornata", {}).items()}
            next_g  = cm_find_next_giornata(rbg_int, cbg)
            data[key]["next_giornata"] = next_g
            data[key]["next_fixtures"] = _filter_future_fixtures(cbg.get(next_g, []))
            log.append(f"  → Prossima: {next_g}ª ({len(data[key]['next_fixtures'])} partite)")
    return errors, log


# ============================================================
# PARSER — Wikipedia
# ============================================================
def _parse_italian_date(text):
    text = text.strip().rstrip(".")
    text = re.sub(r"(\d+)[ºª°]", r"\1", text)
    m = re.match(r"(\d{1,2})\s+(\w+)", text)
    if not m:
        return ""
    day, ms = int(m.group(1)), m.group(2).lower().rstrip(".")
    month   = next((v for k, v in MESI_IT.items() if ms.startswith(k)), 0)
    if not month:
        return ""
    year = 2025 if month >= 7 else 2026
    try:
        return date(year, month, day).isoformat()
    except Exception:
        return ""


def _is_score(t):
    return bool(re.match(r"^\d+\s*[-\u2013]\s*\d+$", t.strip()))


def _is_time_or_dash(t):
    t = t.strip()
    return t == "-" or bool(re.match(r"^\d{1,2}:\d{2}$", t))


def _parse_score(t):
    m = re.match(r"^(\d+)\s*[-\u2013]\s*(\d+)$", t.strip())
    return (int(m.group(1)), int(m.group(2))) if m else None


def _split_match_name(text):
    parts = re.split(r"[-\u2013\u2014]", text)
    if len(parts) < 2:
        return None, None
    if len(parts) == 2:
        return parts[0].strip().replace("*", ""), parts[1].strip().replace("*", "")
    return "-".join(parts[:-1]).strip().replace("*", ""), parts[-1].strip().replace("*", "")


def _is_date_text(text):
    return bool(re.match(r"^\d{1,2}[ºª°]?\s+\w{3}", text.strip()))


def wiki_parse_serie_b(html):
    """Restituisce {giornata: {'results':[], 'fixtures':[]}}"""
    soup = BeautifulSoup(html, "html.parser")
    giornate = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        current_g, current_date = 0, ""
        for row in rows:
            tds = row.find_all("td")
            if not tds or len(tds) > 10:
                continue
            if len(tds) == 3:
                mid = tds[1].get_text(strip=True)
                gm  = re.match(r"^(\d+)[\u00AAa\u00B0]\s*giornata$", mid, re.IGNORECASE)
                if gm:
                    current_g = int(gm.group(1))
                    giornate.setdefault(current_g, {"results": [], "fixtures": []})
                    current_date = ""
                    continue
            if current_g == 0:
                continue
            if len(tds) == 3:
                pd = _parse_italian_date(tds[0].get_text(strip=True))
                if pd:
                    current_date = pd
                match_text, score_text = tds[1].get_text(strip=True), tds[2].get_text(strip=True)
            elif len(tds) == 2:
                match_text, score_text = tds[0].get_text(strip=True), tds[1].get_text(strip=True)
            else:
                continue
            home, away = _split_match_name(match_text)
            if not home or not away:
                continue
            sc = _parse_score(score_text.strip())
            g_data = giornate[current_g]
            existing_r = {(r["home"], r["away"]) for r in g_data["results"]}
            existing_f = {(f["home"], f["away"]) for f in g_data["fixtures"]}
            if sc:
                if (home, away) not in existing_r:
                    g_data["results"].append({"date": current_date, "home": home, "away": away,
                                              "hg": sc[0], "ag": sc[1]})
            elif _is_time_or_dash(score_text.strip()):
                if (home, away) not in existing_f:
                    g_data["fixtures"].append({"date": current_date, "home": home, "away": away})
    return giornate


def wiki_parse_serie_c_girone(soup, girone_letter):
    """Restituisce {giornata: {'results':[], 'fixtures':[]}}"""
    girone_section = None
    for h2 in soup.find_all("h2"):
        if f"Girone {girone_letter}" in h2.get_text():
            girone_section = h2.parent.parent
            break
    if not girone_section:
        return {}

    all_tables = []
    for child in girone_section.children:
        if not hasattr(child, "name") or child.name != "section":
            continue
        heading = child.find(["h3", "h4"])
        if not heading:
            continue
        if "Calendario" in heading.get_text() or "Risultati" in heading.get_text():
            tbls = child.find_all("table")
            if len(tbls) >= 5:
                all_tables.extend(tbls)

    if not all_tables:
        return {}

    giornate    = {}
    seen_res    = set()
    seen_fix    = set()
    g_re        = re.compile(r"^(\d+)[ªa°]\s*giornata$", re.IGNORECASE)

    for table in all_tables:
        cur_g_a = cur_g_r = 0
        rs_a = rs_r = 0
        date_a = date_r = ""

        for row in table.find_all("tr"):
            tds = row.find_all("td")
            n   = len(tds)
            if n == 0 or n > 10:
                continue
            # Header giornata
            if n == 3:
                mid = tds[1].get_text(strip=True)
                gm  = g_re.match(mid)
                if gm:
                    cur_g_a = int(gm.group(1))
                    rit_m   = re.search(r"(\d+)[ªa°]", tds[2].get_text(strip=True))
                    cur_g_r = int(rit_m.group(1)) if rit_m else cur_g_a + 19
                    giornate.setdefault(cur_g_a, {"results": [], "fixtures": []})
                    giornate.setdefault(cur_g_r, {"results": [], "fixtures": []})
                    rs_a = rs_r = 0
                    continue
            if cur_g_a == 0:
                continue
            idx = 0
            # Data andata
            if rs_a <= 0:
                if idx < n and _is_date_text(tds[idx].get_text(strip=True)):
                    date_a = _parse_italian_date(tds[idx].get_text(strip=True))
                    rs_a   = int(tds[idx].get("rowspan", 1))
                    idx   += 1
                elif n < 3:
                    continue
            rem = n - idx
            if rem < 3:
                continue
            score_a     = tds[idx].get_text(strip=True); idx += 1
            teams_text  = tds[idx].get_text(strip=True); idx += 1
            score_r     = tds[idx].get_text(strip=True); idx += 1
            if rs_r <= 0 and idx < n:
                date_r = _parse_italian_date(tds[idx].get_text(strip=True))
                rs_r   = int(tds[idx].get("rowspan", 1))
            rs_a -= 1; rs_r -= 1
            home, away = _split_match_name(teams_text)
            if not home or not away:
                continue
            # Andata
            sc = _parse_score(score_a)
            if sc:
                key = (date_a, home, away)
                if key not in seen_res:
                    giornate[cur_g_a]["results"].append(
                        {"date": date_a, "home": home, "away": away, "hg": sc[0], "ag": sc[1]}
                    )
                    seen_res.add(key)
            elif _is_time_or_dash(score_a):
                key = (home, away, "a")
                if key not in seen_fix:
                    giornate[cur_g_a]["fixtures"].append({"date": date_a, "home": home, "away": away})
                    seen_fix.add(key)
            # Ritorno (casa/trasferta invertite)
            # Wikipedia mostra sempre lo score nell'ordine Squadra_A - Squadra_B dell'andata,
            # quindi sc[0] = gol di 'home' (che nel ritorno gioca in trasferta)
            #          sc[1] = gol di 'away' (che nel ritorno gioca in casa)
            # → hg e ag vanno invertiti rispetto a come appaiono nella colonna.
            sc = _parse_score(score_r)
            if sc:
                key = (date_r, away, home)
                if key not in seen_res:
                    giornate[cur_g_r]["results"].append(
                        {"date": date_r, "home": away, "away": home, "hg": sc[1], "ag": sc[0]}
                    )
                    seen_res.add(key)
            elif _is_time_or_dash(score_r):
                key = (away, home, "r")
                if key not in seen_fix:
                    giornate[cur_g_r]["fixtures"].append({"date": date_r, "home": away, "away": home})
                    seen_fix.add(key)
    return giornate


def wiki_find_next_giornata(giornate_data):
    today = date.today().isoformat()
    # Solo giornate con almeno un fixture futuro
    candidates = [
        (g, len([f for f in d.get("fixtures", []) if not f.get("date") or f["date"] >= today]))
        for g, d in giornate_data.items()
        if any(not f.get("date") or f["date"] >= today for f in d.get("fixtures", []))
    ]
    if not candidates:
        return max(giornate_data) + 1 if giornate_data else 1
    full = [(g, n) for g, n in candidates if n >= 5]
    if full:
        return min(full)[0]
    return min(candidates)[0]


def _process_wiki_giornate(giornate_data, key, data):
    all_r, rbg = [], {}
    for g_num, g_data in giornate_data.items():
        rbg[str(g_num)] = g_data["results"]
        all_r.extend(g_data["results"])
    if all_r:
        data[key]["results_by_giornata"] = rbg
        data[key]["results"] = all_r
        next_g = wiki_find_next_giornata(giornate_data)
        data[key]["next_giornata"] = next_g
        data[key]["next_fixtures"] = _filter_future_fixtures(
            giornate_data.get(next_g, {}).get("fixtures", [])
        )
        return len(all_r), len(giornate_data), next_g, len(data[key]["next_fixtures"])
    return 0, 0, 0, 0


def scrape_wikipedia(data):
    """Scarica tutti i campionati da Wikipedia e aggiorna data."""
    errors, log = [], []
    custom_urls = data.get("urls", {}).get("wikipedia", {})
    url_b = custom_urls.get("serieB", WIKI_SERIE_B)
    url_c = custom_urls.get("serieC", WIKI_SERIE_C)
    print("📥 Scarico Serie B da Wikipedia...")
    html_b = fetch_page(url_b)
    if html_b is None:
        errors.append("Errore download Serie B")
    else:
        gd = wiki_parse_serie_b(html_b)
        nr, ng, nxg, nf = _process_wiki_giornate(gd, "serieB", data)
        if nr > 0:
            log.append(f"Serie B: {nr} risultati in {ng} giornate → prossima: {nxg}ª ({nf} partite)")
        else:
            log.append("Serie B: ⚠️ nessun risultato")
            errors.append("Nessun risultato Serie B")

    print("📥 Scarico Serie C da Wikipedia...")
    html_c = fetch_page(url_c)
    if html_c is None:
        errors.append("Errore download Serie C")
    else:
        soup_c = BeautifulSoup(html_c, "html.parser")
        for gl, dk in [("A", "serieCa"), ("B", "serieCb"), ("C", "serieCc")]:
            print(f"  📋 Parsing Girone {gl}...")
            gd = wiki_parse_serie_c_girone(soup_c, gl)
            if not gd:
                log.append(f"Serie C Gir.{gl}: ⚠️ sezione non trovata")
                errors.append(f"Girone {gl} non trovato")
                continue
            nr, ng, nxg, nf = _process_wiki_giornate(gd, dk, data)
            if nr > 0:
                log.append(f"Serie C Gir.{gl}: {nr} risultati in {ng} giornate → prossima: {nxg}ª ({nf} partite)")
            else:
                log.append(f"Serie C Gir.{gl}: ⚠️ nessun risultato")
    return errors, log


_LEAGUE_KEYS = ["serieB", "serieCa", "serieCb", "serieCc"]

def get_default_data():
    return {
        "version":  10,
        "source":    "calciomagazine",
        "updatedAt": date.today().isoformat(),
        "urls": {},
        **{k: {
            "results_by_giornata": {},
            "calendar_by_giornata": {},
            "results": [],
            "next_giornata": 0,
            "next_fixtures": [],
        } for k in _LEAGUE_KEYS},
    }


def _normalize_result(r):
    if isinstance(r, list):
        return {"date": r[0], "home": r[1], "away": r[2], "hg": int(r[3]), "ag": int(r[4])}
    return r


def _normalize_fixture(f):
    if "home" not in f and "h" in f:
        return {"home": f["h"], "away": f["a"], "date": f.get("date", "")}
    return f


def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version", 0) < 10:
                print("  📦 Migrazione dati → v10...")
                for k in _LEAGUE_KEYS:
                    if k in data:
                        lg = data[k]
                        if lg.get("results") and isinstance(lg["results"][0], list):
                            lg["results"] = [_normalize_result(r) for r in lg["results"]]
                        if "fixtures" in lg and "next_fixtures" not in lg:
                            old = lg.pop("fixtures", [])
                            lg["next_fixtures"] = [
                                {"date": x.get("date", ""), "home": x.get("h", ""), "away": x.get("a", "")}
                                for x in old
                            ]
                        lg.setdefault("results_by_giornata", {})
                        lg.setdefault("calendar_by_giornata", {})
                        lg.setdefault("next_giornata", 0)
                        lg.setdefault("next_fixtures", [])
                data["version"] = 10
                data.setdefault("source", "calciomagazine")
                save_data(data)
            return data
        except Exception as e:
            print(f"  ⚠️ Errore caricamento: {e}")
    return get_default_data()


def save_data(data):
    data["updatedAt"] = date.today().isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)




# ============================================================
# MODELLO STATISTICO — NuovoMetodo (Media Pesata Forma Recente)
# ============================================================
# Formula:
#   NM = (Σ score_recent × w_recent + Σ score_prev × w_prev) / denominatore
#   denominatore = n_recent × max_score × w_recent + n_prev × max_score × w_prev
#   max_score = 1.0 + bonus
#
# Bonus: punteggio graduato per prestazioni oltre soglia
#   Over (Serie B):  0 gol→0, 1 gol→1.0, 2+ gol→1.0+bonus
#   Under (Serie C): 0 gol→1.0+bonus, 1 gol→1.0, 2+ gol→0

NM_N_RECENT  = 3
NM_N_PREV    = 3
NM_W_RECENT  = 1.3
NM_W_PREV    = 0.7
NM_BONUS     = 0.0    # default 0; UI default: Serie B=0.25, Serie C=0.15


def _weighted_pct(matches, score_fn, n_recent, n_prev, w_recent, w_prev, max_score=1.0):
    """Calcola NM su una lista di partite ordinate cronologicamente.
    score_fn = lambda (date, hg, ag) -> float (0 … max_score).
    max_score = punteggio massimo per partita (1.0 + bonus)."""
    n = len(matches)
    if n == 0:
        return None
    total_needed = n_recent + n_prev
    if n < n_recent:
        total = sum(score_fn(m) for m in matches)
        return total / (n * max_score) if max_score > 0 else None
    last = matches[-n_recent:]
    prev_start = max(0, len(matches) - total_needed)
    prev = matches[prev_start:-n_recent] if n_recent < len(matches) else []
    actual_prev = min(len(prev), n_prev)
    prev = prev[-actual_prev:] if actual_prev > 0 else []

    s_last = sum(score_fn(m) for m in last)
    s_prev = sum(score_fn(m) for m in prev)
    norm = max_score * (w_recent * len(last) + w_prev * len(prev))
    return (s_last * w_recent + s_prev * w_prev) / norm if norm > 0 else None


def compute_nm(results, n_recent=NM_N_RECENT, n_prev=NM_N_PREV,
               w_recent=NM_W_RECENT, w_prev=NM_W_PREV, bonus=NM_BONUS):
    """Calcola NuovoMetodo per ogni squadra con bonus graduato."""
    sorted_res = sorted((_normalize_result(r) for r in results), key=lambda x: x["date"])
    home_m = {}
    away_m = {}
    for r in sorted_res:
        h, a = r["home"], r["away"]
        home_m.setdefault(h, []).append((r["date"], r["hg"], r["ag"]))
        away_m.setdefault(a, []).append((r["date"], r["hg"], r["ag"]))

    b = bonus
    ms = 1.0 + b

    def sc_over(gol):
        if gol == 0:  return 0.0
        if gol == 1:  return 1.0
        return 1.0 + b

    def sc_under(gol):
        if gol == 0:  return 1.0 + b
        if gol == 1:  return 1.0
        return 0.0

    nm = {}
    all_teams = set(list(home_m.keys()) + list(away_m.keys()))
    for team in all_teams:
        hm = home_m.get(team, [])
        am = away_m.get(team, [])
        args = (n_recent, n_prev, w_recent, w_prev, ms)
        nm[team] = {
            "h_ov05":    _weighted_pct(hm, lambda m: sc_over(m[1]),  *args),
            "h_su_u15":  _weighted_pct(hm, lambda m: sc_under(m[2]), *args),
            "a_gf_u15":  _weighted_pct(am, lambda m: sc_under(m[2]), *args),
            "a_gs_ov05": _weighted_pct(am, lambda m: sc_over(m[1]),  *args),
            "h_count": len(hm),
            "a_count": len(am),
        }
    return nm


def calc_elo(results, k=20, start=1500):
    """Rating Elo — informativo, non influenza il modello."""
    sorted_r = sorted((_normalize_result(r) for r in results), key=lambda x: x["date"])
    elo = {}
    for r in sorted_r:
        h, a, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        elo.setdefault(h, start); elo.setdefault(a, start)
        score = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        exp_h = 1.0 / (1.0 + 10 ** ((elo[a] - elo[h]) / 400.0))
        delta = k * (score - exp_h)
        elo[h] = round(elo[h] + delta, 1)
        elo[a] = round(elo[a] - delta, 1)
    return elo


def elo_label(rating):
    if rating >= 1650: return "\u2605\u2605\u2605"
    if rating >= 1560: return "\u2605\u2605"
    if rating >= 1480: return "\u2605"
    if rating >= 1400: return "~"
    return "\u25bc"


def predict_serie_b(results, fixtures, n_recent=NM_N_RECENT, n_prev=NM_N_PREV,
                    w_recent=NM_W_RECENT, w_prev=NM_W_PREV, bonus=NM_BONUS):
    """Serie B: CASA OVER 0.5 — NuovoMetodo."""
    nm   = compute_nm(results, n_recent, n_prev, w_recent, w_prev, bonus)
    elo  = calc_elo(results)
    preds = []
    for fix in fixtures:
        fix = _normalize_fixture(fix)
        h, a = fix["home"], fix["away"]
        nh, na = nm.get(h), nm.get(a)
        if not nh or not na:
            continue
        p_h = nh.get("h_ov05")
        p_a = na.get("a_gs_ov05")
        prob = p_h * p_a if p_h is not None and p_a is not None else None
        if prob is None:
            continue
        preds.append({
            "h": h, "a": a, "prob": round(prob, 4),
            "h_ov05":   round(p_h, 4) if p_h else 0,
            "a_gs_ov05": round(p_a, 4) if p_a else 0,
            "date": fix.get("date", ""), "time": fix.get("time", ""),
            "elo_h": elo.get(h, 1500), "elo_a": elo.get(a, 1500),
            "elo_lbl_h": elo_label(elo.get(h, 1500)),
            "elo_lbl_a": elo_label(elo.get(a, 1500)),
        })
    preds.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": preds, "total": len(results),
            "params": {"n_recent": n_recent, "n_prev": n_prev,
                        "w_recent": w_recent, "w_prev": w_prev, "bonus": bonus}}


def predict_serie_c(results, fixtures, n_recent=NM_N_RECENT, n_prev=NM_N_PREV,
                    w_recent=NM_W_RECENT, w_prev=NM_W_PREV, bonus=NM_BONUS):
    """Serie C: OSPITE UNDER 1.5 — NuovoMetodo."""
    nm   = compute_nm(results, n_recent, n_prev, w_recent, w_prev, bonus)
    elo  = calc_elo(results)
    preds = []
    for fix in fixtures:
        fix = _normalize_fixture(fix)
        h, a = fix["home"], fix["away"]
        nh, na = nm.get(h), nm.get(a)
        if not nh or not na:
            continue
        p_h = nh.get("h_su_u15")
        p_a = na.get("a_gf_u15")
        prob = p_h * p_a if p_h is not None and p_a is not None else None
        if prob is None:
            continue
        preds.append({
            "h": h, "a": a, "prob": round(prob, 4),
            "h_su_u15":  round(p_h, 4) if p_h else 0,
            "a_gf_u15":  round(p_a, 4) if p_a else 0,
            "date": fix.get("date", ""), "time": fix.get("time", ""),
            "elo_h": elo.get(h, 1500), "elo_a": elo.get(a, 1500),
            "elo_lbl_h": elo_label(elo.get(h, 1500)),
            "elo_lbl_a": elo_label(elo.get(a, 1500)),
        })
    preds.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": preds, "total": len(results),
            "params": {"n_recent": n_recent, "n_prev": n_prev,
                        "w_recent": w_recent, "w_prev": w_prev, "bonus": bonus}}


def calc_standings(results):
    st = {}
    for r in results:
        r = _normalize_result(r)
        h, a, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        for t in (h, a):
            if t not in st:
                st[t] = {"g":0,"w":0,"d":0,"l":0,"gf":0,"ga":0,"pts":0}
        st[h]["g"]+=1; st[a]["g"]+=1
        st[h]["gf"]+=hg; st[h]["ga"]+=ag; st[a]["gf"]+=ag; st[a]["ga"]+=hg
        if hg > ag:   st[h]["w"]+=1; st[h]["pts"]+=3; st[a]["l"]+=1
        elif hg < ag: st[a]["w"]+=1; st[a]["pts"]+=3; st[h]["l"]+=1
        else:         st[h]["d"]+=1; st[a]["d"]+=1; st[h]["pts"]+=1; st[a]["pts"]+=1
    return sorted(st.items(), key=lambda x: (-x[1]["pts"], -(x[1]["gf"]-x[1]["ga"])))


# ============================================================



# ============================================================
# FLASK APP
# ============================================================
app = Flask(__name__)


@app.route("/")
def index():
    return render_template_string(HTML_TEMPLATE)


@app.route("/api/status")
def api_status():
    data   = load_data()
    counts = {k: len(data[k].get("results",[])) for k in _LEAGUE_KEYS}
    ng     = {k: data[k].get("next_giornata",0)    for k in _LEAGUE_KEYS}
    nf     = {k: len(data[k].get("next_fixtures",[])) for k in _LEAGUE_KEYS}
    return jsonify({
        "updatedAt": data.get("updatedAt","N/A"),
        "source":    data.get("source","—"),
        "total":     sum(counts.values()),
        "counts":    counts,
        "next_giornata":  ng,
        "next_fixtures":  nf,
    })


@app.route("/api/data")
def api_data():
    return jsonify(load_data())


@app.route("/api/predict")
def api_predict():
    data = load_data()
    # Parametri Serie B
    nrB = int(request.args.get("nRecentB", NM_N_RECENT))
    npB = int(request.args.get("nPrevB",   NM_N_PREV))
    wrB = float(request.args.get("wRecentB", NM_W_RECENT))
    wpB = float(request.args.get("wPrevB",   NM_W_PREV))
    bB  = float(request.args.get("bonusB",   NM_BONUS))
    # Parametri Serie C (tutti i gironi)
    nrC = int(request.args.get("nRecentC", NM_N_RECENT))
    npC = int(request.args.get("nPrevC",   NM_N_PREV))
    wrC = float(request.args.get("wRecentC", NM_W_RECENT))
    wpC = float(request.args.get("wPrevC",   NM_W_PREV))
    bC  = float(request.args.get("bonusC",   NM_BONUS))

    rB = predict_serie_b(data["serieB"].get("results",[]),
                         data["serieB"].get("next_fixtures",[]),
                         nrB, npB, wrB, wpB, bB)
    rB["next_giornata"] = data["serieB"].get("next_giornata",0)

    allC = {}
    for key, gir in [("serieCa","A"),("serieCb","B"),("serieCc","C")]:
        res = predict_serie_c(data[key].get("results",[]),
                              data[key].get("next_fixtures",[]),
                              nrC, npC, wrC, wpC, bC)
        res["girone"] = gir
        res["next_giornata"] = data[key].get("next_giornata",0)
        for p in res["predictions"]:
            p["gir"] = gir
        allC[gir] = res

    return jsonify({"serieB": rB, "serieC": allC})


@app.route("/api/standings")
def api_standings():
    data = load_data()
    return jsonify({k: calc_standings(data[k].get("results",[])) for k in _LEAGUE_KEYS})


@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    source = request.json.get("source","calciomagazine") if request.is_json else "calciomagazine"
    data   = load_data()
    if data.get("version",0) < 8:
        data = get_default_data()
    data["source"] = source

    if source == "wikipedia":
        errors, log = scrape_wikipedia(data)
    else:
        errors, log = scrape_calciomagazine(data)

    save_data(data)

    counts = {k: len(data[k].get("results",[])) for k in _LEAGUE_KEYS}
    total  = sum(counts.values())
    src_label = {"wikipedia": "Wikipedia"}.get(source, "calciomagazine.net")
    msg = f"⚠️ {'; '.join(errors)}" if errors else f"✅ {total} risultati scaricati da {src_label}"
    return jsonify({
        "success": len(errors)==0,
        "source":  source,
        "total":   total,
        "counts":  counts,
        "next_giornata":  {k: data[k].get("next_giornata",0)       for k in _LEAGUE_KEYS},
        "next_fixtures":  {k: len(data[k].get("next_fixtures",[]))  for k in _LEAGUE_KEYS},
        "errors": errors,
        "log":    log,
        "message": msg,
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


@app.route("/api/urls")
def api_urls_get():
    data = load_data()
    custom = data.get("urls", {})
    # Merge defaults con custom per mostrare tutto
    urls = {
        "calciomagazine": {},
        "wikipedia": {
            "serieB": custom.get("wikipedia", {}).get("serieB", WIKI_SERIE_B),
            "serieC": custom.get("wikipedia", {}).get("serieC", WIKI_SERIE_C),
        }
    }
    cm_custom = custom.get("calciomagazine", {})
    for key, cfg in CM_LEAGUES.items():
        cu = cm_custom.get(key, {})
        urls["calciomagazine"][key] = {
            "name": cfg["name"],
            "results_url":  cu.get("results_url", cfg["results_url"]),
            "calendar_url": cu.get("calendar_url", cfg["calendar_url"]),
        }
    return jsonify(urls)


@app.route("/api/urls", methods=["POST"])
def api_urls_set():
    data = load_data()
    data["urls"] = request.json or {}
    save_data(data)
    return jsonify({"success": True})




# ============================================================
# HTML TEMPLATE
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚽ Pronostici Serie B & C — v11 NuovoMetodo</title>
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

/* Source buttons */
.source-bar{display:flex;gap:8px;justify-content:center;margin:10px 0;flex-wrap:wrap}
.source-btn{display:flex;align-items:center;gap:6px;padding:8px 16px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--text2);cursor:pointer;font-size:.78rem;font-weight:600;transition:all .2s}
.source-btn.active{background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border-color:var(--accent);color:var(--accent)}
.source-btn .src-dot{width:6px;height:6px;border-radius:50%;background:var(--border)}
.source-btn.active .src-dot{background:var(--green);box-shadow:0 0 4px var(--green)}

/* Tabs */
.tabs{display:flex;gap:6px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.tab{padding:9px 20px;border-radius:10px;border:1px solid var(--border);background:var(--card);color:var(--text2);cursor:pointer;font-size:0.82rem;font-weight:600;transition:all .2s}
.tab.active,.tab:hover{background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border-color:var(--accent);color:var(--accent);box-shadow:var(--glow)}

/* Params panel */
.params-panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px 20px;margin:12px 0}
.params-panel h3{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--accent2);margin-bottom:12px;letter-spacing:1px;text-transform:uppercase}
.params-grid{display:grid;grid-template-columns:repeat(5,1fr);gap:10px}
.param-item{background:var(--card2);border-radius:10px;padding:10px 14px;border:1px solid var(--border)}
.param-item label{display:block;font-size:.68rem;color:var(--text2);text-transform:uppercase;letter-spacing:.5px;margin-bottom:6px}
.param-row{display:flex;align-items:center;gap:10px}
.param-row input[type=range]{flex:1;accent-color:var(--accent2);height:4px}
.param-row .param-val{font-family:'Space Mono',monospace;font-size:.85rem;font-weight:700;color:var(--accent2);min-width:36px;text-align:right}
.param-formula{font-family:'Space Mono',monospace;font-size:.68rem;color:var(--text2);margin-top:10px;padding:8px 12px;background:var(--card2);border-radius:8px;text-align:center}

/* Buttons */
.btn{padding:8px 18px;border-radius:8px;border:none;font-weight:600;font-size:0.78rem;cursor:pointer;transition:all .2s}
.btn-u{background:linear-gradient(135deg,rgba(34,211,238,0.2),rgba(167,139,250,0.2));color:var(--accent);border:1px solid var(--accent);font-size:0.82rem;padding:10px 22px}
.btn-g{background:rgba(52,211,153,0.15);color:var(--green);border:1px solid rgba(52,211,153,0.3)}
.btn-s{background:var(--card2);color:var(--text2);border:1px solid var(--border)}
.btn-d{background:rgba(248,113,113,0.15);color:var(--red);border:1px solid rgba(248,113,113,0.3)}
.btn:hover{opacity:0.85;transform:translateY(-1px)}.btn:disabled{opacity:0.5;cursor:not-allowed;transform:none}
.dm-bar{display:flex;gap:8px;justify-content:center;margin:8px 0;flex-wrap:wrap}.dm-bar .btn{font-size:0.72rem;padding:6px 14px}

/* Scrape log */
.scrape-log{background:var(--card2);border-radius:8px;padding:10px;margin-top:8px;font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--text2);max-height:250px;overflow-y:auto;display:none}.scrape-log.show{display:block}

/* Cards */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;margin:14px 0}
.match-card{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:16px;transition:all .3s;position:relative;overflow:hidden}
.match-card:hover{border-color:var(--accent);box-shadow:var(--glow);transform:translateY(-2px)}
.match-card .rank{position:absolute;top:10px;right:12px;font-family:'Space Mono',monospace;font-size:0.62rem;color:var(--text2);background:var(--card2);padding:2px 7px;border-radius:6px}
.match-card .teams{font-size:0.95rem;font-weight:600;margin-bottom:8px;line-height:1.4}
.match-card .vs{color:var(--text2);font-weight:400;font-size:0.78rem;margin:0 4px}
.match-card .match-date{font-size:0.68rem;color:var(--text2);font-family:'Space Mono',monospace;margin-bottom:6px;display:flex;align-items:center;gap:5px}

/* Probability */
.prob-container{margin:8px 0}
.prob-label{display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:3px}
.prob-label .type{color:var(--text2)}.prob-label .pct{font-family:'Space Mono',monospace;font-weight:700}
.prob-bar{height:7px;background:var(--card2);border-radius:4px;overflow:hidden}
.prob-fill{height:100%;border-radius:4px;transition:width .6s ease}
.prob-fill.high{background:linear-gradient(90deg,var(--green),#10b981)}
.prob-fill.perfect{background:linear-gradient(90deg,var(--accent),#06b6d4)}
.prob-fill.mid{background:linear-gradient(90deg,var(--orange),#f59e0b)}
.prob-fill.low{background:linear-gradient(90deg,var(--red),#ef4444)}

/* Detail stats — 2x2 grid */
.detail-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:10px;font-size:0.7rem}
.detail-stat{background:var(--card2);border-radius:6px;padding:6px 10px}
.detail-stat .dl{color:var(--text2);font-size:0.6rem;text-transform:uppercase;letter-spacing:0.3px}
.detail-stat .dv{font-family:'Space Mono',monospace;font-weight:700;margin-top:2px;font-size:.82rem}


/* Section titles */
.stitle{font-family:'Space Mono',monospace;font-size:1.05rem;font-weight:700;margin:24px 0 6px;display:flex;align-items:center;gap:10px}
.stitle .dot{width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ssub{font-size:0.75rem;color:var(--text2);margin-bottom:12px}
.giornata-badge{display:inline-block;background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));border:1px solid var(--accent);border-radius:8px;padding:4px 12px;font-family:'Space Mono',monospace;font-size:0.78rem;color:var(--accent);margin-bottom:8px}
.section-sep{border:none;border-top:1px solid var(--border);margin:20px 0}

/* Standings */
.st{width:100%;border-collapse:collapse;font-size:0.75rem;margin:10px 0}
.st th{padding:7px 8px;text-align:center;border-bottom:1px solid var(--border);color:var(--accent);font-family:'Space Mono',monospace;font-size:0.68rem;text-transform:uppercase}
.st td{padding:6px 8px;text-align:center;border-bottom:1px solid var(--border)}
.st td:nth-child(2){text-align:left;font-weight:600}
.st tr:hover{background:rgba(34,211,238,0.04)}

.empty{text-align:center;padding:36px;color:var(--text2);font-size:0.82rem}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:12px 24px;font-size:0.82rem;color:var(--accent);box-shadow:0 8px 30px rgba(0,0,0,0.4);transition:transform .3s ease;z-index:999;pointer-events:none}.toast.show{transform:translateX(-50%) translateY(0)}
.url-group{margin-bottom:12px;padding:10px;background:var(--card2);border-radius:8px;border:1px solid var(--border)}
.url-group h4{font-size:.72rem;color:var(--accent);margin-bottom:8px;font-family:'Space Mono',monospace;text-transform:uppercase;letter-spacing:.5px}
.url-row{display:flex;align-items:center;gap:8px;margin-bottom:6px}
.url-row label{font-size:.65rem;color:var(--text2);min-width:70px;text-transform:uppercase}
.url-row input{flex:1;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:6px 10px;color:var(--text);font-family:'Space Mono',monospace;font-size:.68rem}
.url-row input:focus{outline:none;border-color:var(--accent)}

@media(max-width:600px){
.container{padding:10px}.cards{grid-template-columns:1fr}.match-card{padding:12px}
.dm-bar{flex-direction:column;align-items:stretch}.params-grid{grid-template-columns:repeat(2,1fr)}
}
</style>
</head>
<body>
<div class="container">
<div class="header">
<h1>⚽ Pronostici Serie B & C</h1>
<p>NuovoMetodo — Media Pesata Forma Recente · v11</p>
</div>

<div class="data-status" id="dataStatus">
<span class="status-dot empty"></span>
<span class="status-text">Caricamento...</span>
</div>

<!-- Source selector -->
<div class="source-bar">
  <div class="source-btn active" id="srcCM" onclick="setSource('calciomagazine')">
    <span class="src-dot"></span>calciomagazine.net
  </div>
  <div class="source-btn" id="srcWK" onclick="setSource('wikipedia')">
    <span class="src-dot"></span>Wikipedia
  </div>
</div>

<div style="text-align:center;margin:10px 0">
<button class="btn btn-u" onclick="doScrape()" id="scrapeBtn">🔄 Aggiorna dati</button>
<button class="btn btn-s" onclick="toggleUrls()" style="margin-left:6px">⚙️ URL Fonti</button>
</div>
<div id="urlsPanel" style="display:none">
<div class="params-panel" style="border-color:#475569">
<h3 style="color:#94a3b8">⚙️ URL Fonti Dati — personalizzabili per la prossima stagione</h3>
<div id="urlsGrid" style="font-size:.72rem"></div>
<div style="text-align:center;margin-top:10px">
<button class="btn btn-g" onclick="saveUrls()">💾 Salva URL</button>
<button class="btn btn-s" onclick="resetUrls()" style="margin-left:6px">↩️ Ripristina default</button>
</div>
</div>
</div>
<div class="scrape-log" id="scrapeLog"></div>

<div class="dm-bar">
<button class="btn btn-g" onclick="doExport()">📤 Esporta</button>
<button class="btn btn-s" onclick="document.getElementById('importFile').click()">📥 Importa</button>
<button class="btn btn-d" onclick="doReset()">🗑️ Reset</button>
<input type="file" id="importFile" accept=".json" style="display:none" onchange="doImport(event)">
</div>

<div class="tabs">
<div class="tab active" onclick="switchTab('predictions',this)">📊 Pronostici</div>
<div class="tab" onclick="switchTab('standings',this)">🏆 Classifiche</div>
</div>

<div id="tab_predictions">

<!-- NuovoMetodo params — Serie B -->
<div class="params-panel" style="border-color:var(--accent)">
<h3 style="color:var(--accent)">🎛️ Parametri Serie B — Casa Over 0.5</h3>
<div class="params-grid">
  <div class="param-item">
    <label>📊 Partite recenti</label>
    <div class="param-row">
      <input type="range" id="nRecentB" min="1" max="10" value="3" oninput="updateParams()">
      <span class="param-val" id="nRecentBVal">3</span>
    </div>
  </div>
  <div class="param-item">
    <label>📊 Partite precedenti</label>
    <div class="param-row">
      <input type="range" id="nPrevB" min="0" max="10" value="3" oninput="updateParams()">
      <span class="param-val" id="nPrevBVal">3</span>
    </div>
  </div>
  <div class="param-item">
    <label>⚖️ Peso recenti</label>
    <div class="param-row">
      <input type="range" id="wRecentB" min="50" max="200" value="130" step="5" oninput="updateParams()">
      <span class="param-val" id="wRecentBVal">1.30</span>
    </div>
  </div>
  <div class="param-item">
    <label>⚖️ Peso precedenti</label>
    <div class="param-row">
      <input type="range" id="wPrevB" min="10" max="150" value="70" step="5" oninput="updateParams()">
      <span class="param-val" id="wPrevBVal">0.70</span>
    </div>
  </div>
  <div class="param-item" style="border-color:rgba(34,211,238,0.3)">
    <label>🎯 Bonus prestazione</label>
    <div class="param-row">
      <input type="range" id="bonusB" min="0" max="100" value="25" step="5" oninput="updateParams()">
      <span class="param-val" id="bonusBVal">0.25</span>
    </div>
  </div>
</div>
<div class="param-formula" id="paramFormulaB">
  NM<sub>B</sub> = (eventi<sub>3</sub> × 1.30 + eventi<sub>3</sub> × 0.70) / 6.00
</div>
</div>

<!-- Serie B -->
<div class="stitle"><span class="dot"></span>Serie B — CASA OVER 0.5</div>
<div id="giornataB"></div>
<div class="ssub" id="statsB"></div>
<div class="cards" id="cardsB"></div>

<hr class="section-sep">

<!-- NuovoMetodo params — Serie C -->
<div class="params-panel" style="border-color:var(--accent2)">
<h3>🎛️ Parametri Serie C — Ospite Under 1.5 (tutti i gironi)</h3>
<div class="params-grid">
  <div class="param-item">
    <label>📊 Partite recenti</label>
    <div class="param-row">
      <input type="range" id="nRecentC" min="1" max="10" value="3" oninput="updateParams()">
      <span class="param-val" id="nRecentCVal">3</span>
    </div>
  </div>
  <div class="param-item">
    <label>📊 Partite precedenti</label>
    <div class="param-row">
      <input type="range" id="nPrevC" min="0" max="10" value="3" oninput="updateParams()">
      <span class="param-val" id="nPrevCVal">3</span>
    </div>
  </div>
  <div class="param-item">
    <label>⚖️ Peso recenti</label>
    <div class="param-row">
      <input type="range" id="wRecentC" min="50" max="200" value="130" step="5" oninput="updateParams()">
      <span class="param-val" id="wRecentCVal">1.30</span>
    </div>
  </div>
  <div class="param-item">
    <label>⚖️ Peso precedenti</label>
    <div class="param-row">
      <input type="range" id="wPrevC" min="10" max="150" value="70" step="5" oninput="updateParams()">
      <span class="param-val" id="wPrevCVal">0.70</span>
    </div>
  </div>
  <div class="param-item" style="border-color:rgba(167,139,250,0.3)">
    <label>🎯 Bonus prestazione</label>
    <div class="param-row">
      <input type="range" id="bonusC" min="0" max="100" value="15" step="5" oninput="updateParams()">
      <span class="param-val" id="bonusCVal">0.15</span>
    </div>
  </div>
</div>
<div class="param-formula" id="paramFormulaC">
  NM<sub>C</sub> = (eventi<sub>3</sub> × 1.30 + eventi<sub>3</sub> × 0.70) / 6.00
</div>
</div>

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
let curSource = 'calciomagazine';

function toast(msg){const t=document.getElementById('toast');t.textContent=msg;t.classList.add('show');setTimeout(()=>t.classList.remove('show'),2500)}
function pc(p){return p>=1.0?'perfect':p>=0.70?'high':p>=0.50?'mid':'low'}
function pcol(p){return p>=1.0?'#22d3ee':p>=0.70?'#34d399':p>=0.50?'#fbbf24':'#f87171'}
function pp(v){return v!=null?(v*100).toFixed(0)+'%':'—'}

function getParamsB(){
  return {
    nRecent: +document.getElementById('nRecentB').value,
    nPrev:   +document.getElementById('nPrevB').value,
    wRecent: +document.getElementById('wRecentB').value / 100,
    wPrev:   +document.getElementById('wPrevB').value / 100,
    bonus:   +document.getElementById('bonusB').value / 100,
  }
}
function getParamsC(){
  return {
    nRecent: +document.getElementById('nRecentC').value,
    nPrev:   +document.getElementById('nPrevC').value,
    wRecent: +document.getElementById('wRecentC').value / 100,
    wPrev:   +document.getElementById('wPrevC').value / 100,
    bonus:   +document.getElementById('bonusC').value / 100,
  }
}

function updateParams(){
  const b = getParamsB(), c = getParamsC();
  document.getElementById('nRecentBVal').textContent = b.nRecent;
  document.getElementById('nPrevBVal').textContent   = b.nPrev;
  document.getElementById('wRecentBVal').textContent  = b.wRecent.toFixed(2);
  document.getElementById('wPrevBVal').textContent    = b.wPrev.toFixed(2);
  document.getElementById('bonusBVal').textContent    = b.bonus.toFixed(2);
  const msB = (1 + b.bonus).toFixed(1);
  const normB = (b.nRecent * (1+b.bonus) * b.wRecent + b.nPrev * (1+b.bonus) * b.wPrev).toFixed(2);
  document.getElementById('paramFormulaB').innerHTML = b.bonus > 0 ?
    `NM<sub>B</sub> = (Σscore<sub>${b.nRecent}</sub> × ${b.wRecent.toFixed(2)} + Σscore<sub>${b.nPrev}</sub> × ${b.wPrev.toFixed(2)}) / ${normB} &nbsp;·&nbsp; max=${msB} &nbsp;·&nbsp; <span style="color:var(--accent)">1gol→1.0 · 2+gol→${msB}</span>` :
    `NM<sub>B</sub> = (eventi<sub>${b.nRecent}</sub> × ${b.wRecent.toFixed(2)} + eventi<sub>${b.nPrev}</sub> × ${b.wPrev.toFixed(2)}) / ${(b.nRecent*b.wRecent+b.nPrev*b.wPrev).toFixed(2)}`;
  document.getElementById('nRecentCVal').textContent = c.nRecent;
  document.getElementById('nPrevCVal').textContent   = c.nPrev;
  document.getElementById('wRecentCVal').textContent  = c.wRecent.toFixed(2);
  document.getElementById('wPrevCVal').textContent    = c.wPrev.toFixed(2);
  document.getElementById('bonusCVal').textContent    = c.bonus.toFixed(2);
  const msC = (1 + c.bonus).toFixed(1);
  const normC = (c.nRecent * (1+c.bonus) * c.wRecent + c.nPrev * (1+c.bonus) * c.wPrev).toFixed(2);
  document.getElementById('paramFormulaC').innerHTML = c.bonus > 0 ?
    `NM<sub>C</sub> = (Σscore<sub>${c.nRecent}</sub> × ${c.wRecent.toFixed(2)} + Σscore<sub>${c.nPrev}</sub> × ${c.wPrev.toFixed(2)}) / ${normC} &nbsp;·&nbsp; max=${msC} &nbsp;·&nbsp; <span style="color:var(--accent2)">0gol→${msC} · 1gol→1.0</span>` :
    `NM<sub>C</sub> = (eventi<sub>${c.nRecent}</sub> × ${c.wRecent.toFixed(2)} + eventi<sub>${c.nPrev}</sub> × ${c.wPrev.toFixed(2)}) / ${(c.nRecent*c.wRecent+c.nPrev*c.wPrev).toFixed(2)}`;
  recalc();
}

// ── Status ──
async function updateStatus(){
  const r=await fetch('/api/status');const d=await r.json();
  const dot=d.total>0?'ok':'empty';
  const el=document.getElementById('dataStatus');
  const ng=d.next_giornata||{};
  el.innerHTML=`<span class="status-dot ${dot}"></span><span class="status-text">📊 <b>${d.total}</b> risultati · Agg: <b>${d.updatedAt}</b> · Fonte: <b>${d.source||'—'}</b></span>`;
  // Highlight active source
  if(d.source){curSource=d.source;
    document.querySelectorAll('.source-btn').forEach(b=>b.classList.remove('active'));
    const id=d.source==='wikipedia'?'srcWK':'srcCM';
    const el2=document.getElementById(id);if(el2)el2.classList.add('active')}
}

function setSource(s){
  curSource=s;
  document.querySelectorAll('.source-btn').forEach(b=>b.classList.remove('active'));
  const id=s==='wikipedia'?'srcWK':'srcCM';
  document.getElementById(id).classList.add('active');
}

// ── Scrape ──
async function doScrape(){
  const btn=document.getElementById('scrapeBtn');const log=document.getElementById('scrapeLog');
  btn.disabled=true;btn.textContent='⏳ Scaricamento...';
  log.classList.add('show');log.innerHTML='Connessione...<br>';
  const dot=document.querySelector('.status-dot');if(dot)dot.className='status-dot loading';
  try{
    const r=await fetch('/api/scrape',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({source:curSource})});
    const d=await r.json();
    log.innerHTML+=d.message+'<br>';
    if(d.log)log.innerHTML+=d.log.map(l=>'  '+l).join('<br>')+'<br>';
    if(d.errors&&d.errors.length)log.innerHTML+='<br>Errori: '+d.errors.join(', ')+'<br>';
    toast(d.message);await updateStatus();recalc();
  }catch(e){log.innerHTML+='❌ '+e.message+'<br>';toast('❌ Errore')}
  btn.disabled=false;btn.textContent='🔄 Aggiorna dati';
  setTimeout(()=>log.classList.remove('show'),8000);
}

// ── Cards ──
function fmtDate(d,t){
  if(!d)return'';
  const[y,m,dd]=d.split('-');
  const days=['Dom','Lun','Mar','Mer','Gio','Ven','Sab'];
  const dt=new Date(y,m-1,dd);
  let s=days[dt.getDay()]+' '+dd+'/'+m+'/'+y;
  if(t)s+=' ore '+t;
  return s;
}

function statColor(v){
  if(v>=1.0)  return '#22d3ee';
  if(v>=0.80) return '#34d399';
  if(v>=0.65) return '#fbbf24';
  return '#f87171';
}

function crdB(i,p){
  const dt=p.date?`<div class="match-date">📅 ${fmtDate(p.date,p.time)}</div>`:'';
  return`<div class="match-card"><div class="rank">#${i+1}</div>
${dt}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container">
  <div class="prob-label"><span class="type">CASA OVER 0.5</span>
  <span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
  <div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div>
</div>
<div class="detail-stats">
  <div class="detail-stat"><div class="dl">🏠 Casa OV0.5</div><div class="dv" style="color:${statColor(p.h_ov05)}">${pp(p.h_ov05)}</div></div>
  <div class="detail-stat"><div class="dl">✈️ Osp SU OV0.5</div><div class="dv" style="color:${statColor(p.a_gs_ov05)}">${pp(p.a_gs_ov05)}</div></div>
</div>
</div>`}

function crdC(i,p){
  const dt=p.date?`<div class="match-date">📅 ${fmtDate(p.date,p.time)}</div>`:'';
  return`<div class="match-card"><div class="rank">#${i+1}</div>
${dt}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container">
  <div class="prob-label"><span class="type">OSPITE UNDER 1.5</span>
  <span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
  <div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div>
</div>
<div class="detail-stats">
  <div class="detail-stat"><div class="dl">🏠 Casa SU U1.5</div><div class="dv" style="color:${statColor(p.h_su_u15)}">${pp(p.h_su_u15)}</div></div>
  <div class="detail-stat"><div class="dl">✈️ Osp GF U1.5</div><div class="dv" style="color:${statColor(p.a_gf_u15)}">${pp(p.a_gf_u15)}</div></div>
</div>
</div>`}

// ── Predictions ──
async function recalc(){
  const b = getParamsB(), c = getParamsC();
  const url = `/api/predict?nRecentB=${b.nRecent}&nPrevB=${b.nPrev}&wRecentB=${b.wRecent}&wPrevB=${b.wPrev}&bonusB=${b.bonus}&nRecentC=${c.nRecent}&nPrevC=${c.nPrev}&wRecentC=${c.wRecent}&wPrevC=${c.wPrev}&bonusC=${c.bonus}`;
  const r = await fetch(url);
  const d = await r.json();

  // Serie B
  const bG=d.serieB.next_giornata;
  document.getElementById('giornataB').innerHTML=bG?`<span class="giornata-badge">${bG}ª Giornata</span>`:'';
  document.getElementById('statsB').innerHTML=`📊 <b>${d.serieB.total}</b> partite analizzate`;
  document.getElementById('cardsB').innerHTML=d.serieB.predictions.length?
    d.serieB.predictions.map((p,i)=>crdB(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄</div>';

  // Serie C
  ['A','B','C'].forEach(gir=>{
    const s=gir.toLowerCase();
    const g=d.serieC[gir];if(!g)return;
    const cG=g.next_giornata;
    document.getElementById('giornataC'+s).innerHTML=cG?`<span class="giornata-badge">${cG}ª Giornata</span>`:'';
    document.getElementById('statsC'+s).innerHTML=`📊 <b>${g.total}</b> partite analizzate`;
    document.getElementById('cardsC'+s).innerHTML=g.predictions.length?
      g.predictions.map((p,i)=>crdC(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄</div>';
  });
}

// ── Standings ──
async function renderStandings(){
  const r=await fetch('/api/standings');const d=await r.json();
  function tbl(rows){
    if(!rows.length)return'<tbody><tr><td colspan="9" style="text-align:center;color:var(--text2)">Nessun dato</td></tr></tbody>';
    return`<thead><tr><th>#</th><th>Squadra</th><th>G</th><th>V</th><th>P</th><th>S</th><th>GF</th><th>GS</th><th>Pt</th></tr></thead><tbody>`+
    rows.map(([n,s],i)=>`<tr><td>${i+1}</td><td>${n}</td><td>${s.g}</td><td>${s.w}</td><td>${s.d}</td><td>${s.l}</td><td>${s.gf}</td><td>${s.ga}</td><td><b>${s.pts}</b></td></tr>`).join('')+'</tbody>'}
  document.getElementById('tableB').innerHTML=tbl(d.serieB);
  let h='';[{k:'serieCa',n:'A'},{k:'serieCb',n:'B'},{k:'serieCc',n:'C'}].forEach(g=>{
    h+=`<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C — Girone ${g.n}</div><div style="overflow-x:auto"><table class="st">${tbl(d[g.k])}</table></div>`});
  document.getElementById('tablesC').innerHTML=h;
}

// ── Export/Import/Reset ──
async function doExport(){const r=await fetch('/api/export');const d=await r.json();const b=new Blob([JSON.stringify(d,null,2)],{type:'application/json'});const u=URL.createObjectURL(b);const a=document.createElement('a');a.href=u;a.download=`pronostici_v11_${d.updatedAt||'export'}.json`;a.click();URL.revokeObjectURL(u);toast('📤 Esportato!')}
async function doImport(ev){const f=ev.target.files[0];if(!f)return;const reader=new FileReader();reader.onload=async function(e){try{const d=JSON.parse(e.target.result);const r=await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});const res=await r.json();if(res.success){updateStatus();recalc();toast('📥 Importato!')}else toast('⚠️ '+res.error)}catch(err){toast('⚠️ Errore')}};reader.readAsText(f);ev.target.value=''}
async function doReset(){if(!confirm('Cancellare tutti i dati?'))return;await fetch('/api/reset',{method:'POST'});updateStatus();recalc();toast('🗑️ Reset!')}
function switchTab(tab,el){document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));el.classList.add('active');['predictions','standings'].forEach(t=>{document.getElementById('tab_'+t).style.display=t===tab?'':'none'});if(tab==='standings')renderStandings()}

// ── URL Config ──
function toggleUrls(){const p=document.getElementById('urlsPanel');if(p.style.display==='none'){p.style.display='';loadUrls()}else{p.style.display='none'}}

async function loadUrls(){
  const r=await fetch('/api/urls');const d=await r.json();
  let h='<div class="url-group"><h4>📰 calciomagazine.net</h4>';
  for(const[key,cfg]of Object.entries(d.calciomagazine)){
    h+=`<div style="margin-bottom:8px"><div style="font-size:.68rem;color:var(--accent2);margin-bottom:4px;font-weight:600">${cfg.name}</div>`;
    h+=`<div class="url-row"><label>Risultati</label><input id="url_cm_${key}_res" value="${cfg.results_url}"></div>`;
    h+=`<div class="url-row"><label>Calendario</label><input id="url_cm_${key}_cal" value="${cfg.calendar_url}"></div></div>`;
  }
  h+='</div><div class="url-group"><h4>🌐 Wikipedia</h4>';
  h+=`<div class="url-row"><label>Serie B</label><input id="url_wiki_B" value="${d.wikipedia.serieB}"></div>`;
  h+=`<div class="url-row"><label>Serie C</label><input id="url_wiki_C" value="${d.wikipedia.serieC}"></div>`;
  h+='</div>';
  document.getElementById('urlsGrid').innerHTML=h;
}

async function saveUrls(){
  const urls={calciomagazine:{},wikipedia:{}};
  ['serieB','serieCa','serieCb','serieCc'].forEach(k=>{
    const res=document.getElementById('url_cm_'+k+'_res');
    const cal=document.getElementById('url_cm_'+k+'_cal');
    if(res&&cal)urls.calciomagazine[k]={results_url:res.value.trim(),calendar_url:cal.value.trim()};
  });
  const wb=document.getElementById('url_wiki_B');
  const wc=document.getElementById('url_wiki_C');
  if(wb)urls.wikipedia.serieB=wb.value.trim();
  if(wc)urls.wikipedia.serieC=wc.value.trim();
  await fetch('/api/urls',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(urls)});
  toast('💾 URL salvati!');
}

async function resetUrls(){
  if(!confirm('Ripristinare tutti gli URL ai valori default?'))return;
  await fetch('/api/urls',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({})});
  loadUrls();toast('↩️ URL ripristinati!');
}

// ── Init ──
updateStatus();updateParams();
</script>
</body>
</html>"""



if __name__ == "__main__":
    import threading


    if not DATA_FILE.exists():
        save_data(get_default_data())
        print("📂 Primo avvio — seleziona la fonte e premi 🔄 per scaricare i dati")
    else:
        d     = load_data()
        total = sum(len(d[k].get("results", [])) for k in _LEAGUE_KEYS)
        src   = d.get("source", "calciomagazine")
        print(f"📂 Dati caricati: {total} risultati (fonte: {src})")

    def run_server():
        import logging
        logging.getLogger("werkzeug").setLevel(logging.ERROR)
        app.run(host="127.0.0.1", port=PORT, debug=False, use_reloader=False)

    server = threading.Thread(target=run_server, daemon=True)
    server.start()

    try:
        import webview
        print("⚽ Avvio finestra desktop...")
        webview.create_window(
            "⚽ Pronostici Serie B & C — v11",
            f"http://127.0.0.1:{PORT}",
            width=1100, height=800, min_size=(600, 500),
        )
        webview.start()
    except ImportError:
        print(f"\n⚠️  pywebview non trovato → pip install pywebview")
        print(f"    Oppure apri: http://localhost:{PORT}\n")
        import webbrowser
        webbrowser.open(f"http://localhost:{PORT}")
        try:
            server.join()
        except KeyboardInterrupt:
            pass
