# Design — Statut de zone vs état réel des valves

**Contexte Eyguians (juillet 2026)** : après une panne Tuya (`valve.*` → `unavailable`), les valves sont revenues `closed`, mais le programme d’arrosage n’a pas démarré aux horaires suivants. Un changement manuel de durée a « débloqué » le système. Cause : le **cache de statut de zone** n’était plus aligné avec l’état live des valves.

**Décision** : corriger dans `irrigationprogram` (pas de rustine HA). Deux comportements complémentaires.

---

## Problème en une phrase

Le start scheduled lit une **étiquette en cache** (`Etat de la Zone`). Si elle dit encore `unavailable` alors que la valve est déjà OK, le cycle est abandonné en silence.

---

## Architecture actuelle (simplifiée)

```
valve.* (tuya_local)     ← état live
        │
        │  open / close (commandes)
        ▼
irrigationprogram
  • monitor (horaires, freq, enable, temps d’arrosage, …)
      → update_next_run → calc_next_run → get_status()
      → met à jour sensor « Etat de la Zone »  (= cache)
  • scheduler (chaque minute, match horaire)
      → should_run_ex() lit le cache
      → si OK → open valve
```

**Trou** : le monitor **n’écoute pas** les `valve.*` (solenoid).  
Retour `unavailable` → `closed` = aucun refresh du cache.

---

## Comportements cibles

### 1. Re-check live au moment d’ouvrir

**Quand** : start scheduled (ou path équivalent), avant d’exclure une zone.

**Si** le cache dit `unavailable` (ou équivalent « device pas joignable ») :

1. Relire l’état **live** de la valve (`solenoid_state` / `get_status()`).
2. Si la valve est réellement OK (`closed` / `open` / off/on selon type) → **autoriser** la zone et **mettre à jour le cache**.
3. Si vraiment unavailable → refuser la zone (comme aujourd’hui).

**Effet** : même si le cache est stale au coup de 20:25, on ne rate pas le cycle.

### 2. Changement d’état valve → refresh du cache

**Quand** : toute transition d’état sur l’entité solenoid de la zone (`valve.*` ou switch selon config).

**Alors** : déclencher le même chemin que les autres moniteurs (`update_next_run` debouncé) pour recalculer statut + `next_run`.

**Effet** : dès que les valves reviennent, le tableau de bord et le cache sont justes **sans** toucher à une durée.

---

## Pourquoi les deux

| Mécanisme | Rôle |
|-----------|------|
| (2) Monitor solenoid | Cache / UI à jour en continu |
| (1) Re-check au start | Filet si un recalc a été manqué (debounce, boot, charge Pi) |

Pas redondant : (2) pour la vérité au fil de l’eau, (1) pour ne jamais rater un horaire sur un cache pourri.

---

## Hors scope

- Failsafe YAML Eyguians (durée / perte contrôleur) — filet physique, inchangé.
- Watchdog Notification Manager — alertes only.
- Contournement HA du type « toggle `number.temps_d_arrosage` » — rustine, à supprimer une fois le fix livré.

---

## Implémentation prévue (Irrigation-V5)

| # | Fichier | Changement |
|---|---------|------------|
| 2 | `program.py` | `monitor_append(zone.solenoid, …)` pour chaque zone (avec debounce existant / `low_power_mode`) |
| 1 | `zone.py` `should_run_ex` | Si cache ∈ {unavailable, …} → `get_status()` live ; si OK → refresh status sensor et continuer |

**Option perf** (si Tuya trop bavard sur Pi3) : ne déclencher le monitor solenoid que sur transitions impliquant `unavailable` / `unknown`. À trancher après test Eyg.

**Observabilité** : un `_LOGGER.info` (ou debug) quand un start aurait été skippé sur cache stale mais sauvé par le re-check live — utile pour le prochain incident.

---

## Tests

1. Unit : cache `unavailable`, solenoid mock `closed` → `should_run_ex(scheduled=True)` → `True` + status rafraîchi.
2. Unit : cache `unavailable`, solenoid mock `unavailable` → `False`.
3. Unit / intégration légère : event state_change solenoid → `update_next_run` appelé (debounce OK).
4. Regress : rain / zone disabled / next_run futur inchangés.

---

## Déploiement Eyguians

1. Release Irrigation-V5 + `./sync_prod/sync_to_ha.sh --irrigation --apply` (cible Eyg).
2. Remettre `custom_components.irrigationprogram` en `warning` dans le logger après validation.
3. Vérifier logbook au prochain retour de valves ou au prochain 20:25.

---

## Références incident

- Eyguians Tuya LK06 `192.168.1.14` — unavailable prolongé (#171 Homeassistant_automation).
- Logbook : dernier cycle auto 20/07 ; valves de retour 22/07 ~18:49 ; starts manqués 22–23 à 20:25 ; déblocage après edit durée 23/07 20:32 ; starts OK en test 20:59 / 21:08.
