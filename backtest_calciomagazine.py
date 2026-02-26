#!/usr/bin/env python3
"""
⚽ Backtest Walk-Forward — Pronostici Serie B & C
==================================================
Fonte dati: calciomagazine.net  (fallback: pronostici_data.json locale)

Metodologia walk-forward:
  Per ogni giornata N (dalla 6ª in poi):
    - TRAIN  → risultati giornate 1 … N-1
    - TEST   → predice le partite della giornata N
    - Confronta con il risultato reale già noto

Metriche calcolate per 4 configurazioni del modello:
  Brier Score, Log-Loss, Accuracy@soglia (55/60/65/70/75/80%)
  ROI simulato, Calibrazione, Andamento nel tempo

Output: ./backtest_output/backtest_report.txt
        ./backtest_output/backtest_grafici.png
"""

import json, re, os, math, sys
from datetime import date, timedelta
from pathlib import Path
from collections import defaultdict

# ── dipendenze ────────────────────────────────────────────────
for pkg in ["requests"]:
    try: import requests
    except ImportError: os.system(f"{sys.executable} -m pip install {pkg} -q")
import requests as req

# matplotlib opzionale
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.gridspec as gridspec
    HAS_MPL = True
except ImportError:
    HAS_MPL = False

# ─────────────────────────────────────────────────────────────
# CONFIGURAZIONE
# ─────────────────────────────────────────────────────────────
LEAGUES = {
    "serieB": {
        "name": "Serie B",
        "results_url": "https://www.calciomagazine.net/risultati-serie-b-120385.html",
    },
    "serieCa": {
        "name": "Serie C Girone A",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-a-120404.html",
    },
    "serieCb": {
        "name": "Serie C Girone B",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-b-120417.html",
    },
    "serieCc": {
        "name": "Serie C Girone C",
        "results_url": "https://www.calciomagazine.net/risultati-serie-c-girone-c-120418.html",
    },
}

MIN_TRAIN_GIORNATE = 5     # non testare le prime N giornate (troppo poco training)
DECAY_XI           = 0.010  # v10: emivita 70gg (era 0.005 = 139gg)
RHO_DEFAULT        = -0.13
CALIB_T            = 1.30   # v10: temperature scaling
PRIOR_MIN          = 8      # v10: partite minime per non applicare prior
HF_SHRINK_MAX      = 0.65   # v10: shrinkage max fattore campo
ODD                = 1.85   # quota simulata fissa
OUT_DIR            = Path("./backtest_output")
OUT_DIR.mkdir(exist_ok=True)

# Confronto diretto v9 (vecchio) vs v10 (nuovo) + modello base
CONFIGS = [
    {"label": "Poisson puro",     "decay": False, "hf": False, "dc": False, "calib": False},
    {"label": "v9 completo",      "decay": True,  "hf": True,  "dc": True,  "calib": False},
    {"label": "v10 no-calib",     "decay": True,  "hf": True,  "dc": True,  "calib": False,
     "v10_model": True},   # usa decay/HF/prior v10, senza calibrazione
    {"label": "v10 + Calib",      "decay": True,  "hf": True,  "dc": True,  "calib": True,
     "v10_model": True},   # modello completo v10
]

# ─────────────────────────────────────────────────────────────
# SCRAPING — calciomagazine.net
# ─────────────────────────────────────────────────────────────
def fetch_page(url):
    import urllib3; urllib3.disable_warnings()
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


