#!/usr/bin/env python3
"""
⚽ Pronostici Serie B & C — v5 Python/Flask
Modello Poisson con aggiornamento automatico da calciomagazine.net
"""

import json, re, os, math, sys
from datetime import datetime, date
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

URLS = {
    "serieB":  "https://www.calciomagazine.net/risultati-serie-b-120385.html",
    "serieCa": "https://www.calciomagazine.net/risultati-serie-c-girone-a-120404.html",
    "serieCb": "https://www.calciomagazine.net/risultati-serie-c-girone-b-120417.html",
    "serieCc": "https://www.calciomagazine.net/risultati-serie-c-girone-c-120418.html",
}

# URLs calciomagazine.net per i calendari (prossime partite)
CALENDARIO_URLS = {
    "serieB":  None,  # Serie B usa il calendario dalla pagina risultati
    "serieCa": "https://www.calciomagazine.net/calendario-serie-c-girone-a-99200.html",
    "serieCb": "https://www.calciomagazine.net/calendario-serie-c-girone-b-99208.html",
    "serieCc": "https://www.calciomagazine.net/calendario-serie-c-girone-c-99209.html",
}

# ============================================================
# SCRAPING & PARSING
# ============================================================
def scrape_page(url):
    """Fetch a page from calciomagazine.net."""
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        r = req.get(url, headers=headers, timeout=20)
        r.raise_for_status()
        return r.text
    except Exception as e:
        return None

def parse_results(html_text):
    """Parse match results from calciomagazine HTML."""
    results = []
    if not html_text:
        return results

    # Pattern: DD.MM. ore HH:MM TeamA-TeamB H : A  (or TeamA-TeamB H : A)
    pattern = r'(\d{2})\.(\d{2})\.\s+(?:ore\s+\d{2}:\d{2}\s+)?(.+?)\s+(\d+)\s*:\s*(\d+)'
    
    for m in re.finditer(pattern, html_text):
        day, month = int(m.group(1)), int(m.group(2))
        teams_str = m.group(3).strip()
        hg, ag = int(m.group(4)), int(m.group(5))

        # Split teams: last hyphen surrounded by spaces
        parts = re.split(r'\s+-\s+', teams_str)
        if len(parts) < 2:
            parts = re.split(r'-(?=[A-Z])', teams_str, maxsplit=1)
        if len(parts) < 2:
            continue

        home = parts[0].strip()
        away = parts[-1].strip() if len(parts) == 2 else '-'.join(parts[1:]).strip()

        # Clean team names
        home = re.sub(r'(?<![Uu\d])\d+$', '', home).strip()
        away = re.sub(r'(?<![Uu\d])\d+$', '', away).strip()
        
        # Normalize
        home = home.replace('Südtirol', 'Sudtirol').replace('FC Südtirol', 'Sudtirol')
        away = away.replace('Südtirol', 'Sudtirol').replace('FC Südtirol', 'Sudtirol')

        # Date: month >= 8 -> 2025, else -> 2026
        year = 2025 if month >= 8 else 2026
        try:
            d = date(year, month, day)
            date_str = d.isoformat()
        except ValueError:
            continue

        results.append([date_str, home, away, hg, ag])

    # Sort by date desc
    results.sort(key=lambda x: x[0], reverse=True)
    return results

def strip_html(html):
    """Remove HTML tags and decode entities to get plain text."""
    text = re.sub(r'<br\s*/?>', '\n', html)
    text = re.sub(r'</(?:p|div|li|tr|h\d)>', '\n', text)
    text = re.sub(r'<[^>]+>', ' ', text)
    # Decode common HTML entities
    for ent, ch in [('&amp;', '&'), ('&lt;', '<'), ('&gt;', '>'),
                     ('&quot;', '"'), ('&#039;', "'"), ('&nbsp;', ' '),
                     ('&#8211;', '-'), ('&#8217;', "'"), ('&rsquo;', "'"),
                     ('&lsquo;', "'"), ('&ndash;', '-'), ('&mdash;', '-'),
                     ('&#224;', 'à'), ('&#232;', 'è'), ('&#236;', 'ì'),
                     ('&#242;', 'ò'), ('&#249;', 'ù')]:
        text = text.replace(ent, ch)
    # Collapse whitespace on each line
    lines = []
    for line in text.split('\n'):
        line = ' '.join(line.split()).strip()
        if line:
            lines.append(line)
    return '\n'.join(lines)

