# Crowd Positioning Engine — Design Spec

**Date**: 2026-06-21
**Status**: Approved (V1 implementation in progress)
**Author**: OMEGA Quantitative Research

## Thesis

> OMEGA ne trade plus le prix. Il trade le **positioning de la foule**.

80% des traders perdent parce qu'ils font ce que 80% font : ils pile dans les
extrêmes overcrowdés au pire moment, puis se font liquider dans la cascade
inverse. Le Crowd Positioning Engine détecte ces extrêmes quantitativement et
prend le trade inverse — le trade qui fait le plus mal au plus de monde.

C'est l'arête d'un outsider : les géants institutionnels ne peuvent pas faire de
sentiment-based contrarian trading (compliance, tailles trop grosses pour fader
le retail). On est completement en dehors de leur dogme.

## Architecture (Approche A — couche 1.5)

```
Layer 1 (Data Nexus)                 NOUVEAU                 Layer 2 (Alpha Swarm)
+-----------------------------+   +---------------------+   +---------------------+
| BinanceWSFeed (funding, L2) |-->|                     |   | PPO trend (existant)|
| FundingRateSignal           |   |  CROWD POSITIONING  |   | PPO meanrev         |
| LSRatioSignal (futures REST)|-->|  ENGINE             |   | LLM macro           |
| OI/LiquidationFeed (V3)     |   |                     |   | StatArb             |
| RSSNewsFeed (LLM sentiment) |-->|  Fusionne 3 signaux |-->|                     |
| F&G / social (V3)           |   |  -> CrowdPositioning|   | ContrarianAgent NEW |<- fade extremes
+-----------------------------+   |     Event           |   +---------------------+
                                  +----------+----------+
                                             |
                                  modifie RegimeWeightRouter
                                  (trend defunded si crowd extreme)
```

Le Crowd Engine est une couche entre Data Nexus et Alpha Swarm. Il ne remplace
rien — il reconfigure la swarm existante et nourrit un nouvel agent contrarian.

## Le contrat — CrowdPositioningEvent

```python
@dataclass(frozen=True)
class CrowdPositioningEvent:
    symbol: str
    timestamp: str
    crowd_score: float        # [-1,+1] neg = foule short overcrowded, pos = long overcrowded
    conviction: float         # [0,1] significance statistique de l'extreme
    horizon: str              # "minutes" | "hours" | "days"
    components: Dict          # {"funding": +0.8, "ls_ratio": +0.6, "sentiment": +0.4}
    regime_hint: str          # "cascade_imminent" | "euphoria" | "fear" | "neutral"
    expected_move_bps: float  # taille du move inverse attendu
```

**Regles d'or**:
- `crowd_score > 0` → foule trop long → on SHORT (fade)
- `crowd_score < 0` → foule trop short → on LONG
- `conviction` reduite quand les signaux divergent (foule long sur funding
  MAIS sentiment neutre = setup faible)

## Les 3 signaux

Chaque signal sort un score normalise [-1,+1] + un horizon.

| Signal | Source | Score | Horizon | V1 |
|--------|--------|-------|---------|----|
| Funding rate | Binance @markPrice | tanh(funding/threshold), threshold=0.0005 | hours | yes |
| L/S ratio | Binance futures REST /globalLongShortAccountRatio | (long_pct-50)/50 | hours-days | yes |
| Sentiment | Fear&Greed API | F&G>80 greed(+), <20 fear(-) | days | yes |

### Fusion

```
crowd_score = w1*funding + w2*ls_ratio + w3*sentiment   (clamp [-1,1])
conviction  = |crowd_score| * (1 - divergence)          # agree = high conviction
horizon     = max(horizons des composants significatifs)
```

Poids initiaux: funding=0.40, ls_ratio=0.35, sentiment=0.25. Apprenables via
le GeneticOptimizer existant en V4.

## ContrarianAgent

Fade les extremes. Rule-based (pas de ML — un extreme est un threshold).

- Seuil d'entree: |crowd_score| > 0.5
- Side: inverse de crowd_score
- Confidence: conviction * 0.85 (cap)
- TP/SL asymetriques: TP = expected_move_bps, stop = 0.3 * TP
  (petites pertes frequentes, gain enorme quand la cascade claque)
- Holding period: pilotee par l'horizon (minutes=60 bars, hours=240, days=1440)

## Reconfiguration de regime (le "Regime C")

Le Crowd Engine pousse dans RegimeWeightRouter:
- conviction > 0.7 ET regime_hint == cascade_imminent
  -> trend weight 0.05, contrarian weight 0.50
- regime_hint == neutral -> poids normaux

## Phasage

| Phase | Scope | Verifiable |
|-------|-------|------------|
| V1 | event + engine + funding + ls_ratio + contrarian + wiring | live dry-run crowd_score bouge |
| V2 | sentiment F&G + reconfig regime | agent contrarian emit signaux reels |
| V3 | open interest + liquidation heatmap + social | prediction cascade mesurable |
| V4 | apprentissage poids fusion via GeneticOptimizer | auto-tuning |

V1+V2 implementes dans ce commit. V3+V4 en suite.

## Hors-scope (YAGNI)

- Pas de scraping X/Reddit en V1 (bruit, legal)
- Pas de DEX/on-chain liquidations en V1
- Pas de nouveau NN — contrarian est rule-based

## Tests cibles

- unit: chaque signal -> score correct pour inputs extremes/neutres
- unit: fusion -> conviction reduite a la divergence
- unit: contrarian -> n'emet rien sous le seuil, fade au-dessus, TP/SL asymetriques
- integration: engine dans l'orchestrator, CrowdPositioningEvent route au contrarian
- regression: les 23 tests existants passent toujours
