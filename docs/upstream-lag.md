# Analyse du retard fork vs upstream

> **Statut :** document de référence uniquement — **ne pas merger** de sync upstream automatique sur la base de ce document.  
> **Objectif :** mémoriser l’écart entre le fork `rjullien/Irrigation-V5` et l’upstream `petergridge/Irrigation-V5`, sans déclencher de réconciliation.

## English summary

Fork tip **V2026.07.26.2** vs upstream **V2026.08.04**. On `main`, the histories have diverged (~**39 ahead / 19 behind**). The fork carries intentional local behavior (low-power, resume-after-reboot, zone/valve cache, Tuya close-on-lag, `freq_start_date`, Créneau rotation, options fixes). Do **not** blind-merge upstream; when ready, cherry-pick / reconcile carefully.

---

## Versions

| Côté | Dépôt | Dernière release / tip | Notes |
|------|--------|-------------------------|--------|
| **Fork** | [rjullien/Irrigation-V5](https://github.com/rjullien/Irrigation-V5) | **V2026.07.26.2** | Manifest local `2026.7.26` ; fonctionne bien en prod (Eyguians) après nombreuses mods |
| **Upstream** | [petergridge/Irrigation-V5](https://github.com/petergridge/Irrigation-V5) | **V2026.08.04** | Tip `main` upstream au moment de cette analyse |

Comparaison `main` (fork…upstream) :

```text
~39 ahead  /  ~19 behind
```

Les historiques ont **divergé** : le fork n’est plus un simple fast-forward derrière upstream.

Document d’alignement antérieur (partiellement obsolète) : [`docs/upstream-alignment.md`](upstream-alignment.md).

---

## Modifications notables du fork (résumé historique)

Issues / comportements locaux intentionnels, issus de l’historique git du fork :

1. **Low-power / perf** — mode frugal, réduction charge CPU/SD sur hôtes contraints (`perf/frugal-lowpower`, release `V2026.06.01-lowpower.1`).
2. **Reprise après reboot HA** — checkpoint + reprise mid-cycle (ne pas couper les valves au restart) ; voir [`design-resume-after-reboot.md`](design-resume-after-reboot.md).
3. **Cache statut zone / valve** — refresh depuis l’état live du solenoid ; évite les démarrages silencieux abandonnés après panne Tuya ; voir [`design-zone-status-valve-cache.md`](design-zone-status-valve-cache.md).
4. **Close-on-lag Tuya** — fermer les valves après `open` non confirmé pour éviter la cascade d’ouvertures retardées ; voir [`design-solenoid-close-on-lag.md`](design-solenoid-close-on-lag.md).
5. **`freq_start_date`** — décalage déterministe des cycles fréquence (ex. arrosage moitié/moitié) ; voir [`eyg-half-half-watering.md`](eyg-half-half-watering.md).
6. **Select Créneau (rotation)** — sélection du créneau pour les slots de rotation multi-programmes.
7. **Fix zones sans freq/eco + persist `low_power`** — tolérance options (`config_flow`) + persistance du mode low-power (`V2026.07.26.2`).
8. **Tests / docs** — corrections de tests, notes de design et release notes locales.

Autres écarts structurels connus : manifest **sans** dépendance `lovelace` (compat HA 2026.x) — upstream la conserve encore.

---

## Pourquoi ne pas auto-merger / syncer maintenant

- **Divergence réelle** (~39 / ~19) : un merge ou un sync aveugle risquerait des conflits massifs et des régressions.
- **Comportements locaux volontaires** : reprise après reboot, cache valve, close-on-lag, low-power, `freq_start_date`, Créneau — le fork « marche bien » en l’état ; René **ne souhaite pas** de sync upstream pour l’instant.
- **Upstream apporte aussi des changements** (dont dépendance `lovelace`, tags / numérotation, fixes déjà partiellement portés ou à réévaluer) qui ne se transposent pas en fast-forward.

Ce document sert de **rappel d’équipe** : le retard est connu et accepté.

---

## Action future suggérée (quand l’équipe sera prête)

1. **Ne pas** faire de sync / merge aveugle de `petergridge/Irrigation-V5` vers `main`.
2. **Inventorier** les commits upstream encore absents (les ~19 behind) et les features fork à préserver (les ~39 ahead).
3. **Cherry-pick / réconcilier** commit par commit (ou petit lot thématique), avec tests et validation terrain.
4. Mettre à jour ce fichier et [`upstream-alignment.md`](upstream-alignment.md) après chaque portage réussi.

---

## Références rapides

| Élément | Lien / valeur |
|---------|----------------|
| Fork tip | `V2026.07.26.2` |
| Upstream tip | `V2026.08.04` |
| Écart `main` | ~ahead 39 / behind 19 |
| Alignement antérieur | [`upstream-alignment.md`](upstream-alignment.md) |

*Document rédigé pour garder la mémoire du lag — PR documentation seule, à laisser ouverte / non mergée si utilisée comme balise d’équipe, ou merger uniquement le fichier MD sans sync de code.*
