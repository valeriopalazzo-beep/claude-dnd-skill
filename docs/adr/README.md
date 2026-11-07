# Decisioni (ADR) — consumo della quota nella skill dnd

Nate dal grill del 2026-11-07. Glossario: [../glossario.md](../glossario.md).

| ADR | Decisione |
|---|---|
| [0001](0001-priorita-limite-d-uso.md) | La priorità è il limite delle 5 ore, non il contesto |
| [0002](0002-avviso-quota.md) | Avviso della quota nel terminale e sul display, al 75% e al 90% |
| [0003](0003-state-caldo-e-freddo.md) | state.md diviso in caldo e freddo, con riletture obbligatorie |
| [0004](0004-freddo-in-markdown.md) | Il freddo sta in `state-archive.md`, niente database |
| [0005](0005-skill-situazionale-con-inneschi.md) | Le sezioni situazionali in `SKILL-situations.md`, con indice degli inneschi |
| [0006](0006-checkpoint-a-200k.md) | Soglia del checkpoint a 200k |
| [0007](0007-meno-chiamate-e-letture.md) | Invio + attesa in una chiamata, resume più leggero, state.md letto una volta |

## Piano di lavoro (una commit per punto)

1. [x] Quota: la statusline salva `rate_limits`, il display mostra l'indicatore, il DM avvisa al 75%/90% (ADR 0002) — `5f36ce9`, `scripts/quota.py`
2. [x] `context_size.py`: limite a 200k (ADR 0006) — `5d726c6`
3. [x] `send.py … && autorun_wait.py` in una chiamata, in primo piano (ADR 0007) — `18a4f28`
4. [x] Resume: solo la voce "in corso" del session-log; state.md una volta per conversazione (ADR 0007) — `4d8790a`, `scripts/session_log.py`
5. [x] `SKILL-situations.md` + indice degli inneschi + promemoria negli script (ADR 0005) — `71f91ee`
6. [x] `state-archive.md` + script di estrazione + riletture obbligatorie + spostamento al `save` (ADR 0003, 0004) — `4ab2559`, `79ce721`, `scripts/archive.py`
7. [x] Migrazione del Trio e della Corona, con backup `pre-archivio` prima (dati delle campagne, nessuna commit)
8. [x] ADR e glossario

Fatto il 2026-11-07. Risultati della migrazione:

| | prima | dopo |
|---|---|---|
| SKILL.md | 131 KB | 95 KB (40 KB in `SKILL-situations.md`) |
| state.md del Trio | 83 KB | 46 KB |
| state.md della Corona | 47 KB | 36 KB |
| session-log letto al resume (Trio) | 19 KB | 8 KB (solo la voce in corso) |

Alla prossima sessione: misurare chiamate, contesto medio e ora del limite con l'analisi dei transcript
e confrontarli con la sessione 13 (limite alle 23:06, ~470 chiamate, contesto medio ~240k).
