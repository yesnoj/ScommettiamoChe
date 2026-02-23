# ⚽ Pronostici Serie B & C

Applicazione Python/Flask per pronostici statistici sulle partite di **Serie B** e **Serie C** (Gironi A, B, C) del campionato italiano 2025-2026. Utilizza il modello di distribuzione di **Poisson** per calcolare le probabilità di esiti specifici sulle prossime partite da giocare.

---

## 📊 Cosa calcola

| Lega | Pronostico | Significato |
|------|-----------|-------------|
| **Serie B** | **Casa Over 0.5** | Probabilità che la squadra di casa segni almeno 1 gol |
| **Serie C** (Gir. A/B/C) | **Ospite Under 1.5** | Probabilità che la squadra ospite subisca al massimo 1 gol |

Le partite vengono ordinate per probabilità decrescente, permettendo di individuare rapidamente le scommesse statisticamente più promettenti.

---

## 🧮 Il modello Poisson

Il cuore dell'applicazione è la [distribuzione di Poisson](https://it.wikipedia.org/wiki/Distribuzione_di_Poisson), un modello statistico classico per eventi rari e indipendenti, ampiamente usato nell'analisi calcistica.

### Come funziona

Per ogni partita futura, l'app calcola un parametro **λ (lambda)** che rappresenta il numero atteso di gol. Il calcolo si basa su:

**Serie B — Casa Over 0.5:**
```
λ = (Attacco_Casa × Difesa_Ospite) / Media_Lega

dove:
  Attacco_Casa   = media gol fatti in casa dalla squadra di casa
  Difesa_Ospite  = media gol subiti in trasferta dalla squadra ospite
  Media_Lega     = media gol per partita dell'intero campionato

P(Casa Over 0.5) = 1 − P(X = 0) = 1 − e^(−λ)
```

**Serie C — Ospite Under 1.5:**
```
λ = (Attacco_Ospite × Difesa_Casa) / Media_Lega

dove:
  Attacco_Ospite = media gol fatti in trasferta dalla squadra ospite
  Difesa_Casa    = media gol subiti in casa dalla squadra di casa

P(Ospite Under 1.5) = P(X = 0) + P(X = 1) = e^(−λ) + λ·e^(−λ)
```

### Range statistici

L'utente può scegliere su quale sottoinsieme di partite calcolare le statistiche:

- **Tutta la stagione** — tutti i risultati disponibili
- **Ultime N partite** (5, 8, 10, 15) — per squadra, per catturare il "momento di forma"
- **Solo 2026** — solo partite del girone di ritorno
- **Ultimi 30/60 giorni** — finestra temporale mobile
- **Personalizzato** — numero di partite a scelta (3-38)

---

## 📁 Struttura del progetto

Il repository contiene **due versioni indipendenti** che differiscono esclusivamente per la fonte dati utilizzata per lo scraping. Il modello statistico, l'interfaccia web e le funzionalità sono identiche.

```
pronostici-serie-b-c/
├── README.md
├── pronostici_app_calcioMagazine.py    ← Fonte: calciomagazine.net
└── pronostici_app_Wikipedia.py         ← Fonte: it.wikipedia.org
```

### Perché due versioni?

I siti di calcio cambiano frequentemente struttura HTML. Avere due fonti dati garantisce ridondanza: se una fonte smette di funzionare, l'altra resta disponibile. Le due versioni sono completamente intercambiabili.

---

## 🔍 Le due fonti dati

### 1. `pronostici_app_calcioMagazine.py`

