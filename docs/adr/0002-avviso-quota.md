# ADR 0002 — Avviso della quota: nel terminale e sul display

Data: 2026-11-07 · Stato: accettata

## Contesto

Nella sessione 13 il limite delle 5 ore è arrivato senza preavviso, e nella sessione 5 ha interrotto
il `save` a metà. Claude Code passa alla statusline la percentuale vera del limite:
`rate_limits.five_hour.used_percentage` e `resets_at` (presenti solo per gli abbonati, e solo finché la
finestra è aperta).

## Decisione

- La statusline (`~/.claude/statusline-command.js`) scrive `rate_limits` in un file del runtime della skill
  a ogni aggiornamento.
- **Display:** un indicatore discreto per tutti, ad esempio "quota 72% · reset 01:20".
- **Terminale:** il DM legge il file a ogni pausa naturale (insieme a `context_size.py`) e avvisa a voce:
  - **75%:** "rimane circa un'ora, puntiamo a chiudere la scena".
  - **90%:** chiude la scena e fa subito `save`, che a sua volta consuma quota.
- Se il dato manca (statusline vecchia, finestra appena resettata, nessun abbonamento) non si mostra niente
  e non si avvisa: mai stime.

## Conseguenze

- Dipende da un campo di Claude Code che potrebbe cambiare nome: lo script lo legge in modo tollerante.
- Il file è un dato locale del PC del DM e non va nel repo della campagna.