def extract_text(html):
    if not html: return ""
    import html as hm
    text = re.sub(r"<br\s*/?>", "\n", html)
    text = re.sub(r"</(?:p|div|li|h[1-6]|tr)>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]+", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return hm.unescape(text).strip()


def infer_year(month):
    return 2025 if month >= 7 else 2026


def parse_results(html):
    """
    Estrae i risultati da una pagina risultati di calciomagazine.net.
    Restituisce {giornata_num: [lista result dict]}
    """
    text    = extract_text(html)
    giornate = {}
    cur_g   = 0

    g_re  = re.compile(r"(\d+)[ªa°]\s*Giornata", re.IGNORECASE)
    # pattern principale: DD.MM. ore HH:MM CasaTeam-OspiteTeam G : G
    r_re  = re.compile(
        r"(\d{2})\.(\d{2})\.\s*ore\s*\d{1,2}:\d{2}\s+"
        r"(.+?)\s*[-–]\s*(.+?)\s+(\d+)\s*:\s*(\d+)"
    )
    # pattern alternativo: senza "ore"
    r_re2 = re.compile(
        r"(\d{2})\.(\d{2})\.\s+(.+?)\s*[-–]\s*(.+?)\s+(\d+)\s*:\s*(\d+)"
    )

    for line in text.split("\n"):
        line = line.strip()
        if not line: continue
        if any(w in line.lower() for w in ("rinviata","annullata","sospesa")): continue

        gm = g_re.search(line)
        if gm:
            cur_g = int(gm.group(1))
            giornate.setdefault(cur_g, [])
            continue

        rm = r_re.search(line) or r_re2.search(line)
        if rm and cur_g > 0:
            day, month = int(rm.group(1)), int(rm.group(2))
            home, away = rm.group(3).strip(), rm.group(4).strip()
            hg,   ag   = int(rm.group(5)), int(rm.group(6))
            try:    ds = date(infer_year(month), month, day).isoformat()
            except: ds = f"{infer_year(month)}-{month:02d}-{day:02d}"
            giornate[cur_g].append(
                {"date": ds, "home": home, "away": away, "hg": hg, "ag": ag}
            )

    return giornate   # {1: [r,...], 2: [...], ...}


def scrape_calciomagazine():
    """
    Scarica i risultati da calciomagazine.net per tutti i campionati.
    Ritorna {key: {giornata_num: [results]}}
    """
    print("\n📥 Scarico dati da calciomagazine.net...")
    all_data = {}
    ok = True
    for key, cfg in LEAGUES.items():
        print(f"  → {cfg['name']}...", end=" ", flush=True)
        html = fetch_page(cfg["results_url"])
        if not html:
            print("FALLITO")
            ok = False
            all_data[key] = {}
            continue
        gd = parse_results(html)
        total = sum(len(v) for v in gd.values())
        print(f"{len(gd)} giornate, {total} risultati")
        all_data[key] = gd
    return all_data, ok


# ─────────────────────────────────────────────────────────────
# FALLBACK — pronostici_data.json locale
# ─────────────────────────────────────────────────────────────
def load_from_json():
    for candidate in [
        Path("pronostici_data.json"),
        Path(__file__).parent / "pronostici_data.json",
    ]:
        if candidate.exists():
            print(f"\n  ⚠️  Uso dati locali: {candidate.resolve()}")
            with open(candidate, "r", encoding="utf-8") as f:
                jdata = json.load(f)
            result = {}
            for key in ["serieB", "serieCa", "serieCb", "serieCc"]:
                rbg = jdata.get(key, {}).get("results_by_giornata", {})
                result[key] = {int(k): v for k, v in rbg.items()}
            total = sum(sum(len(v) for v in gd.values()) for gd in result.values())
            print(f"  Caricati {total} risultati dal JSON locale")
            for key in result:
                name = LEAGUES[key]["name"]
                ng = len(result[key])
                nr = sum(len(v) for v in result[key].values())
                print(f"    {name}: {ng} giornate, {nr} risultati")
            return result
    return None


# ─────────────────────────────────────────────────────────────
# MOTORE STATISTICO (identico a pronostici_app.py)
# ─────────────────────────────────────────────────────────────
def poisson_pmf(lam, k):
    if lam <= 0: return 1.0 if k == 0 else 0.0
    return (lam**k) * math.exp(-lam) / math.factorial(k)


def calc_stats(results, use_decay=True):
    today = date.today(); stats = {}
    for r in results:
        h, a, hg, ag = r["home"], r["away"], r["hg"], r["ag"]
        if use_decay:
            try:
                days = max((today - date.fromisoformat(r["date"])).days, 0)
                w = math.exp(-DECAY_XI * days)
            except: w = 0.5
        else: w = 1.0
        for t in (h, a):
            if t not in stats:
                stats[t] = {"hgf_w":0,"hga_w":0,"hw":0,
                            "agf_w":0,"aga_w":0,"aw":0,
                            "hg":0,"ag":0,
                            "home_gf":0,"home_ga":0,"away_gf":0,"away_ga":0}
        stats[h]["hgf_w"]+=hg*w; stats[h]["hga_w"]+=ag*w
        stats[h]["hw"]+=w;       stats[h]["hg"]+=1
        stats[h]["home_gf"]+=hg; stats[h]["home_ga"]+=ag
        stats[a]["agf_w"]+=ag*w; stats[a]["aga_w"]+=hg*w
        stats[a]["aw"]+=w;       stats[a]["ag"]+=1
        stats[a]["away_gf"]+=ag; stats[a]["away_ga"]+=hg
    return stats


def apply_seasonal_prior(stats, avg):
    """v10: per squadre con pochi dati, blend con media lega."""
    corrected = {}
    for team, s in stats.items():
        n_tot = s["hg"] + s["ag"]
        alpha = min(n_tot / PRIOR_MIN, 1.0)
        if alpha >= 1.0:
            corrected[team] = s; continue
        hA = s["hgf_w"]/s["hw"] if s["hw"]>0 else avg
        hD = s["hga_w"]/s["hw"] if s["hw"]>0 else avg
        aA = s["agf_w"]/s["aw"] if s["aw"]>0 else avg
        aD = s["aga_w"]/s["aw"] if s["aw"]>0 else avg
        ns = dict(s)
        if s["hw"]>0:
            ns["hgf_w"] = (alpha*hA + (1-alpha)*avg) * s["hw"]
            ns["hga_w"] = (alpha*hD + (1-alpha)*avg) * s["hw"]
        if s["aw"]>0:
            ns["agf_w"] = (alpha*aA + (1-alpha)*avg) * s["aw"]
            ns["aga_w"] = (alpha*aD + (1-alpha)*avg) * s["aw"]
        corrected[team] = ns
    return corrected


def temperature_scale(prob, T=CALIB_T):
    """v10: temperature scaling per ridurre overconfidence."""
    if T <= 0 or T == 1.0: return prob
    prob = max(min(prob, 0.9999), 0.0001)
    logit = math.log(prob / (1.0 - prob))
    return 1.0 / (1.0 + math.exp(-logit / T))


def league_avg(stats):
    tg = sum(t["hgf_w"] for t in stats.values())
    tw = sum(t["hw"]    for t in stats.values())
    return tg/tw if tw > 0 else 1.2


def calc_home_factors(stats, avg, adaptive=False):
    """Fattore campo. Se adaptive=True usa shrinkage proporzionale ai dati (v10)."""
    th  = sum(t["home_gf"] for t in stats.values())
    ta  = sum(t["away_gf"] for t in stats.values())
    thm = sum(t["hg"]      for t in stats.values())
    tam = sum(t["ag"]      for t in stats.values())
    lhr = (th/thm)/(ta/tam) if thm>0 and tam>0 and ta>0 else 1.2
    factors = {}
    for team, s in stats.items():
        n = s["hg"] + s["ag"]
        if adaptive:
            # v10: shrinkage cresce con i dati, max HF_SHRINK_MAX
            if n >= 5:
                thr = s["home_gf"]/s["hg"] if s["hg"]>0 else ta/tam if tam>0 else 1.0
                tar = s["away_gf"]/s["ag"] if s["ag"]>0 else ta/tam if tam>0 else 1.0
                raw = (thr/tar)/lhr if tar>0 and lhr>0 else 1.0
                alpha = min(n/20, 1.0) * HF_SHRINK_MAX
                factors[team] = alpha*raw + (1-alpha)
            else:
                factors[team] = 1.0
        else:
            # v9: shrinkage fisso 0.7
            if s["hg"]>=3 and s["ag"]>=3:
                thr = s["home_gf"]/s["hg"]
                tar = s["away_gf"]/s["ag"] if s["ag"]>0 else (ta/tam if tam>0 else 1.0)
                raw = (thr/tar)/lhr if tar>0 and lhr>0 else 1.0
                factors[team] = 0.7*raw + 0.3
            else: factors[team] = 1.0
    return factors


def dc_tau(hg, ag, lh, la, rho):
    if hg==0 and ag==0: return 1 - lh*la*rho
    if hg==0 and ag==1: return 1 + lh*rho
    if hg==1 and ag==0: return 1 + la*rho
    if hg==1 and ag==1: return 1 - rho
    return 1.0


def estimate_rho(results, stats, avg):
    if len(results) < 30: return RHO_DEFAULT
    obs = {(0,0):0,(1,1):0}; exp = {(0,0):0.0,(1,1):0.0}; n = 0
    for r in results:
        hs  = stats.get(r["home"]); as_ = stats.get(r["away"])
        if not hs or not as_ or not hs["hw"] or not as_["aw"]: continue
        lh = (hs["hgf_w"]/hs["hw"])*(as_["aga_w"]/as_["aw"])/avg if avg>0 else 1.0
        la = (as_["agf_w"]/as_["aw"])*(hs["hga_w"]/hs["hw"])/avg if avg>0 else 1.0
        k  = (min(r["hg"],2), min(r["ag"],2))
        if k in obs: obs[k]+=1; exp[k]+=poisson_pmf(lh,k[0])*poisson_pmf(la,k[1])
        n += 1
    if n < 30: return RHO_DEFAULT
    rhos = [-(obs[k]/(exp[k]*n)-1)*0.3 for k in [(0,0),(1,1)] if exp[k]>0]
    return round(max(-0.25, min(0.05, sum(rhos)/len(rhos) if rhos else RHO_DEFAULT)), 4)


def dc_matrix(lh, la, rho, mg=8):
    return [[max(poisson_pmf(lh,i)*poisson_pmf(la,j)*dc_tau(i,j,lh,la,rho),0)
             for j in range(mg+1)] for i in range(mg+1)]


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


# ─────────────────────────────────────────────────────────────
# WALK-FORWARD BACKTEST
# ─────────────────────────────────────────────────────────────
def predict_one(h, a, train, prob_fn, use_decay, use_hf, use_dc,
                use_calib=False, v10_model=False):
    stats = calc_stats(train, use_decay)
    avg   = league_avg(stats)
    if v10_model:
        stats = apply_seasonal_prior(stats, avg)
    hf    = calc_home_factors(stats, avg, adaptive=v10_model) if use_hf else {}
    rho   = estimate_rho(train, stats, avg) if use_dc else 0.0
    hs, as_ = stats.get(h), stats.get(a)
    if not hs or not as_: return None
    hA = hs["hgf_w"]/hs["hw"] if hs["hw"]>0 else avg
    aD = as_["aga_w"]/as_["aw"] if as_["aw"]>0 else avg
    aA = as_["agf_w"]/as_["aw"] if as_["aw"]>0 else avg*0.8
    hD = hs["hga_w"]/hs["hw"] if hs["hw"]>0 else avg
    hf_ = hf.get(h, 1.0) if use_hf else 1.0
    lh  = (hA*aD)/avg * hf_
    la  = (aA*hD)/avg / hf_
    prob = prob_fn(lh, la, rho, use_dc)
    if use_calib:
        prob = temperature_scale(prob)
    return prob


def run_backtest(giornate_dict, bet_type, league_name):
    """
    giornate_dict: {giornata_num: [result_dict, ...]}
    bet_type: 'over05' | 'under15'
    """
    prob_fn  = prob_home_over_05  if bet_type == "over05" else prob_away_under_15
    outcome  = (lambda r: 1 if r["hg"] > 0 else 0) if bet_type == "over05" \
               else (lambda r: 1 if r["ag"] <= 1 else 0)

    sorted_g = sorted(giornate_dict.keys())
    by_cfg   = {cfg["label"]: [] for cfg in CONFIGS}

    for i, g_test in enumerate(sorted_g):
        if i < MIN_TRAIN_GIORNATE: continue
        train = [r for g in sorted_g[:i] for r in giornate_dict[g]]
        test  = giornate_dict.get(g_test, [])
        if not test: continue

        for cfg in CONFIGS:
            for r in test:
                p = predict_one(r["home"], r["away"], train, prob_fn,
                                cfg["decay"], cfg["hf"], cfg["dc"],
                                use_calib=cfg.get("calib", False),
                                v10_model=cfg.get("v10_model", False))
                if p is None: continue
                by_cfg[cfg["label"]].append({
                    "prob":     p,
                    "outcome":  outcome(r),
                    "giornata": g_test,
                    "home":     r["home"],
                    "away":     r["away"],
                    "hg":       r["hg"],
                    "ag":       r["ag"],
                    "date":     r.get("date", ""),
                })

    tested_g = len([g for i,g in enumerate(sorted_g)
                    if i >= MIN_TRAIN_GIORNATE and giornate_dict.get(g)])
    n_preds  = len(by_cfg[CONFIGS[-1]["label"]])
    print(f"    {league_name}: {tested_g} giornate testate, {n_preds} previsioni")
    return by_cfg


# ─────────────────────────────────────────────────────────────
# METRICHE
# ─────────────────────────────────────────────────────────────
def brier(preds):
    if not preds: return float("nan")
    return sum((p["prob"]-p["outcome"])**2 for p in preds) / len(preds)

def logloss(preds, eps=1e-7):
    if not preds: return float("nan")
    return -sum(p["outcome"]*math.log(p["prob"]+eps) +
                (1-p["outcome"])*math.log(1-p["prob"]+eps)
                for p in preds) / len(preds)

def acc_at(preds, thr):
    sub = [p for p in preds if p["prob"] >= thr]
    if not sub: return 0.0, 0
    return sum(1 for p in sub if p["outcome"]==1)/len(sub), len(sub)

def roi(preds, thr, odd=ODD):
    sub = [p for p in preds if p["prob"] >= thr]
    if not sub: return 0.0, 0
    inv  = len(sub)
    gain = sum(odd for p in sub if p["outcome"]==1)
    return (gain - inv) / inv * 100, len(sub)

def calibration(preds, n=10):
    bsize = 1.0/n
    bkts  = defaultdict(lambda: {"n":0,"hits":0})
    for p in preds:
        b = min(int(p["prob"]/bsize), n-1)
        bkts[b]["n"]+=1; bkts[b]["hits"]+=p["outcome"]
    return [{"mid":(b+0.5)*bsize,
             "n": bkts[b]["n"],
             "freq": bkts[b]["hits"]/bkts[b]["n"] if bkts[b]["n"]>0 else float("nan")}
            for b in range(n)]

def top_misses(preds, n=10):
    wrong = [p for p in preds if p["outcome"]==0]
    return sorted(wrong, key=lambda x: x["prob"], reverse=True)[:n]


# ─────────────────────────────────────────────────────────────
# REPORT TESTUALE
# ─────────────────────────────────────────────────────────────
SEP  = "═"*74
SEP2 = "━"*74
THRS = [0.55, 0.60, 0.65, 0.70, 0.75, 0.80]

def write_report(all_data, source_label, f=None):
    def w(s=""):
        print(s)
        if f: f.write(s+"\n")

    w(SEP)
    w(f"  BACKTEST WALK-FORWARD — Serie B & C 2025-2026  (v9 vs v10)")
    w(f"  Fonte: {source_label}  |  Training minimo: {MIN_TRAIN_GIORNATE} giornate")
    w(f"  v10: Decay 70gg | Prior stagionale (n<{PRIOR_MIN}) | HF adattivo | Calib T={CALIB_T}")
    w(f"  Generato il: {date.today().isoformat()}")
    w(f"  Quota simulata: {ODD}  |  Modelli confrontati: {len(CONFIGS)}")
    w(SEP)

    for league_name, bet_type, by_cfg in all_data:
        label = "CASA OVER 0.5" if bet_type=="over05" else "OSPITE UNDER 1.5"
        w(); w(SEP2)
        w(f"  {league_name}  —  {label}")
        w(SEP2)

        # ── tabella metriche ─────────────────────────────────
        w()
        w(f"  {'Modello':<33} {'N':>5}  {'Brier':>6}  {'LL':>6}  "
          f"{'Acc55':>8}  {'Acc65':>8}  {'Acc75':>8}")
        w("  " + "─"*70)
        for cfg in CONFIGS:
            preds = by_cfg[cfg["label"]]
            if not preds: continue
            bs       = brier(preds)
            ll       = logloss(preds)
            a55, n55 = acc_at(preds, 0.55)
            a65, n65 = acc_at(preds, 0.65)
            a75, n75 = acc_at(preds, 0.75)
            star = " ◄" if cfg["label"] == CONFIGS[-1]["label"] else ""
            w(f"  {cfg['label']:<33} {len(preds):>5}  {bs:>6.4f}  {ll:>6.4f}  "
              f"{a55*100:>5.1f}%({n55:3d})  {a65*100:>5.1f}%({n65:3d})  "
              f"{a75*100:>5.1f}%({n75:3d}){star}")

        # ── ROI simulato ─────────────────────────────────────
        best   = by_cfg[CONFIGS[-1]["label"]]
        best_l = CONFIGS[-1]["label"]
        if not best: continue
        w()
        w(f"  ROI simulato — {best_l}  (quota {ODD})")
        w(f"  {'Soglia':>7}  {'Scommesse':>10}  {'Vinte':>6}  {'ROI':>8}")
        w("  " + "─"*38)
        for thr in THRS:
            r, nb = roi(best, thr)
            wins = sum(1 for p in best if p["prob"]>=thr and p["outcome"]==1)
            marker = "  ← POSITIVO" if r > 0 else ""
            w(f"  {thr*100:>5.0f}%   {nb:>10}  {wins:>6}  {r:>+7.1f}%{marker}")

        # ── calibrazione ─────────────────────────────────────
        w()
        w(f"  Calibrazione — {best_l}")
        w(f"  {'Prob. prevista':>15}  {'N':>5}  {'Freq. reale':>12}  Grafico")
        w("  " + "─"*56)
        for b in calibration(best, 10):
            if b["n"] == 0: continue
            bar = "█" * int(b["freq"]*20) if not math.isnan(b["freq"]) else ""
            fs  = f"{b['freq']*100:5.1f}%" if not math.isnan(b["freq"]) else "  n/d "
            w(f"  {b['mid']*100:>13.0f}%  {b['n']:>5}  {fs:>12}  {bar}")

        # ── worst misses ─────────────────────────────────────
        misses = top_misses(best, 8)
        if misses:
            w()
            w(f"  Top-8 previsioni più sicure andate storte — {best_l}")
            w(f"  {'Partita':<38}  {'Data':>12}  {'Prob':>5}  Risultato")
            w("  " + "─"*70)
            for p in misses:
                match = f"{p['home']} - {p['away']}"[:36]
                w(f"  {match:<38}  {p['date']:>12}  {p['prob']*100:>4.1f}%  "
                  f"{p['hg']}-{p['ag']}")

    # ── riepilogo finale ─────────────────────────────────────
    w(); w(SEP)
    w("  RIEPILOGO FINALE — modello completo (+ Decay + HF + Dixon-Coles)")
    w(SEP)
    w()
    w(f"  {'Campionato':<26}  {'N':>5}  {'Brier':>6}  {'LL':>6}  "
      f"{'Acc60%':>7}  {'Acc70%':>7}  {'ROI60%':>7}  {'ROI70%':>7}")
    w("  " + "─"*74)
    for league_name, bet_type, by_cfg in all_data:
        preds  = by_cfg[CONFIGS[-1]["label"]]
        if not preds: continue
        bs     = brier(preds)
        ll     = logloss(preds)
        a60, _ = acc_at(preds, 0.60)
        a70, _ = acc_at(preds, 0.70)
        r60, _ = roi(preds, 0.60)
        r70, _ = roi(preds, 0.70)
        w(f"  {league_name:<26}  {len(preds):>5}  {bs:>6.4f}  {ll:>6.4f}  "
          f"{a60*100:>6.1f}%  {a70*100:>6.1f}%  {r60:>+6.1f}%  {r70:>+6.1f}%")
    w()


# ─────────────────────────────────────────────────────────────
# GRAFICI
# ─────────────────────────────────────────────────────────────
CMAP = ["#22d3ee", "#a78bfa", "#34d399", "#fbbf24"]

def make_plots(all_data, source_label):
    if not HAS_MPL:
        print("\n  ℹ️  matplotlib non installato — salto grafici")
        print("     pip install matplotlib")
        return None

    n    = len(all_data)
    v9   = CONFIGS[1]["label"]   # "v9 completo"
    v10c = CONFIGS[3]["label"]   # "v10 + Calib"
    DARK = "#111827"
    COL_V9  = "#fbbf24"
    COL_V10 = "#34d399"

    fig = plt.figure(figsize=(24, 6*n), facecolor="#0a0e17")
    fig.suptitle(
        f"⚽ Backtest Walk-Forward — v9 vs v10  |  {source_label}  ({date.today().isoformat()})",
        color="white", fontsize=13, fontweight="bold", y=1.002)
    gs = gridspec.GridSpec(n, 4, figure=fig, hspace=0.55, wspace=0.38)

    for row, (lname, bet_type, by_cfg) in enumerate(all_data):
        p_v9  = by_cfg.get(v9,  [])
        p_v10 = by_cfg.get(v10c, [])
        label_short = lname.replace("Serie C Girone","Gir.")

        # ── col 0: Brier Score bar — tutti i modelli ─────────
        ax = fig.add_subplot(gs[row, 0])
        ax.set_facecolor(DARK)
        cfg_labels = [c["label"] for c in CONFIGS if by_cfg.get(c["label"])]
        briers_v   = [brier(by_cfg[c["label"]]) for c in CONFIGS if by_cfg.get(c["label"])]
        colors_b   = ["#64748b","#fbbf24","#22d3ee","#34d399"][:len(cfg_labels)]
        bars = ax.barh(cfg_labels, briers_v, color=colors_b, alpha=0.85, height=0.5)
        for bar, val in zip(bars, briers_v):
            ax.text(val+0.001, bar.get_y()+bar.get_height()/2,
                    f"{val:.4f}", va="center", fontsize=8, color="white")
        ax.set_xlabel("Brier Score (↓ meglio)", color="white", fontsize=8)
        ax.set_title(f"{label_short}\nBrier Score — tutti i modelli", color="white", fontsize=9)
        ax.tick_params(colors="white", labelsize=7)
        ax.set_xlim(0, max(briers_v)*1.3 if briers_v else 0.4)
        for sp in ax.spines.values(): sp.set_color("#1e293b")

        # ── col 1: Accuracy per soglia — v9 vs v10 ───────────
        ax = fig.add_subplot(gs[row, 1])
        ax.set_facecolor(DARK)
        thrs_x = [t*100 for t in THRS]
        acc9  = [acc_at(p_v9,  t)[0]*100 for t in THRS]
        acc10 = [acc_at(p_v10, t)[0]*100 for t in THRS]
        n9    = [acc_at(p_v9,  t)[1]     for t in THRS]
        n10   = [acc_at(p_v10, t)[1]     for t in THRS]
        ax.plot(thrs_x, acc9,  "o-", color=COL_V9,  lw=2, ms=5, label="v9")
        ax.plot(thrs_x, acc10, "s-", color=COL_V10, lw=2, ms=5, label="v10")
        for tx, a9, a10, nb in zip(thrs_x, acc9, acc10, n10):
            delta = a10 - a9
            col = COL_V10 if delta >= 0 else "#f87171"
            ax.annotate(f"{delta:+.1f}pp\n(n={nb})", (tx, max(a9,a10)),
                        textcoords="offset points", xytext=(0,6),
                        fontsize=6, color=col, ha="center")
        ax.axhline(50, color="#94a3b8", ls="--", lw=0.7)
        ax.set_xlabel("Soglia (%)", color="white", fontsize=8)
        ax.set_ylabel("Accuracy (%)", color="white", fontsize=8)
        ax.set_title("Accuracy per soglia  v9 vs v10", color="white", fontsize=9)
        ax.legend(fontsize=8, labelcolor="white", facecolor="#1a2235", edgecolor="#1e293b")
        ax.tick_params(colors="white", labelsize=7)
        ax.set_ylim(40, min(100, max(acc9+acc10)+18))
        for sp in ax.spines.values(): sp.set_color("#1e293b")

        # ── col 2: ROI cumulativo soglia 70% — v9 vs v10 ─────
        ax = fig.add_subplot(gs[row, 2])
        ax.set_facecolor(DARK)
        for preds, col, lbl in [(p_v9, COL_V9, "v9"), (p_v10, COL_V10, "v10")]:
            bets = sorted([p for p in preds if p["prob"] >= 0.70],
                          key=lambda x: (x["giornata"], x["date"]))
            if not bets: continue
            inv, gain, cumroi = 0, 0, []
            for p in bets:
                inv  += 1
                gain += ODD if p["outcome"]==1 else 0
                cumroi.append((gain-inv)/inv*100)
            ax.plot(cumroi, color=col, lw=2,
                    label=f"{lbl}  ROI={cumroi[-1]:+.1f}%  (n={len(bets)})", alpha=0.9)
        ax.axhline(0, color="white", ls="--", lw=0.6)
        ax.set_xlabel("Scommesse cronologiche", color="white", fontsize=8)
        ax.set_ylabel("ROI cumulativo (%)", color="white", fontsize=8)
        ax.set_title(f"ROI cumulativo  (soglia ≥70%, quota {ODD})", color="white", fontsize=9)
        ax.legend(fontsize=7, labelcolor="white", facecolor="#1a2235", edgecolor="#1e293b")
        ax.tick_params(colors="white", labelsize=7)
        for sp in ax.spines.values(): sp.set_color("#1e293b")

        # ── col 3: Calibrazione — v9 vs v10 ──────────────────
        ax = fig.add_subplot(gs[row, 3])
        ax.set_facecolor(DARK)
        ax.plot([30,100],[30,100], "--", color="#94a3b8", lw=1, label="Perfetta")
        for preds, col, lbl in [(p_v9, COL_V9,"v9"), (p_v10, COL_V10,"v10")]:
            cal   = calibration(preds, 10)
            mids  = [b["mid"]*100 for b in cal if b["n"]>3]
            freqs = [b["freq"]*100 if not math.isnan(b["freq"]) else 0
                     for b in cal if b["n"]>3]
            sizes = [b["n"]*4 for b in cal if b["n"]>3]
            ns_cal = [b["n"] for b in cal if b["n"]>3]
            ax.scatter(mids, freqs, s=sizes, color=col, alpha=0.85, label=lbl, zorder=5)
            if len(mids) > 1:
                ax.plot(mids, freqs, color=col, lw=1, alpha=0.4)
            for mx, fy, nb in zip(mids, freqs, ns_cal):
                ax.annotate(f"{nb}", (mx, fy), textcoords="offset points",
                            xytext=(3,3), fontsize=6, color=col)
        ax.set_xlim(30, 105); ax.set_ylim(0, 110)
        ax.set_xlabel("Prob. prevista (%)", color="white", fontsize=8)
        ax.set_ylabel("Freq. reale (%)", color="white", fontsize=8)
        ax.set_title("Calibrazione  v9 vs v10  (→ diagonale)", color="white", fontsize=9)
        ax.legend(fontsize=7, labelcolor="white", facecolor="#1a2235", edgecolor="#1e293b")
        ax.tick_params(colors="white", labelsize=7)
        for sp in ax.spines.values(): sp.set_color("#1e293b")

    out = OUT_DIR / "backtest_grafici.png"
    plt.savefig(out, dpi=130, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close()
    print(f"\n  Grafici → {out.resolve()}")
    return out


# ─────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────
def main():
    print(SEP)
    print("  ⚽ BACKTEST WALK-FORWARD — Pronostici Serie B & C 2025-2026")
    print(f"  Metodologia: train su G(1..N-1), test su G(N)  |  min.train={MIN_TRAIN_GIORNATE}")
    print(f"  Modelli confrontati: {len(CONFIGS)}")
    print(SEP)

    # ── 1. Carica dati ────────────────────────────────────────
    raw_data, ok = scrape_calciomagazine()

    # Se qualche campionato è vuoto, prova il JSON locale come integrazione
    total_scraped = sum(sum(len(v) for v in gd.values()) for gd in raw_data.values())
    if total_scraped == 0 or not ok:
        fallback = load_from_json()
        if fallback:
            # Usa fallback solo per i campionati vuoti
            for key in raw_data:
                if not raw_data[key]:
                    raw_data[key] = fallback.get(key, {})
            source_label = "calciomagazine.net + JSON locale"
        else:
            print("\n❌ Nessuna fonte dati disponibile.")
            print("   Soluzioni:")
            print("   1. Verifica la connessione Internet")
            print("   2. Avvia pronostici_app.py, premi 🔄 con fonte CalcioMagazine,")
            print("      poi metti pronostici_data.json nella stessa cartella di questo script")
            return None, None
    else:
        source_label = "calciomagazine.net"

    total = sum(sum(len(v) for v in gd.values()) for gd in raw_data.values())
    print(f"\n  Totale risultati caricati: {total}")

    # ── 2. Backtest ───────────────────────────────────────────
    print(f"\n{SEP}")
    print("  ESECUZIONE BACKTEST")
    print(SEP)
    all_data = []

    for key, bet_type in [("serieB","over05"),
                          ("serieCa","under15"),
                          ("serieCb","under15"),
                          ("serieCc","under15")]:
        name = LEAGUES[key]["name"]
        print(f"\n  ► {name}  ({'CASA OVER 0.5' if bet_type=='over05' else 'OSPITE UNDER 1.5'})...")
        by_cfg = run_backtest(raw_data[key], bet_type, name)
        all_data.append((name, bet_type, by_cfg))

    # ── 3. Report ─────────────────────────────────────────────
    print(f"\n{SEP}")
    print("  RISULTATI")
    print(SEP)
    rep_path = OUT_DIR / "backtest_report.txt"
    with open(rep_path, "w", encoding="utf-8") as f:
        write_report(all_data, source_label, f)

    # ── 4. Grafici ────────────────────────────────────────────
    png_path = make_plots(all_data, source_label)

    print(f"\n  Report  → {rep_path.resolve()}")
    if png_path:
        print(f"  Grafici → {png_path.resolve()}")
    print()
    return all_data, png_path


if __name__ == "__main__":
    main()
