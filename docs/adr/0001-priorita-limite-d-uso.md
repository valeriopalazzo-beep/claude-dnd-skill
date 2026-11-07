# ADR 0001 — La priorità è il limite d'uso, non il contesto

Data: 2026-11-07 · Stato: accettata

## Contesto

Sessione 13 del Trio (2026-11-06). La finestra delle 5 ore si è aperta alle 20:20 con il `load`
e il limite è arrivato alle 23:06, senza preavviso: partita ferma fino al reset dell'1:20.

Misure dai transcript:

| Conversazione | Chiamate | Contesto iniziale | Contesto medio | Massimo |
|---|---|---|---|---|
| 1 (load) | 193 | 49k | 287k | 403k |
| 2 (resume) | 117 | 95k | 233k | 311k |
| 3 (resume) | 95 | 95k | 215k | 287k |
| 4 (resume, limite) | 119 | 95k | 234k | 318k |

Circa 470 chiamate in 2 ore e 46 minuti. Ogni chiamata rilegge tutto il contesto, quindi il consumo è
più o meno *chiamate × contesto medio*. Dopo ogni `/clear` si riparte da 95k: un terzo del contesto medio
è la base fissa (SKILL.md ~37k, state.md ~24k, world.md ~11k, schede ~9k).

## Decisione

1. L'obiettivo è far durare la quota per tutta la serata e **avvisare prima** che finisca.
2. Il contesto pieno non è un problema: il `/clear` per Valerio vale come una pausa ("andare in bagno").
   Si può fare più spesso, se conviene.
3. Restano escluse le leve bocciate il 2026-10-25: Sonnet, meno ragionamento, meno controlli sul manuale,
   narrazione più corta.

## Conseguenze

- Si può abbassare la soglia del checkpoint, se i numeri lo giustificano.
- Si può dimagrire la base fissa che ogni resume rilegge.
- Serve un avviso basato sul dato vero del limite: la statusline lo riceve (`rate_limits.five_hour`).
