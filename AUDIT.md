# OMEGA — Audit Complet & Liste des Bugs

Date de l'audit : 2026-06-20
Auditeur : ZCode
Tests smoke avant correction : **12/12 PASS** (tests trop superficiels — ne couvrent pas les flux intégrés)

## Méthodologie
Lecture intégrale des 53 fichiers Python (6 couches + orchestrator + scripts + tests), puis
vérification fonctionnelle de chaque bug suspecté via des scripts de reproduction ciblés.

---

## 🔴 BUGS CRITIQUES (cassent le système en production)

### C1. Pipeline de trading live CASSÉ — `risk_aegis.on_market()` jamais appelé
**Fichier** : `omega/orchestrator.py` (`_on_market`, ligne ~139-151)
**Symptôme** : En live trading, **AUCUN ordre n'est jamais émis**.
**Cause** : `_process_signals()` lit le prix courant via `self.risk_aegis.portfolio_heat._last_prices.get(symbol)`,
mais ce dict n'est **jamais peuplé** car `risk_aegis.on_market()` n'est appelé nulle part dans l'orchestrateur.
Résultat : `price <= 0` → warning "No price available" → tous les signaux sont skip.
**Reproduction** : confirmée (voir ci-dessus).
**Correctif** : appeler `self.risk_aegis.on_market(event)` dans `_on_market()` avant le sizing.

### C2. Agent PPO mélange l'état de tous les symboles
**Fichier** : `omega/alpha_swarm/ppo_agent.py`
**Symptôme** : `_history`, `_last_action`, `_last_price`, `buffer` sont des attributs uniques partagés
entre tous les symboles. Quand l'orchestrateur crée `PPOAgent(symbols=('BTCUSDT','ETHUSDT','SOLUSDT'))`,
les barres BTC/ETH/SOL sont mashées dans le même buffer → features incohérentes → signaux garbage.
**Reproduction** : confirmée — `_history` mélange les symboles.
**Correctif** : per-symbol state dans le PPO agent (`_history[sym]`, `_last_action[sym]`, `_entry_price[sym]`).

