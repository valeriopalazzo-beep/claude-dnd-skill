# ADR 0007 — Meno chiamate per risveglio e meno letture ripetute

Data: 2026-11-07 · Stato: accettata

## Contesto

Sessione 13, terza conversazione: 43 risvegli dai telefoni e 123 chiamate in 41 minuti. Quasi ogni
risveglio costava due chiamate: `send.py` per la narrazione e `autorun_wait.py` per ripartire. Il display
raggruppa già le azioni (fuori dal combattimento aspetta tutto il gruppo), quindi i risvegli sono necessari.
Il resume leggeva tutto session-log.md (~18k token) e state.md è stato letto due volte nella stessa
conversazione.

## Decisione

1. **Invio e attesa nella stessa chiamata:** `send.py … && autorun_wait.py`, in primo piano. Se l'invio
   fallisce il comando esce subito, quindi l'errore si vede subito. Da 2 chiamate a 1 per risveglio.
   *(Correzione del 2026-11-07, in implementazione: nel grill era scritto "in background", ma una chiamata
   in background costa un giro in più — il risultato "avviato" va letto, poi arriva la notifica. L'attesa
   resta in primo piano come prima; il risparmio viene dall'unire le due chiamate.)*
2. **Il resume legge solo la voce "in corso"** del session-log scritta dal checkpoint, non il file intero.
3. **state.md si legge una volta per conversazione;** dopo si consultano le singole sezioni.

## Conseguenze

- Circa un terzo di chiamate in meno durante il gioco libero.
- Le eccezioni restano: quando serve leggere un risultato prima di narrare (dadi, lookup), quella lettura
  è una chiamata a parte, raggruppata come già prevede *One call where one is enough*.
