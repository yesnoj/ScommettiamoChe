#!/usr/bin/env python3
"""
⚽ Pronostici Serie B & C — v9 Python/Flask
Modello Dixon-Coles + Decadimento temporale + Fattore campo

Fonti dati selezionabili:
  • calciomagazine.net  (parser testo)
  • Wikipedia it.       (parser HTML/BeautifulSoup)
  • Tuttosport          (parser HTML/BeautifulSoup, img-alt strategy)
"""

import json, re, os, math, sys
from datetime import datetime, date, timedelta
from pathlib import Path

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
APP_DIR   = Path(__file__).parent
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

# ── Fonti Tuttosport ───────────────────────────────────────────
TS_LEAGUES = {
    "serieB":  "https://www.tuttosport.com/live/calendario-serie-b",
    "serieCa": "https://www.tuttosport.com/live/calendario-serie-c-girone-a",
    "serieCb": "https://www.tuttosport.com/live/calendario-serie-c-girone-b",
    "serieCc": "https://www.tuttosport.com/live/calendario-serie-c-girone-c",
}

TS_LEAGUE_NAMES = {
    "serieB":  "Serie B",
    "serieCa": "Serie C Girone A",
    "serieCb": "Serie C Girone B",
    "serieCc": "Serie C Girone C",
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
        r"(\d{2})\.(\d{2})\.\s*ore\s*\d{1,2}:\d{2}\s+"
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
        rm = r_re.search(line) or r_alt.search(line)
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
        r"(\d{2})\.(\d{2})\.(\d{2,4})\s+ore\s*\d{1,2}:\d{2}\s+"
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
            home     = fm.group(4).strip()
            away     = re.sub(r"\s*\*+$", "", fm.group(5)).strip()
            try:
                date_str = date(year, month, day_n).isoformat()
            except ValueError:
                date_str = f"{year}-{month:02d}-{day_n:02d}"
            giornate[current_g].append({"date": date_str, "home": home, "away": away})
    return giornate


def cm_find_next_giornata(results_by_g, calendar_by_g):
    if calendar_by_g:
        all_g = sorted(set(list(results_by_g) + list(calendar_by_g)))
        for g in all_g:
            if len(results_by_g.get(g, [])) < len(calendar_by_g.get(g, [])):
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
    for key, cfg in CM_LEAGUES.items():
        name = cfg["name"]
        # Risultati
        html_r = fetch_page(cfg["results_url"])
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
        html_c = fetch_page(cfg["calendar_url"])
        if html_c is None and "calendar_url_alt" in cfg:
            log.append(f"  → URL alternativo per {name}...")
            html_c = fetch_page(cfg["calendar_url_alt"])
        if html_c is None:
            errors.append(f"Errore calendario {name}")
        else:
            cbg = cm_parse_calendar(html_c)
            data[key]["calendar_by_giornata"] = {str(k): v for k, v in cbg.items()}
            rbg_int = {int(k): v for k, v in data[key].get("results_by_giornata", {}).items()}
            next_g  = cm_find_next_giornata(rbg_int, cbg)
            data[key]["next_giornata"] = next_g
            data[key]["next_fixtures"] = cbg.get(next_g, [])
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
            sc = _parse_score(score_r)
            if sc:
                key = (date_r, away, home)
                if key not in seen_res:
                    giornate[cur_g_r]["results"].append(
                        {"date": date_r, "home": away, "away": home, "hg": sc[0], "ag": sc[1]}
                    )
                    seen_res.add(key)
            elif _is_time_or_dash(score_r):
                key = (away, home, "r")
                if key not in seen_fix:
                    giornate[cur_g_r]["fixtures"].append({"date": date_r, "home": away, "away": home})
                    seen_fix.add(key)
    return giornate


def wiki_find_next_giornata(giornate_data):
    candidates = [
        (g, len(d.get("fixtures", [])))
        for g, d in giornate_data.items()
        if d.get("fixtures")
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
        data[key]["next_fixtures"] = giornate_data.get(next_g, {}).get("fixtures", [])
        return len(all_r), len(giornate_data), next_g, len(data[key]["next_fixtures"])
    return 0, 0, 0, 0


def scrape_wikipedia(data):
    """Scarica tutti i campionati da Wikipedia e aggiorna data."""
    errors, log = [], []
    print("📥 Scarico Serie B da Wikipedia...")
    html_b = fetch_page(WIKI_SERIE_B)
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
    html_c = fetch_page(WIKI_SERIE_C)
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


# ============================================================
# PARSER — Tuttosport
# ============================================================
# Page structure observed:
#   Serie B  → explicit round number: "Serie B 27a giornata"
#   Serie C  → no round number: "Serie C girone c" repeated per round
#              (rounds counted sequentially by header occurrences)
#   Dates    → "venerdì 22.08.2025"  (DD.MM.YYYY)  — Serie B
#           OR "domenica 2025.08.24" (YYYY.MM.DD)  — Serie C
#   Matches  → <a href="/live/partita/...">
#                 <img alt="HomeTeam"> ... score/time ... <img alt="AwayTeam">
#              </a>
#   Score    → "G - G"    (played)
#   Fixture  → "HH:MM"    (upcoming) or absent text (TBD)
# ============================================================

_TS_WEEKDAY = re.compile(
    r"(?:luned[iì]|marted[iì]|mercoled[iì]|gioved[iì]|venerd[iì]|sabato|domenica)",
    re.I,
)
_TS_PARTITA = re.compile(r"/live/partita/")


def _ts_parse_date(text):
    """Parse DD.MM.YYYY or YYYY.MM.DD → ISO date string."""
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})", text)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat()
        except ValueError:
            pass
    m = re.search(r"(\d{4})\.(\d{2})\.(\d{2})", text)
    if m:
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat()
        except ValueError:
            pass
    return ""


