# ⚽ Pronostici Serie B & C — v10

Applicazione Flask/Python per il calcolo di probabilità statistiche su partite di **Serie B** e **Serie C** (Gironi A, B, C) del campionato italiano 2025-2026.

Il modello si basa su **distribuzione di Poisson** con correzioni avanzate (Dixon-Coles, decadimento temporale, fattore campo, calibrazione) e produce due tipologie di pronostico:

| Campionato | Scommessa analizzata | Soglia consigliata |
|---|---|---|
| Serie B | **Casa Over 0.5** — la squadra di casa segna almeno 1 gol | ≥ 65% |
| Serie C Girone A | **Ospite Under 1.5** — l'ospite segna 0 o 1 gol | ≥ 70% |
| Serie C Girone B | **Ospite Under 1.5** | ≥ 72% |
| Serie C Girone C | **Ospite Under 1.5** | ≥ 72% |

> Le soglie consigliate sono ricavate dal backtest walk-forward su dati reali 2025-2026 (quota simulata 1.85).

---

## File del progetto

```
pronostici_app_v10.py      ← app principale (unico file, tutto incluso)
backtest_calciomagazine.py ← script backtest walk-forward separato
pronostici_data.json       ← database locale (creato al primo avvio)
README.md
```

---

## Requisiti

- Python 3.9+
- Dipendenze (installate automaticamente al primo avvio se mancanti):

```
flask
requests
beautifulsoup4
pywebview        # opzionale — per la finestra desktop nativa
matplotlib       # opzionale — richiesto da backtest_calciomagazine.py
```

Installazione manuale:

```bash
pip install flask requests beautifulsoup4 pywebview matplotlib
```

---

## Avvio

```bash
python pronostici_app_v10.py
```

- Con **pywebview** installato → si apre automaticamente una finestra desktop
- Senza pywebview → si apre il browser predefinito su `http://localhost:5050`
- Il server Flask gira in background sulla porta **5050**

> ⚠️ L'app richiede un PC o Mac con Python installato. Non è compatibile con iOS/Android.

---

## Fonti dati

### 1. calciomagazine.net *(default)*

Due richieste HTTP per campionato: risultati + calendario separati.

| Campionato | URL risultati |
|---|---|
| Serie B | `/risultati-serie-b-120385.html` |
| Serie C Girone A | `/risultati-serie-c-girone-a-120404.html` |
| Serie C Girone B | `/risultati-serie-c-girone-b-120417.html` |
| Serie C Girone C | `/risultati-serie-c-girone-c-120418.html` |

Parser: estrazione testo grezzo → regex su pattern `DD.MM. ore HH:MM TeamA-TeamB G : G`.

Unica fonte che estrae anche l'**orario della partita** (es. "20:30"), mostrato sulla card.

### 2. Wikipedia (it.)

Due richieste totali (pagina Serie B + pagina Serie C).

- `https://it.wikipedia.org/wiki/Serie_B_2025-2026`
- `https://it.wikipedia.org/wiki/Serie_C_2025-2026`

Parser: BeautifulSoup su tabelle HTML.

**Formato Serie C — andata/ritorno combinato:** ogni riga della tabella Wikipedia contiene sia il risultato dell'andata che quello del ritorno. Lo schema mostra sempre i punteggi nell'ordine `Squadra_A − Squadra_B` indipendentemente da chi gioca in casa nel ritorno. Il parser inverte correttamente `hg`/`ag` per le gare di ritorno:

```
Andata:  home=Squadra_A, away=Squadra_B  → hg=sc[0], ag=sc[1]  ✅
Ritorno: home=Squadra_B, away=Squadra_A  → hg=sc[1], ag=sc[0]  ✅ (invertito)
```

> ⚠️ Senza questa inversione i gol segnati/subiti del ritorno verrebbero attribuiti alla squadra
> sbagliata, alterando le statistiche di attacco e difesa con effetti diretti sui λ e sulle probabilità.

---

## Modello statistico

Il sistema è composto da due livelli distinti: il **modello probabilistico** (controllato dai toggle) e il **rating Elo** (puramente informativo).

---

### Modello probabilistico — 5 layer in cascata

Ogni layer è attivabile/disattivabile indipendentemente dalla UI tramite toggle. Con tutti i toggle spenti il modello usa solo il **Poisson Base**.

