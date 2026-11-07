# ADR 0006 — Soglia del checkpoint a 200k

Data: 2026-11-07 · Stato: accettata

## Contesto

Il consumo segue il contesto medio. Nella sessione 13 il contesto cresceva di circa 5–6k token al minuto
di gioco, e il resume impiegava circa un minuto. Con la base attesa di ~55k (ADR 0003 e 0005):

| Soglia | Pausa circa ogni | Contesto medio | Rispetto a oggi (~240k) |
|---|---|---|---|
| 300k | 45 min | ~175k | −25% |
| 200k | 25 min | ~125k | −45% |
| 150k | 17 min | ~100k | −55% |

## Decisione

`context_size.py` passa da `DEFAULT_LIMIT = 300_000` a `200_000`. Restano invariate le regole del
checkpoint: solo nelle pause naturali, mai con un combattimento, un tiro, un voto, un inseguimento
o un enigma aperti. Il `/clear` lo scrive Valerio.

## Conseguenze

- Circa una pausa ogni 25 minuti, che Valerio considera accettabile (una pausa "per il bagno").
- Se in un combattimento lungo si supera la soglia, si aspetta la fine: la soglia è un invito, non un obbligo.
- Da verificare alla prossima sessione con le misure reali dei transcript.