def ts_parse_calendar(html, league_key):
    """
    Parse a single Tuttosport calendar page.

    Parameters
    ----------
    html       : raw HTML string from fetch_page()
    league_key : 'serieB' | 'serieCa' | 'serieCb' | 'serieCc'

    Returns
    -------
    dict  {giornata_num (int): {'results': [...], 'fixtures': [...]}}
    """
    is_serie_b = (league_key == "serieB")
    # Girone letter for Serie C matching ("a", "b", "c")
    girone_letter = "" if is_serie_b else league_key[-1]  # 'a', 'b' or 'c'

    soup = BeautifulSoup(html, "html.parser")
    giornate: dict = {}
    current_g    = 0
    current_date = ""
    g_counter    = 0

    # For Serie C: track which DOM nodes already triggered a round increment
    # to avoid double-counting parent + child nodes with the same short text.
    _seen_round_el_ids: set = set()
    _seen_match_el_ids: set = set()

    # Regex for round header (Serie B)
    _sb_round_re = re.compile(r"(\d+)[a-z°ª]+\s*giornata", re.I)
    # Regex for Serie C round header
    _sc_round_re = re.compile(
        r"serie\s*c\s*(?:girone\s*)?" + re.escape(girone_letter), re.I
    )

    for el in soup.descendants:
        # Skip NavigableString (plain text nodes) — only process Tag objects
        if not hasattr(el, "name") or el.name is None:
            continue

        # ── A) Match link ─────────────────────────────────────────────
        if el.name == "a" and _TS_PARTITA.search(el.get("href", "")):
            el_id = id(el)
            if el_id in _seen_match_el_ids:
                continue
            _seen_match_el_ids.add(el_id)

            if current_g == 0:
                continue

            imgs = el.find_all("img")
            if len(imgs) < 2:
                continue

            home = imgs[0].get("alt", "").strip()
            away = imgs[-1].get("alt", "").strip()
            if not home or not away or home == away:
                continue

            # Extract middle text (score or time) — join all text nodes in link
            # excluding the team-name strings
            link_text = el.get_text(separator="§")
            parts     = [p.strip() for p in link_text.split("§") if p.strip()]
            mid_parts = [p for p in parts if p != home and p != away]
            mid       = " ".join(mid_parts).strip()

            g_data = giornate.setdefault(current_g, {"results": [], "fixtures": []})

            sc = re.match(r"^(\d+)\s*-\s*(\d+)$", mid)
            if sc:
                hg, ag = int(sc.group(1)), int(sc.group(2))
                if not any(r["home"] == home and r["away"] == away for r in g_data["results"]):
                    g_data["results"].append({
                        "date": current_date, "home": home, "away": away,
                        "hg": hg, "ag": ag,
                    })
            else:
                # Upcoming fixture (time "HH:MM", "-", or empty)
                if not any(f["home"] == home and f["away"] == away for f in g_data["fixtures"]):
                    g_data["fixtures"].append({
                        "date": current_date, "home": home, "away": away,
                    })
            continue   # don't re-process as header/date

        # Skip elements that are inside a match link (already handled above)
        if el.find_parent("a", href=_TS_PARTITA):
            continue

        txt = el.get_text(strip=True)
        if not txt:
            continue

        # ── B) Round header ───────────────────────────────────────────
        if is_serie_b:
            gm = _sb_round_re.search(txt)
            if gm and re.search(r"serie\s*b", txt, re.I) and len(txt) <= 80:
                new_g = int(gm.group(1))
                if new_g != current_g:
                    current_g = new_g
                    giornate.setdefault(current_g, {"results": [], "fixtures": []})
        else:
            # Serie C: count each new "Serie C girone X" header block once
            if _sc_round_re.search(txt) and len(txt) <= 50:
                # Avoid double-counting parent + child carrying identical text
                if not any(id(p) in _seen_round_el_ids for p in el.parents):
                    _seen_round_el_ids.add(id(el))
                    g_counter += 1
                    current_g = g_counter
                    giornate.setdefault(current_g, {"results": [], "fixtures": []})

        # ── C) Date ───────────────────────────────────────────────────
        if _TS_WEEKDAY.search(txt) and len(txt) <= 60:
            d_str = _ts_parse_date(txt)
            if d_str:
                current_date = d_str

    return giornate


def scrape_tuttosport(data):
    """Scarica tutti i campionati da Tuttosport e aggiorna data."""
    errors: list = []
    log:    list = []

    for key, url in TS_LEAGUES.items():
        name = TS_LEAGUE_NAMES[key]
        print(f"📥 {name} da Tuttosport...")
        html = fetch_page(url)
        if html is None:
            errors.append(f"Errore download {name}")
            log.append(f"{name}: ❌ fetch fallito")
            continue

        gd = ts_parse_calendar(html, league_key=key)
        if not gd:
            errors.append(f"Nessun dato parsato per {name}")
            log.append(f"{name}: ⚠️ parser non ha trovato dati")
            continue

        nr, ng, nxg, nf = _process_wiki_giornate(gd, key, data)
        if nr > 0:
            log.append(f"{name}: {nr} risultati in {ng} giornate → prossima: {nxg}ª ({nf} partite)")
        else:
            prev = len(data[key].get("results", []))
            log.append(f"{name}: ⚠️ nessun risultato trovato (mantengo {prev} precedenti)")
            if prev == 0:
                errors.append(f"Nessun risultato per {name}")

    return errors, log


_LEAGUE_KEYS = ["serieB", "serieCa", "serieCb", "serieCc"]

def get_default_data():
    return {
        "version":   9,
        "source":    "calciomagazine",
        "updatedAt": date.today().isoformat(),
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
            if data.get("version", 0) < 9:
                print("  📦 Migrazione dati → v9...")
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
                data["version"] = 9
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
# MODELLO STATISTICO — Poisson / Dixon-Coles / Decay / HF
# ============================================================
DECAY_XI    = 0.005
RHO_DEFAULT = -0.13


def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)


def calc_stats(results, use_decay=True):
    today = date.today()
    stats = {}
    for r in results:
        r = _normalize_result(r)
        home, away, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        if use_decay:
            try:
                days = max((today - date.fromisoformat(r["date"])).days, 0)
                w = math.exp(-DECAY_XI * days)
            except Exception:
                w = 0.5
        else:
            w = 1.0
        for t in (home, away):
            if t not in stats:
                stats[t] = {"hgf_w":0,"hga_w":0,"hw":0,"agf_w":0,"aga_w":0,"aw":0,
                            "hg":0,"ag":0,"home_gf":0,"home_ga":0,"away_gf":0,"away_ga":0}
        stats[home]["hgf_w"] += hg * w; stats[home]["hga_w"] += ag * w
        stats[home]["hw"] += w;         stats[home]["hg"] += 1
        stats[home]["home_gf"] += hg;   stats[home]["home_ga"] += ag
        stats[away]["agf_w"] += ag * w; stats[away]["aga_w"] += hg * w
        stats[away]["aw"] += w;         stats[away]["ag"] += 1
        stats[away]["away_gf"] += ag;   stats[away]["away_ga"] += hg
    return stats


def league_avg(stats):
    tg = sum(t["hgf_w"] for t in stats.values())
    tw = sum(t["hw"]    for t in stats.values())
    return tg / tw if tw > 0 else 1.2


