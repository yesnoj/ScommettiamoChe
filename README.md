# ⚽ Pronostici Serie B & C

Applicazione desktop/web per generare pronostici statistici sulle partite di **Serie B** e **Serie C** del campionato italiano di calcio, basata sulla distribuzione di Poisson.

I dati vengono scaricati automaticamente da [calciomagazine.net](https://www.calciomagazine.net).

![Python](https://img.shields.io/badge/Python-3.8+-blue?logo=python&logoColor=white)
![Flask](https://img.shields.io/badge/Flask-3.x-lightgrey?logo=flask)
![License](https://img.shields.io/badge/License-MIT-green)

---

## Funzionalità

- **Pronostici automatici** per le prossime partite con probabilità calcolate via modello di Poisson
- **Scraping automatico** di risultati e calendari da calciomagazine.net
- **Classifiche live** calcolate dai risultati scaricati
- **Filtri statistici** configurabili (ultime N partite, ultimi 30/60 giorni, solo 2026, ecc.)
- **Esportazione/importazione** dei dati in formato JSON per backup
- **Interfaccia desktop** nativa con pywebview (opzionale) o browser
- **Deduplicazione automatica** dei risultati durante lo scrape
- **Date delle partite** mostrate nelle card dei pronostici

## Campionati coperti

| Campionato | Pronostico |
|---|---|
| **Serie B** | Casa Over 0.5 — probabilità che la squadra di casa segni almeno 1 gol |
| **Serie C Girone A** | Ospite Under 1.5 — probabilità che la squadra ospite segni meno di 2 gol |
| **Serie C Girone B** | Ospite Under 1.5 |
| **Serie C Girone C** | Ospite Under 1.5 |

---

## Installazione

### Requisiti

- Python 3.8+
- Connessione internet (per lo scraping)

### Setup rapido

```bash
# Clona il repository
git clone https://github.com/<tuo-username>/pronostici-serie-b-c.git
cd pronostici-serie-b-c

# Installa le dipendenze
pip install flask requests

# (Opzionale) Per la finestra desktop nativa
pip install pywebview

# Avvia l'applicazione
python pronostici_app.py
```

Al primo avvio vengono caricati automaticamente ~1000 risultati seed incorporati nello script (dati calciomagazine.net, febbraio 2026).

### Avvio

L'app si avvia su `http://localhost:5050`:

- **Con pywebview installato**: si apre automaticamente una finestra desktop nativa
- **Senza pywebview**: si apre il browser predefinito

---

## Come funziona

### Il modello di Poisson

Il motore predittivo utilizza la [distribuzione di Poisson](https://it.wikipedia.org/wiki/Distribuzione_di_Poisson) per stimare la probabilità di un certo numero di gol in una partita. Il parametro chiave è **λ (lambda)**, che rappresenta il numero di gol attesi.

#### Serie B — Casa Over 0.5

Per ogni partita, si calcola il lambda della squadra di casa:

```
λ_casa = (Attacco_Casa × Difesa_Trasferta) / Media_Lega
```

Dove:
- **Attacco Casa** = media gol fatti in casa dalla squadra di casa
- **Difesa Trasferta** = media gol subiti in trasferta dalla squadra ospite
- **Media Lega** = media gol per partita dell'intero campionato

La probabilità "Casa Over 0.5" è:

```
P(gol ≥ 1) = 1 − P(gol = 0) = 1 − e^(−λ)
```

#### Serie C — Ospite Under 1.5

Per la Serie C, si calcola il lambda della squadra ospite:

```
λ_ospite = (Attacco_Trasferta × Difesa_Casa) / Media_Lega
```

La probabilità "Ospite Under 1.5" è:

```
P(gol ≤ 1) = P(0) + P(1) = e^(−λ) + λ × e^(−λ)
```

### Lettura delle card

Ogni card mostra:

| Campo | Significato |
|---|---|
| **📅 Data** | Data della partita (es. Ven 21/02/2026) |
| **Probabilità %** | Probabilità calcolata dal modello (verde ≥75%, giallo ≥55%, rosso <55%) |
| **λ Casa/Ospite** | Gol attesi — più è alto, più gol sono probabili |
| **Att / Def** | Media attacco della squadra analizzata / Media difesa dell'avversario |
| **P. Casa** | Numero di partite in casa su cui si basa il calcolo |
| **P. Trasf.** | Numero di partite in trasferta su cui si basa il calcolo |

### Filtri statistici

I pronostici possono essere ricalcolati su diversi range di dati:

| Filtro | Descrizione |
|---|---|
| Tutta la stagione | Tutti i risultati disponibili |
| Ultime 5/8/10/15 | Ultime N partite per squadra |
| Solo 2026 | Solo partite dal 1 gennaio 2026 |
| Ultimi 30gg / 60gg | Partite degli ultimi 30 o 60 giorni |
| Personalizzato | Numero di partite a scelta |

---

## Fonti dati

| Dato | Fonte | URL |
|---|---|---|
| Risultati Serie B | calciomagazine.net | [Risultati Serie B](https://www.calciomagazine.net/risultati-serie-b-120385.html) |
| Risultati Serie C Gir. A | calciomagazine.net | [Risultati Serie C A](https://www.calciomagazine.net/risultati-serie-c-girone-a-120404.html) |
| Risultati Serie C Gir. B | calciomagazine.net | [Risultati Serie C B](https://www.calciomagazine.net/risultati-serie-c-girone-b-120417.html) |
| Risultati Serie C Gir. C | calciomagazine.net | [Risultati Serie C C](https://www.calciomagazine.net/risultati-serie-c-girone-c-120418.html) |
| Calendario Serie C Gir. A | calciomagazine.net | [Calendario Serie C A](https://www.calciomagazine.net/calendario-serie-c-girone-a-99200.html) |
| Calendario Serie C Gir. B | calciomagazine.net | [Calendario Serie C B](https://www.calciomagazine.net/calendario-serie-c-girone-b-99208.html) |
| Calendario Serie C Gir. C | calciomagazine.net | [Calendario Serie C C](https://www.calciomagazine.net/calendario-serie-c-girone-c-99209.html) |

Il calendario di Serie B viene estratto automaticamente dal link presente nella pagina dei risultati.

---

## Architettura

```
pronostici_app.py          # Applicazione completa (singolo file)
pronostici_data.json       # Dati persistenti (generato automaticamente)
```

### Stack tecnologico

- **Backend**: Python/Flask — API REST + scraping con `requests` e `re`
- **Frontend**: HTML/CSS/JS inline nel template Flask — nessun framework, nessun build step
- **Persistenza**: file JSON locale (`pronostici_data.json`)
- **Desktop** (opzionale): pywebview per finestra nativa

### Flusso dati

```
calciomagazine.net
        │
        ▼
   [Scraping]  ──→  parse_results()          ──→  risultati []
        │            scrape_fixtures_from_    ──→  fixtures []
        │            calendario()
        ▼
  pronostici_data.json
        │
        ▼
   [Modello Poisson]  ──→  predict_serie_b()  ──→  API /api/predict
                           predict_serie_c()
        │
        ▼
   [Frontend JS]  ──→  Card con pronostici
```

### API endpoints

| Metodo | Endpoint | Descrizione |
|---|---|---|
| `GET` | `/` | Interfaccia web |
| `GET` | `/api/data` | Tutti i dati raw (risultati + fixture) |
| `GET` | `/api/predict?range=all&customN=10` | Pronostici calcolati |
| `GET` | `/api/standings` | Classifiche di tutti i campionati |
| `GET` | `/api/status` | Stato dei dati (conteggi, data aggiornamento) |
| `GET` | `/api/export` | Esporta tutti i dati in JSON |
| `POST` | `/api/scrape` | Scarica risultati e fixture da calciomagazine.net |
| `POST` | `/api/import` | Importa dati da JSON |
| `POST` | `/api/reset` | Ripristina dati seed iniziali |
| `POST` | `/api/fixtures` | Salva fixture manualmente |
| `POST` | `/api/add_results` | Aggiungi risultati manualmente |

### Struttura dati (pronostici_data.json)

```json
{
  "version": 5,
  "updatedAt": "2026-02-18",
  "serieB": {
    "results": [
      ["2026-02-15", "Venezia", "Pescara", 3, 1],
      ["2026-02-15", "Palermo", "Sudtirol", 1, 0]
    ],
    "fixtures": [
      {"h": "Venezia", "a": "Pescara", "date": "2026-02-22"},
      {"h": "Frosinone", "a": "Empoli", "date": "2026-02-22"}
    ]
  },
  "serieCa": { "results": [], "fixtures": [] },
  "serieCb": { "results": [], "fixtures": [] },
  "serieCc": { "results": [], "fixtures": [] }
}
```

Ogni risultato è un array `[data, casa, trasferta, gol_casa, gol_trasferta]`.

---

## Utilizzo

### Flusso normale

1. Avvia l'app con `python pronostici_app.py`
2. Premi **🔄 Aggiorna da calciomagazine.net** per scaricare i dati più recenti
3. Consulta i **📊 Pronostici**, ordinati per probabilità decrescente
4. Usa i **filtri** per affinare l'analisi (es. solo ultime 8 partite)
5. Consulta le **🏆 Classifiche** per il contesto

### Backup e ripristino

- **📤 Esporta JSON**: salva un file di backup con tutti i dati
- **📥 Importa JSON**: ricarica un backup precedentemente esportato
- **🗑 Reset**: ripristina i dati seed iniziali incorporati nello script

---

## Limitazioni e disclaimer

- I pronostici sono **puramente statistici** e basati sullo storico dei risultati
- Il modello di Poisson assume indipendenza tra gol e non considera: infortuni, squalifiche, motivazioni, condizioni meteo, mercato, ecc.
- Le probabilità **non sono** consigli di scommessa
- Lo scraping dipende dalla struttura HTML di calciomagazine.net — eventuali modifiche al sito potrebbero richiedere aggiornamenti al parser
- La determinazione automatica della "prossima giornata" si basa sul numero di giornata più recente trovato nei risultati

---

## Licenza

MIT
