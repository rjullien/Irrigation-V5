# Alignement upstream petergridge/Irrigation-V5

## État

Fork divergé à `V2026.06.01` (`bc74bb9`). Tip upstream : `V2026.07.02` / `V2026.07.03`.

## Features locales conservées

- Low-power / frugal
- Zone status cache (solenoid live)
- Resume mid-cycle après reboot HA
- `msg` / notification partial-setup (issue #171) — consolidé avec upstream `msg_parts`
- Manifest sans dépendance `lovelace` (HA 2026.x)

## Fixes upstream portés (équivalents)

| Upstream | Statut local |
|----------|--------------|
| #305 partial-setup notification | Porté (`msg_parts` agrégé) |
| #307 pump valve-domain / lagging state | Porté (`pump.py`) |
| #309 mutable `last_ran` default | Déjà présent (+ `remaining_override` resume) |
| #311 sunrise NameError | Déjà présent |
| #313 sensor async_update guards | Déjà présent |
| #315 per-program zone queues + IZD guard | Déjà présent (low-power) |

## Non porté volontairement

- Dépendance `lovelace` (cassante HA 2026.x)
- Numérotation / tags upstream (`V2026.07.0x`) — on garde `2026.7.xx` fork
- Contenu exclusif upstream sans équivalent local utile (traductions seules, etc.)
