# ⚽ Pronostici Serie B & C — v10

Applicazione Flask/Python per il calcolo di probabilità statistiche su partite di **Serie B** e **Serie C** (Gironi A, B, C) del campionato italiano 2025-2026.

Il modello si basa su **distribuzione di Poisson** con correzioni avanzate (Dixon-Coles, decadimento temporale, fattore campo, calibrazione) e produce due tipologie di pronostico:

| Campionato | Scommessa analizzata | Soglia consigliata |
|---|---|---|
| Serie B | **Casa Over 0.5** — la squadra di casa segna almeno 1 gol | ≥ 65% |
| Serie C Girone A | **Ospite Under 1.5** — l'ospite segna 0 o 1 gol | ≥ 70% |
| Serie C Girone B | **Ospite Under 1.5** | ≥ 72% |
| Serie C Girone C | **Ospite Under 1.5** | ≥ 72% |

> Le soglie consigliate sono ricavate dal backtest walk-forward su dati reali 2025-2026 (quota simulata 1.85). Sono visibili direttamente in app sotto ogni sezione.

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

> ⚠️ L'app richiede un PC o Mac con Python installato. Non è compatibile con iOS/Android (sandbox di sistema).

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

### 2. Wikipedia (it.)

Due richieste totali (pagina Serie B + pagina Serie C).

- `https://it.wikipedia.org/wiki/Serie_B_2025-2026`
- `https://it.wikipedia.org/wiki/Serie_C_2025-2026`

Parser: BeautifulSoup su tabelle HTML. La Serie C usa il formato andata/ritorno combinato su un'unica riga; le squadre vengono invertite automaticamente per le gare di ritorno.

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

Peso esponenziale decrescente nel tempo — le partite recenti pesano di più:

```
w(t) = exp(−ξ × giorni_fa)    con ξ = 0.010 (emivita ~70 giorni)
```

Il parametro ξ = 0.010 (raddoppiato rispetto alla v9 dove era 0.005 = 139gg) tiene conto del mercato di gennaio e dell'evoluzione delle rose nel corso della stagione.

Quando il Decay è attivo viene applicato anche il **prior stagionale**: le squadre con meno di 8 partite disputate vengono avvicinate alla media di lega per evitare stime estreme (problema tipico delle neopromosse):

```
stat_corretta = α × stat_squadra + (1 − α) × media_lega
α = min(partite_giocate / 8, 1.0)
```

#### [3] 🏟️ Fattore campo (HF)

Rapporto gol casa/trasferta per ogni squadra rispetto alla media di lega. In v10 usa **shrinkage adattivo** proporzionale ai dati disponibili:

```
α_shrinkage = min(partite_totali / 20, 1.0) × 0.65
HF = α_shrinkage × raw_factor + (1 − α_shrinkage)
```

Con poche partite il fattore tende a 1.0 (nessun effetto); con 20+ partite raggiunge lo shrinkage massimo (0.65).

#### [4] 📐 Dixon-Coles (DC)

Correzione della matrice di Poisson per i punteggi bassi (0-0, 0-1, 1-0, 1-1) dove la distribuzione indipendente sistematicamente sovra o sottostima le frequenze reali. Il parametro ρ viene stimato dai dati osservati (range: −0.25 / +0.05).

#### [5] 🎯 Calibrazione (Temperature Scaling)

Riduce l'overconfidence sistematica del modello, emersa dal backtest: quando il modello prevedeva 90%+, la frequenza reale era ~83-85%.

```
p_calibrata = sigmoid(logit(p_raw) / T)    con T = 1.30
```

Effetti pratici:

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

Il rating Elo viene calcolato **in parallelo** dalla storia completa delle partite e mostrato in fondo a ogni card come contesto sulla forza relativa. **Non entra nel calcolo della probabilità.**

```
E_casa = 1 / (1 + 10^((Elo_ospite − Elo_casa) / 400))
Δ = K × (risultato − E_casa)    con K = 20
```

Tutte le squadre partono da 1500 a inizio stagione. I rating diventano significativi dopo ~8-10 giornate.

| Rating | Stelle | Colore |
|---|---|---|
| ≥ 1650 | ★★★ | 🟢 Verde |
| 1560–1649 | ★★ | 🔵 Cyan |
| 1480–1559 | ★ | ⚪ Bianco |
| 1400–1479 | ~ | 🟡 Arancione |
| < 1400 | ▼ | 🔴 Rosso |