def scrape_fixtures_from_calendario(results_html, calendario_url=None):
    """Extract next fixtures from calciomagazine.net calendario page.
    
    If calendario_url is provided, fetch that directly.
    Otherwise, try to find the calendario link from the results page HTML.
    
    Calendario format (plain text after stripping HTML):
        Nª Giornata
        Giorno DD.MM.YY ore HH:MM Casa-Trasferta
        ...
    """
    cal_html = None
    results_text = ""
    
    if calendario_url:
        # Fetch calendario directly from provided URL
        cal_html = scrape_page(calendario_url)
        if results_html:
            results_text = strip_html(results_html)
    else:
        # Try to find calendario link from results page (original approach for Serie B)
        if not results_html:
            return []
        results_text = strip_html(results_html)
        cal_match = re.search(r'href="(https://www\.calciomagazine\.net/calendario-[^"]+)"', results_html)
        if not cal_match:
            cal_match = re.search(r'href="(/calendario-[^"]+)"', results_html)
            if cal_match:
                cal_url = "https://www.calciomagazine.net" + cal_match.group(1)
            else:
                return []
        else:
            cal_url = cal_match.group(1)
        cal_html = scrape_page(cal_url)
    
    if not cal_html:
        return []
    
    cal_text = strip_html(cal_html)
    
    # Find latest giornata number from the RESULTS page (first one listed = most recent)
    latest_giornata = 0
    if results_text:
        latest_match = re.search(r'(\d+)\s*[ªa°]\s*Giornata', results_text)
        if latest_match:
            latest_giornata = int(latest_match.group(1))
    
    # If we couldn't determine from results, find it from today's date
    if latest_giornata == 0:
        today = date.today()
        # Parse all giornate and find the last one with dates before today
        all_giornate = re.findall(r'(\d+)\s*[ªa°]\s*Giornata', cal_text)
        # Split by giornata headers and check dates
        parts = re.split(r'(\d+)\s*[ªa°]\s*Giornata', cal_text)
        for i in range(1, len(parts) - 1, 2):
            try:
                gnum = int(parts[i].strip())
            except ValueError:
                continue
            block = parts[i + 1]
            # Find dates in this block
            dates_in_block = re.findall(r'(\d{2})\.(\d{2})\.(\d{2,4})', block)
            if dates_in_block:
                last_date_str = dates_in_block[-1]
                d, m, y = int(last_date_str[0]), int(last_date_str[1]), int(last_date_str[2])
                if y < 100:
                    y += 2000
                try:
                    block_date = date(y, m, d)
                    if block_date < today:
                        latest_giornata = gnum
                except ValueError:
                    pass
    
    next_giornata = latest_giornata + 1
    
    # Split calendario plain text by giornata headers
    parts = re.split(r'(\d+)\s*[ªa°]\s*Giornata', cal_text)
    
    # parts = [before, "1", block1, "2", block2, ...]
    fixtures = []
    for i in range(1, len(parts) - 1, 2):
        try:
            gnum = int(parts[i].strip())
        except ValueError:
            continue
        if gnum != next_giornata:
            continue
        
        block = parts[i + 1]
        for line in block.split('\n'):
            line = line.strip()
            if not line:
                continue
            # Stop if we hit another giornata header
            if re.match(r'\d+\s*[ªa°]\s*Giornata', line):
                break
            # Extract date (DD.MM.YY or DD.MM.YYYY) and teams after "ore HH:MM"
            date_match = re.search(r'(\d{2})\.(\d{2})\.(\d{2,4})', line)
            m = re.search(r'ore\s+\d{2}:\d{2}\s+(.+)', line)
            if not m:
                continue
            teams_str = m.group(1).strip()
            # Skip if it has a score (already played)
            if re.search(r'\d+\s*:\s*\d+', teams_str):
                continue
            # Parse fixture date
            fix_date = ""
            if date_match:
                fd, fm, fy = int(date_match.group(1)), int(date_match.group(2)), int(date_match.group(3))
                if fy < 100:
                    fy += 2000
                try:
                    fix_date = date(fy, fm, fd).isoformat()
                except ValueError:
                    pass
            # Split on hyphen. Try " - " first (spaced), then "-"
            if ' - ' in teams_str:
                tparts = teams_str.split(' - ', 1)
            elif '-' in teams_str:
                tparts = teams_str.split('-', 1)
            else:
                continue
            if len(tparts) != 2:
                continue
            h = tparts[0].strip()
            a = tparts[1].strip()
            # Clean trailing footnote numbers but preserve U23/U21 etc
            h = re.sub(r'(?<![Uu\d])\d+$', '', h).strip()
            a = re.sub(r'(?<![Uu\d])\d+$', '', a).strip()
            # Normalize team names
            for old, new in [('Südtirol', 'Sudtirol'), ('FC Südtirol', 'Sudtirol'),
                             ('Südtirol', 'Sudtirol'), ('FC Südtirol', 'Sudtirol'),
                             ('Union Brescia', 'Brescia'), ('Virtus Entella', 'Entella'),
                             ('L.R. Vicenza', 'L.R. Vicenza'),
                             ('Juventus U23', 'Juventus U23'),
                             ('Inter U23', 'Inter U23'),
                             ('Atalanta U23', 'Atalanta U23')]:
                if h == old: h = new
                if a == old: a = new
            if h and a and len(h) > 1 and len(a) > 1:
                fix_obj = {"h": h, "a": a}
                if fix_date:
                    fix_obj["date"] = fix_date
                fixtures.append(fix_obj)
        break  # Found our giornata, stop
    
    return fixtures