### C3. Récompense "meanrev" identique à "trend" (terme momentum = ret)
**Fichier** : `omega/alpha_swarm/ppo_agent.py` (`_compute_reward`, lignes 387-391)
**Symptôme** : Pour le mode meanrev, `momentum = (price - prev_price)/prev_price` est calculé
avec **la même formule** que `ret`. Donc `pnl = pnl - 0.3*direction*momentum` = scaling trivial
à 0.7x, pas une vraie récompense de mean-reversion.
**Reproduction** : confirmée — reward meanrev = 70 quand trend = 100 pour le même mouvement.
**Correctif** : implémenter un vrai reward meanrev basé sur la distance à une moyenne mobile
(récompenser le fade de l'extrême).

---

## 🟠 BUGS MAJEURS (comportement incorrect mais ne plante pas)

### M1. `SmartOrderRouter.select_algorithm` utilise `limit_price` (toujours None pour MARKET)
**Fichier** : `omega/execution/sor.py` (ligne 49)
**Symptôme** : `notional = order.qty * (order.limit_price or 0.0)` → pour un MARKET order,
`limit_price=None` → notional=0 → bucket "moyen" ($10k) → **TWAP toujours choisi**, peu importe
la taille réelle. Un ordre de $1M est découpé en 5 tranches au lieu de l'iceberg.
**Correctif** : utiliser un prix d'arrivée passé en paramètre, ou demander au venue.

### M2. Chemin de données Linux hardcodé (`/home/z/my-project/data`)
**Fichiers** : `settings.py` (ligne 155, 174), `trade_autopsy.py` (ligne 59), `online_learning.py` (ligne 31),
`generate_whitepaper.py` (lignes 36-54, 1040)
**Symptôme** : Sur Windows, `Path("/home/z/my-project/data")` devient `\home\z\my-project\data`
(relatif invalide). `load_settings()` fait `data_dir.mkdir(parents=True, exist_ok=True)` qui crée
un dossier `home\z\...` dans le cwd. `TradeAutopsy.__init__` tente `os.makedirs` pareil.
**Correctif** : valeur par défaut portable via `Path.home() / ".omega" / "data"` ou relatif `./data`.

### M3. `MonteCarloEngine.run()` ne met pas à jour `_last_multiplier` sur early-return
**Fichier** : `omega/risk_aegis/monte_carlo.py` (lignes 50-51, 56)
**Symptôme** : `aegis.on_signal` lit `self.monte_carlo._last_multiplier` (pas la valeur de retour
de `run()`). Si `run()` retourne 1.0 tôt (<50 returns), `_last_multiplier` reste à sa valeur précédente
(qui pourrait être 0.2) → sizing erroné.
**Correctif** : toujours setter `self._last_multiplier` avant tout return.

### M4. `ExecutionBlade` ne câble pas les credentials Binance depuis les settings
**Fichier** : `omega/execution/blade.py` + `settings.py`
**Symptôme** : `Settings` a `binance_api_key/secret` et `data_nexus.binance_testnet`, mais
`ExecutionBlade(settings.execution)` crée un `BinanceExecutor()` par défaut qui ne reçoit RIEN.
Ça marche uniquement parce que `BinanceExecutor.__init__` lit aussi `BINANCE_*` env vars.
Mais `binance_testnet` est lu dans `data_nexus` et jamais transmis à l'exécuteur → le testnet
ne s'active jamais via settings (env var oui).
**Correctif** : propager api_key/secret/testnet de settings vers BinanceExecutor.

### M5. `news_feed._score()` bloque l'event loop (subprocess synchrone)
**Fichier** : `omega/data_nexus/news_feed.py` (ligne 139)
**Symptôme** : `subprocess.run(..., timeout=20)` est synchrone, appelé dans une coroutine async.
Chaque headline bloque la loop jusqu'à 20s. Avec plusieurs feeds, le système se fige.
**Correctif** : `await asyncio.to_thread(subprocess.run, ...)` (comme fait dans `llm_macro_agent.py`).

### M6. `KellyPositionSizer.size()` — ligne 88 écrase la ligne 87 (code mort)
**Fichier** : `omega/risk_aegis/kelly.py` (lignes 87-88)
```python
agent = signal.metadata.get("contributing_agents", [signal.agent])[0] if signal.metadata else signal.agent
agent = signal.agent if signal.agent == "debate_chamber" else signal.agent  # <-- toujours signal.agent
```
**Symptôme** : la ligne 88 est un no-op (les deux branches retournent `signal.agent`). La logique
d'attribution à l'agent contributeur (pour les stats Kelly par agent) est morte. Les stats
Kelly sont toujours attribuées à `debate_chamber` au lieu de l'agent d'origine.
**Correctif** : extraire le vrai agent contributeur depuis metadata.

### M7. `binance_feed._parse_trade()` met bid=ask=0.0
**Fichier** : `omega/data_nexus/binance_feed.py` (lignes 143-145)
**Symptôme** : Les events `trade` ont `bid=0.0, ask=0.0, bid_qty=0.0, ask_qty=0.0`. Or le PPO
utilise `bid_qty/ask_qty` pour l'order book imbalance. Les trades seuls donnent des features
nulles. Les events `depth20` corrigent, mais entre deux snapshots de depth les features
sont intermittentes.
**Correctif** : maintenir un cache du dernier bid/ask connu par symbole et l'attacher aux trades.

---

## 🟡 BUGS MINEURS / CODE QUALITY

### m1. `ChildOrder.metadata: dict = None` — devrait être `field(default_factory=dict)`
**Fichier** : `omega/execution/algorithms.py` (ligne 38)
Pas de mutable-default partagé car None, mais l'accès `metadata["k"]` plante si non-set.
Cohérence : utiliser `field(default_factory=dict)`.

### m2. `kafka_bus.subscribe()` — consumer Kafka réel "omitted for brevity"
**Fichier** : `omega/data_nexus/kafka_bus.py` (lignes 167-180)
Avec un vrai Kafka, on peut publish mais jamais consume via `subscribe()`. Pas bloquant car
l'orchestrateur utilise `data_nexus.subscribe()` (queue in-process), mais c'est un gap documenté
qui empêche le replay multi-service. À documenter clairement.

### m3. `orchestrator.stop()` attrape `Exception` trop largement
**Fichier** : `omega/orchestrator.py` (ligne 107) — masque les vraies erreurs de shutdown.
Utiliser `except asyncio.CancelledError: pass` + `except Exception as e: logger.warning(...)`.

### m4. `genetic_optimizer` — `daily_pnl_bps` est en fait du pnl par trade, pas par jour
**Fichier** : `omega/meta_cognition/genetic_optimizer.py` (update_fitness)
Le nom dit "daily" mais `meta.py` l'appelle par trade fermé. Le calcul de Sharpe annuelisé
(×sqrt(365)) est donc faux. Renommer + recalibrer.

### m5. `load_historical_data` dans `train_ppo.py` parse_dates même pour parquet
Si le parquet n'a pas de colonne "timestamp", `pd.read_csv` n'est pas appelé mais l'index
peut être numérique. Le `EnvConfig` suppose un DataFrame indexé. Fragile mais OK pour les
données fournies.

### m6. `RL environment` live mode ne tracke pas `_t` ni `max_episode_bars`
`step_live` n'incrémente jamais `self._t`, donc les stats sont fausses et l'épisode live ne
termine jamais sur `max_episode_bars` (uniquement sur kill switch / -50%).

### m7. `aegis.on_signal` : `Side.FLAT` → `side_str = "short"` (ligne 123)
`side_str = "long" if signal.side == Side.BUY else "short"` — un signal FLAT devient "short".
Les signaux FLAT ne devraient de toute façon pas passer le floor de confidence, mais c'est
sémantiquement faux.

### m8. `pyproject.toml` `[project.scripts]` pointe vers `scripts.train_ppo:main`
Mais `scripts/` n'est pas un package (pas de `__init__.py`), donc `pip install -e .` + 
`omega-train` plante. Soit ajouter `scripts/__init__.py`, soit retirer les entry points.

### m9. `__init__.py` typo : `__author__ = "OMGA Quantitative Research"` (OMGA au lieu de OMEGA)

### m10. `requirements.txt` mentionne `# z-ai CLI must be installed separately at /usr/local/bin/z-ai`
Mais le path par défaut dans settings est `/usr/local/bin/z-ai` — un fichier inexistant fait que
le LLM macro agent / autopsy / news scoring échouent silencieusement (subprocess returncode != 0).
Devrait être configurable et détectable (check d'existence + warning clair).

---

## ✅ CE QUI FONCTIONNE BIEN
- Architecture 6 couches propre, séparation des responsabilités claire.
- Fallbacks in-process pour Kafka/Milvus (vraies implémentations, pas mocks).
- Kelly sizing, Monte Carlo, kill switch corrects unitairement.
- HMM regime detector + state-to-regime mapping déterministe.
- Stat-arb cointegration (Engle-Granger + ADF) correct.
- Binance executor HMAC signing correct.
- Smoke tests passent (mais insuffisants — ne testent pas le flux intégré).

---

## RÉSUMÉ DES CORRECTIONS APPLIQUÉES

Toutes les corrections ci-dessous sont appliquées et **vérifiées par des tests**.
Statut final des tests après correction :
- `tests/test_smoke.py` : **12/12 PASS** (inchangé, aucune régression)
- `tests/test_regression.py` : **10/10 PASS** (nouveaux tests, un par bug)
- `scripts/train_ppo.py --episodes 2` : tourne end-to-end (équity tracking, Sharpe reporté)
- `scripts/backtest.py` : tourne end-to-end (bug `.iloc` label/positional corrigé)

| ID | Bug | Fichier(s) | Statut |
|----|-----|-----------|--------|
| C1 | pipeline live mort (risk_aegis.on_market jamais appelé) | orchestrator.py | ✅ corrigé + test |
| C2 | PPO mélange l'état de tous les symboles | alpha_swarm/ppo_agent.py | ✅ corrigé + test |
| C3 | reward meanrev = 0.7× trend (terme momentum = ret) | alpha_swarm/ppo_agent.py | ✅ corrigé + test |
| M1 | SOR choisit toujours TWAP (limit_price=None) | execution/sor.py, blade.py | ✅ corrigé + test |
| M2 | data_dir hardcodé Linux `/home/z/...` | config/settings.py, meta_cognition/* | ✅ corrigé + test |
| M3 | MonteCarlo `_last_multiplier` stalé sur early return | risk_aegis/monte_carlo.py | ✅ corrigé + test |
| M4 | ExecutionBlade ne câble pas creds/testnet | execution/blade.py, orchestrator.py | ✅ corrigé + test |
| M5 | news `_score` bloque l'event loop (subprocess sync) | data_nexus/news_feed.py | ✅ corrigé + test |
| M6 | Kelly dead code (stats attribuées à debate_chamber) | risk_aegis/kelly.py | ✅ corrigé + test |
| M7 | binance trade events bid/ask = 0 | data_nexus/binance_feed.py | ✅ corrigé + test |
| m1 | ChildOrder.metadata mutable default None | execution/algorithms.py | ✅ corrigé |
| m3 | orchestrator.stop attrape Exception trop largement | orchestrator.py, nexus.py | ✅ corrigé |
| m4 | GA `daily_pnl_bps` = pnl/trade, Sharpe ×√365 faux | meta_cognition/genetic_optimizer.py | ✅ corrigé |
| m6 | RL env live mode n'incrémente pas `_t` | rl_environment.py | ✅ corrigé |
| m7 | aegis FLAT → "short" | risk_aegis/aegis.py | ✅ corrigé |
| m8 | entry points pyproject pointent vers scripts non-package | scripts/__init__.py, pyproject.toml | ✅ corrigé |
| m9 | typo `__author__ = "OMGA..."` | omega/__init__.py | ✅ corrigé |
| m10 | z-ai CLI manquant → échec silencieux | alpha_swarm/llm_macro_agent.py | ✅ warning ajouté |
| bonus | backtest `.iloc["close"]` TypeError | scripts/backtest.py | ✅ corrigé |

### Gaps documentés (non-bloquants, par design)
- **Kafka consumer réel non implémenté** dans `kafka_bus.subscribe()` : en production avec Kafka,
  on peut publish mais la consommation passe toujours par le fallback in-process. L'orchestrateur
  utilise `data_nexus.subscribe()` (queue in-process), donc le flux live fonctionne. À implémenter
  pour le replay multi-service. (audit m2)
- **FPGA / exécution C++ basse-latence** : mentionné dans le whitepaper, non implémenté (par design).

### Comment rejouer les tests
```bash
pip install -r requirements.txt
python tests/test_smoke.py        # 12 tests fonctionnels
python tests/test_regression.py   # 10 tests de non-régression (audit)
python scripts/train_ppo.py --episodes 3   # entraînement end-to-end
```
