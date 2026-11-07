# Glossario — consumo e contesto della skill dnd

- **Quota (limite delle 5 ore):** la parte dell'abbonamento claude.ai consumata in una finestra di 5 ore,
  che parte dal primo messaggio. Quando arriva al 100% la partita si ferma fino al reset.
  Claude Code la passa alla statusline in `rate_limits.five_hour.used_percentage`.
- **Contesto:** tutto quello che il modello rilegge a ogni chiamata (istruzioni, file letti, conversazione).
  Si misura con `scripts/context_size.py`.
- **Chiamata:** ogni risposta del modello, cioè ogni giro con uno strumento. Un'azione di un giocatore
  ne costa in media 5–6.
- **Base fissa:** il contesto subito dopo `load` o `resume`, prima di narrare (oggi ~95k).
  Si rilegge in ogni chiamata della conversazione.
- **Checkpoint:** salvataggio a metà sessione (`/dm:dnd checkpoint`), seguito da `/clear` e `/dm:dnd resume`.
  Riporta il contesto alla base fissa.
- **Caldo:** la parte di state.md sempre letta a `load` e `resume` (situazione, flag attivi, fatti fissati,
  note di stile, missioni attive, capitolo corrente). Vedi ADR 0003.
- **Freddo:** la storia che non serve a ogni turno (mosse passate delle fazioni, archivio sessioni,
  capitoli chiusi o futuri). Sta in `state-archive.md` e si consulta con una domanda mirata. Vedi ADR 0004.
- **Indice dell'archivio:** `archive.py index`, poche righe lette a `load` e `resume` per sapere cosa c'è
  nel freddo e quando chiederlo.
- **Rilettura obbligatoria:** la consultazione del freddo prima di un evento che lo tocca, e la sua
  rilettura completa nel `prep` di fine sessione.
- **Sezione situazionale:** una parte delle istruzioni che serve solo in certi momenti (inseguimenti,
  trappole, prep…). Sta in `SKILL-situations.md`. Vedi ADR 0005.
- **Innesco:** la riga dell'indice in SKILL.md che dice quando aprire una sezione situazionale; ripetuta
  dallo script che apre la situazione.
- **Risveglio:** ogni volta che l'attesa dei telefoni (`autorun_wait.py`) finisce e il DM deve rispondere.
- **Pausa naturale:** un momento senza combattimenti, tiri, voti, inseguimenti o enigmi aperti.
  È l'unico momento in cui si può fare checkpoint.