def deduplicate_results(results):
    """Remove duplicate results keeping the first occurrence."""
    seen = set()
    unique = []
    for r in results:
        key = (r[0], r[1], r[2], r[3], r[4])
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique

# ============================================================
# DATA MANAGEMENT
# ============================================================
def get_default_data():
    """Return empty data structure."""
    return {
        "version": 5,
        "updatedAt": date.today().isoformat(),
        "serieB":  {"results": [], "fixtures": []},
        "serieCa": {"results": [], "fixtures": []},
        "serieCb": {"results": [], "fixtures": []},
        "serieCc": {"results": [], "fixtures": []},
    }

def get_seed_data():
    """Decode embedded seed data (1039 results from calciomagazine.net, 2026-02-18)."""
    import gzip, base64
    SEED_B64 = "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"
    try:
        return json.loads(gzip.decompress(base64.b64decode(SEED_B64)).decode('utf-8'))
    except Exception:
        return get_default_data()

def load_data():
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return get_default_data()

def save_data(data):
    data["updatedAt"] = date.today().isoformat()
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)

# ============================================================
# POISSON MODEL
# ============================================================
def poisson_pmf(lam, k):
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return (lam ** k) * math.exp(-lam) / math.factorial(k)

def calc_stats(results):
    """Calculate team stats from results."""
    stats = {}
    for r in results:
        _, home, away, hg, ag = r[0], r[1], r[2], r[3], r[4]
        if home not in stats:
            stats[home] = {"hgf": 0, "hga": 0, "hg": 0, "agf": 0, "aga": 0, "ag": 0}
        if away not in stats:
            stats[away] = {"hgf": 0, "hga": 0, "hg": 0, "agf": 0, "aga": 0, "ag": 0}
        stats[home]["hgf"] += hg
        stats[home]["hga"] += ag
        stats[home]["hg"] += 1
        stats[away]["agf"] += ag
        stats[away]["aga"] += hg
        stats[away]["ag"] += 1
    return stats

def league_avg(stats):
    total_goals = sum(t["hgf"] for t in stats.values())
    total_matches = sum(t["hg"] for t in stats.values())
    return total_goals / total_matches if total_matches > 0 else 1.2