```
[1] Poisson Base → [2] + Decay → [3] + Fattore Campo → [4] + Dixon-Coles → [5] + Calibrazione
```

#### [1] Poisson Base *(sempre attivo)*

Stima i lambda (gol attesi) per ogni partita:

```
λ_casa   = (Att_casa × Def_ospite) / Media_lega
λ_ospite = (Att_ospite × Def_casa) / Media_lega
```

Da questi lambda deriva la probabilità grezza tramite la distribuzione di Poisson:

```
P(Casa Over 0.5)    = 1 − P(λ_casa = 0)
P(Ospite Under 1.5) = P(λ_ospite = 0) + P(λ_ospite = 1)
```

#### [2] ⏱️ Decadimento temporale (Decay)

Peso esponenziale decrescente — le partite recenti pesano di più:

```
w(t) = exp(−ξ × giorni_fa)    con ξ = 0.010 (emivita ~70 giorni)
```

Quando il Decay è attivo viene applicato anche il **prior stagionale**: le squadre con meno di 8 partite vengono avvicinate alla media lega per evitare stime estreme:

```
stat_corretta = α × stat_squadra + (1 − α) × media_lega
α = min(partite_giocate / 8, 1.0)
```

#### [3] 🏟️ Fattore campo (HF)

Rapporto gol casa/trasferta per squadra vs media lega, con **shrinkage adattivo**:

```
α_shrinkage = min(partite_totali / 20, 1.0) × 0.65
HF = α_shrinkage × raw_factor + (1 − α_shrinkage)
```

Con poche partite il fattore tende a 1.0; con 20+ raggiunge lo shrinkage massimo (0.65).

#### [4] 📐 Dixon-Coles (DC)

Correzione della matrice di Poisson per i punteggi bassi (0-0, 0-1, 1-0, 1-1). Il parametro ρ viene stimato dai dati osservati (range: −0.25 / +0.05).

#### [5] 🎯 Calibrazione (Temperature Scaling)

Riduce l'overconfidence sistematica emersa dal backtest:

```
p_calibrata = sigmoid(logit(p_raw) / T)    con T = 1.30
```

| Probabilità grezza | Dopo calibrazione |
|---|---|
| 96% | ~92% |
| 90% | ~84% |
| 80% | ~74% |
| 70% | ~66% |
| 50% | 50% (invariato) |

La probabilità calibrata è quella mostrata sulla barra. Il valore grezzo (raw) è mostrato in parentesi accanto.

---

### Rating Elo — informativo, non influenza la probabilità

```
E_casa = 1 / (1 + 10^((Elo_ospite − Elo_casa) / 400))
Δ = K × (risultato − E_casa)    con K = 20
```

Tutte le squadre partono da 1500 a inizio stagione.

| Rating | Stelle | Colore |
|---|---|---|
| ≥ 1650 | ★★★ | 🟢 Verde |
| 1560–1649 | ★★ | 🔵 Cyan |
| 1480–1559 | ★ | ⚪ Bianco |
| 1400–1479 | ~ | 🟡 Arancione |
| < 1400 | ▼ | 🔴 Rosso |

> ⚠️ L'Elo usa sempre la storia completa indipendentemente dal range statistiche selezionato.

---

## Interfaccia

### Tab Pronostici

Le partite della prossima giornata sono ordinate per probabilità decrescente. Ogni card mostra:

- **Percentuale calibrata** con barra colorata (🟢 ≥ 80% · 🟡 65–79% · 🔴 < 65%)
- **Quota fair** = 1 / probabilità
- **Raw** = probabilità prima della calibrazione
- **λ Casa / Ospite** — gol attesi
- **Att / Def** — parametri attacco/difesa usati per i lambda
- **Fattore campo** e **ρ Dixon-Coles**
- **Blocco Elo** — rating e stelle per entrambe le squadre

### Tab Classifiche

Codice colore zone: 🟢 promozione diretta · 🔵 playoff · 🔴 retrocessione.

### Range statistiche

| Opzione | Descrizione |
|---|---|
| Tutta la stagione | Tutti i risultati disponibili |
| Ultime 5 / 8 / 10 / 15 | Ultime N partite per squadra |
| Solo 2026 | Partite dal 01/01/2026 |
| Ultimi 30gg / 60gg | Finestra temporale mobile |
| Personalizzato | Numero arbitrario di partite per squadra |

---

## Guida integrata

