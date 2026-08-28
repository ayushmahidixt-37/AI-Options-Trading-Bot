# Evaluation log

Append one row per run, **before** interpreting the numbers. Never edit or delete
a row. A poor result that gets removed is how a search disguises itself as a test.

Mark any run whose data range overlaps 2020-08-03 .. 2026-08-20 as
**CONTAMINATED** — the strategy was derived from that range, so such a run
measures nothing about whether it generalises.

| Date run | Data range | Trades | Net P&L | Win% | PF | Max DD% | Verdict | Notes |
|---|---|---|---|---|---|---|---|---|
| 2026-08-28 | 2024-01-01..2024-09-30 | 37 | -7,676.75 | 27.0% | 0.55 | 19.1% | NOT CONFIRMED (2/6) | **CONTAMINATED** — harness plumbing test only. Range is inside the development archive, so this measures nothing about generalisation. Reproduces the known 2024 losing stretch (quarterly record: -1,256 / -3,982 / -1,780), which is the point: the harness returns the number it should. |

## Standing status

**Open — not Confirmed.** Requires 40+ trades on data after 2026-08-20 meeting all
six criteria in `PROTOCOL.md`. Until then: paper only, 2% risk maximum.
