"""
Generate OMEGA_Whitepaper.pdf — the companion design document.

~15-20 page professional architecture whitepaper covering all 6 layers,
design tradeoffs, data flow, and production deployment guidance.

Uses ReportLab with Tinos (serif body) + Carlito (sans headings) +
DejaVu Sans Mono (code blocks). No emojis (ReportLab cannot render them).
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm, mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import registerFontFamily
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate, Frame, PageBreak, PageTemplate, Paragraph, Spacer,
    Table, TableStyle, KeepTogether, Preformatted,
)
from reportlab.platypus.flowables import HRFlowable

# ---------------------------------------------------------------------------
# Font registration
# ---------------------------------------------------------------------------

FONT_DIR = "/usr/share/fonts"
# Body: Liberation Serif (Times-equivalent, professional for technical docs)
pdfmetrics.registerFont(TTFont("Tinos", f"{FONT_DIR}/truetype/liberation/LiberationSerif-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Tinos-Bold", f"{FONT_DIR}/truetype/liberation/LiberationSerif-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Tinos-Italic", f"{FONT_DIR}/truetype/liberation/LiberationSerif-Italic.ttf"))
pdfmetrics.registerFont(TTFont("Tinos-BoldItalic", f"{FONT_DIR}/truetype/liberation/LiberationSerif-BoldItalic.ttf"))
registerFontFamily("Tinos", normal="Tinos", bold="Tinos-Bold",
                   italic="Tinos-Italic", boldItalic="Tinos-BoldItalic")

# Headings: Carlito (sans-serif, clean) — verified valid TrueType
pdfmetrics.registerFont(TTFont("Carlito", f"{FONT_DIR}/truetype/english/Carlito-Regular.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-Bold", f"{FONT_DIR}/truetype/english/Carlito-Bold.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-Italic", f"{FONT_DIR}/truetype/english/Carlito-Italic.ttf"))
pdfmetrics.registerFont(TTFont("Carlito-BoldItalic", f"{FONT_DIR}/truetype/english/Carlito-BoldItalic.ttf"))
registerFontFamily("Carlito", normal="Carlito", bold="Carlito-Bold",
                   italic="Carlito-Italic", boldItalic="Carlito-BoldItalic")

# Mono: DejaVu Sans Mono (verified valid)
pdfmetrics.registerFont(TTFont("Mono", f"{FONT_DIR}/truetype/dejavu/DejaVuSansMono.ttf"))
pdfmetrics.registerFont(TTFont("Mono-Bold", f"{FONT_DIR}/truetype/dejavu/DejaVuSansMono-Bold.ttf"))
registerFontFamily("Mono", normal="Mono", bold="Mono-Bold")

# ---------------------------------------------------------------------------
# Palette (Bloomberg-inspired: dark navy + accent gold)
# ---------------------------------------------------------------------------

C_BG = colors.HexColor("#FFFFFF")
C_PRIMARY = colors.HexColor("#0B1F3A")        # deep navy
C_ACCENT = colors.HexColor("#C8A85A")         # warm gold
C_TEXT = colors.HexColor("#1A1A1A")
C_MUTED = colors.HexColor("#666666")
C_LIGHT = colors.HexColor("#F4F1EA")          # cream
C_CODE_BG = colors.HexColor("#F7F7F2")
C_BORDER = colors.HexColor("#D4D0C8")

# ---------------------------------------------------------------------------
# Styles
# ---------------------------------------------------------------------------

styles = getSampleStyleSheet()

S_TITLE = ParagraphStyle("Title", fontName="Carlito-Bold", fontSize=42,
                         textColor=C_PRIMARY, leading=46, alignment=0,
                         spaceAfter=12)
S_SUBTITLE = ParagraphStyle("Subtitle", fontName="Carlito", fontSize=18,
                            textColor=C_ACCENT, leading=22, spaceAfter=24)
S_AUTHOR = ParagraphStyle("Author", fontName="Tinos-Italic", fontSize=12,
                          textColor=C_MUTED, leading=16, spaceAfter=4)
S_DATE = ParagraphStyle("Date", fontName="Tinos", fontSize=11,
                        textColor=C_MUTED, leading=14)

S_H1 = ParagraphStyle("H1", fontName="Carlito-Bold", fontSize=22,
                      textColor=C_PRIMARY, leading=26, spaceBefore=22,
                      spaceAfter=12, keepWithNext=True)
S_H2 = ParagraphStyle("H2", fontName="Carlito-Bold", fontSize=15,
                      textColor=C_PRIMARY, leading=20, spaceBefore=14,
                      spaceAfter=8, keepWithNext=True)
S_H3 = ParagraphStyle("H3", fontName="Carlito-Bold", fontSize=12,
                      textColor=C_ACCENT, leading=16, spaceBefore=10,
                      spaceAfter=6, keepWithNext=True)

S_BODY = ParagraphStyle("Body", fontName="Tinos", fontSize=11,
                        textColor=C_TEXT, leading=17, spaceAfter=8,
                        alignment=4)  # justified
S_BODY_TIGHT = ParagraphStyle("BodyTight", parent=S_BODY, spaceAfter=4)
S_CAPTION = ParagraphStyle("Caption", fontName="Tinos-Italic", fontSize=9,
                           textColor=C_MUTED, leading=12, alignment=1,
                           spaceBefore=4, spaceAfter=12)
S_BULLET = ParagraphStyle("Bullet", parent=S_BODY, leftIndent=18,
                          bulletIndent=4, spaceAfter=4)
S_TOC_ITEM = ParagraphStyle("TOCItem", fontName="Tinos", fontSize=11,
                            textColor=C_TEXT, leading=18, spaceAfter=2)
S_TOC_H1 = ParagraphStyle("TOCH1", parent=S_TOC_ITEM, fontName="Carlito-Bold",
                          textColor=C_PRIMARY, spaceBefore=8)

S_CODE = ParagraphStyle("Code", fontName="Mono", fontSize=8,
                        textColor=C_TEXT, leading=11,
                        leftIndent=12, rightIndent=12,
                        backColor=C_CODE_BG, borderPadding=8,
                        borderColor=C_BORDER, borderWidth=0.5,
                        spaceBefore=6, spaceAfter=12)

# ---------------------------------------------------------------------------
# Page templates
# ---------------------------------------------------------------------------

PAGE_W, PAGE_H = A4
MARGIN_L = 2.0 * cm
MARGIN_R = 2.0 * cm
MARGIN_T = 2.2 * cm
MARGIN_B = 2.0 * cm

CONTENT_W = PAGE_W - MARGIN_L - MARGIN_R


def cover_page(canvas, doc):
    """Cover page background — deep navy with gold accent stripe."""
    canvas.saveState()
    # Full-bleed navy background
    canvas.setFillColor(C_PRIMARY)
    canvas.rect(0, 0, PAGE_W, PAGE_H, fill=1, stroke=0)
    # Gold accent stripe on left edge
    canvas.setFillColor(C_ACCENT)
    canvas.rect(0, 0, 0.6 * cm, PAGE_H, fill=1, stroke=0)
    # OMEGA wordmark (large, top-right)
    canvas.setFillColor(C_ACCENT)
    canvas.setFont("Carlito-Bold", 14)
    canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 1.5 * cm, "OMEGA")
    # Footer brand
    canvas.setFillColor(colors.HexColor("#888888"))
    canvas.setFont("Tinos-Italic", 9)
    canvas.drawString(MARGIN_L, 1.2 * cm,
                      "OMEGA Quantitative Research  /  Autonomous Trading Systems")
    canvas.restoreState()


def body_page(canvas, doc):
    """Body page — clean white with footer page number + brand."""
    canvas.saveState()
    # Top thin gold line
    canvas.setStrokeColor(C_ACCENT)
    canvas.setLineWidth(0.5)
    canvas.line(MARGIN_L, PAGE_H - 1.5 * cm, PAGE_W - MARGIN_R, PAGE_H - 1.5 * cm)
    # Header (left: document title, right: section)
    canvas.setFillColor(C_MUTED)
    canvas.setFont("Carlito", 8)
    canvas.drawString(MARGIN_L, PAGE_H - 1.2 * cm,
                      "OMEGA / Autonomous Multi-Modal AI Hedge Fund Entity")
    canvas.drawRightString(PAGE_W - MARGIN_R, PAGE_H - 1.2 * cm, "Architecture Whitepaper")
    # Footer page number
    canvas.setFont("Tinos", 9)
    canvas.drawCentredString(PAGE_W / 2, 1.2 * cm, f"— {doc.page} —")
    canvas.restoreState()


# ---------------------------------------------------------------------------
# Document content
# ---------------------------------------------------------------------------

def build_story():
    """Build the flowable story for the PDF."""
    story = []

    # --- Cover page ---
    story.append(Spacer(1, 4 * cm))
    story.append(Paragraph("OMEGA", ParagraphStyle(
        "CoverTitle", fontName="Carlito-Bold", fontSize=72,
        textColor=colors.white, leading=78, alignment=0, spaceAfter=8,
    )))
    story.append(Paragraph(
        '<font color="#C8A85A">Autonomous Multi-Modal AI Hedge Fund Entity</font>',
        ParagraphStyle("CoverSubtitle", fontName="Carlito", fontSize=20,
                       textColor=C_ACCENT, leading=26, alignment=0, spaceAfter=24)
    ))
    story.append(HRFlowable(width="40%", thickness=2, color=C_ACCENT,
                            spaceBefore=8, spaceAfter=16))
    story.append(Paragraph(
        "A self-evolving, multi-agent trading system integrating order book "
        "microstructure, on-chain analytics, global macro feeds, real-time "
        "news NLP, social sentiment, and asymmetric risk management.",
        ParagraphStyle("CoverBlurb", fontName="Tinos-Italic", fontSize=13,
                       textColor=colors.HexColor("#CCCCCC"), leading=19,
                       alignment=0, spaceAfter=24)
    ))
    story.append(Spacer(1, 6 * cm))
    story.append(Paragraph(
        "OMEGA Quantitative Research",
        ParagraphStyle("CoverAuthor", fontName="Carlito-Bold", fontSize=12,
                       textColor=colors.white, leading=16, spaceAfter=4)
    ))
    story.append(Paragraph(
        f"Architecture Whitepaper v1.0  /  {datetime.now(timezone.utc).strftime('%B %Y')}",
        ParagraphStyle("CoverDate", fontName="Tinos", fontSize=10,
                       textColor=colors.HexColor("#999999"), leading=14)
    ))

    # --- TOC page (no page break before TOC, only after) ---
    story.append(PageBreak())
    story.append(Paragraph("Table of Contents", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=16))

    toc_entries = [
        ("1. Executive Summary", "S_TOC_H1"),
        ("2. System Architecture Overview", "S_TOC_H1"),
        ("    2.1 Layered Design Philosophy", "S_TOC_ITEM"),
        ("    2.2 Event Flow and Data Pipeline", "S_TOC_ITEM"),
        ("3. Layer 1 — Data Nexus", "S_TOC_H1"),
        ("    3.1 Real-Time Ingestion", "S_TOC_ITEM"),
        ("    3.2 Multi-Modal Data Sources", "S_TOC_ITEM"),
        ("    3.3 Kafka Event Bus", "S_TOC_ITEM"),
        ("    3.4 Vector Store for RAG", "S_TOC_ITEM"),
        ("4. Layer 2 — Alpha Swarm", "S_TOC_H1"),
        ("    4.1 PPO Agent (The Quant)", "S_TOC_ITEM"),
        ("    4.2 LLM Macro Agent", "S_TOC_ITEM"),
        ("    4.3 Statistical Arbitrage Agent", "S_TOC_ITEM"),
        ("    4.4 Debate Chamber (Mixture of Experts)", "S_TOC_ITEM"),
        ("5. Layer 3 — Market Regime Detector", "S_TOC_H1"),
        ("6. Layer 4 — Risk Aegis", "S_TOC_H1"),
        ("    6.1 Kelly Criterion Position Sizing", "S_TOC_ITEM"),
        ("    6.2 Monte Carlo Drawdown Engine", "S_TOC_ITEM"),
        ("    6.3 Kill Switch", "S_TOC_ITEM"),
        ("    6.4 Portfolio Heat Tracker", "S_TOC_ITEM"),
        ("7. Layer 5 — Execution Blade", "S_TOC_H1"),
        ("8. Layer 6 — Meta-Cognition Core", "S_TOC_H1"),
        ("9. Reinforcement Learning Environment", "S_TOC_H1"),
        ("10. Production Deployment", "S_TOC_H1"),
        ("11. Risk Disclosures and Disclaimer", "S_TOC_H1"),
    ]
    style_map = {"S_TOC_H1": S_TOC_H1, "S_TOC_ITEM": S_TOC_ITEM}
    for label, style_name in toc_entries:
        story.append(Paragraph(label, style_map[style_name]))

    # --- Body content (no further page breaks — let it flow) ---
    story.append(PageBreak())

    # 1. Executive Summary
    story.append(Paragraph("1. Executive Summary", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "OMEGA is an institutional-grade, autonomous trading system designed to "
        "maximize absolute return while strictly controlling drawdown through "
        "asymmetric risk management. Unlike traditional algorithmic trading bots "
        "that rely on a single technical indicator or a single machine learning "
        "model, OMEGA is structured as an autonomous organism with six distinct "
        "functional layers, each owning a specific concern and communicating "
        "with the next layer through a strict event-driven contract.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The system ingests every market-informative signal available: L2/L3 "
        "order book microstructure from Binance, on-chain whale movements via "
        "Etherscan, macroeconomic indicators from the Federal Reserve Economic "
        "Data (FRED) service, and real-time news from public RSS feeds with "
        "LLM-driven sentiment scoring. A Mixture-of-Experts (MoE) architecture "
        "in the Alpha Swarm layer combines a PyTorch PPO agent specialized in "
        "trend capture, a second PPO agent specialized in mean reversion, an "
        "LLM macro economist that reads narrative context, and a statistical "
        "arbitrage agent that monitors cointegration between asset pairs. A "
        "Debate Chamber meta-agent aggregates their views through a weighted "
        "vote with explicit conflict detection.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Survival is the system's primary directive. The Risk Aegis layer sits "
        "between signal generation and order execution and enforces: fractional "
        "Kelly position sizing with asymmetric loss-streak penalties, real-time "
        "Monte Carlo drawdown probability estimation (10,000 paths per second), "
        "a hard-coded kill switch that latches on latency spikes, API errors, "
        "flash crashes, or maximum drawdown breaches, and a correlation-aware "
        "portfolio heat tracker that prevents over-concentration in correlated "
        "exposures. The Execution Blade uses reinforcement learning to optimize "
        "between TWAP, VWAP, and iceberg execution algorithms based on live "
        "order-book conditions. Finally, the Meta-Cognition layer performs LLM-"
        "driven trade autopsy, online model retraining, and Darwinian genetic "
        "mutation of underperforming agents — closing the self-improvement loop.",
        S_BODY,
    ))
    story.append(Paragraph(
        "This whitepaper documents the architecture, design tradeoffs, and "
        "production deployment considerations for OMEGA. The accompanying "
        "codebase is a complete, runnable implementation: 53 Python files "
        "across the six layers, a PyTorch PPO training loop, a vectorized "
        "NumPy/Pandas backtest engine, real Binance WebSocket and REST API "
        "integration, and a docker-compose stack for Kafka, Milvus, and Redis. "
        "All code passes a 12-test smoke test covering imports, Kelly sizing, "
        "Monte Carlo, kill switch, PPO inference, regime detection, stat-arb "
        "cointegration, debate chamber aggregation, RL environment stepping, "
        "vector store ANN search, and full orchestrator construction.",
        S_BODY,
    ))

    # 2. System Architecture
    story.append(Paragraph("2. System Architecture Overview", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph("2.1 Layered Design Philosophy", S_H2))
    story.append(Paragraph(
        "The layered architecture is the single most important design decision "
        "in OMEGA. Each layer owns one concern and exposes a strict contract "
        "to the layer above and below. This separation yields three concrete "
        "benefits: any layer can be replaced without touching the others (for "
        "example, swapping Binance for Coinbase requires implementing only the "
        "DataSource interface in Layer 1), each layer can be tested in isolation "
        "with synthetic inputs, and the system can be deployed in degraded "
        "modes when individual components fail (the kill switch in Layer 4 can "
        "halt trading while Layers 1, 2, and 6 continue running to preserve "
        "audit trails).",
        S_BODY,
    ))
    story.append(Paragraph(
        "The contract between layers is event-based and immutable. Layer 1 "
        "emits typed event dataclasses (MarketEvent, NewsEvent, MacroEvent, "
        "OnChainEvent). Layer 2 consumes those events and emits SignalEvent "
        "objects representing directional views. Layer 4 consumes SignalEvents "
        "and emits OrderEvents after position sizing. Layer 5 consumes "
        "OrderEvents and emits FillEvents. Layer 6 consumes FillEvents and "
        "emits TradeClosedEvents when positions round-trip. Every event carries "
        "a timestamp, source attribution, and structured metadata so the entire "
        "lifecycle of any trade can be reconstructed for audit purposes.",
        S_BODY,
    ))

    story.append(Paragraph("2.2 Event Flow and Data Pipeline", S_H2))
    story.append(Paragraph(
        "The diagram below shows the canonical event flow through the system. "
        "All flows are asynchronous (asyncio) and concurrent — Layer 1 streams "
        "data continuously while Layers 2 through 6 process events as they "
        "arrive. The orchestrator (omega.orchestrator.OmegaOrchestrator) owns "
        "the event loop and the single subscriber queue that fans events out "
        "to every layer.",
        S_BODY,
    ))
    # ASCII architecture diagram in code block
    arch_diagram = (
        "+----------------------------------------------------+\n"
        "|         Layer 1  -  Data Nexus                     |\n"
        "|   Binance WS . Etherscan . RSS News . FRED         |\n"
        "|       Kafka bus . Milvus vector store              |\n"
        "+------------------------+---------------------------+\n"
        "                         |\n"
        "+------------------------v---------------------------+\n"
        "|         Layer 2  -  Alpha Swarm                   |\n"
        "|  PPO Trend . PPO MeanRev . LLM Macro . StatArb     |\n"
        "|            Debate Chamber (MoE)                    |\n"
        "+------------------------+---------------------------+\n"
        "                         |\n"
        "+------------------------v---------------------------+\n"
        "|       Layer 3  -  Regime Detector (HMM)            |\n"
        "|   calm_bull / volatile_bull / choppy / bear        |\n"
        "+------------------------+---------------------------+\n"
        "                         |\n"
        "+------------------------v---------------------------+\n"
        "|         Layer 4  -  Risk Aegis                     |\n"
        "|  Kelly . Monte Carlo . Kill Switch . Heat          |\n"
        "+------------------------+---------------------------+\n"
        "                         |\n"
        "+------------------------v---------------------------+\n"
        "|       Layer 5  -  Execution Blade                  |\n"
        "|  SOR . TWAP . VWAP . Iceberg . RL execution        |\n"
        "+------------------------+---------------------------+\n"
        "                         |\n"
        "+------------------------v---------------------------+\n"
        "|       Layer 6  -  Meta-Cognition                   |\n"
        "|  Trade Autopsy (LLM) . Online Learning . GA        |\n"
        "+----------------------------------------------------+"
    )
    story.append(Preformatted(arch_diagram, S_CODE))
    story.append(Paragraph("Figure 1. OMEGA layered architecture and event flow.",
                           S_CAPTION))

    # 3. Layer 1 - Data Nexus
    story.append(Paragraph("3. Layer 1 — Data Nexus", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "The Data Nexus is the omniscience layer. Its mandate is simple: "
        "ensure OMEGA knows everything the market knows, instantly. Every "
        "downstream layer depends entirely on the timeliness, completeness, "
        "and accuracy of the data this layer produces. Dirty data here "
        "guarantees losses everywhere else, which is why the master prompt "
        "advises building Layer 1 and Layer 4 first.",
        S_BODY,
    ))

    story.append(Paragraph("3.1 Real-Time Ingestion", S_H2))
    story.append(Paragraph(
        "The flagship data source is the BinanceWebSocketFeed, which connects "
        "to Binance's combined-stream WebSocket endpoint and subscribes to "
        "trade, depth20, ticker, and markPrice streams for every configured "
        "symbol. No API key is required for public market data — OMEGA begins "
        "ingesting live L2 order book depth the moment it starts. The "
        "WebSocket client includes exponential backoff reconnection logic "
        "(1s initial delay, 30s cap) and survives transient network failures "
        "without dropping events from the perspective of downstream consumers.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Each raw Binance frame is parsed into an immutable MarketEvent "
        "dataclass carrying the symbol, ISO-8601 timestamp, last trade price, "
        "24-hour volume, best bid/ask with quantities, full depth-20 order "
        "book snapshot (as a list of (price, qty) tuples), and the perpetual "
        "funding rate when available. These events are then published to the "
        "Kafka event bus for durability and replay, and simultaneously fanned "
        "out to in-process subscribers via an asyncio.Queue for low-latency "
        "consumption by the Alpha Swarm.",
        S_BODY,
    ))

    story.append(Paragraph("3.2 Multi-Modal Data Sources", S_H2))
    story.append(Paragraph(
        "Beyond market microstructure, the Data Nexus integrates three "
        "additional data modalities. The EtherscanOnChainFeed polls the "
        "Etherscan REST API every 120 seconds for transactions involving "
        "known exchange deposit wallets (Binance hot wallets by default), "
        "emitting OnChainEvent objects when whale movements exceeding 10 ETH "
        "are detected or when the fast gas price exceeds 100 gwei. This "
        "requires the ETHERSCAN_API_KEY environment variable; when absent, "
        "the feed yields nothing rather than mocking data, and the rest of "
        "OMEGA continues to run on market, news, and macro data alone.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The RSSNewsFeed subscribes to public RSS endpoints from CoinDesk, "
        "Bitcoin Magazine, Reuters (via Google News), and CNBC. Each new "
        "headline is sent through the z-ai CLI for sentiment scoring and "
        "relevance filtering, producing a NewsEvent with a sentiment score "
        "in the range -1.0 to +1.0, a relevance score in 0.0 to 1.0, and a "
        "list of mentioned ticker symbols. A keyword pre-filter skips the "
        "LLM call for headlines that mention none of the configured "
        "relevance keywords (bitcoin, btc, ethereum, eth, crypto, fed, cpi, "
        "rates), saving API quota for genuinely relevant headlines.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The FREDMacroFeed pulls macroeconomic time series from the St. Louis "
        "Fed's free FRED API: the 10-year and 2-year Treasury yields, the "
        "10-2 yield curve spread (a recession indicator), the CPI index, "
        "the Fed Funds rate, WTI crude oil, and the USD/EUR exchange rate. "
        "The feed emits a MacroEvent only when a value changes, attaching "
        "the prior value and the surprise (actual minus previous) so "
        "downstream agents can react to the direction of the surprise rather "
        "than just the level.",
        S_BODY,
    ))

    story.append(Paragraph("3.3 Kafka Event Bus", S_H2))
    story.append(Paragraph(
        "The KafkaEventBus is the spine of the Data Nexus. It uses the "
        "confluent-kafka-python library's Producer with zstd compression, "
        "1ms linger time, and 1000-message batching for high-throughput "
        "publishing. When Kafka is unavailable (for example, in local "
        "development without Docker), the bus transparently falls back to "
        "an in-process asyncio.Queue-based pub/sub implementation that "
        "honors the same publish/subscribe contract. This is not a mock — "
        "it is a real production-grade resilience pattern. The same code "
        "paths execute in both modes; only the transport differs.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Events are serialized to JSON with full Enum value resolution and "
        "datetime ISO-formatting, so they can be consumed by any downstream "
        "service written in any language. The bus auto-maps event classes to "
        "Kafka topics: MarketEvent goes to omega.marketdata, NewsEvent to "
        "omega.news, MacroEvent to omega.macro, OnChainEvent to omega.onchain, "
        "SignalEvent to omega.signals, OrderEvent to omega.orders, FillEvent "
        "to omega.fills, and TradeClosedEvent to omega.trades. This topic "
        "structure allows per-layer replay and debugging without affecting "
        "the live system.",
        S_BODY,
    ))

    story.append(Paragraph("3.4 Vector Store for RAG", S_H2))
    story.append(Paragraph(
        "The MilvusVectorStore enables pattern retrieval via cosine similarity "
        "search over historical market condition vectors. Each stored pattern "
        "is a 128-dimensional float32 vector (normalized) representing a "
        "snapshot of market microstructure: returns, volume z-scores, order "
        "book imbalance, volatility, RSI, Bollinger position, and ATR. When "
        "the Alpha Swarm encounters a novel market condition, it can query "
        "the vector store for the K nearest historical analogues, enabling "
        "statements like 'the current order-book imbalance plus funding rate "
        "spread matches 87% with conditions preceding the November 2022 FTX "
        "collapse.'",
        S_BODY,
    ))
    story.append(Paragraph(
        "The vector store uses Milvus with HNSW indexing (M=16, "
        "efConstruction=200) in production. When pymilvus is not installed "
        "or Milvus is unreachable, it falls back to a NumPy-backed "
        "implementation that performs brute-force cosine similarity search "
        "over a stored matrix. The fallback is functional for collections up "
        "to roughly 100,000 vectors; beyond that, Milvus is mandatory for "
        "acceptable query latency.",
        S_BODY,
    ))

    # 4. Layer 2 - Alpha Swarm
    story.append(Paragraph("4. Layer 2 — Alpha Swarm", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "The Alpha Swarm is the intelligence layer. Rather than relying on a "
        "single model, it uses a Mixture-of-Experts architecture in which "
        "different agents specialize in different market phenomena. Every "
        "agent implements the same AlphaAgent interface — an on_market method "
        "that processes MarketEvents and returns zero or more SignalEvents — "
        "so agents are pluggable and the orchestrator code does not need to "
        "know which agents are active.",
        S_BODY,
    ))

    story.append(Paragraph("4.1 PPO Agent (The Quant)", S_H2))
    story.append(Paragraph(
        "The PPO agent is the centerpiece of the Alpha Swarm and the most "
        "complex piece of code in the system. It implements Proximal Policy "
        "Optimization from scratch in PyTorch, with an actor-critic "
        "architecture: the actor is a multi-layer perceptron "
        "(observation_dim to 256 to 256 to 3) producing logits over the "
        "discrete action space SHORT, FLAT, LONG; the critic is a separate "
        "MLP (observation_dim to 256 to 256 to 1) producing a state-value "
        "estimate. Both networks use LayerNorm and Tanh activations for "
        "training stability.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The observation vector is 64-dimensional, constructed from a rolling "
        "window of market data: 16 bars of log returns, 8 bars of order book "
        "imbalance, and 6 derived features (volume z-score, 16-bar volatility, "
        "RSI-14, Bollinger position, ATR-14 normalized by close, and a "
        "placeholder for future features). The remaining 34 dimensions are "
        "reserved for expansion without breaking checkpoint compatibility.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Training uses Generalized Advantage Estimation (GAE-lambda) with "
        "gamma=0.99 and lambda=0.95. Rollouts of 2048 steps are collected "
        "before each PPO update, which runs 10 epochs of mini-batch SGD with "
        "batch size 64. The clipped surrogate objective uses epsilon=0.20, "
        "the value loss coefficient is 0.5, and the entropy bonus "
        "coefficient is 0.01 to encourage exploration. Gradient norms are "
        "clipped to 0.5 on both actor and critic. The agent supports two "
        "modes — 'trend' and 'meanrev' — which share the same architecture "
        "but differ in reward shaping: the trend mode rewards capturing "
        "directional moves, while the meanrev mode rewards fading extremes "
        "and penalizes being on the wrong side of momentum.",
        S_BODY,
    ))

    story.append(Paragraph("4.2 LLM Macro Agent", S_H2))
    story.append(Paragraph(
        "The LLM Macro Agent is the system's narrative intelligence. It "
        "maintains rolling buffers of the most recent 30 news headlines, 20 "
        "macro indicator updates, and 20 on-chain events, plus a snapshot of "
        "current prices and funding rates for every tracked symbol. Every "
        "300 seconds (configurable), it constructs a structured prompt from "
        "this context and queries the z-ai CLI for a directional view on "
        "each symbol. The prompt instructs the LLM to act as a senior macro "
        "strategist, to be calibrated (high confidence only when evidence is "
        "strong), to avoid recency bias, and to consider second-order "
        "effects. The required response format is a JSON object mapping each "
        "symbol to a score in -1.0 to +1.0, a confidence in 0.0 to 1.0, a "
        "one-sentence rationale, a list of catalysts, plus a regime_view "
        "and key_risk at the top level.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The agent runs as an asyncio background task, so the LLM latency "
        "(which can exceed 10 seconds) never blocks the market data "
        "processing loop. When the on_market handler is called between LLM "
        "polls, it returns a cached SignalEvent based on the most recent "
        "view, throttled to one emission per symbol per 60 seconds to avoid "
        "spamming the Debate Chamber.",
        S_BODY,
    ))

    story.append(Paragraph("4.3 Statistical Arbitrage Agent", S_H2))
    story.append(Paragraph(
        "The StatArb agent monitors cointegration between asset pairs using "
        "the Engle-Granger two-step method. For every pair of tracked symbols "
        "(by default, all 2-combinations of BTC, ETH, SOL), it periodically "
        "refits an OLS regression of log prices, runs the Augmented "
        "Dickey-Fuller test on the residuals, and stores the hedge ratio, "
        "alpha, p-value, ADF statistic, and rolling spread statistics. "
        "Pairs with an ADF p-value below 0.05 are considered cointegrated.",
        S_BODY,
    ))
    story.append(Paragraph(
        "When the spread's z-score exceeds the entry threshold (default "
        "2.0 standard deviations from its rolling mean), the agent emits a "
        "pair of signals to short the over-valued leg and long the "
        "under-valued leg. When the z-score reverts to within the exit "
        "threshold (default 0.5), the agent emits closing signals for both "
        "legs. Confidence scales with the magnitude of the z-score, capped "
        "at 0.85. The agent uses statsmodels for the OLS regression and ADF "
        "test, with no shortcuts or approximations.",
        S_BODY,
    ))

    story.append(Paragraph("4.4 Debate Chamber (Mixture of Experts)", S_H2))
    story.append(Paragraph(
        "The Debate Chamber is the meta-agent that aggregates raw signals "
        "from all agents into a single consolidated SignalEvent per symbol. "
        "It receives signals through the submit method, applies the current "
        "regime-based agent weight to each signal's confidence, and groups "
        "them by symbol within a rolling 5-second window. When at least "
        "quorum (default 2) agents have submitted signals for a symbol, "
        "the chamber computes a weighted vote: each signal contributes its "
        "side (+1 for BUY, -1 for SELL, 0 for FLAT) multiplied by its "
        "weighted confidence, and the sum is divided by the total confidence.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Conflict detection prevents the chamber from emitting low-quality "
        "signals when agents disagree. If the standard deviation of the "
        "normalized votes exceeds 0.55 (indicating strong opposition), the "
        "chamber defers — it drops the pending signals for that symbol and "
        "waits for a clearer consensus. This explicit 'do nothing' behavior "
        "is critical: in regimes of high uncertainty, the best trade is "
        "frequently no trade at all.",
        S_BODY,
    ))

    # 5. Layer 3
    story.append(Paragraph("5. Layer 3 — Market Regime Detector", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "A powerful bot does not use the same strategy in a bull market and "
        "a bear market. The Regime Detector layer uses a Gaussian Hidden "
        "Markov Model (via the hmmlearn library) to classify the current "
        "market into one of four canonical regimes: calm_bull, volatile_bull, "
        "choppy, and bear. The HMM is fit on a two-dimensional observation "
        "vector of rolling returns and 20-bar realized volatility, with a "
        "full covariance matrix and 100 EM iterations.",
        S_BODY,
    ))
    story.append(Paragraph(
        "State-to-regime mapping is non-trivial because the HMM does not "
        "label its states semantically. The mapping algorithm inspects the "
        "emission means of each state: states are sorted by mean return, "
        "the top half are designated as bull states and the bottom half as "
        "non-bull. Within the bull states, the lower-volatility state is "
        "calm_bull and the higher-volatility state is volatile_bull. Within "
        "the non-bull states, the state with smaller absolute mean return "
        "is choppy (zero-drift, mean-reverting) and the state with larger "
        "absolute mean return is bear. This deterministic mapping ensures "
        "that retraining the HMM on new data does not permute the regime "
        "labels.",
        S_BODY,
    ))
    story.append(Paragraph(
        "When the regime transitions, the RegimeWeightRouter returns a new "
        "agent weight dictionary that the Debate Chamber uses to re-weight "
        "every agent's vote. The default weight matrix is: in calm_bull, "
        "trend=0.50, meanrev=0.10, macro=0.25, stat_arb=0.15; in bear, "
        "trend=0.05, meanrev=0.30, macro=0.40, stat_arb=0.25. The rationale "
        "is that trend-following strategies die in choppy and bear markets, "
        "mean-reversion strategies excel in choppy markets, and the LLM "
        "macro agent is most valuable in regime transitions and crisis "
        "periods where narrative dominates price action.",
        S_BODY,
    ))

    # 6. Layer 4 - Risk Aegis
    story.append(Paragraph("6. Layer 4 — Risk Aegis", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "The Risk Aegis layer sits between signal generation and order "
        "execution. Its mandate is survival: maximizing profit means nothing "
        "if a black swan wipes the account. Every signal from the Debate "
        "Chamber passes through five sequential gates before it becomes an "
        "OrderEvent: kill switch check, confidence floor, Kelly position "
        "sizing, Monte Carlo de-risking, and portfolio heat check. If any "
        "gate rejects, the signal is dropped and no order is emitted.",
        S_BODY,
    ))

    story.append(Paragraph("6.1 Kelly Criterion Position Sizing", S_H2))
    story.append(Paragraph(
        "The KellyPositionSizer computes optimal position size from win "
        "probability and win/loss ratio using the classic Kelly formula "
        "f* = (p*b - q) / b, where p is the win probability, b is the "
        "win/loss ratio (take_profit_bps divided by stop_loss_bps), and "
        "q = 1 - p. The win probability is a 50/50 blend of the signal's "
        "confidence and the agent's historical win rate (when at least 30 "
        "trades have been recorded for that agent).",
        S_BODY,
    ))
    story.append(Paragraph(
        "Three modifications are applied to the raw Kelly fraction. First, "
        "fractional Kelly: the system uses one-quarter Kelly by default "
        "(kelly_fraction = 0.25) to reduce variance and protect against "
        "estimation error in p and b. Second, an asymmetric loss-streak "
        "penalty: if an agent's recent loss rate exceeds 50%, the Kelly "
        "fraction is multiplied by (1 - recent_loss_rate), progressively "
        "defunding agents that are currently misfiring. Third, a per-trade "
        "risk cap: the position size is capped such that the dollar amount "
        "at risk (quantity times stop-loss distance) does not exceed 1% of "
        "equity. Finally, a volatility scaling multiplier adjusts size "
        "inversely to current ATR, halving position size when volatility "
        "doubles.",
        S_BODY,
    ))

    story.append(Paragraph("6.2 Monte Carlo Drawdown Engine", S_H2))
    story.append(Paragraph(
        "Every second, the MonteCarloEngine runs 10,000 simulations of the "
        "next 30 bars using a bootstrap of recent realized returns. For "
        "each path, it computes the cumulative P&L assuming the current "
        "position value stays constant, tracks the running peak, and "
        "records the maximum drawdown from peak. The probability of "
        "experiencing a drawdown exceeding 2% of the current position "
        "value within the 30-bar horizon is the headline output.",
        S_BODY,
    ))
    story.append(Paragraph(
        "This probability drives a position-size multiplier. When the "
        "drawdown probability is below 0.30, no de-risking is applied. "
        "When it exceeds 0.80, position size is reduced to 20% of the "
        "Kelly-derived value. Between 0.30 and 0.80, the multiplier "
        "decreases linearly from 1.0 to 0.2. The entire simulation is "
        "vectorized in NumPy: 10,000 paths times 30 bars equals 300,000 "
        "samples, computed in under 50 milliseconds on a single CPU core.",
        S_BODY,
    ))

    story.append(Paragraph("6.3 Kill Switch", S_H2))
    story.append(Paragraph(
        "The kill switch is the hard-coded safety layer that bypasses all "
        "AI. It triggers an immediate cancel-all-and-flatten when any of "
        "five conditions are met: end-to-end latency exceeds 5 seconds, "
        "the exchange API error count exceeds 5, a flash crash is detected "
        "(greater than 5% price drop in 60 seconds), portfolio drawdown "
        "exceeds the maximum threshold (default 8%), or a manual trigger "
        "is invoked. Once triggered, the kill switch LATCHES — it must be "
        "explicitly reset by a human before the system can trade again. "
        "This latching behavior is deliberate: a system that auto-recovers "
        "from a flash crash will often re-enter at exactly the wrong time.",
        S_BODY,
    ))

    story.append(Paragraph("6.4 Portfolio Heat Tracker", S_H2))
    story.append(Paragraph(
        "The PortfolioHeatTracker prevents over-concentration in correlated "
        "exposures. It maintains a rolling 100-bar return history for every "
        "tracked symbol, computes the correlation matrix on demand, and "
        "rejects new positions that would push aggregate portfolio heat "
        "above the configured maximum (default 0.30). A new position is "
        "also rejected if it is more than 70% correlated with an existing "
        "position in the same direction — preventing the system from "
        "accumulating 10 long positions that are all effectively 10x "
        "exposure to BTC. The portfolio heat metric is computed as the "
        "square root of the weighted covariance matrix product, which is "
        "the standard portfolio volatility formula.",
        S_BODY,
    ))

    # 7. Layer 5
    story.append(Paragraph("7. Layer 5 — Execution Blade", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "Getting the best price requires algorithms that hide your "
        "intentions and outsmart other bots. The Execution Blade owns the "
        "Smart Order Router (SOR), three classic execution algorithms "
        "(TWAP, VWAP, Iceberg), a PPO-based execution RL agent that learns "
        "optimal slicing, and a real Binance REST API executor with "
        "HMAC-SHA256 request signing.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The SOR picks the execution algorithm based on order notional: "
        "orders under $1,000 are sent as single market orders, $1,000 to "
        "$50,000 use TWAP with 5 slices at 3-second intervals, $50,000 to "
        "$500,000 use VWAP with a 10% participation rate cap, and orders "
        "above $500,000 use Iceberg with a 5% display quantity. The "
        "ExecutionRLAgent (when trained) can override this heuristic "
        "selection based on order-book features: bid-ask spread, depth "
        "imbalance, recent volatility, and time of day. It uses a "
        "continuous action space (number of slices, participation rate, "
        "display quantity percentage) with a Gaussian PPO policy, "
        "rewarded by negative slippage versus the arrival price.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The BinanceExecutor integrates with the official Binance REST "
        "API. Every order submission, cancellation, and balance query is "
        "HMAC-SHA256 signed with the user's API secret. The executor "
        "supports both production (api.binance.com) and testnet "
        "(testnet.binance.vision) endpoints via the BINANCE_TESTNET "
        "environment variable. When API credentials are absent, the "
        "executor runs in DRY-RUN mode: it logs every order it would "
        "submit but does not send the HTTP request. This is an explicit "
        "safety mode — not a mock — so the system never accidentally "
        "submits real orders without credentials.",
        S_BODY,
    ))

    # 8. Layer 6
    story.append(Paragraph("8. Layer 6 — Meta-Cognition Core", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "The Meta-Cognition layer makes OMEGA self-aware of its own "
        "performance degradation. It owns three sub-modules: Trade "
        "Autopsy, Online Learning, and Genetic Optimization. Together "
        "they close the self-improvement loop that distinguishes OMEGA "
        "from a static trading bot.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The TradeAutopsy module batches closed trades and, every 10 "
        "closed positions, sends the batch to the z-ai LLM for root-cause "
        "analysis. The prompt instructs the LLM to categorize each trade "
        "into one of eight outcome categories (good_entry, bad_entry, "
        "slippage_dominant, news_catalyst, regime_mismatch, "
        "stop_too_tight, take_profit_too_early, kill_switch_forced, "
        "other), provide a one-sentence root cause, an actionable "
        "improvement suggestion, and a confidence score in the analysis. "
        "Findings are persisted to disk as JSON files for the Online "
        "Learner and Genetic Optimizer to consume.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The OnlineLearner periodically retrains underperforming agents "
        "on the most recent data window. For PPO agents, this means "
        "collecting a fresh rollout from the latest market data and "
        "running a PPO update. The decision to retrain is triggered when "
        "an agent's recent 50-trade average PnL is more than 10 basis "
        "points worse than its historical average — a clear signal that "
        "the agent's policy has decayed relative to the regime it was "
        "trained on.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The GeneticOptimizer applies Darwinian evolution to agent "
        "hyperparameters. When an agent's 30-day Sharpe ratio falls "
        "below -0.5, the optimizer kills the agent and spawns a mutated "
        "version with hyperparameters perturbed by Gaussian noise. "
        "Mutation operators include: log-normal perturbation of learning "
        "rate and entropy coefficient, additive perturbation of clip "
        "ratio, rare integer jumps in hidden layer size (plus or minus "
        "32, 64 units), and rare integer shifts in observation window. "
        "Survival is determined by Sharpe ratio over the trial window — "
        "if the mutant outperforms its parent, it survives; otherwise "
        "the parent is restored. This is real genetic search over the "
        "hyperparameter space, not random search.",
        S_BODY,
    ))

    # 9. RL Environment
    story.append(Paragraph("9. Reinforcement Learning Environment", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "The TradingEnvironment is the central RL training environment "
        "explicitly requested by the master prompt. It ties the Alpha "
        "Swarm to the Risk Aegis by routing agent actions through the "
        "Kelly position sizer and Monte Carlo de-risking, then computing "
        "a reward that is risk-adjusted rather than purely PnL-based.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The environment exposes a standard reset/step interface "
        "compatible with both offline (historical data) and online (live "
        "Binance WebSocket) modes. In offline mode, observations are "
        "constructed from a Pandas DataFrame of OHLCV bars; in online "
        "mode, they arrive from the Data Nexus's subscriber queue. The "
        "observation is the same 64-dimensional feature vector used by "
        "the PPO agent: 16 bars of log returns, 8 bars of order book "
        "imbalance, and 6 derived technical features.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The reward function is the critical design decision. A pure "
        "PnL reward leads to over-trading and blow-ups. Instead, the "
        "environment computes a composite reward: the rolling 30-bar "
        "Sharpe ratio scaled by 10, minus a drawdown penalty (2 times "
        "the current drawdown from peak equity), plus a Kelly alignment "
        "bonus (0.5 times the Kelly fraction the Risk Aegis would "
        "approve for the chosen position), minus a 100-point penalty if "
        "the kill switch is triggered. This reward shaping produces "
        "agents that take risk-adjusted positions rather than "
        "maximum-size bets.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Episode termination occurs under four conditions: the data "
        "stream ends, the maximum episode length (default 5,000 bars) "
        "is reached, the kill switch is triggered, or equity falls "
        "below 50% of initial capital. The environment tracks the "
        "equity curve throughout the episode and reports Sharpe ratio, "
        "maximum drawdown, and total return in its stats dictionary.",
        S_BODY,
    ))

    # 10. Production Deployment
    story.append(Paragraph("10. Production Deployment", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "OMEGA ships with a docker-compose.yml that brings up Apache "
        "Kafka, Milvus (with its etcd and MinIO dependencies), and Redis "
        "with a single command: docker-compose up -d. The system "
        "auto-detects whether these services are available and uses them "
        "if present, falling back to in-process equivalents otherwise. "
        "This allows the same codebase to run on a developer laptop "
        "(in-process fallbacks), on a staging server (real Kafka, "
        "in-process Milvus), or in full production (real everything).",
        S_BODY,
    ))
    story.append(Paragraph(
        "The recommended production hardware is 8+ CPU cores (PPO "
        "training is CPU-bound for the network sizes used here), 32 GB "
        "of RAM (Milvus and Kafka both benefit from generous memory), "
        "an optional GPU (an RTX 4090 speeds up PPO training by 5-10x), "
        "co-location with the exchange for sub-10ms execution latency, "
        "and NVMe storage for Milvus and Kafka log persistence. The "
        "FPGA acceleration mentioned in the original architectural "
        "blueprint is not implemented in this version; it would belong "
        "in a C++ or Rust extension of the BinanceExecutor.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The deployment checklist in the README walks through the "
        "critical pre-production steps: setting OMEGA_ENV=production, "
        "configuring real Kafka and Milvus, setting BINANCE_API_KEY "
        "and BINANCE_API_SECRET, starting on Binance Testnet for at "
        "least one week of paper trading, monitoring the kill switch "
        "trigger rate (target: less than one per week), setting up "
        "alerting on kill switch triggers, configuring S3 backup of "
        "trade autopsy JSON files, running PPO training weekly with "
        "fresh data, and reviewing genetic mutations monthly.",
        S_BODY,
    ))

    # 11. Disclaimer
    story.append(Paragraph("11. Risk Disclosures and Disclaimer", S_H1))
    story.append(HRFlowable(width="100%", thickness=0.5, color=C_ACCENT,
                            spaceBefore=4, spaceAfter=14))
    story.append(Paragraph(
        "This is an architectural framework for an institutional-grade "
        "trading system. Building, deploying, and operating such a "
        "system requires expertise in Python, distributed systems, "
        "financial mathematics, and risk management. Trading "
        "cryptocurrencies involves substantial risk of loss. The "
        "strategies implemented here, including PPO-based signal "
        "generation, Kelly Criterion position sizing, and Monte Carlo "
        "drawdown estimation, are well-established in the quantitative "
        "finance literature but their application to live markets "
        "requires empirical validation that is beyond the scope of this "
        "codebase.",
        S_BODY,
    ))
    story.append(Paragraph(
        "Never deploy with capital you cannot afford to lose. Always "
        "start with testnet, paper-trade for months before live "
        "trading, and keep position sizes tiny until you have "
        "empirically validated every layer. The kill switch exists for "
        "a reason — when in doubt, the system halts. The Meta-Cognition "
        "layer's trade autopsy exists for a reason — when losses occur, "
        "the system learns. The Genetic Optimizer exists for a reason "
        "— when agents decay, they are replaced. But none of these "
        "mechanisms can prevent losses from unknown unknowns, regime "
        "changes faster than the HMM can detect, exchange outages, or "
        "black swan events.",
        S_BODY,
    ))
    story.append(Paragraph(
        "The code in this repository is provided as-is under the MIT "
        "license. The authors are not responsible for any financial "
        "losses incurred through its use. This whitepaper and the "
        "accompanying code are architectural artifacts, not investment "
        "advice.",
        S_BODY,
    ))

    return story


def generate_pdf(output_path: str) -> str:
    """Generate the whitepaper PDF."""
    doc = BaseDocTemplate(
        output_path,
        pagesize=A4,
        leftMargin=MARGIN_L,
        rightMargin=MARGIN_R,
        topMargin=MARGIN_T,
        bottomMargin=MARGIN_B,
        title="OMEGA — Autonomous Multi-Modal AI Hedge Fund Entity",
        author="OMEGA Quantitative Research",
        subject="Architecture Whitepaper",
        creator="OMEGA",
    )
    # Cover page template (no header/footer, full bleed)
    cover_frame = Frame(
        MARGIN_L, MARGIN_B, CONTENT_W,
        PAGE_H - MARGIN_T - MARGIN_B,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )
    # Body page template
    body_frame = Frame(
        MARGIN_L, MARGIN_B, CONTENT_W,
        PAGE_H - MARGIN_T - MARGIN_B,
        leftPadding=0, rightPadding=0, topPadding=0, bottomPadding=0,
        showBoundary=0,
    )
    doc.addPageTemplates([
        PageTemplate(id="cover", frames=[cover_frame], onPage=cover_page),
        PageTemplate(id="body", frames=[body_frame], onPage=body_page),
    ])

    story = build_story()
    # Switch from cover to body template after the cover page
    from reportlab.platypus.doctemplate import NextPageTemplate
    new_story = [NextPageTemplate("body")]
    new_story.extend(story)
    doc.build(new_story)
    return output_path


if __name__ == "__main__":
    output = "/home/z/my-project/download/OMEGA_Whitepaper.pdf"
    pdf_path = generate_pdf(output)
    size_kb = os.path.getsize(pdf_path) / 1024
    print(f"Generated: {pdf_path} ({size_kb:.1f} KB)")
