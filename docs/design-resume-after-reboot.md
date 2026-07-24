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
| Pendant le cycle | Checkpoint Store toutes les ~10 s + flush forcé sur pause / `homeassistant_stop` |
| Au stop HA | **Ne pas** fermer les valves ; **conserver** le checkpoint s’il reste des zones ; **effacer** s’il n’en reste plus ; **ne pas** unpause le suivant |
| Au boot | Charger le checkpoint **avant** les platforms ; skip `solenoid_turn_off` pour les solenoids encore en watering ; reprendre avec downtime ajusté |

## Multi-programmes (N ≥ 1)

Le Store est partagé et protégé par un `asyncio.Lock`.

```json
{
  "programs": { "<entry_id>": { "...checkpoint..." } },
  "interlock_queue": ["<entry_id_head>", "<entry_id_next>", "..."]
}
```

- Chaque programme a son checkpoint sous sa clé.
- `interlock_queue` persiste l’ordre `QUEUEDPROGRAMS`.
- Au boot : `async_restore_interlock_queue_ready` attend que tous les programmes **encore chargés** soient dans `PROGRAMS` (timeout 5 s → partial) ; **seul le head** (ou tous si interlock off) arrose immédiatement ; les suivants reprennent **en pause** jusqu’au hand-off (`async_turn_off` du précédent).
- Resume échoué : même barrière (`_ready`) puis pop self + unpause du head suivant.

## Downtime & sécurité

1. **Stale guard** : si `age > Σ(remaining au checkpoint) + 300s` → discard (même si paused).
2. **Paused** (`paused=true`) : **delta = 0** (pause user / attente interlock ≠ arrosage) ; pas de skip-off valve au boot.
3. **Séquentiel (parallel=1)** : downtime consommé zone par zone (running puis file).
4. **Parallèle** : chaque zone *running* perd le downtime **indépendamment** ; file inchangée.
5. **Reconcile T0→T1** : au boot le skip-off utilise un snapshot T0 ; au resume, fermer toute valve encore dans `raw.running` mais plus dans `adjusted.running` (sinon fuite).
6. **Runner paused** : seed `_program_remaining` + boucle tant que `_paused` / zones restantes — ne coupe pas si remaining pas encore recalculé.
7. **Countdown resume** : `calculate_program_remaining` utilise `_resume_overrides` pour les zones encore en file (pas le `default_run_time` plein).
8. **Pause user** : `pause_program` force un save immédiat (`paused=true`) car le monitor skip le save périodique pendant la pause.
9. **Stop HA sans zones** : **efface** le checkpoint (pas de resurrection d’un vieux payload).
10. **Checkpoint writes** : jamais persister un payload `adjusted` (déjà réduit) — toujours le raw / rebuild via `build_checkpoint`.

## Fichiers

- `runtime_checkpoint.py` — Store, lock, `apply_downtime`, restore queue (+ `_ready`)
- `program.py` — save / clear / resume / reconcile / hand-off / pause flush
- `zone.py` — skip startup off + `async_set_resume_state` + `remaining_override`
- `__init__.py` — load checkpoint + **precompute** `apply_downtime` once per entry (shared by all zones)

## Limites (v1)

- Mode volume / eco : un segment temps (`remaining_override`) — **warning log** si wait/repeat restants droppés.
- Flush `homeassistant_stop` fire-and-forget (baseline = checkpoint ~10 s + flush pause).
- Au stop : pas d’unpause du programme suivant (file restaurée au boot).
- Scope production validé : **1 programme, watering time, séquentiel** (Eyguians). Interlock N≥2 / eco / volume = supportés avec les garde-fous ci-dessus, moins smoke-testés.
