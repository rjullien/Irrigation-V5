# Plan Eyguians — arrosage moitié / moitié (jours alternés)

## Objectif

Jour A : secteurs 1–3 · Jour B : secteurs 4–6 (ajustable).  
Fréquence 2, décalage permanent même après reboot.

## Prérequis

Release avec `freq_start_date` (cette PR / manifest `2026.7.25`).

## Config HA

Créer **2 programmes** Irrigation (interlock ON recommandé) :

| Programme | Zones | Fréquence | `freq_start_date` | Heure |
|-----------|-------|-----------|-------------------|-------|
| Arrosage Jour A | valves 1, 2, 3 | `2` | `2026-07-23` | ex. 22:10 |
| Arrosage Jour B | valves 4, 5, 6 | `2` | `2026-07-24` | ex. 22:10 |

Règle : `(aujourd'hui - freq_start_date).days % 2 == 0` → jour d’arrosage.

Avec les dates ci-dessus :
- 23, 25, 27… → Jour A
- 24, 26, 28… → Jour B

Désactiver / retirer les zones du programme unique actuel pour éviter le double arrosage.

## Champ UI

Dans la config programme : **Date de début de cycle (AAAA-MM-JJ)**.  
Laisser vide = comportement legacy (`last_ran`).
