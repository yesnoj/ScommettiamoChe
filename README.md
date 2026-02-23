# ⚽ Pronostici Serie B & C — v9

Applicazione Flask/Python per il calcolo di probabilità statistiche su partite di **Serie B** e **Serie C** (Gironi A, B, C) del campionato italiano.

Il modello si basa su **distribuzione di Poisson** con correzioni avanzate (Dixon-Coles, decadimento temporale, fattore campo) e produce due tipologie di pronostico:

| Campionato | Scommessa analizzata |
|---|---|
| Serie B | **Casa Over 0.5** — la squadra di casa segna almeno 1 gol |
| Serie C (A, B, C) | **Ospite Under 1.5** — l'ospite segna 0 o 1 gol |

---

## File del progetto

```
pronostici_app.py        ← unico file sorgente, contiene tutto
pronostici_data.json     ← database locale (creato al primo avvio)
README.md
```

> ℹ️ Rispetto alle versioni precedenti (v7 con due script separati), **v9 è un file unico** che include i parser per tutte e tre le fonti dati.

---

## Requisiti

- Python 3.9+
- Dipendenze (installate automaticamente al primo avvio se mancanti):

```
flask
requests
beautifulsoup4
pywebview        # opzionale — per la finestra desktop nativa
```

Installazione manuale:

```bash
pip install flask requests beautifulsoup4 pywebview
```

---

## Avvio

```bash
python pronostici_app.py
```

- Con **pywebview** installato → si apre automaticamente una finestra desktop
- Senza pywebview → si apre il browser predefinito su `http://localhost:5050`
- In entrambi i casi il server Flask gira in background sulla porta **5050**

---

## Fonti dati selezionabili

Dalla UI è possibile scegliere il parser prima di premere **🔄 Aggiorna dati**. La scelta viene salvata nel JSON locale e ripristinata ai successivi avvii.

### 1. calciomagazine.net *(default)*

Effettua **due richieste HTTP separate** per ogni campionato: una per i risultati e una per il calendario.

| Chiave | URL risultati | URL calendario |
|---|---|---|
| Serie B | `/risultati-serie-b-120385.html` | `/calendario-serie-b-99638.html` |
| Serie C Girone A | `/risultati-serie-c-girone-a-120404.html` | `/calendario-serie-c-girone-a-99207.html` |
| Serie C Girone B | `/risultati-serie-c-girone-b-120417.html` | `/calendario-serie-c-girone-b-99208.html` |
| Serie C Girone C | `/risultati-serie-c-girone-c-120418.html` | `/calendario-serie-c-girone-c-99209.html` |

**Parser:** estrazione testo grezzo → regex su pattern `DD.MM. ore HH:MM TeamA-TeamB G : G`.

### 2. Wikipedia (it.)

Effettua **due richieste** (pagina Serie B e pagina Serie C).

- `https://it.wikipedia.org/wiki/Serie_B_2025-2026`
- `https://it.wikipedia.org/wiki/Serie_C_2025-2026`

**Parser:** BeautifulSoup su tabelle HTML. La Serie C usa il formato andata/ritorno combinato su un'unica riga; le squadre di casa e trasferta vengono invertite automaticamente per il ritorno.

### 3. Tuttosport *(aggiunto in v9)*

Effettua **quattro richieste** (una per campionato) dalla sezione `/live/calendario-*`.

| Campionato | URL |
|---|---|
| Serie B | `https://www.tuttosport.com/live/calendario-serie-b` |
| Serie C Girone A | `https://www.tuttosport.com/live/calendario-serie-c-girone-a` |
| Serie C Girone B | `https://www.tuttosport.com/live/calendario-serie-c-girone-b` |
| Serie C Girone C | `https://www.tuttosport.com/live/calendario-serie-c-girone-c` |

**Parser:** BeautifulSoup con strategia **img-alt** — i nomi delle squadre vengono letti dall'attributo `alt` delle immagini badge all'interno di ogni link `/live/partita/...`. Questo approccio è robusto rispetto a variazioni di formattazione del testo.

- **Serie B**: il numero di giornata è esplicito nell'header (`"27a giornata"`)
- **Serie C**: nessun numero di giornata nell'HTML → viene contato sequenzialmente ogni nuovo blocco `"Serie C girone X"`
- **Date**: supporta sia `DD.MM.YYYY` (Serie B) che `YYYY.MM.DD` (Serie C)

---

## Modello statistico

Il modello calcola le probabilità in quattro strati sovrapposti, ognuno attivabile/disattivabile dalla UI:

```
[1] Poisson Base  →  [2] + Decadimento  →  [3] + Fattore Campo  →  [4] + Dixon-Coles
```