Il bottone **📖 Guida** nella barra strumenti apre un modal con la documentazione completa, navigabile per sezioni: panoramica, fonti dati, modello statistico, layer togglabili, lettura delle card, range statistiche, Elo, backtest v10, guida sviluppatori.

Il modal si chiude con `ESC` o cliccando fuori dal riquadro.

---

## Backtest walk-forward

```bash
python backtest_calciomagazine.py
```

Output: `./backtest_output/backtest_report.txt` e `./backtest_output/backtest_grafici.png`

Risultati su dati reali 2025-2026 (1.039 partite, quota simulata 1.85):

| Campionato | Brier Score v9 | Brier Score v10 | ROI @soglia consigliata |
|---|---|---|---|
| Serie B | 0.2155 | **0.2005** | +41–47% |
| Gir. A | 0.2154 | **0.1994** | +44% |
| Gir. B | 0.2420 | **0.2279** | +37% |
| Gir. C | 0.2354 | **0.2152** | +38% |

> v10 batte il Poisson puro in tutti e 4 i gironi. v9 era peggiore del Poisson puro: i layer aggiuntivi senza calibrazione degradavano le previsioni.

---

## API Flask

| Metodo | Endpoint | Descrizione |
|---|---|---|
| GET | `/` | Interfaccia web principale |
| GET | `/api/status` | Stato dati: conteggi, data aggiornamento, prossime giornate |
| GET | `/api/predict` | Pronostici — params: `range`, `customN`, `decay`, `hf`, `dc`, `calib` |
| GET | `/api/standings` | Classifiche per tutti i campionati |
| POST | `/api/scrape` | Scarica/aggiorna dati — body param: `source` (`calciomagazine` \| `wikipedia`) |
| GET | `/api/export` | Esporta il database JSON corrente |
| POST | `/api/import` | Importa un database JSON |
| POST | `/api/reset` | Cancella tutti i dati |

---

## Struttura del database JSON

```json
{
  "version": 10,
  "source": "calciomagazine",
  "updatedAt": "2026-03-05",
  "serieB": {
    "results_by_giornata": { "1": [...], "2": [...] },
    "results": [
      {"date": "2025-08-22", "home": "Mantova", "away": "Carrarese", "hg": 1, "ag": 0}
    ],
    "next_giornata": 29,
    "next_fixtures": [
      {"date": "2026-03-08", "time": "20:30", "home": "Mantova", "away": "Carrarese"}
    ]
  },
  "serieCa": {},
  "serieCb": {},
  "serieCc": {}
}
```

Il campo `time` è presente solo per i dati scaricati da calciomagazine.net. Il database è retrocompatibile con versioni precedenti.

---

## Tooltip informativi

Tooltip al passaggio del mouse su ogni elemento interattivo, gestiti da un layer globale (`#gtooltip`) che non viene mai tagliato dall'`overflow:hidden` delle card. Coprono: campi delle card, toggle modello, bottoni principali, selettori fonte.

Il bottone **💬 Tooltip ON/OFF** disabilita/riabilita tutti i tooltip globalmente.

---

## Distribuzione come eseguibile (.exe)

### Build manuale

```bash
py -3.12 -m pip install pyinstaller flask requests beautifulsoup4 pywebview
py -3.12 -m PyInstaller --onefile --noconsole --name "PronosticiCalcio" pronostici_app_v10.py
```

### Build automatica

Doppio click su `build_exe.bat`: verifica Python 3.12, aggiorna dipendenze, compila, copia `PronosticiCalcio.exe` nella cartella corrente.

### File dati a runtime

```python
def get_base_dir():
    if getattr(sys, 'frozen', False):  # dentro l'exe
        return Path(sys.executable).parent
    return Path(__file__).parent
```

`pronostici_data.json` viene sempre letto/scritto **accanto all'exe**. Quando l'app gira come exe tutto l'output viene rediretto a `pronostici.log` nella stessa cartella.

---

## Note tecniche

- Server Flask su `127.0.0.1:5050` (solo locale, non esposto in rete)
- Thread Flask daemon: si chiude automaticamente con la finestra
- Fetch HTTP con `verify=False` per compatibilità con proxy aziendali
- BeautifulSoup usa `html.parser` della stdlib (nessuna dipendenza aggiuntiva)
- Il filtro `_filter_future_fixtures()` esclude automaticamente fixture con data già passata, evitando che partite rinviate o con date errate vengano proposte come upcoming
