# Alignement upstream petergridge/Irrigation-V5

## État

Fork divergé à `V2026.06.01` (`bc74bb9`). Tip upstream : **`V2026.08.04`**.

Tip fork : **`V2026.07.27`** (manifest `2026.7.27`) — port chirurgical des correctifs August + gap `delay_time` (voir PR « port upstream August »).

Histories divergées (~39 ahead / ~19 behind avant ce port). **Pas de merge aveugle.**

Document mémo retard (ne pas merger) : PR #16 / [`docs/upstream-lag.md`](upstream-lag.md) sur la branche docs — **laisser ouvert**, ce port August est une PR séparée.

## Features locales conservées

- Low-power / frugal
- Zone status cache (solenoid live)
- Resume mid-cycle après reboot HA
- Tuya close-on-lag
- `freq_start_date` / Créneau rotation
- Options : zones sans freq/eco + persist `low_power`
- `msg` / notification partial-setup (issue #171) — consolidé avec upstream `msg_parts`
- Manifest **sans** dépendance `lovelace` (HA 2026.x)

## Fixes upstream portés (équivalents)

| Upstream | Statut local |
|----------|--------------|
| #305 partial-setup notification | Porté (`msg_parts` agrégé) |
| #307 pump valve-domain / lagging state | Porté (`pump.py`) |
| #309 mutable `last_ran` default | Déjà présent (+ `remaining_override` resume) |
| #311 sunrise NameError | Déjà présent |
| #313 sensor async_update guards | Déjà présent |
| #315 per-program zone queues + IZD guard | Déjà présent (low-power) |
| **V2026.07.01 `delay_time` sensor** | **Porté (gap manquant après align #10)** |
| **V2026.08.01 rain delay next-run** | **Porté (`zone.py` + `delay_time`)** |
| **V2026.08.01 offline zone edit** | **Porté (`config_flow` + `switch.py`)** |
| **V2026.08.01 pause-on-start (water source)** | **Porté (`program.py` / zone status)** |
| **V2026.08.02–.04 card YAML quotes + card optim** | **Porté (`program.py` YAML + `irrigation-card.js`)** |

## Non porté volontairement

- Dépendance `lovelace` (cassante HA 2026.x)
- Refactor massif `sensor.py` vers `_attr_native_value` (non requis pour les fixes August ; éviter de casser le throttle low-power)
- Numérotation / tags upstream (`V2026.08.0x`) — on garde `2026.7.xx` fork (`2026.7.27` = align August)
- Contenu exclusif upstream sans équivalent local utile

## Residual behind / ahead

Après ce port : les commits July listés ci-dessus restent couverts par équivalents locaux ; le delta August utile est porté. Le fork reste **ahead** (safeguards + features locales) et peut rester **légèrement behind** sur commits upstream purement cosmétiques / lovelace / numérotation.
