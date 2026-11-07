# ADR 0003 — state.md diviso in caldo e freddo, con riletture obbligatorie

Data: 2026-11-07 · Stato: accettata (formato del freddo da decidere, vedi ADR 0004)

## Contesto

Nel Trio state.md pesa 85 KB (~24k token). Dopo la migrazione del 2026-10-25 pesava 30 KB: ricresce a
ogni sessione. Lo leggo a ogni `load` e a ogni `resume`, e nella sessione 13 l'ho letto due volte nella
stessa conversazione. Le sezioni più pesanti sono quasi tutte storia: Faction Moves 17 KB,
Live State Flags 14, Recent Events 13, Campaign Arc 12, Continuity Archive 7.

## Decisione

**Caldo** (sempre letto a `load` e `resume`): Current Situation, Pinned Facts, Live State Flags
ancora attivi, DM Style Notes, Active Quests, il capitolo corrente del Campaign Arc, Session Flags,
le ultime Recent Events, Wish List.

**Freddo** (consultato su richiesta): le mosse passate delle fazioni, l'archivio delle sessioni,
i capitoli dell'arco già chiusi o futuri, i flag esauriti, gli eventi più vecchi.

Valerio accetta lo scambio a una condizione: **il freddo non va dimenticato**. Riletture obbligatorie:

1. **Prima di un evento che lo tocca**, una domanda mirata: prima di una mossa di fazione la sua storia;
   quando torna un NPC già incontrato la sua scheda e le sue relazioni; quando si torna in un luogo
   già visitato quello che vi è successo; quando avanza una missione la sua storia.
2. **A fine sessione, nel `prep`**, il freddo si rilegge tutto. È il momento in cui si scrivono i segreti
   e gli indizi della sessione dopo, cioè il modo in cui i fatti lontani tornano "caldi".
3. **Al `save`** quello che non è più attivo scende nel freddo, così il caldo non ricresce.

## Conseguenze

- La base fissa scende di 15–20k token per chiamata.
- Le regole di rilettura vanno scritte in SKILL.md come obbligo, non come consiglio.
- Il `prep` diventa più caro (una lettura completa in più), ma una volta per sessione e in una
  conversazione che poi si chiude.