def calc_home_factors(stats, avg):
    th   = sum(t["home_gf"] for t in stats.values())
    ta   = sum(t["away_gf"] for t in stats.values())
    thm  = sum(t["hg"]      for t in stats.values())
    tam  = sum(t["ag"]      for t in stats.values())
    lhr  = (th / thm) / (ta / tam) if thm > 0 and tam > 0 and ta > 0 else 1.2
    factors = {}
    for team, s in stats.items():
        if s["hg"] >= 3 and s["ag"] >= 3:
            thr = s["home_gf"] / s["hg"]
            tar = s["away_gf"] / s["ag"] if s["ag"] > 0 else ta / tam if tam > 0 else 1.0
            raw = (thr / tar) / lhr if tar > 0 and lhr > 0 else 1.0
            factors[team] = 0.7 * raw + 0.3
        else:
            factors[team] = 1.0
    return factors


def dc_tau(hg, ag, lh, la, rho):
    if hg == 0 and ag == 0: return 1 - lh * la * rho
    if hg == 0 and ag == 1: return 1 + lh * rho
    if hg == 1 and ag == 0: return 1 + la * rho
    if hg == 1 and ag == 1: return 1 - rho
    return 1.0


def estimate_rho(results, stats, avg):
    results = [_normalize_result(r) for r in results]
    if len(results) < 30:
        return RHO_DEFAULT
    obs = {(0,0):0,(1,1):0}; exp = {(0,0):0.0,(1,1):0.0}; n = 0
    for r in results:
        hs = stats.get(r["home"]); as_ = stats.get(r["away"])
        if not hs or not as_ or not hs["hw"] or not as_["aw"]:
            continue
        lh = (hs["hgf_w"]/hs["hw"]) * (as_["aga_w"]/as_["aw"]) / avg if avg > 0 else 1.0
        la = (as_["agf_w"]/as_["aw"]) * (hs["hga_w"]/hs["hw"]) / avg if avg > 0 else 1.0
        k  = (min(r["hg"],2), min(r["ag"],2))
        if k in obs:
            obs[k] += 1
            exp[k] += poisson_pmf(lh, k[0]) * poisson_pmf(la, k[1])
        n += 1
    if n < 30:
        return RHO_DEFAULT
    rhos = [-(obs[k]/(exp[k]*n)-1)*0.3 for k in [(0,0),(1,1)] if exp[k] > 0]
    return round(max(-0.25, min(0.05, sum(rhos)/len(rhos) if rhos else RHO_DEFAULT)), 4)


def dc_matrix(lh, la, rho, mg=8):
    return [
        [max(poisson_pmf(lh,i)*poisson_pmf(la,j)*dc_tau(i,j,lh,la,rho), 0)
         for j in range(mg+1)]
        for i in range(mg+1)
    ]


def prob_home_over_05(lh, la, rho, use_dc=True):
    if use_dc:
        p = dc_matrix(lh, la, rho)
        return min(max(1 - sum(p[0][j] for j in range(len(p[0]))), 0.01), 0.99)
    return min(max(1 - poisson_pmf(lh, 0), 0.01), 0.99)


def prob_away_under_15(lh, la, rho, use_dc=True):
    if use_dc:
        p = dc_matrix(lh, la, rho)
        return min(max(sum(p[i][0]+p[i][1] for i in range(len(p))), 0.01), 0.99)
    return min(max(poisson_pmf(la,0)+poisson_pmf(la,1), 0.01), 0.99)


def filter_results(results, range_type, custom_n=10):
    results = [_normalize_result(r) for r in results]
    if range_type == "all":
        return results
    now = date.today()
    if range_type == "2026":
        return [r for r in results if r["date"] >= "2026-01-01"]
    if range_type == "last30d":
        cut = (now - timedelta(days=30)).isoformat()
        return [r for r in results if r["date"] >= cut]
    if range_type == "last60d":
        cut = (now - timedelta(days=60)).isoformat()
        return [r for r in results if r["date"] >= cut]
    if range_type.startswith("last") or range_type == "custom":
        n = custom_n if range_type == "custom" else int(range_type.replace("last",""))
        sorted_r = sorted(results, key=lambda x: x["date"], reverse=True)
        tc, kept = {}, []
        for r in sorted_r:
            h, a = r["home"], r["away"]
            ch, ca = tc.get(h,0), tc.get(a,0)
            if ch < n or ca < n:
                kept.append(r)
                if ch < n: tc[h] = ch+1
                if ca < n: tc[a] = ca+1
        return kept
    return results


def _lam_params(h, a, stats, avg, hf, use_hf):
    hs, as_ = stats.get(h), stats.get(a)
    if not hs or not as_:
        return None, None, None, None, None, None
    hA  = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
    aD  = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
    aA  = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
    hD  = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
    hf_ = hf.get(h,1.0) if use_hf else 1.0
    lh  = (hA*aD)/avg * hf_
    la  = (aA*hD)/avg / hf_
    return lh, la, hA, aD, aA, hD, hf_


def predict_serie_b(results, fixtures, range_type="all", custom_n=10,
                    use_decay=True, use_hf=True, use_dc=True):
    filtered = filter_results(results, range_type, custom_n)
    stats    = calc_stats(filtered, use_decay)
    avg      = league_avg(stats)
    hf       = calc_home_factors(stats, avg) if use_hf else {}
    rho      = estimate_rho(filtered, stats, avg) if use_dc else 0.0
    preds    = []
    for fix in fixtures:
        fix     = _normalize_fixture(fix)
        h, a    = fix["home"], fix["away"]
        hs, as_ = stats.get(h), stats.get(a)
        if not hs or not as_:
            continue
        hA  = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
        aD  = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
        aA  = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
        hD  = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
        hf_ = hf.get(h,1.0) if use_hf else 1.0
        lh  = (hA*aD)/avg * hf_
        la  = (aA*hD)/avg / hf_
        prob = prob_home_over_05(lh, la, rho, use_dc)
        preds.append({"h":h,"a":a,"prob":round(prob,4),"lam":round(lh,2),"lam_a":round(la,2),
                      "hA":round(hA,2),"aD":round(aD,2),"hG":hs["hg"],"aG":as_["ag"],
                      "hf":round(hf_,2),"rho":round(rho,3),"date":fix.get("date","")})
    preds.sort(key=lambda x: x["prob"], reverse=True)
    layers = ["Poisson"]+(["Decay"] if use_decay else [])+(["HF"] if use_hf else [])+(["D-C"] if use_dc else [])
    return {"predictions":preds,"total":len(filtered),"avg":round(avg,2),"rho":round(rho,3),"model":" + ".join(layers)}


