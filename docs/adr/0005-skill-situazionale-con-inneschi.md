# ADR 0005 — Le sezioni situazionali di SKILL.md in un file a parte, con inneschi

Data: 2026-11-07 · Stato: accettata

## Contesto

SKILL.md pesa 130 KB (~37k token) ed entra intero a ogni `load` e `resume`. Circa 45 KB riguardano
situazioni che capitano solo a volte: Session Prep, Images & Maps, XP Awards, Tutor Mode,
Guided entry, Directory Layout, inseguimenti, trappole, provviste, malattie, roccaforte,
tempo libero, enigmi, voti, prove di gruppo, viaggio e simili.

## Decisione

- Le sezioni situazionali vanno in `SKILL-situations.md`, senza cambiarne il testo.
- In SKILL.md resta un **indice degli inneschi**: una riga per sezione nella forma
  "quando succede X → leggi *Sezione* in SKILL-situations.md prima di risolvere", scritta come obbligo.
- Il cuore resta in SKILL.md: Active DM Mode, regole 5e obbligatorie, standard del DM,
  convenzioni sui dadi e tutte le regole del tavolo (reazioni, aspettare tutti, dadi dal telefono…).

## Come non dimenticarle (condizione di Valerio)

1. L'indice degli inneschi sta nel cuore, quindi è sempre davanti a me.
2. Rete di sicurezza negli script: il comando che apre una situazione (per esempio l'avvio di un
   inseguimento o di un viaggio) stampa una riga che ricorda di leggere la sezione corrispondente.
   Il promemoria arriva proprio nel momento in cui serve.
3. Una sezione letta resta in contesto fino al prossimo `/clear`: dopo un `resume`, alla situazione
   successiva la si rilegge.

## Conseguenze

- Circa 12k token in meno su ogni chiamata. Spostate 16 sezioni (40 KB): Guided entry, Session Prep,
  Directory Layout, Votes, Downtime, stronghold, Group checks, Chases, XP Awards, Puzzles, Supplies, On the road,
  Diseases, Traps, Images & Maps, Tutor Mode. Un test (`tests/test_situations.py`) tiene d'accordo inneschi,
  sezioni e promemoria degli script.
- Le sezioni spostate vanno tenute sincronizzate con upstream come blocchi interi, perché il testo non cambia.