def filter_results(results, range_type, custom_n=10):
    """Filter results by range."""
    if range_type == "all":
        return results
    
    now = date.today()
    
    if range_type == "2026":
        return [r for r in results if r[0] >= "2026-01-01"]
    
    if range_type == "last30d":
        from datetime import timedelta
        cutoff = (now - timedelta(days=30)).isoformat()
        return [r for r in results if r[0] >= cutoff]
    
    if range_type == "last60d":
        from datetime import timedelta
        cutoff = (now - timedelta(days=60)).isoformat()
        return [r for r in results if r[0] >= cutoff]
    
    # lastN or custom
    if range_type.startswith("last") or range_type == "custom":
        n = custom_n if range_type == "custom" else int(range_type.replace("last", ""))
        sorted_r = sorted(results, key=lambda x: x[0], reverse=True)
        team_count = {}
        kept = []
        for r in sorted_r:
            h, a = r[1], r[2]
            ch = team_count.get(h, 0)
            ca = team_count.get(a, 0)
            if ch < n or ca < n:
                kept.append(r)
                if ch < n:
                    team_count[h] = ch + 1
                if ca < n:
                    team_count[a] = ca + 1
        return kept
    
    return results

def predict_serie_b(data, range_type="all", custom_n=10):
    """Serie B: Casa Over 0.5"""
    filtered = filter_results(data["serieB"]["results"], range_type, custom_n)
    stats = calc_stats(filtered)
    avg = league_avg(stats)
    predictions = []
    
    for fix in data["serieB"]["fixtures"]:
        h, a = fix["h"], fix["a"]
        hs = stats.get(h)
        as_ = stats.get(a)
        if not hs or not as_:
            continue
        hA = hs["hgf"] / hs["hg"] if hs["hg"] > 0 else avg
        aD = as_["aga"] / as_["ag"] if as_["ag"] > 0 else avg
        lam = (hA * aD) / avg
        prob = min(1 - poisson_pmf(lam, 0), 0.99)
        predictions.append({
            "h": h, "a": a, "prob": round(prob, 4), "lam": round(lam, 2),
            "hA": round(hA, 2), "aD": round(aD, 2), "hG": hs["hg"], "aG": as_["ag"],
            "date": fix.get("date", "")
        })
    
    predictions.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": predictions, "total": len(filtered), "avg": round(avg, 2)}

def predict_serie_c(data, key, range_type="all", custom_n=10):
    """Serie C: Ospite Under 1.5"""
    filtered = filter_results(data[key]["results"], range_type, custom_n)
    stats = calc_stats(filtered)
    avg = league_avg(stats)
    predictions = []
    
    for fix in data[key]["fixtures"]:
        h, a = fix["h"], fix["a"]
        hs = stats.get(h)
        as_ = stats.get(a)
        if not hs or not as_:
            continue
        aA = as_["agf"] / as_["ag"] if as_["ag"] > 0 else avg * 0.8
        hD = hs["hga"] / hs["hg"] if hs["hg"] > 0 else avg
        lam = (aA * hD) / avg
        prob = min(poisson_pmf(lam, 0) + poisson_pmf(lam, 1), 0.99)
        predictions.append({
            "h": h, "a": a, "prob": round(prob, 4), "lam": round(lam, 2),
            "aA": round(aA, 2), "hD": round(hD, 2), "hG": hs["hg"], "aG": as_["ag"],
            "date": fix.get("date", "")
        })
    
    predictions.sort(key=lambda x: x["prob"], reverse=True)
    return {"predictions": predictions, "total": len(filtered), "avg": round(avg, 2)}

def calc_standings(results):
    """Calculate league standings."""
    st = {}
    for r in results:
        _, h, a, hg, ag = r
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
    
    rB = predict_serie_b(data, range_type, custom_n)
    
    allC = []
    totC = 0
    for key, gir in [("serieCa", "A"), ("serieCb", "B"), ("serieCc", "C")]:
        res = predict_serie_c(data, key, range_type, custom_n)
        totC += res["total"]
        for p in res["predictions"]:
            p["gir"] = gir
            allC.append(p)
    allC.sort(key=lambda x: x["prob"], reverse=True)
    
    return jsonify({"serieB": rB, "serieC": {"predictions": allC, "total": totC}})

