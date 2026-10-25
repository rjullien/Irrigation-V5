# Inventaire actuel des différences fork / upstream

**English summary:** Fork version `2026.8.4.1` is based on upstream `V2026.08.04`.
`git rev-list --left-right --count V2026.08.04...origin/main` returned `0 46` on 2026-10-25.
The fork keeps deliberate local behavior; port upstream changes deliberately and record ancestry (see `AGENTS.md`).

## État de référence

Inventaire vérifié le **2026-10-25** sur `origin/main`. Le tag upstream
`V2026.08.04` (`6f547cf`) est ancêtre de la branche :

```text
$ git rev-list --left-right --count V2026.08.04...origin/main
0 46
```

Le manifest du fork est versionné `2026.8.4.1` : base upstream `2026.8.4`,
première révision fork `.1`. Le diff complet de code, tests et documentation
représente 57 fichiers (`+5774/-1746`).

Pas de merge aveugle : porter et tester les changements un par un, puis
enregistrer l’ascendance avec `git merge -s ours <tag>` si le port a été fait
manuellement. Procédure complète : [`AGENTS.md`](../AGENTS.md).

## Différences fonctionnelles restantes

| Fonctionnalité | Fichiers | Commits fork | Raison / comportement | Tests présents | État upstream vérifié au 2026-10-25 |
|---|---|---|---|---|---|
| Mode frugal et baisse de charge CPU/recorder | `program.py`, `sensor.py`, `zone.py`, `config_flow.py`, `const.py` | `d557100`, `5c7156f` | Limite les écritures d’entités de compte à rebours sans fausser le temps interne; option basse consommation. | `test_perf_frugal.py` | La recherche ciblée GitHub « low power » n’a retourné aucun PR; cela ne prouve pas l’absence d’une proposition sous un autre titre. |
| Reprise du cycle après redémarrage HA | `runtime_checkpoint.py`, `program.py`, `zone.py`, `__init__.py`, `const.py` | `3431062`, `660a91e`, `b3cc34f`, `c71249c` | Persiste le checkpoint, reprend le temps restant et restaure la file/interlock sans couper les vannes encore nécessaires. | `test_runtime_checkpoint.py` | La recherche ciblée « resume after reboot » n’a retourné aucun PR; état non conclu au-delà de cette recherche. |
| Rafraîchissement du statut zone depuis la vanne | `zone.py`, `program.py`, `const.py` | `b55beb1`, `43d333f` | Si le cache est absent ou inconnu, relit l’état live du solénoïde plutôt que de laisser un statut zone périmé. | `test_zone_status_cache.py` | La recherche ciblée « zone status cache » n’a retourné aucun PR; état non conclu au-delà de cette recherche. |
| Fermeture après ouverture Tuya non confirmée | `zone.py` | `a030f35` | Après un `open` non confirmé, émet une fermeture et attend une fenêtre de stabilisation pour prévenir les ouvertures retardées en cascade. | `test_solenoid_close_on_lag.py` | La recherche ciblée « close on lag » n’a retourné aucun PR; état non conclu au-delà de cette recherche. |
| Décalage déterministe des cycles fréquents | `config_flow.py`, `const.py`, `__init__.py`, `zone.py`, `strings.json` | `602e02c` | `freq_start_date` permet d’ancrer le cycle de fréquence sur une date choisie. | `test_freq_start_date.py`, `test_rain_delay_august.py` | Le PR upstream [#299](https://github.com/petergridge/Irrigation-V5/pull/299) est **fermé sans merge** (`merged_at: null`). |
| Sélecteur de créneau de rotation | `rotation.py`, `select.py`, `const.py`, `icons.json`, `strings.json` | `d148dc0` | Le select « Créneau » choisit le jour du cycle pour alterner les programmes. | `test_rotation.py`, `test_select.py` | La recherche ciblée « rotation » n’a retourné aucun PR; état non conclu au-delà de cette recherche. |
| Tolérance aux options de zone partielles et persistance low-power | `config_flow.py` | `5c7156f` | Accepte des dictionnaires de zone historiques sans `freq`/`eco` et conserve `low_power` dans les options. | `test_perf_frugal.py` couvre le comportement low-power; aucun test dédié du repli `freq`/`eco` n’a été identifié. | Aucun statut PR dédié vérifié. |
| Fusion des options partielles avec les données stockées | `__init__.py`, `config_flow.py`, `diagnostics.py`, `select.py` | `9396b48` | Les options HA deviennent un overlay sur `entry.data`, au lieu de remplacer toute la configuration même si elles ne sont que partielles. | `test_init.py` (`test_async_setup_entry_merges_partial_options`), `test_select.py` | La recherche ciblée « partial options » n’a retourné aucun PR; état non conclu au-delà de cette recherche. |
| Recalcul du prochain cycle après rain delay | `program.py`, `zone.py` | `4cfa3b8` | Utilise l’heure effective de délai et ne décale pas le cycle depuis le `last_updated` du switch de rain delay. | `test_rain_delay_august.py` | Les PRs upstream #305, #307, #309, #311, #313 et #315 sont fermées et fusionnées; le statut de cette adaptation n’est pas établi par ces PRs. |
| Setup d’une zone offline et pause au démarrage | `config_flow.py`, `zone.py` | `4cfa3b8` | Garde les zones configurées même si leur entité est momentanément absente, avec l’ID comme nom de repli. | `test_pause_on_start_august.py` | Les PRs upstream #305, #307, #309, #311, #313 et #315 sont fermées et fusionnées; le statut de cette adaptation n’est pas établi par ces PRs. |
| Reset du switch rain-delay au démarrage | `switch.py` | `4cfa3b8` | `EnableRainDelay` démarre systématiquement sur `off` au lieu de restaurer son dernier état. | `test_switch.py` couvre l’entité et son état par défaut; aucun test dédié du reset au démarrage n’a été identifié. | Aucun statut PR dédié vérifié. |
| Dépendance Lovelace facultative | `manifest.json` | `b57f8e6`, `43fee35` | Retire `lovelace` de `dependencies` et le place dans `after_dependencies` pour ne pas imposer cette intégration au démarrage. | Aucun test dédié identifié. | Le tag `V2026.08.04` garde `lovelace` dans `dependencies`; le fork le rend facultatif. |

### Vérifications de changements évoqués dans l’historique

Les PRs upstream #305 (notification de setup partiel), #307 (pompe `valve`),
#309, #311, #313 et #315 sont fermées et fusionnées selon l’API GitHub.
`pump.py` et `tests/test_pump.py` ne diffèrent pas du tag : le support de pompe
`valve` est donc aligné, pas une différence fonctionnelle fork à maintenir.
Le comportement d’avertissement d’initialisation de #305 est aussi présent
dans le tag; les différences résiduelles de `__init__.py` portent notamment
sur la reprise checkpoint et la fusion des options.

## Différences non fonctionnelles

| Domaine | Fichiers | Contenu vérifié |
|---|---|---|
| CI et environnement de test | `.github/workflows/HACS.yml`, `.github/workflows/pytest.yml`, `.gitignore`, `requirements-test.txt`, `tests/` | Ajout du workflow pytest et de son cache de dépendances; HACS ignore `issues`/`topics` pour ce fork privé; dépendances et fixtures de test ajoutées ou adaptées. |
| Chaînes et traductions | `strings.json`, `translations/*.json` | `strings.json` ajoute six clés; les traductions anglaise et française ajoutent cinq clés chacune. `es.json` modifie deux valeurs et `nl.json` trois; les autres traductions comparées sont identiques au tag. |
| Documentation et consignes | `RELEASE_NOTES.md`, `docs/design-*.md`, `docs/eyg-half-half-watering.md`, `docs/upstream-alignment.md`, `AGENTS.md`, `CLAUDE.md` | Notes de conception et historique de port, consignes d’ascendance, tests et conventions de version. |

## Pièges pour la prochaine synchronisation

- `zone.py` : cache de statut, reprise, fermeture après commande Tuya non confirmée et fréquence.
- `program.py` et `__init__.py` : checkpoint, files/interlocks, low-power et fusion des options.
- `sensor.py` / `switch.py` : fréquence des écritures, pause et états restaurés.
- `config_flow.py`, `select.py` et `rotation.py` : options fork et Créneau.
- `strings.json` et `translations/` : nouvelles clés à conserver lors d’une régénération.
- `manifest.json` : version fork et dépendance Lovelace facultative.

Vérifier chaque conflit contre le comportement décrit ici et les tests cités;
ne pas résoudre par remplacement global d’un côté.
