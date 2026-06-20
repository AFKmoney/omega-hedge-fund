"""Layer 5 — Execution Blade: RL-driven smart order routing."""
from omega.execution.base import Executor
from omega.execution.algorithms import TWAP, VWAP, Iceberg
from omega.execution.sor import SmartOrderRouter
from omega.execution.binance_executor import BinanceExecutor
from omega.execution.execution_rl import ExecutionRLAgent
from omega.execution.blade import ExecutionBlade

__all__ = [
    "Executor",
    "TWAP",
    "VWAP",
    "Iceberg",
    "SmartOrderRouter",
    "BinanceExecutor",
    "ExecutionRLAgent",
    "ExecutionBlade",
]