@app.route("/api/standings")
def api_standings():
    data = load_data()
    return jsonify({
        "serieB": calc_standings(data["serieB"]["results"]),
        "serieCa": calc_standings(data["serieCa"]["results"]),
        "serieCb": calc_standings(data["serieCb"]["results"]),
        "serieCc": calc_standings(data["serieCc"]["results"]),
    })

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    """Scrape all leagues from calciomagazine.net."""
    data = load_data()
    errors = []
    log = []
    
    for key, url in URLS.items():
        html = scrape_page(url)
        if html is None:
            errors.append(f"Errore scaricamento risultati {key}")
            continue
        
        new_results = parse_results(html)
        # Deduplicate results
        new_results = deduplicate_results(new_results)
        
        # Get fixtures: use direct calendario URL if available, otherwise extract from results page
        cal_url = CALENDARIO_URLS.get(key)
        new_fixtures = scrape_fixtures_from_calendario(html, calendario_url=cal_url)
        
        # Replace results entirely if we got a reasonable number
        if len(new_results) > 0:
            data[key]["results"] = new_results
        
        # Update fixtures if we found any
        if new_fixtures:
            data[key]["fixtures"] = new_fixtures
        
        log.append(f"{key}: {len(new_results)} ris, {len(new_fixtures)} fix")
    
    save_data(data)
    
    counts = {k: len(data[k]["results"]) for k in URLS}
    fix_counts = {k: len(data[k]["fixtures"]) for k in URLS}
    total = sum(counts.values())
    total_fix = sum(fix_counts.values())
    
    if errors:
        msg = f"⚠️ {'; '.join(errors)}"
    else:
        msg = f"✅ {total} risultati · {total_fix} prossime partite"
    
    return jsonify({
        "success": len(errors) == 0,
        "total": total,
        "counts": counts,
        "fixtures": fix_counts,
        "errors": errors,
        "log": log,
        "message": msg
    })


@app.route("/api/fixtures", methods=["POST"])
def api_save_fixtures():
    """Save fixtures."""
    data = load_data()
    body = request.json
    for key in ["serieB", "serieCa", "serieCb", "serieCc"]:
        if key in body:
            data[key]["fixtures"] = body[key]
    save_data(data)
    return jsonify({"success": True})

@app.route("/api/add_results", methods=["POST"])
def api_add_results():
    """Add manual results."""
    data = load_data()
    body = request.json
    league = body.get("league", "serieB")
    new_results = body.get("results", [])
    
    existing = {(r[0], r[1], r[2]) for r in data[league]["results"]}
    added = 0
    for r in new_results:
        if len(r) >= 5:
            k = (r[0], r[1], r[2])
            if k not in existing:
                data[league]["results"].append([r[0], r[1], r[2], int(r[3]), int(r[4])])
                existing.add(k)
                added += 1
    
    data[league]["results"].sort(key=lambda x: x[0], reverse=True)
    save_data(data)
    return jsonify({"success": True, "added": added})

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
    save_data(get_seed_data())
    return jsonify({"success": True})

@app.route("/api/status")
def api_status():
    data = load_data()
    counts = {k: len(data[k]["results"]) for k in ["serieB", "serieCa", "serieCb", "serieCc"]}
    fix_counts = {k: len(data[k]["fixtures"]) for k in ["serieB", "serieCa", "serieCb", "serieCc"]}
    return jsonify({
        "updatedAt": data.get("updatedAt", "N/A"),
        "total": sum(counts.values()),
        "counts": counts,
        "fixtures": fix_counts,
    })