**Fonte:** [calciomagazine.net](https://www.calciomagazine.net)

Effettua lo scraping di **due pagine separate** per ogni campionato:
- **Pagina risultati** — contiene tutti i punteggi delle partite giocate, raggruppati per giornata
- **Pagina calendario** — contiene le date e gli orari delle partite future

Il parsing è **text-based**: l'HTML viene convertito in testo pulito e poi analizzato con regex. I formati gestiti sono:

```
27ª Giornata
16.02. ore 20:30 Pineto-Ternana 0 : 3              ← risultato
Domenica 22.02.2026 ore 18:30 Arezzo-Campobasso     ← prossima partita
```

La prossima giornata viene determinata confrontando risultati e calendario: la prima giornata con meno risultati che partite in programma è quella "da giocare".

**Dipendenze:** `flask`, `requests`

**URL scraping (stagione 2025-2026):**

| Lega | Risultati | Calendario |
|------|-----------|------------|
| Serie B | `risultati-serie-b-120385.html` | `calendario-serie-b-99638.html` |
| Serie C Gir. A | `risultati-serie-c-girone-a-120404.html` | `calendario-serie-c-girone-a-99207.html` |
| Serie C Gir. B | `risultati-serie-c-girone-b-120417.html` | `calendario-serie-c-girone-b-99208.html` |
| Serie C Gir. C | `risultati-serie-c-girone-c-120418.html` | `calendario-serie-c-girone-c-99209.html` |

---

### 2. `pronostici_app_Wikipedia.py`

**Fonte:** [it.wikipedia.org](https://it.wikipedia.org)

Effettua lo scraping di **due sole pagine** Wikipedia per tutti i dati:
- [`Serie_B_2025-2026`](https://it.wikipedia.org/wiki/Serie_B_2025-2026) — risultati e calendario completo
- [`Serie_C_2025-2026`](https://it.wikipedia.org/wiki/Serie_C_2025-2026) — tutti e 3 i gironi in un'unica pagina

Il parsing è **DOM-based** con BeautifulSoup. Le due leghe hanno strutture HTML molto diverse:

**Serie B** — formato semplice, una partita per riga:
```
| 8 dic. | Avellino-Juve Stabia | 1-1 |     ← risultato
| 1 mar. | Monza-Catanzaro      | 19:30 |   ← prossima partita (orario)
| 5 apr. | Empoli-Cesena        | - |        ← da programmare
```

**Serie C** — formato combinato andata/ritorno, due partite per riga:
```
| 23 ago. | 2-2 | AlbinoLeffe-Dolomiti Bellunesi | 0-2 | 4 gen. |
   ↑ data     ↑ ris.        ↑ squadre            ↑ ris.   ↑ data
  andata    andata                              ritorno  ritorno
```

Le colonne data usano `rowspan` per raggruppare più partite nella stessa data. Il parser tiene traccia dei rowspan attivi per entrambe le colonne (andata e ritorno). La struttura della pagina Wiki varia anche tra gironi: A e B hanno le tabelle nella sezione "Calendario", il C nella sezione "Risultati".

La deduplicazione è necessaria perché Wikipedia mostra ogni tabella in tre viste (collassata, espansa andata, espansa ritorno).

**Dipendenze:** `flask`, `requests`, `beautifulsoup4`

---

## 🚀 Installazione e avvio

### Requisiti
- **Python 3.8+**
- Connessione internet (per lo scraping)

### Avvio rapido

```bash
# Clona il repository
git clone https://github.com/TUO-USERNAME/pronostici-serie-b-c.git
cd pronostici-serie-b-c

# Scegli la versione da usare:

# Opzione A — calciomagazine.net
pip install flask requests
python pronostici_app_calcioMagazine.py

# Opzione B — Wikipedia
pip install flask requests beautifulsoup4
python pronostici_app_Wikipedia.py
```

L'app si avvia su **`http://localhost:5050`**.

### Modalità desktop (opzionale)

Se `pywebview` è installato, l'app si apre automaticamente in una finestra desktop nativa invece del browser:

```bash
pip install pywebview
python pronostici_app_Wikipedia.py   # si apre come app desktop
```

---

## 🖥️ Interfaccia

L'interfaccia web è una **Single Page Application** integrata direttamente nel file Python (nessun file HTML esterno). Include:

### Tab Pronostici
- **Serie B — Casa Over 0.5**: card per ogni partita della prossima giornata, ordinate per probabilità
- **Serie C — Ospite Under 1.5**: card per tutti e 3 i gironi, ordinate per probabilità globale
- Ogni card mostra: probabilità %, barra visuale colorata (verde/giallo/rosso), λ, rating attacco/difesa, numero partite giocate, data

### Tab Classifiche
- Classifica completa Serie B
- Classifiche separate per ogni girone di Serie C
- Colonne: G (giocate), V (vittorie), P (pareggi), S (sconfitte), GF, GS, Pt

### Pannello Range Statistiche
- Selettore per cambiare il periodo di calcolo (tutta la stagione, ultime N, ecc.)
- Ricalcolo istantaneo dei pronostici

### Gestione dati
- **🔄 Aggiorna** — scarica i dati aggiornati dalla fonte
- **📤 Esporta JSON** — backup completo dei dati
- **📥 Importa JSON** — ripristino da backup
- **🗑️ Reset** — cancella tutti i dati

---

## 🔌 API REST

Entrambe le versioni espongono le stesse API:

| Metodo | Endpoint | Descrizione |
|--------|----------|-------------|
| `GET` | `/` | Interfaccia web |
| `GET` | `/api/status` | Stato dati: conteggi risultati, data ultimo aggiornamento |
| `GET` | `/api/predict?range=all&customN=10` | Pronostici con filtro range |
| `GET` | `/api/standings` | Classifiche di tutti i campionati |
| `GET` | `/api/data` | Dump completo dati grezzi |
| `GET` | `/api/export` | Esportazione dati (identico a `/api/data`) |
| `POST` | `/api/scrape` | Avvia scraping dalla fonte dati |
| `POST` | `/api/import` | Importa dati da JSON |
| `POST` | `/api/reset` | Reset a dati vuoti |

---

## 💾 Persistenza dati

I dati vengono salvati in `pronostici_data.json` nella stessa cartella dello script. Il file viene creato automaticamente al primo scraping e aggiornato ad ogni operazione.

Struttura del JSON (v7):

```json
{
  "version": 7,
  "updatedAt": "2026-02-23",
  "serieB": {
    "results": [
      {"date": "2025-12-08", "home": "Avellino", "away": "Juve Stabia", "hg": 1, "ag": 1}
    ],
    "results_by_giornata": {"1": [...], "2": [...]},
    "next_giornata": 27,
    "next_fixtures": [
      {"date": "2026-03-01", "home": "Monza", "away": "Catanzaro"}
    ]
  },
  "serieCa": { "..." : "stessa struttura" },
  "serieCb": { "..." : "stessa struttura" },
  "serieCc": { "..." : "stessa struttura" }
}
```

La versione calcioMagazine include anche `calendar_by_giornata` con il calendario completo di tutte le giornate.

Entrambe le versioni supportano la **migrazione automatica** dal vecchio formato v6 (liste) al nuovo formato v7 (dizionari).

---

## ⚠️ Note e limitazioni

- **Nessuna quota bookmaker**: l'app calcola solo probabilità statistiche pure, senza incorporare le quote dei siti di scommesse. Le probabilità non tengono conto del margine del bookmaker.
- **Modello semplificato**: il Poisson assume indipendenza tra i gol e non considera fattori come infortuni, squalifiche, motivazione, condizioni meteo.
- **Dipendenza dalla struttura HTML**: lo scraping può rompersi se i siti fonte cambiano layout. In tal caso, usare l'altra versione come fallback.
- **SSL**: entrambe le versioni usano `verify=False` nelle richieste HTTPS per aggirare problemi di certificati su alcuni sistemi. Non è ideale per la sicurezza, ma funzionale per lo scraping.
- **Solo stagione corrente**: gli URL sono codificati per la stagione 2025-2026 e andranno aggiornati manualmente per le stagioni successive.

---

## 📜 Licenza

Progetto personale a scopo educativo e di intrattenimento. I dati delle partite appartengono alle rispettive fonti (calciomagazine.net, Wikipedia). Il gioco d'azzardo comporta rischi: gioca responsabilmente.
