# ⚽ Pronostici Serie B & C — v11

Applicazione Flask/Python per il calcolo di probabilità statistiche su partite di **Serie B** e **Serie C** (Gironi A, B, C) del campionato italiano.

Il modello si basa sul **NuovoMetodo** — media pesata sulla forma recente con bonus prestazione — e produce due tipologie di pronostico:

| Campionato | Scommessa analizzata |
|---|---|
| Serie B | **Casa Over 0.5** — la squadra di casa segna almeno 1 gol |
| Serie C Girone A/B/C | **Ospite Under 1.5** — l'ospite segna 0 o 1 gol |

---

## File del progetto

```
pronostici_app_v11.py      ← app principale (unico file, tutto incluso)
pronostici_data.json       ← database locale (creato al primo avvio)
pronostici_app_v11.spec    ← configurazione PyInstaller per build exe
README.md
```

---

## Requisiti

- Python 3.9+
- Dipendenze:

```
flask
requests
beautifulsoup4
pywebview        # opzionale — per la finestra desktop nativa
```

Installazione (macOS con Homebrew richiede un virtual environment):

```bash
cd ~/Documents/GitHub/ScommettiamoChe
python3 -m venv venv
source venv/bin/activate
pip install flask requests beautifulsoup4 pywebview
```

---

## Avvio

```bash
# macOS (con venv)
source venv/bin/activate
python3 pronostici_app_v11.py

# Windows
python pronostici_app_v11.py
```

- Con **pywebview** → finestra desktop nativa
- Senza pywebview → browser su `http://localhost:5050`

---

## Fonti dati

L'app supporta due fonti dati, selezionabili dall'interfaccia.

### 1. calciomagazine.net *(default)*

Due richieste HTTP per campionato: risultati + calendario separati. Unica fonte che estrae l'**orario della partita**.

### 2. Wikipedia (it.)

Due richieste totali (pagina Serie B + pagina Serie C). Il parser Serie C gestisce il formato andata/ritorno combinato, invertendo correttamente `hg`/`ag` per le gare di ritorno.

### URL personalizzabili

Il bottone **⚙️ URL Fonti** nell'interfaccia apre un pannello dove è possibile modificare tutti gli URL di scraping. Questo permette di adattare l'app alla prossima stagione senza modificare il codice sorgente.

Gli URL personalizzati vengono salvati nel database JSON e usati al successivo aggiornamento. Se un URL non è impostato, il sistema usa il default hardcoded. Il bottone **↩️ Ripristina default** riporta tutti gli URL ai valori originali.

Struttura nel JSON:

```json
{
  "urls": {
    "calciomagazine": {
      "serieB":  { "results_url": "...", "calendar_url": "..." },
      "serieCa": { "results_url": "...", "calendar_url": "..." },
      "serieCb": { "results_url": "...", "calendar_url": "..." },
      "serieCc": { "results_url": "...", "calendar_url": "..." }
    },
    "wikipedia": {
      "serieB": "https://it.wikipedia.org/wiki/Serie_B_2025-2026",
      "serieC": "https://it.wikipedia.org/wiki/Serie_C_2025-2026"
    }
  }
}
```

---

## Modello statistico — NuovoMetodo

### Principio

Per ogni squadra si prendono le ultime N partite (casa o trasferta) e si dividono in due gruppi: le più recenti (peso alto) e le precedenti (peso basso).

### Formula

```
NM = (Σ score_recenti × peso_recente + Σ score_precedenti × peso_precedente) / denominatore
```

dove `denominatore = n_recenti × max_score × peso_recente + n_precedenti × max_score × peso_precedente`.

### Parametri

Serie B e Serie C hanno pannelli parametri **indipendenti**. I parametri della Serie C si applicano a tutti e 3 i gironi.

| Parametro | Default B | Default C | Range | Descrizione |
|---|---|---|---|---|
| Partite recenti | 3 | 3 | 1–10 | Ultime N partite nel gruppo ad alto peso |
| Partite precedenti | 3 | 3 | 0–10 | N partite nel gruppo a basso peso |
| Peso recenti | 1.30 | 1.30 | 0.50–2.00 | Moltiplicatore per il gruppo recente |
| Peso precedenti | 0.70 | 0.70 | 0.10–1.50 | Moltiplicatore per il gruppo precedente |
| Bonus prestazione | 0.25 | 0.15 | 0.00–1.00 | Punteggio graduato per prestazioni oltre soglia |

### Bonus prestazione

Con `bonus = 0` ogni partita vale 1 (soddisfa la condizione) o 0 (non la soddisfa). Con bonus > 0, le prestazioni più marcate ricevono un punteggio superiore:

**Serie B (over — premia chi segna di più):**

| Gol segnati/subiti | Punteggio |
|---|---|
| 0 | 0 |
| 1 | 1.0 |
| 2+ | 1.0 + bonus |

**Serie C (under — premia chi subisce/segna meno):**

| Gol segnati/subiti | Punteggio |
|---|---|
| 0 | 1.0 + bonus |
| 1 | 1.0 |
| 2+ | 0 |

Il punteggio massimo per partita diventa `1 + bonus`, e il denominatore scala di conseguenza. Con `bonus = 0` il modello è equivalente al conteggio binario puro.

