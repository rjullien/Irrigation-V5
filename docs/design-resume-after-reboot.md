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
| Au boot | Charger le checkpoint **avant** les platforms ; skip `solenoid_turn_off` pour les solenoids encore en watering ; reprendre avec downtime ajusté |

## Downtime

1. **Stale guard** : si `downtime > Σ(remaining au checkpoint) + 300s` → discard (pas de resume fantôme après une longue panne).
2. **Running** : remaining − downtime (ordre).
3. **Séquentiel (parallel=1)** : le surplus de downtime **déborde** sur la file (zones raccourcies / sautées) pour coller à la timeline prévue.
4. **Parallèle** : file inchangée (seules les zones running sont réduites).

Note hardware : pendant l’outage seule la valve running reste ouverte ; l’ajustement de la file est un choix de **cohérence d’horaire**, pas une reconstitution volume parfaite.

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

`entry_id == program_unique_id`. Writes passent par un `asyncio.Lock` (read-modify-write atomique multi-programmes).

Flush `homeassistant_stop` : fire-and-forget ; le checkpoint périodique ~10 s reste la baseline durable (Store = write+rename atomique).

## Fichiers

- `runtime_checkpoint.py` — Store helpers + `apply_downtime` + lock
- `program.py` — save / clear / resume + listener STOP
- `zone.py` — skip startup off + `async_set_resume_state` + `remaining_override`
- `__init__.py` — load checkpoint before `async_forward_entry_setups`

## Limites (v1)

- Mode volume / eco wait-repeat : reprise = **un** segment temps (`remaining_override`), wait/repeats restants droppés.
- Pas de reprise si checkpoint absent / corrompu / trop vieux / tout déjà écoulé.
- Pendant le trou reboot, l’eau continue côté hardware si la valve était ouverte — voulu.
