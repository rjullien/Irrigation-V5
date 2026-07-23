# Design — reprise du cycle d’arrosage après reboot HA

## Problème

Un reboot HA pendant un cycle :

1. Annule les tâches asyncio (`async_turn_on` / `async_turn_on_from_program`) sans cleanup.
2. Au redémarrage, `Zone.async_added_to_hass` forçait `solenoid_turn_off` → coupe l’eau.
3. Aucune reprise : le programme repart `off`, les zones suivantes ne tournent pas.

Incident Eyguians 2026-07-23 : cycle en cours (zone 2) coupé net au restart.

## Décision

**Reprendre le cycle** (pas seulement fermer proprement) :

| Phase | Comportement |
|-------|----------------|
| Pendant le cycle | Checkpoint Store toutes les ~10 s + flush sur `homeassistant_stop` |
| Au stop HA | **Ne pas** fermer les valves ; **ne pas** effacer le checkpoint |
| Au boot | Charger le checkpoint **avant** les platforms ; skip `solenoid_turn_off` pour les solenoids encore en watering ; reprendre avec `remaining − downtime` |

## Checkpoint (`irrigationprogram.runtime_checkpoint`)

```json
{
  "programs": {
    "<entry_id>": {
      "version": 1,
      "scheduled": true,
      "start_time": "...",
      "checkpoint_ts": "...",
      "running": [{"solenoid": "valve.x", "remaining_s": 512}],
      "remaining": [{"solenoid": "valve.y", "remaining_s": 1800}]
    }
  }
}
```

- Zones **running** : remaining diminué du downtime.
- Zones **queued** : remaining inchangé (pas encore d’eau).
- Si running expire pendant le downtime → passer à la suite de la file.

## Fichiers

- `runtime_checkpoint.py` — Store helpers + `apply_downtime`
- `program.py` — save / clear / resume + listener STOP
- `zone.py` — skip startup off + `remaining_override` dans `time()`
- `__init__.py` — load checkpoint before `async_forward_entry_setups`

## Limites (v1)

- Mode volume / eco wait-repeat : reprise simplifiée (un segment temps = remaining).
- Pas de reprise si checkpoint absent / corrompu / tout déjà écoulé.
- Pendant le trou reboot (~1–2 min), l’eau continue côté hardware si la valve était ouverte — voulu.
