# ADR 0004 — Il freddo sta in file Markdown, non in un database

Data: 2026-11-07 · Stato: accettata

## Contesto

Valerio aveva chiesto se MySQL o Mongo mi sarebbero stati più comodi. Nel mio contesto entra lo stesso
testo da qualunque posto venga: risparmio solo con domande più precise, non cambiando il contenitore.
Molto del freddo è già strutturato e interrogabile: `factions.json`, `quests.json`, `people.json`,
`timeline.json`, e il grafo delle relazioni (`campaign_graph.py scene-context`).

## Decisione

- Il freddo di state.md va in `state-archive.md`, accanto a state.md, con le stesse intestazioni `##`
  e una sottosezione per voce (fazione, sessione, capitolo).
- Uno script estrae solo il pezzo richiesto (una sezione, una voce, oppure le righe che nominano qualcosa)
  invece di leggere il file intero.
- Niente SQLite, MySQL o Mongo.

## Alternative scartate

- **SQLite:** domande più strutturate, ma ogni scrittura passa da codice nuovo e il file non si legge più a occhio.
- **MySQL/Mongo:** un server sempre acceso da installare, avviare e salvare a parte, senza nessun guadagno in token.
- **Ricerca per significato (embedding):** da riconsiderare solo se le domande mirate si rivelano insufficienti.

## Conseguenze

- Valerio può aprire e correggere il freddo a mano; finisce nei backup zip come tutto il resto.
- Resta compatibile con la skill originale: una campagna senza `state-archive.md` funziona come prima.