**Come usarlo:** serve come sanity check visivo. Se il modello dà 85% ma l'Elo mostra casa ▼ vs ospite ★★★, vale la pena ragionarci prima di giocare.

> ⚠️ L'Elo usa sempre la storia completa indipendentemente dal range statistiche selezionato nella UI.

---

## Interfaccia

### Tab Pronostici

Le partite della prossima giornata sono ordinate per probabilità decrescente. Ogni card mostra:

- **Percentuale calibrata** con barra colorata (🟢 ≥ 80% · 🟡 65–79% · 🔴 < 65%)
- **Quota fair** = 1 / probabilità — la quota minima bookmaker per avere edge positivo
- **Raw** = probabilità prima della calibrazione
- **λ Casa / Ospite** — gol attesi
- **Att / Def** — parametri attacco/difesa usati per i lambda
- **Fattore campo** e **ρ Dixon-Coles**
- **Blocco Elo** — rating corrente e stelle per entrambe le squadre

Sotto ogni sezione è presente un banner 🎯 con la soglia consigliata dal backtest e i dati sintetici di ROI e accuracy.

### Tab Classifiche

Tabella per ogni campionato con codice colore zone:
- 🟢 Verde — prime 2 posizioni (promozione diretta)
- 🔵 Ciano — posizioni 3–8 (playoff)
- 🔴 Rosso — ultime 3 posizioni (zona retrocessione)

### Range statistiche

Filtrano le partite usate per il calcolo dei lambda:

| Opzione | Descrizione |
|---|---|
| Tutta la stagione | Tutti i risultati disponibili |
| Ultime 5 / 8 / 10 / 15 | Ultime N partite per squadra |
| Solo 2026 | Partite dal 01/01/2026 |
| Ultimi 30gg / 60gg | Finestra temporale mobile |
| Personalizzato | Numero arbitrario di partite per squadra |

---

## Backtest walk-forward

Lo script `backtest_calciomagazine.py` valida il modello con metodologia walk-forward:

- **Train:** giornate 1 … N−1
- **Test:** previsione giornata N con esito già noto
- **Minimo training:** 5 giornate
- Confronta 4 configurazioni: Poisson puro · v9 completo · v10 senza calibrazione · v10 completo

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

> v10 batte il Poisson puro in tutti e 4 i gironi. v9 invece era peggiore del Poisson puro, segno che i layer aggiuntivi senza calibrazione degradavano le previsioni.

---

## API Flask

| Metodo | Endpoint | Descrizione |
|---|---|---|
| GET | `/` | Interfaccia web principale |
| GET | `/api/status` | Stato dati: conteggi, data aggiornamento, prossime giornate |
| GET | `/api/predict` | Pronostici — params: `range`, `customN`, `decay`, `hf`, `dc`, `calib` |
| GET | `/api/standings` | Classifiche per tutti i campionati |
| POST | `/api/scrape` | Scarica/aggiorna dati |
| GET | `/api/export` | Esporta il database JSON corrente |
| POST | `/api/import` | Importa un database JSON |
| POST | `/api/reset` | Cancella tutti i dati |

---

## Struttura del database JSON

```json
{
  "version": 7,
  "updatedAt": "2026-02-26",
  "serieB": {
    "results_by_giornata": { "1": [...], "2": [...] },
    "results": [
      {"date": "2025-08-22", "home": "Mantova", "away": "Carrarese", "hg": 1, "ag": 0}
    ],
    "next_giornata": 29,
    "next_fixtures": [
      {"date": "2026-03-01", "home": "Mantova", "away": "Carrarese"}
    ]
  },
  "serieCa": {},
  "serieCb": {},
  "serieCc": {}
}
```

Il database è retrocompatibile con versioni precedenti: i campi mancanti vengono aggiunti automaticamente al caricamento.

---

## Note tecniche

- Il server Flask gira su `127.0.0.1:5050` (solo locale, non esposto in rete)
- Il thread Flask è daemon: si chiude automaticamente con la finestra
- I fetch HTTP disabilitano la verifica SSL (`verify=False`) per compatibilità con alcuni proxy aziendali
- BeautifulSoup usa il parser `html.parser` della stdlib (nessuna dipendenza esterna aggiuntiva)