# ============================================================
# HTML TEMPLATE
# ============================================================
HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="it">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>⚽ Pronostici Serie B & C — v5 Python</title>
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
.match-card .girone{font-size:0.62rem;color:var(--accent2);font-family:'Space Mono',monospace;margin-bottom:4px}
.match-card .match-date{font-size:0.68rem;color:var(--text2);font-family:'Space Mono',monospace;margin-bottom:6px;display:flex;align-items:center;gap:5px}
.match-card .match-date .date-icon{opacity:0.5}
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
.epanel{background:var(--card);border:1px solid var(--border);border-radius:14px;padding:18px;margin:12px 0}
.epanel h3{font-family:'Space Mono',monospace;font-size:0.82rem;color:var(--accent);margin-bottom:8px}
.epanel p{font-size:0.75rem;color:var(--text2);margin-bottom:8px;line-height:1.6}
.epanel textarea{width:100%;min-height:120px;padding:10px;border-radius:8px;border:1px solid var(--border);background:var(--card2);color:var(--text);font-family:'Space Mono',monospace;font-size:0.72rem;resize:vertical}
.epanel code{background:var(--card2);padding:2px 6px;border-radius:4px;font-size:0.72rem;color:var(--accent)}
.btn-row{display:flex;gap:8px;margin-top:10px;flex-wrap:wrap}
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
.info-box{background:linear-gradient(135deg,rgba(34,211,238,0.05),rgba(167,139,250,0.05));border:1px solid rgba(34,211,238,0.15);border-radius:12px;padding:14px 18px;margin:12px 0;font-size:0.75rem;color:var(--text2);line-height:1.7}
.info-box strong{color:var(--accent)}
.toast{position:fixed;bottom:20px;left:50%;transform:translateX(-50%) translateY(80px);background:var(--card);border:1px solid var(--accent);border-radius:10px;padding:12px 24px;font-size:0.82rem;color:var(--accent);box-shadow:0 8px 30px rgba(0,0,0,0.4);transition:transform .3s ease;z-index:999;pointer-events:none}
.toast.show{transform:translateX(-50%) translateY(0)}
.scrape-log{background:var(--card2);border-radius:8px;padding:10px;margin-top:8px;font-family:'Space Mono',monospace;font-size:0.7rem;color:var(--text2);max-height:200px;overflow-y:auto;display:none}
.scrape-log.show{display:block}
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
<p>Modello Poisson — v5 Python/Flask + aggiornamento automatico</p>
</div>

<div class="data-status" id="dataStatus">
<span class="status-dot empty"></span>
<span class="status-text">Caricamento...</span>
</div>

<!-- UPDATE BUTTON -->
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
<div class="stitle"><span class="dot"></span>Serie B — CASA OVER 0.5</div>
<div class="ssub" id="statsB"></div>
<div class="cards" id="cardsB"></div>
<div class="stitle"><span class="dot" style="background:var(--accent2)"></span>Serie C — OSPITE UNDER 1.5</div>
<div class="ssub" id="statsC"></div>
<div class="cards" id="cardsC"></div>
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
    el.innerHTML=`<span class="status-dot ${dot}"></span><span class="status-text">📊 <b>${d.total}</b> risultati (B:${d.counts.serieB} · CA:${d.counts.serieCa} · CB:${d.counts.serieCb} · CC:${d.counts.serieCc}) · Agg: <b>${d.updatedAt}</b></span>`;
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
        if(d.counts){
            log.innerHTML+=`Risultati — B: ${d.counts.serieB} · CA: ${d.counts.serieCa} · CB: ${d.counts.serieCb} · CC: ${d.counts.serieCc}<br>`;
            log.innerHTML+=`Totale: <b>${d.total}</b> risultati<br>`;
        }
        if(d.fixtures){
            log.innerHTML+=`Fixtures — B: ${d.fixtures.serieB} · CA: ${d.fixtures.serieCa} · CB: ${d.fixtures.serieCb} · CC: ${d.fixtures.serieCc}<br>`;
        }
        if(d.log){log.innerHTML+=d.log.join('<br>')+'<br>'}
        if(d.errors&&d.errors.length>0){log.innerHTML+='Errori: '+d.errors.join(', ')+'<br>'}
        toast(d.message);
        await updateStatus();
        recalc();
    }catch(e){
        log.innerHTML+='❌ Errore: '+e.message+'<br>';
        toast('❌ Errore di connessione');
    }
    btn.disabled=false;btn.textContent='🔄 Aggiorna da calciomagazine.net';
    setTimeout(()=>log.classList.remove('show'),5000);
}