Esempio con `bonus = 0.25`: una squadra che segna sempre 1 gol ottiene NM inferiore a una che ne segna sempre 2+, anche se entrambe soddisfano "over 0.5".

### Le metriche

| Metrica | Contesto | Condizione |
|---|---|---|
| 🏠 **Casa OV0.5** | Partite in casa | La squadra ha segnato ≥ 1 gol |
| 🏠 **Casa SU U1.5** | Partite in casa | La squadra ha subito ≤ 1 gol |
| ✈️ **Osp GF U1.5** | Partite in trasferta | La squadra ha segnato ≤ 1 gol |
| ✈️ **Osp SU OV0.5** | Partite in trasferta | La squadra ha subito ≥ 1 gol |

### Probabilità combinata

| Campionato | Formula card | Significato |
|---|---|---|
| Serie B | `Casa OV0.5 × Osp SU OV0.5` | La casa segna **e** l'ospite subisce |
| Serie C | `Casa SU U1.5 × Osp GF U1.5` | La casa non subisce **e** l'ospite non segna |

### Dati insufficienti

Se una squadra ha meno partite del necessario: con 0 partite casa/trasferta la previsione viene esclusa; con 1-2 partite il calcolo usa solo quelle disponibili (risultati potenzialmente estremi).

---

### Rating Elo — informativo

Punteggio di forza relativa basato su vittorie/sconfitte. Tutte le squadre partono da 1500. Non influenza il calcolo delle probabilità.

| Rating | Stelle |
|---|---|
| ≥ 1650 | ★★★ |
| 1560–1649 | ★★ |
| 1480–1559 | ★ |
| 1400–1479 | ~ |
| < 1400 | ▼ |

---

## Interfaccia

### Tab Pronostici

Ogni card mostra:
- **Percentuale combinata** con barra colorata (🔵 100% · 🟢 ≥ 70% · 🟡 50–69% · 🔴 < 50%)
- **2 metriche NM** che compongono la probabilità (Serie B: Casa OV0.5 + Osp SU OV0.5 · Serie C: Casa SU U1.5 + Osp GF U1.5)
- **Elo** informativo per entrambe le squadre

I pannelli parametri sono posizionati subito prima della sezione che controllano: parametri B prima delle card Serie B, parametri C prima delle card Serie C.

### Tab Classifiche

Tabella per ogni campionato con G, V, P, S, GF, GS, Pt.

---

## API Flask

| Metodo | Endpoint | Descrizione |
|---|---|---|
| GET | `/` | Interfaccia web |
| GET | `/api/status` | Stato dati |
| GET | `/api/predict` | Pronostici con parametri NM |
| GET | `/api/standings` | Classifiche |
| POST | `/api/scrape` | Scarica/aggiorna dati |
| GET | `/api/export` | Esporta JSON |
| POST | `/api/import` | Importa JSON |
| POST | `/api/reset` | Cancella dati |
| GET | `/api/urls` | Leggi URL fonti (default + custom) |
| POST | `/api/urls` | Salva URL fonti custom |

### Parametri `/api/predict`

| Param | Default | Descrizione |
|---|---|---|
| `nRecentB` / `nRecentC` | 3 | Partite recenti |
| `nPrevB` / `nPrevC` | 3 | Partite precedenti |
| `wRecentB` / `wRecentC` | 1.3 | Peso recenti |
| `wPrevB` / `wPrevC` | 0.7 | Peso precedenti |
| `bonusB` / `bonusC` | 0.0 | Bonus prestazione |

---

## Distribuzione come eseguibile

### Windows (.exe)

```bash
py -3.12 -m pip install pyinstaller flask requests beautifulsoup4 pywebview
py -3.12 -m PyInstaller pronostici_app_v11.spec
```

### macOS

```bash
pip install pyinstaller
python3 -m PyInstaller pronostici_app_v11.spec
```

PyInstaller genera eseguibili solo per il sistema su cui gira (non cross-platform).

### File dati a runtime

`pronostici_data.json` viene sempre letto/scritto accanto all'eseguibile. Log su `pronostici.log`.

---

## Note tecniche

- Server Flask su `127.0.0.1:5050` (solo locale)
- Thread Flask daemon: si chiude con la finestra
- Fetch HTTP con `verify=False` per compatibilità con proxy aziendali
- BeautifulSoup usa `html.parser` della stdlib
- `_filter_future_fixtures()` esclude fixture con data passata
- macOS con Homebrew Python richiede virtual environment

---

## Changelog

### v11 — NuovoMetodo
- Modello completamente nuovo basato su frequenze empiriche pesate
- Parametri separati per Serie B e Serie C (5 slider ciascuno)
- Bonus prestazione: punteggio graduato per over/under performance
- URL fonti personalizzabili dall'interfaccia (⚙️ URL Fonti)
- Card semplificate: solo le 2 metriche rilevanti + probabilità combinata + Elo
- Colore azzurro per probabilità 100%
- Rimosso Poisson, Dixon-Coles, Decay, Fattore Campo, Calibrazione
- Rimosso Tuttosport come fonte dati
- Da 2171 righe (v10) a ~1500 righe

### v10 — Calibrazione + Backtest
- Calibrazione via temperature scaling
- Backtest walk-forward
- Fix inversione gol ritorno Serie C Wikipedia