def predict_serie_c(results, fixtures, range_type="all", custom_n=10,
                    use_decay=True, use_hf=True, use_dc=True):
    filtered = filter_results(results, range_type, custom_n)
    stats    = calc_stats(filtered, use_decay)
    avg      = league_avg(stats)
    hf       = calc_home_factors(stats, avg) if use_hf else {}
    rho      = estimate_rho(filtered, stats, avg) if use_dc else 0.0
    preds    = []
    for fix in fixtures:
        fix     = _normalize_fixture(fix)
        h, a    = fix["home"], fix["away"]
        hs, as_ = stats.get(h), stats.get(a)
        if not hs or not as_:
            continue
        hA  = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
        aD  = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
        aA  = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
        hD  = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
        hf_ = hf.get(h,1.0) if use_hf else 1.0
        lh  = (hA*aD)/avg * hf_
        la  = (aA*hD)/avg / hf_
        prob = prob_away_under_15(lh, la, rho, use_dc)
        preds.append({"h":h,"a":a,"prob":round(prob,4),"lam":round(la,2),"lam_h":round(lh,2),
                      "aA":round(aA,2),"hD":round(hD,2),"hG":hs["hg"],"aG":as_["ag"],
                      "hf":round(hf_,2),"rho":round(rho,3),"date":fix.get("date","")})
    preds.sort(key=lambda x: x["prob"], reverse=True)
    layers = ["Poisson"]+(["Decay"] if use_decay else [])+(["HF"] if use_hf else [])+(["D-C"] if use_dc else [])
    return {"predictions":preds,"total":len(filtered),"avg":round(avg,2),"rho":round(rho,3),"model":" + ".join(layers)}


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
    rt   = request.args.get("range","all")
    cn   = int(request.args.get("customN",10))
    ud   = request.args.get("decay","1") == "1"
    uh   = request.args.get("hf","1")    == "1"
    udc  = request.args.get("dc","1")    == "1"

    rB = predict_serie_b(data["serieB"].get("results",[]),
                         data["serieB"].get("next_fixtures",[]),
                         rt, cn, ud, uh, udc)
    rB["next_giornata"] = data["serieB"].get("next_giornata",0)

    allC = {}
    for key, gir in [("serieCa","A"),("serieCb","B"),("serieCc","C")]:
        res = predict_serie_c(data[key].get("results",[]),
                              data[key].get("next_fixtures",[]),
                              rt, cn, ud, uh, udc)
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
    elif source == "tuttosport":
        errors, log = scrape_tuttosport(data)
    else:
        errors, log = scrape_calciomagazine(data)

    save_data(data)

    counts = {k: len(data[k].get("results",[])) for k in _LEAGUE_KEYS}
    total  = sum(counts.values())
    src_label = {"wikipedia": "Wikipedia", "tuttosport": "Tuttosport"}.get(source, "calciomagazine.net")
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


# ============================================================
# HTML TEMPLATE
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚽ Pronostici Serie B & C — v9</title>
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

/* ── Header ── */
.header{text-align:center;padding:32px 16px 16px;position:relative;overflow:hidden}
.header::before{content:'';position:absolute;top:0;left:50%;transform:translateX(-50%);
  width:600px;height:200px;background:radial-gradient(ellipse,rgba(34,211,238,0.08),transparent 70%);pointer-events:none}
.header h1{font-family:'Space Mono',monospace;font-size:clamp(1.3rem,3.5vw,2.1rem);font-weight:700;
  background:linear-gradient(135deg,var(--accent),var(--accent2));-webkit-background-clip:text;-webkit-text-fill-color:transparent}
.header p{color:var(--text2);font-size:0.82rem;margin-top:4px}

/* ── Status ── */
.data-status{display:flex;align-items:center;justify-content:center;gap:10px;flex-wrap:wrap;
  margin:10px 0 6px;font-size:0.72rem;font-family:'Space Mono',monospace}
.status-dot{width:8px;height:8px;border-radius:50%;display:inline-block}
.status-dot.ok{background:var(--green);box-shadow:0 0 6px var(--green)}
.status-dot.empty{background:var(--orange);box-shadow:0 0 6px var(--orange)}
.status-dot.loading{background:var(--accent);box-shadow:0 0 6px var(--accent);animation:pulse 1s infinite}
@keyframes pulse{0%,100%{opacity:1}50%{opacity:.4}}
.status-text{color:var(--text2)}.status-text b{color:var(--accent)}

/* ── Source Selector ── */
.source-panel{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:14px 18px;margin:12px 0}
.source-panel h3{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--accent2);
  margin-bottom:10px;letter-spacing:1px;text-transform:uppercase}
.source-btns{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.source-btn{display:flex;align-items:center;gap:8px;padding:8px 16px;border-radius:10px;
  border:1px solid var(--border);background:var(--card2);color:var(--text2);cursor:pointer;
  font-size:0.8rem;font-weight:600;transition:all .2s;user-select:none}
.source-btn:hover{border-color:var(--accent2);color:var(--accent2)}
.source-btn.active{background:rgba(167,139,250,0.15);border-color:var(--accent2);color:var(--accent2);
  box-shadow:0 0 12px rgba(167,139,250,0.2)}
.source-btn .src-dot{width:8px;height:8px;border-radius:50%;background:var(--border)}
.source-btn.active .src-dot{background:var(--accent2);box-shadow:0 0 6px var(--accent2)}
.source-info{font-size:0.7rem;color:var(--text2);margin-top:8px;font-family:'Space Mono',monospace;
  padding:5px 10px;background:var(--card2);border-radius:6px}

/* ── Scrape button + bar ── */
.scrape-bar{display:flex;gap:8px;justify-content:center;margin:14px 0;flex-wrap:wrap}
.btn{padding:8px 18px;border-radius:8px;border:none;font-weight:600;font-size:0.78rem;cursor:pointer;transition:all .2s}
.btn-u{background:linear-gradient(135deg,rgba(34,211,238,0.2),rgba(167,139,250,0.2));
  color:var(--accent);border:1px solid var(--accent);font-size:0.82rem;padding:10px 22px}
.btn-g{background:rgba(52,211,153,0.15);color:var(--green);border:1px solid rgba(52,211,153,0.3)}
.btn-s{background:var(--card2);color:var(--text2);border:1px solid var(--border)}
.btn-d{background:rgba(248,113,113,0.15);color:var(--red);border:1px solid rgba(248,113,113,0.3)}
.btn:hover{opacity:.85;transform:translateY(-1px)}
.btn:disabled{opacity:.5;cursor:not-allowed;transform:none}
.dm-bar{display:flex;gap:8px;justify-content:center;margin:8px 0;flex-wrap:wrap}
.dm-bar .btn{font-size:0.72rem;padding:6px 14px}
.scrape-log{background:var(--card2);border-radius:8px;padding:10px;margin-top:8px;
  font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--text2);
  max-height:250px;overflow-y:auto;display:none}