### 1. Poisson Base
Per ogni partita stima i **lambda** (gol attesi) di casa e ospite:

```
λ_casa = (Att_casa × Def_ospite) / Media_lega
λ_ospite = (Att_ospite × Def_casa) / Media_lega
```

### 2. Decadimento temporale (Decay)
Peso esponenziale decrescente: le partite recenti contano di più.

```
w(t) = exp(-ξ × giorni_fa)    con ξ = 0.005 (emivita ~139 giorni)
```

### 3. Fattore campo (HF — Home Factor)
Rapporto gol casa/trasferta per ogni squadra rispetto alla media di lega, con **shrinkage** verso 1.0 per squadre con pochi dati:

```
HF = 0.7 × (gol_casa_per_partita / gol_trasferta_per_partita) / lhr + 0.3
```

### 4. Dixon-Coles (DC)
Correzione della matrice di Poisson per i punteggi bassi (0-0, 0-1, 1-0, 1-1), dove la distribuzione indipendente sovrastima alcune frequenze. Il parametro ρ (rho) viene stimato dai dati osservati.

### Calcolo probabilità

```
P(Casa Over 0.5)   = 1 − P(casa segna 0 gol)
P(Ospite Under 1.5) = P(ospite segna 0) + P(ospite segna 1)
```

### Range statistiche
La UI permette di filtrare le partite usate per il calcolo:

| Opzione | Descrizione |
|---|---|
| Tutta la stagione | Tutti i risultati disponibili |
| Ultime 5 / 8 / 10 / 15 | Ultime N partite **per squadra** |
| Solo 2026 | Partite dal 01/01/2026 |
| Ultimi 30gg / 60gg | Finestra temporale mobile |
| Personalizzato | Numero arbitrario di partite per squadra |

---

## Interfaccia

### Tab Pronostici
Le partite della prossima giornata sono ordinate per probabilità decrescente. Ogni card mostra:
- Percentuale e barra di probabilità (🟢 ≥ 75% · 🟡 55–75% · 🔴 < 55%)
- Lambda casa e ospite
- Parametri Attacco/Difesa
- Fattore campo (HF) e coefficiente Dixon-Coles (ρ)

### Tab Classifiche
Tabella per ogni campionato con codice colore zone:
- 🟢 **Verde** — prime 2 posizioni (promozione diretta)
- 🔵 **Ciano** — posizioni 3–8 (playoff)
- 🔴 **Rosso** — ultime 3 posizioni (zona retrocessione)

---

## API Flask

Tutti gli endpoint sono accessibili via browser o script:

| Metodo | Endpoint | Descrizione |
|---|---|---|
| GET | `/` | Interfaccia web principale |
| GET | `/api/status` | Stato dati: conteggi, data aggiornamento, prossime giornate |
| GET | `/api/predict` | Pronostici (params: `range`, `customN`, `decay`, `hf`, `dc`) |
| GET | `/api/standings` | Classifiche per tutti i campionati |
| POST | `/api/scrape` | Scarica/aggiorna dati (body JSON: `{"source": "calciomagazine"|"wikipedia"|"tuttosport"}`) |
| GET | `/api/export` | Esporta il database JSON corrente |
| POST | `/api/import` | Importa un database JSON |
| POST | `/api/reset` | Cancella tutti i dati |

---

## Struttura del database JSON

```json
{
  "version": 9,
  "source": "tuttosport",
  "updatedAt": "2026-02-24",
  "serieB": {
    "results_by_giornata": { "1": [...], "2": [...] },
    "results": [ {"date":"2025-08-22","home":"Pescara","away":"Cesena","hg":1,"ag":3} ],
    "next_giornata": 28,
    "next_fixtures": [ {"date":"2026-02-28","home":"Frosinone","away":"Venezia"} ]
  },
  "serieCa": { ... },
  "serieCb": { ... },
  "serieCc": { ... }
}
```

> Il campo `calendar_by_giornata` è presente solo quando la fonte è `calciomagazine` (che scarica calendari separati). Per Wikipedia e Tuttosport i fixture vengono estratti direttamente dalla stessa pagina dei risultati.

---

## Migrazione da versioni precedenti

Il file JSON è **retrocompatibile**: il caricamento rileva automaticamente versioni precedenti (v6, v7, v8) e aggiunge i campi mancanti.

---

## Note tecniche

- Il server Flask gira su `127.0.0.1:5050` (solo locale, non esposto in rete)
- Il thread Flask è daemon: si chiude automaticamente con la finestra
- I fetch HTTP disabilitano la verifica SSL (`verify=False`) per compatibilità con alcuni proxy aziendali
- BeautifulSoup usa il parser `html.parser` della stdlib (nessuna dipendenza esterna aggiuntiva)
