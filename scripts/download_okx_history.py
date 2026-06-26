#!/usr/bin/env python3
"""
Download real OHLCV history from OKX public API (no key needed).

OKX returns max 100 candles per call. This script paginates backward to fetch
~40k 1-minute candles (~28 days) for one instrument, then saves as CSV
compatible with train_ppo.py.

Usage:
    python scripts/download_okx_history.py --inst BTC-USDT-SWAP --bars 40000
    python scripts/download_okx_history.py --inst ETH-USDT-SWAP --bars 20000
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

import aiohttp
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from omega.utils.logger import get_logger

logger = get_logger("omega.scripts.download_okx")

_OKX_CANDLES = "https://www.okx.com/api/v5/market/history-candles"


async def fetch_all(inst: str, total: int, bar: str = "1m") -> pd.DataFrame:
    """Paginate backward through OKX candle history using the 'after' param.

    OKX returns candles newest-first. 'after=<ts>' returns candles OLDER than ts.
    So to go back in time: take the oldest ts of each batch, use it as 'after'
    for the next batch.
    """
    rows = []
    after = ""  # empty = start from newest
    batch = 0
    async with aiohttp.ClientSession() as session:
        while len(rows) < total:
            params = {"instId": inst, "bar": bar, "limit": 100}
            if after:
                params["after"] = after
            try:
                async with session.get(_OKX_CANDLES, params=params, timeout=15) as resp:
                    payload = await resp.json()
            except Exception as exc:
                logger.warning(f"Fetch failed (batch {batch}): {exc}, retrying...")
                await asyncio.sleep(2)
                continue
            data = payload.get("data", [])
            if not data:
                logger.info(f"No more data after {len(rows)} candles")
                break
            # Deduplicate against what we have (OKX can overlap at boundaries)
            existing_ts = {r[0] for r in rows}
            new = [r for r in data if r[0] not in existing_ts]
            rows.extend(new)
            # data is newest->oldest; the oldest is data[-1]; use it as next 'after'
            after = data[-1][0]
            batch += 1
            if batch % 50 == 0:
                logger.info(f"Fetched {len(rows)}/{total} unique candles ({batch} batches)")
            if not new:
                # No new data → stop to avoid infinite loop
                logger.info(f"No new unique candles, stopping at {len(rows)}")
                break
            await asyncio.sleep(0.15)  # rate limit
    if not rows:
        return pd.DataFrame()
    # OKX format: [ts, o, h, l, c, vol, volCcy, volCcyQuote, confirm]
    df = pd.DataFrame(rows, columns=["ts", "open", "high", "low", "close", "volume",
                                      "volCcy", "volCcyQuote", "confirm"])
    df["timestamp"] = pd.to_datetime(df["ts"].astype("int64"), unit="ms", utc=True)
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = df[col].astype("float32")
    df = df[["timestamp", "open", "high", "low", "close", "volume"]].sort_values("timestamp")
    df = df.drop_duplicates(subset="timestamp").reset_index(drop=True)
    # Reverse so oldest is first (chronological)
    df = df.iloc[::-1].reset_index(drop=True)
    return df.set_index("timestamp")


def main() -> None:
    p = argparse.ArgumentParser(description="Download OKX candle history")
    p.add_argument("--inst", type=str, default="BTC-USDT-SWAP")
    p.add_argument("--bars", type=int, default=40000)
    p.add_argument("--bar", type=str, default="1m")
    p.add_argument("--out", type=str, default=None)
    args = p.parse_args()

    out = Path(args.out) if args.out else Path(f"tests/_okx_{args.inst.replace('-','')}.csv")
    logger.info(f"Downloading {args.bars} {args.bar} candles for {args.inst}...")
    df = asyncio.run(fetch_all(args.inst, args.bars, args.bar))
    if df.empty:
        logger.error("No data downloaded")
        sys.exit(1)
    # Convert to the format train_ppo expects
    df.index.name = "timestamp"
    df.to_csv(out)
    # Stats
    rets = df["close"].pct_change().dropna()
    logger.info(
        f"Saved {len(df)} bars to {out} | "
        f"range: {df.index[0]} to {df.index[-1]} | "
        f"price: ${df['close'].iloc[0]:,.0f} → ${df['close'].iloc[-1]:,.0f} | "
        f"ret mean: {rets.mean()*100:.4f}% std: {rets.std()*100:.4f}%"
    )
    print(f"\n  ✓ {len(df)} bars saved to {out}")


if __name__ == "__main__":
    main()