.scrape-log.show{display:block}

/* ── Tabs ── */
.tabs{display:flex;gap:6px;justify-content:center;margin:12px 0;flex-wrap:wrap}
.tab{padding:9px 20px;border-radius:10px;border:1px solid var(--border);background:var(--card);
  color:var(--text2);cursor:pointer;font-size:0.82rem;font-weight:600;transition:all .2s}
.tab.active,.tab:hover{background:linear-gradient(135deg,rgba(34,211,238,0.15),rgba(167,139,250,0.15));
  border-color:var(--accent);color:var(--accent);box-shadow:var(--glow)}

/* ── Panels ── */
.range-panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin:12px 0}
.range-panel h3{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--accent);
  margin-bottom:10px;letter-spacing:1px;text-transform:uppercase}
.model-panel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:14px 18px;margin:12px 0}
.model-panel h3{font-family:'Space Mono',monospace;font-size:0.75rem;color:var(--accent2);
  margin-bottom:10px;letter-spacing:1px;text-transform:uppercase}
.model-toggles{display:flex;flex-wrap:wrap;gap:10px;align-items:center}
.model-toggle{display:flex;align-items:center;gap:8px;background:var(--card2);border-radius:8px;
  padding:6px 12px;cursor:pointer;transition:all .2s;border:1px solid var(--border);user-select:none}
.model-toggle:hover{border-color:var(--accent2)}
.model-toggle.active{background:rgba(167,139,250,0.12);border-color:var(--accent2)}
.model-toggle .toggle-sw{width:34px;height:18px;border-radius:9px;background:var(--border);
  position:relative;transition:background .2s;flex-shrink:0}
.model-toggle.active .toggle-sw{background:var(--accent2)}
.model-toggle .toggle-sw::after{content:'';position:absolute;top:2px;left:2px;width:14px;height:14px;
  border-radius:50%;background:var(--text2);transition:all .2s}
.model-toggle.active .toggle-sw::after{left:18px;background:#fff}
.model-toggle .toggle-label{font-size:0.75rem;font-weight:500;color:var(--text2)}
.model-toggle.active .toggle-label{color:var(--accent2)}
.model-toggle .toggle-base{font-size:0.75rem;font-weight:600;color:var(--green)}
.model-info{font-family:'Space Mono',monospace;font-size:0.68rem;color:var(--text2);
  margin-top:8px;padding:6px 10px;background:var(--card2);border-radius:6px}
.range-grid{display:flex;flex-wrap:wrap;gap:6px}
.range-btn{padding:6px 14px;border-radius:8px;border:1px solid var(--border);background:var(--card2);
  color:var(--text2);cursor:pointer;font-size:0.75rem;font-weight:500;transition:all .2s}
.range-btn.active,.range-btn:hover{background:rgba(34,211,238,0.12);border-color:var(--accent);color:var(--accent)}
.custom-n{display:none;align-items:center;gap:8px;margin-top:8px}
.custom-n.show{display:flex}
.custom-n input{width:55px;padding:5px 8px;border-radius:6px;border:1px solid var(--border);
  background:var(--card2);color:var(--text);font-size:0.82rem;text-align:center}
.custom-n label{font-size:0.75rem;color:var(--text2)}

/* ── Cards ── */
.cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(310px,1fr));gap:12px;margin:14px 0}
.match-card{background:var(--card);border:1px solid var(--border);border-radius:14px;
  padding:16px;transition:all .3s;position:relative;overflow:hidden}
.match-card:hover{border-color:var(--accent);box-shadow:var(--glow);transform:translateY(-2px)}
.match-card .rank{position:absolute;top:10px;right:12px;font-family:'Space Mono',monospace;
  font-size:0.62rem;color:var(--text2);background:var(--card2);padding:2px 7px;border-radius:6px}