// ============================================================
// PREDICTIONS
// ============================================================
function fmtDate(d){if(!d)return'';const[y,m,dd]=d.split('-');const days=['Dom','Lun','Mar','Mer','Gio','Ven','Sab'];const dt=new Date(y,m-1,dd);return days[dt.getDay()]+' '+dd+'/'+m+'/'+y}

function crdB(i,p){
const dateHtml=p.date?`<div class="match-date"><span class="date-icon">📅</span>${fmtDate(p.date)}</div>`:'';
return`<div class="match-card"><div class="rank">#${i+1}</div>
${dateHtml}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container"><div class="prob-label"><span class="type">CASA OVER 0.5</span>
<span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
<div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
<div class="detail-stats">
<div class="detail-stat"><div class="dl">λ Casa</div><div class="dv">${p.lam}</div></div>
<div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.hA} / ${p.aD}</div></div>
<div class="detail-stat"><div class="dl">P. Casa</div><div class="dv">${p.hG}</div></div>
<div class="detail-stat"><div class="dl">P. Trasf.</div><div class="dv">${p.aG}</div></div>
</div></div>`}

function crdC(i,p){
const dateHtml=p.date?`<div class="match-date"><span class="date-icon">📅</span>${fmtDate(p.date)}</div>`:'';
return`<div class="match-card"><div class="rank">#${i+1}</div>
<div class="girone">GIRONE ${p.gir}</div>
${dateHtml}
<div class="teams">${p.h} <span class="vs">vs</span> ${p.a}</div>
<div class="prob-container"><div class="prob-label"><span class="type">OSPITE UNDER 1.5</span>
<span class="pct" style="color:${pcol(p.prob)}">${(p.prob*100).toFixed(1)}%</span></div>
<div class="prob-bar"><div class="prob-fill ${pc(p.prob)}" style="width:${p.prob*100}%"></div></div></div>
<div class="detail-stats">
<div class="detail-stat"><div class="dl">λ Ospite</div><div class="dv">${p.lam}</div></div>
<div class="detail-stat"><div class="dl">Att / Def</div><div class="dv">${p.aA} / ${p.hD}</div></div>
<div class="detail-stat"><div class="dl">P. Casa</div><div class="dv">${p.hG}</div></div>
<div class="detail-stat"><div class="dl">P. Trasf.</div><div class="dv">${p.aG}</div></div>
</div></div>`}

async function recalc(){
    const cn=document.getElementById('customNval');
    const n=cn?cn.value:10;
    const r=await fetch(`/api/predict?range=${curRange}&customN=${n}`);
    const d=await r.json();
    
    document.getElementById('statsB').innerHTML=`📊 <b>${d.serieB.total}</b> partite · Media: <b>${d.serieB.avg}</b> gol/p`;
    document.getElementById('cardsB').innerHTML=d.serieB.predictions.length?
        d.serieB.predictions.map((p,i)=>crdB(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';
    
    document.getElementById('statsC').innerHTML=`📊 <b>${d.serieC.total}</b> partite (3 gironi) · ${d.serieC.predictions.length} prossime`;
    document.getElementById('cardsC').innerHTML=d.serieC.predictions.length?
        d.serieC.predictions.map((p,i)=>crdC(i,p)).join(''):'<div class="empty">Nessun dato — premi 🔄 per scaricare</div>';
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
    a.href=u;a.download=`pronostici_backup_${d.updatedAt||'export'}.json`;a.click();URL.revokeObjectURL(u);
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
# MAIN — seed initial data if file doesn't exist
# ============================================================
if __name__ == "__main__":
    import threading

    # Init data
    if not DATA_FILE.exists():
        seed = get_seed_data()
        total = sum(len(seed[k]["results"]) for k in ["serieB", "serieCa", "serieCb", "serieCc"])
        print(f"📂 Primo avvio — caricati {total} risultati iniziali (calciomagazine.net 2026-02-18)")
        save_data(seed)
    else:
        d = load_data()
        total = sum(len(d[k]["results"]) for k in ["serieB", "serieCa", "serieCb", "serieCc"])
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
            "⚽ Pronostici Serie B & C",
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