.match-card .teams{font-size:0.95rem;font-weight:600;margin-bottom:8px;line-height:1.4}
.match-card .vs{color:var(--text2);font-weight:400;font-size:0.78rem;margin:0 4px}
.match-card .match-date{font-size:0.68rem;color:var(--text2);font-family:'Space Mono',monospace;margin-bottom:6px}
.prob-container{margin:8px 0}
.prob-label{display:flex;justify-content:space-between;font-size:0.72rem;margin-bottom:3px}
.prob-label .type{color:var(--text2)}.prob-label .pct{font-family:'Space Mono',monospace;font-weight:700}
.prob-bar{height:7px;background:var(--card2);border-radius:4px;overflow:hidden}
.prob-fill{height:100%;border-radius:4px;transition:width .6s ease}
.prob-fill.high{background:linear-gradient(90deg,var(--green),#10b981)}
.prob-fill.mid{background:linear-gradient(90deg,var(--orange),#f59e0b)}
.prob-fill.low{background:linear-gradient(90deg,var(--red),#ef4444)}
.detail-stats{display:grid;grid-template-columns:1fr 1fr;gap:6px;margin-top:8px;font-size:0.7rem}
.detail-stat{background:var(--card2);border-radius:6px;padding:5px 9px}
.detail-stat .dl{color:var(--text2);font-size:0.62rem;text-transform:uppercase;letter-spacing:.5px}
.detail-stat .dv{font-family:'Space Mono',monospace;font-weight:700;color:var(--text);margin-top:1px}

/* ── Misc ── */
.stitle{font-family:'Space Mono',monospace;font-size:1.05rem;font-weight:700;
  margin:24px 0 6px;display:flex;align-items:center;gap:10px}
.stitle .dot{width:8px;height:8px;border-radius:50%;background:var(--accent)}
.ssub{font-size:0.75rem;color:var(--text2);margin-bottom:12px}
.giornata-badge{display:inline-block;background:linear-gradient(135deg,rgba(34,211,238,.15),rgba(167,139,250,.15));
  border:1px solid var(--accent);border-radius:8px;padding:4px 12px;
  font-family:'Space Mono',monospace;font-size:0.78rem;color:var(--accent);margin-bottom:8px}
.st{width:100%;border-collapse:collapse;font-size:0.75rem;margin:10px 0;table-layout:fixed}
.st th{padding:6px 5px;text-align:center;border-bottom:1px solid var(--border);
  color:var(--accent);font-family:'Space Mono',monospace;font-size:0.65rem;text-transform:uppercase;
  overflow:hidden;white-space:nowrap}
.st td{padding:5px 5px;text-align:center;border-bottom:1px solid var(--border);
  overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.st td:nth-child(1){width:28px;color:var(--text2);font-size:0.68rem}
.st td:nth-child(2){text-align:left;font-weight:600;width:auto}
.st td:nth-child(3),.st td:nth-child(4),.st td:nth-child(5),
.st td:nth-child(6),.st td:nth-child(7),.st td:nth-child(8){width:34px}
.st td:nth-child(9){width:36px;font-weight:700;color:var(--accent)}
.st th:nth-child(1){width:28px}
.st th:nth-child(2){text-align:left;width:auto}
.st th:nth-child(3),.st th:nth-child(4),.st th:nth-child(5),
.st th:nth-child(6),.st th:nth-child(7),.st th:nth-child(8){width:34px}
.st th:nth-child(9){width:36px}
.st tr:hover{background:rgba(34,211,238,.04)}
.empty{text-align:center;padding:36px;color:var(--text2);font-size:0.82rem}
.section-sep{border:none;border-top:1px solid var(--border);margin:20px 0}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);
  background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:12px 24px;
  font-size:0.82rem;color:var(--accent);box-shadow:0 8px 30px rgba(0,0,0,.4);
  transition:transform .3s ease;z-index:999;pointer-events:none}
.toast.show{transform:translateX(-50%) translateY(0)}
@media(max-width:600px){
  .container{padding:10px}.cards{grid-template-columns:1fr}.range-grid{gap:4px}
  .range-btn{padding:5px 9px;font-size:.7rem}.match-card{padding:12px}
  .dm-bar,.scrape-bar{flex-direction:column;align-items:stretch}
}
</style>
</head>
<body>
<div class="container">

<!-- ═══ HEADER ═══ -->
<div class="header">
  <h1>⚽ Pronostici Serie B & C</h1>
  <p>Dixon-Coles + Decadimento temporale + Fattore campo — v9</p>
</div>

<div class="data-status" id="dataStatus">
  <span class="status-dot empty"></span>
  <span class="status-text">Caricamento...</span>
</div>

<!-- ═══ SOURCE SELECTOR ═══ -->
<div class="source-panel">
  <h3>🌐 Fonte Dati</h3>
  <div class="source-btns">
    <div class="source-btn active" id="srcCM" onclick="setSource('calciomagazine')">
      <span class="src-dot"></span>calciomagazine.net
    </div>
    <div class="source-btn" id="srcWK" onclick="setSource('wikipedia')">
      <span class="src-dot"></span>Wikipedia (it.)
    </div>
    <div class="source-btn" id="srcTS" onclick="setSource('tuttosport')">
      <span class="src-dot"></span>Tuttosport
    </div>
  </div>
  <div class="source-info" id="sourceInfo">
    📡 Fonte attiva: <b id="srcLabel">calciomagazine.net</b> — risultati e calendari separati
  </div>
</div>

<!-- ═══ SCRAPE ═══ -->
<div class="scrape-bar">
  <button class="btn btn-u" onclick="doScrape()" id="scrapeBtn">🔄 Aggiorna dati</button>
</div>
<div class="scrape-log" id="scrapeLog"></div>

<div class="dm-bar">
  <button class="btn btn-g" onclick="doExport()">📤 Esporta JSON</button>
  <button class="btn btn-s" onclick="document.getElementById('importFile').click()">📥 Importa JSON</button>
  <button class="btn btn-d" onclick="doReset()">🗑️ Reset</button>
  <input type="file" id="importFile" accept=".json" style="display:none" onchange="doImport(event)">
</div>

<!-- ═══ TABS ═══ -->
<div class="tabs">
  <div class="tab active" onclick="switchTab('predictions',this)">📊 Pronostici</div>
  <div class="tab" onclick="switchTab('standings',this)">🏆 Classifiche</div>
</div>

<!-- ═══ RANGE ═══ -->
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

<!-- ═══ MODEL ═══ -->
<div class="model-panel">
  <h3>🧮 Modello Statistico</h3>
  <div class="model-toggles">
    <div class="model-toggle" style="opacity:.7;cursor:default"><span class="toggle-base">📊 Poisson Base</span></div>
    <div class="model-toggle active" id="togDecay" onclick="toggleModel(this,'decay')"><span class="toggle-sw"></span><span class="toggle-label">⏱️ Decadimento</span></div>
    <div class="model-toggle active" id="togHF"    onclick="toggleModel(this,'hf')"><span class="toggle-sw"></span><span class="toggle-label">🏟️ Fattore Campo</span></div>
    <div class="model-toggle active" id="togDC"    onclick="toggleModel(this,'dc')"><span class="toggle-sw"></span><span class="toggle-label">📐 Dixon-Coles</span></div>
  </div>
  <div class="model-info" id="modelInfo">Modello: Poisson + Decay + HF + D-C</div>
</div>

<!-- ═══ PREDICTIONS ═══ -->
<div id="tab_predictions">
  <div class="stitle"><span class="dot"></span>Serie B — CASA OVER 0.5</div>
  <div id="giornataB"></div><div class="ssub" id="statsB"></div><div class="cards" id="cardsB"></div>
  <hr class="section-sep">
  <div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone A — OSPITE UNDER 1.5</div>
  <div id="giornataCa"></div><div class="ssub" id="statsCa"></div><div class="cards" id="cardsCa"></div>
  <hr class="section-sep">
  <div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone B — OSPITE UNDER 1.5</div>
  <div id="giornataCb"></div><div class="ssub" id="statsCb"></div><div class="cards" id="cardsCb"></div>
  <hr class="section-sep">
  <div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C Girone C — OSPITE UNDER 1.5</div>
  <div id="giornataCc"></div><div class="ssub" id="statsCc"></div><div class="cards" id="cardsCc"></div>
</div>

<!-- ═══ STANDINGS ═══ -->
<div id="tab_standings" style="display:none">
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin:10px 0 4px;font-size:0.7rem;color:var(--text2)">
    <span style="display:flex;align-items:center;gap:5px"><span style="width:10px;height:10px;border-radius:2px;background:rgba(52,211,153,0.3);display:inline-block"></span>Promozione diretta</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:10px;height:10px;border-radius:2px;background:rgba(34,211,238,0.15);display:inline-block"></span>Playoff</span>
    <span style="display:flex;align-items:center;gap:5px"><span style="width:10px;height:10px;border-radius:2px;background:rgba(248,113,113,0.2);display:inline-block"></span>Retrocessione</span>
  </div>
  <div class="stitle"><span class="dot"></span>Classifica Serie B</div>
  <div style="overflow-x:auto"><table class="st" id="tableB"></table></div>
  <div id="tablesC"></div>
</div>

</div><!-- /container -->
<div class="toast" id="toast"></div>

<script>
// ── State ──────────────────────────────────────────────────────────────────────
let curRange  = 'all';
let curSource = 'calciomagazine';
let mDecay = true, mHF = true, mDC = true;

const SOURCE_META = {
  calciomagazine: {
    label: 'calciomagazine.net',
    info:  '📡 Fonte attiva: <b>calciomagazine.net</b> — risultati e calendari separati',
  },
  wikipedia: {
    label: 'Wikipedia (it.)',
    info:  '📡 Fonte attiva: <b>Wikipedia (it.)</b> — tabella andata/ritorno combinata',
  },
  tuttosport: {
    label: 'Tuttosport',
    info:  '📡 Fonte attiva: <b>Tuttosport</b> — calendario live (img-alt parser)',
  },
};

// ── Source Selector ────────────────────────────────────────────────────────────
function setSource(src) {
  curSource = src;
  document.querySelectorAll('.source-btn').forEach(b => b.classList.remove('active'));
  const ids = { calciomagazine: 'srcCM', wikipedia: 'srcWK', tuttosport: 'srcTS' };
  document.getElementById(ids[src] || 'srcCM').classList.add('active');
  document.getElementById('sourceInfo').innerHTML = SOURCE_META[src].info;
  const btn = document.getElementById('scrapeBtn');
  btn.textContent = `🔄 Aggiorna da ${SOURCE_META[src].label}`;
}

// ── Helpers ────────────────────────────────────────────────────────────────────
function toast(msg) {
  const t = document.getElementById('toast');
  t.textContent = msg; t.classList.add('show');
  setTimeout(() => t.classList.remove('show'), 2600);
}
function pc(p) { return p >= 0.75 ? 'high' : p >= 0.55 ? 'mid' : 'low'; }
function pcol(p) { return p >= 0.75 ? '#34d399' : p >= 0.55 ? '#fbbf24' : '#f87171'; }
function fmtDate(d) {
  if (!d) return '';
  const [y,m,dd] = d.split('-');
  const days = ['Dom','Lun','Mar','Mer','Gio','Ven','Sab'];
  return days[new Date(+y,m-1,+dd).getDay()] + ' ' + dd + '/' + m + '/' + y;
}

// ── Status ─────────────────────────────────────────────────────────────────────
async function updateStatus() {
  const r = await fetch('/api/status'), d = await r.json();
  const dot = d.total > 0 ? 'ok' : 'empty';
  const ng  = d.next_giornata || {};
  const el  = document.getElementById('dataStatus');
  const srcBadge = {wikipedia:'🌐 Wikipedia', tuttosport:'📰 Tuttosport'};
  const badge = srcBadge[d.source] || '📰 calciomagazine';
  el.innerHTML = `<span class="status-dot ${dot}"></span>
    <span class="status-text">${badge} · 📊 <b>${d.total}</b> risultati · Agg: <b>${d.updatedAt}</b>
    · Prossime: B=${ng.serieB||'?'}ª CA=${ng.serieCa||'?'}ª CB=${ng.serieCb||'?'}ª CC=${ng.serieCc||'?'}ª</span>`;
  // Sync selector to saved source
  if (d.source && d.source !== curSource) setSource(d.source);
}

// ── Scrape ─────────────────────────────────────────────────────────────────────
async function doScrape() {
  const btn = document.getElementById('scrapeBtn');
  const log = document.getElementById('scrapeLog');
  const srcLabel = SOURCE_META[curSource].label;
  btn.disabled = true; btn.textContent = `⏳ Scaricamento da ${srcLabel}...`;
  log.classList.add('show'); log.innerHTML = `Connessione a ${srcLabel}...<br>`;
  const dot = document.querySelector('.status-dot');
  if (dot) dot.className = 'status-dot loading';
  try {
    const r = await fetch('/api/scrape', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({ source: curSource }),
    });
    const d = await r.json();
    log.innerHTML += d.message + '<br>';
    if (d.log) log.innerHTML += d.log.map(l => '  ' + l).join('<br>') + '<br>';
    if (d.errors && d.errors.length) log.innerHTML += '<br>Errori: ' + d.errors.join(', ') + '<br>';
    toast(d.message);
    await updateStatus();
    recalc();
  } catch (e) {
    log.innerHTML += '❌ ' + e.message + '<br>';
    toast('❌ Errore di connessione');
  }
  btn.disabled = false;
  btn.textContent = `🔄 Aggiorna da ${srcLabel}`;
  setTimeout(() => log.classList.remove('show'), 9000);
}

// ── Cards ──────────────────────────────────────────────────────────────────────
function crdB(i, p) {
  const dt = p.date ? `<div class="match-date">📅 ${fmtDate(p.date)}</div>` : '';
  return `<div class="match-card"><div class="rank">#${i+1}</div>
    ${dt}<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
    <div class="prob-container"><div class="prob-label">
      <span class="type">CASA OVER 0.5</span>
      <span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
      <div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
    <div class="detail-stats">
      <div class="detail-stat"><div class="dl">λ Casa / Osp</div><div class="dv">${p.lam} / ${p.lam_a||'—'}</div></div>
      <div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.hA} / ${p.aD}</div></div>
      <div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div>
      <div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div>
    </div></div>`;
}

function crdC(i, p) {
  const dt = p.date ? `<div class="match-date">📅 ${fmtDate(p.date)}</div>` : '';
  return `<div class="match-card"><div class="rank">#${i+1}</div>
    ${dt}<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
    <div class="prob-container"><div class="prob-label">
      <span class="type">OSPITE UNDER 1.5</span>
      <span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
      <div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
    <div class="detail-stats">
      <div class="detail-stat"><div class="dl">λ Osp / Casa</div><div class="dv">${p.lam} / ${p.lam_h||'—'}</div></div>
      <div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.aA} / ${p.hD}</div></div>
      <div class="detail-stat"><div class="dl">🏟️ Campo</div><div class="dv">${p.hf||'—'}</div></div>
      <div class="detail-stat"><div class="dl">ρ D-C</div><div class="dv">${p.rho||'—'}</div></div>
    </div></div>`;
}

// ── Recalc ─────────────────────────────────────────────────────────────────────
async function recalc() {
  const n = document.getElementById('customNval')?.value || 10;
  const r = await fetch(`/api/predict?range=${curRange}&customN=${n}&decay=${mDecay?1:0}&hf=${mHF?1:0}&dc=${mDC?1:0}`);
  const d = await r.json();
  document.getElementById('modelInfo').textContent = 'Modello: ' + (d.serieB.model || 'Poisson');

  // Serie B
  const bG = d.serieB.next_giornata;
  document.getElementById('giornataB').innerHTML = bG ? `<span class="giornata-badge">${bG}ª Giornata</span>` : '';
  document.getElementById('statsB').innerHTML = `📊 <b>${d.serieB.total}</b> partite analizzate · Media gol: <b>${d.serieB.avg}</b>`;
  document.getElementById('cardsB').innerHTML = d.serieB.predictions.length
    ? d.serieB.predictions.map((p,i) => crdB(i,p)).join('')
    : '<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';

  // Serie C gironi
  ['A','B','C'].forEach(gir => {
    const s  = gir.toLowerCase();
    const gd = d.serieC[gir];
    if (!gd) return;
    document.getElementById('giornataC'+s).innerHTML = gd.next_giornata ? `<span class="giornata-badge">${gd.next_giornata}ª Giornata</span>` : '';
    document.getElementById('statsC'+s).innerHTML = `📊 <b>${gd.total}</b> partite analizzate · Media gol: <b>${gd.avg}</b>`;
    document.getElementById('cardsC'+s).innerHTML = gd.predictions.length
      ? gd.predictions.map((p,i) => crdC(i,p)).join('')
      : '<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';
  });
}

// ── Standings ──────────────────────────────────────────────────────────────────
async function renderStandings() {
  const r = await fetch('/api/standings'), d = await r.json();
  function tbl(rows, isSerieC) {
    if (!rows.length) return '<tbody><tr><td colspan="9" style="text-align:center;color:var(--text2);padding:20px">Nessun dato</td></tr></tbody>';
    // Zone colors: top 2 = promotion direct, 3-8 = playoff, bottom 2 = relegation (SerieB) or similar
    const nTeams = rows.length;
    function rowStyle(i) {
      if (i < 2)          return 'background:rgba(52,211,153,0.07)';   // promozione diretta
      if (!isSerieC && i < 8) return 'background:rgba(34,211,238,0.04)'; // playoff SerieB
      if (isSerieC && i < 8)  return 'background:rgba(34,211,238,0.04)'; // playoff SerieC
      if (i >= nTeams - 3) return 'background:rgba(248,113,113,0.07)'; // retrocessione
      return '';
    }
    return `<thead><tr>
      <th>#</th><th>Squadra</th><th>G</th><th>V</th><th>P</th><th>S</th><th>GF</th><th>GS</th><th>Pt</th>
    </tr></thead><tbody>`
      + rows.map(([n, s], i) =>
          `<tr style="${rowStyle(i)}">
            <td>${i+1}</td>
            <td title="${n}">${n}</td>
            <td>${s.g}</td><td>${s.w}</td><td>${s.d}</td><td>${s.l}</td>
            <td>${s.gf}</td><td>${s.ga}</td>
            <td>${s.pts}</td>
          </tr>`
        ).join('')
      + '</tbody>';
  }
  document.getElementById('tableB').innerHTML = tbl(d.serieB, false);
  let h = '';
  [{k:'serieCa',n:'A'},{k:'serieCb',n:'B'},{k:'serieCc',n:'C'}].forEach(g => {
    h += `<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C — Girone ${g.n}</div>
          <div style="overflow-x:auto"><table class="st">${tbl(d[g.k], true)}</table></div>`;
  });
  document.getElementById('tablesC').innerHTML = h;
}

// ── Export / Import / Reset ────────────────────────────────────────────────────
async function doExport() {
  const r = await fetch('/api/export'), d = await r.json();
  const b = new Blob([JSON.stringify(d,null,2)],{type:'application/json'});
  const u = URL.createObjectURL(b);
  const a = document.createElement('a');
  a.href = u; a.download = `pronostici_v9_${d.updatedAt||'export'}.json`; a.click();
  URL.revokeObjectURL(u); toast('📤 Dati esportati!');
}

async function doImport(ev) {
  const f = ev.target.files[0]; if (!f) return;
  const reader = new FileReader();
  reader.onload = async e => {
    try {
      const d = JSON.parse(e.target.result);
      const r = await fetch('/api/import',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(d)});
      const res = await r.json();
      if (res.success) { updateStatus(); recalc(); toast('📥 Dati importati!'); }
      else toast('⚠️ ' + res.error);
    } catch { toast('⚠️ Errore parsing JSON'); }
  };
  reader.readAsText(f); ev.target.value = '';
}

async function doReset() {
  if (!confirm('⚠️ Cancellare tutti i dati?')) return;
  await fetch('/api/reset',{method:'POST'});
  updateStatus(); recalc(); toast('🗑️ Dati cancellati');
}

// ── UI helpers ─────────────────────────────────────────────────────────────────
function toggleModel(el, flag) {
  if (flag==='decay') { mDecay=!mDecay; el.classList.toggle('active',mDecay); }
  if (flag==='hf')    { mHF=!mHF;     el.classList.toggle('active',mHF); }
  if (flag==='dc')    { mDC=!mDC;     el.classList.toggle('active',mDC); }
  recalc();
}

function setRange(r, el) {
  curRange = r;
  document.querySelectorAll('.range-btn').forEach(b => b.classList.remove('active'));
  el.classList.add('active');
  document.getElementById('customN').classList.toggle('show', r==='custom');
  recalc();
}

function switchTab(tab, el) {
  document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
  el.classList.add('active');
  ['predictions','standings'].forEach(t => {
    document.getElementById('tab_'+t).style.display = t===tab ? '' : 'none';
  });
  if (tab === 'standings') renderStandings();
}

// ── Init ───────────────────────────────────────────────────────────────────────
setSource('calciomagazine');
updateStatus();
recalc();
</script>
</body>
</html>"""


# ============================================================
# MAIN
# ============================================================
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
            "⚽ Pronostici Serie B & C — v9",
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
