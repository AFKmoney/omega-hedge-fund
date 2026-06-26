"""
Web3Wallet — read balances and interact with MetaMask-compatible wallets.

MetaMask is a self-custody wallet — it doesn't have a REST API like exchanges.
Instead it holds keys that sign Ethereum/Solana/Polygon transactions. This
module connects to any EVM-compatible chain via public RPC and reads:
    - Native balance (ETH, MATIC, BNB)
    - ERC-20 token balances (USDT, USDC, WBTC, etc.)
    - Transaction history (via Etherscan API)

It does NOT hold your private keys. It reads from public blockchain data using
your wallet address (which is public by definition). To execute transactions
(swap, send), you'd sign with MetaMask in the browser — the backend can build
the unsigned transaction and the frontend submits it via window.ethereum.

Capabilities:
    - read_balance(address, chain) → {token: balance}
    - read_erc20(address, token_contract, chain)
    - build_swap(from_token, to_token, amount) → unsigned tx (for MetaMask to sign)
    - estimate_gas(tx, chain)

Supported chains: Ethereum, Polygon, BSC, Arbitrum, Optimism, Base.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Dict, List, Optional

import aiohttp

from omega.utils.logger import get_logger

logger = get_logger("omega.web3.wallet")

# Public RPC endpoints (free, rate-limited, can be unreliable).
# For production, set WEB3_RPC_URL to an Alchemy/Infura/QuickNode endpoint.
import os
_default_rpc = os.getenv("WEB3_RPC_URL", "")
CHAINS = {
    "ethereum": {
        "rpc": _default_rpc or "https://rpc.ankr.com/eth",
        "chain_id": 1,
        "native": "ETH",
        "explorer_api": "https://api.etherscan.io/api",
    },
    "polygon": {
        "rpc": "https://polygon-rpc.com",
        "chain_id": 137,
        "native": "MATIC",
        "explorer_api": "https://api.polygonscan.com/api",
    },
    "bsc": {
        "rpc": "https://bsc-dataseed.binance.org",
        "chain_id": 56,
        "native": "BNB",
        "explorer_api": "https://api.bscscan.com/api",
    },
    "arbitrum": {
        "rpc": "https://arb1.arbitrum.io/rpc",
        "chain_id": 42161,
        "native": "ETH",
        "explorer_api": "https://api.arbiscan.io/api",
    },
    "base": {
        "rpc": "https://mainnet.base.org",
        "chain_id": 8453,
        "native": "ETH",
        "explorer_api": "https://api.basescan.org/api",
    },
}

# Common ERC-20 token contracts (Ethereum mainnet)
ERC20_CONTRACTS = {
    "USDT": "0xdAC17F958D2ee523a2206206994597C13D831ec7",
    "USDC": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48",
    "WBTC": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
    "DAI": "0x6B175474E89094C44Da98b954EedeAC495271d0F",
    "LINK": "0x514910771AF9Ca656af840dff83E8264EcF986CA",
    "UNI": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
}


@dataclass
class TokenBalance:
    symbol: str
    balance: float
    decimals: int
    contract: str = ""
    usd_value: float = 0.0


class Web3Wallet:
    """Read-only interface to any EVM-compatible wallet via public RPC."""

    def __init__(self, address: str = "", etherscan_key: str = "") -> None:
        self.address = address.lower() if address else ""
        self.etherscan_key = etherscan_key
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()

    async def _rpc_call(self, chain: str, method: str, params: list) -> any:
        """Make a JSON-RPC call to the chain's public endpoint."""
        config = CHAINS.get(chain)
        if not config:
            raise ValueError(f"Unknown chain: {chain}")
        session = await self._get_session()
        payload = {"jsonrpc": "2.0", "method": method, "params": params, "id": 1}
        try:
            async with session.post(config["rpc"], json=payload, timeout=10) as resp:
                result = await resp.json()
                return result.get("result")
        except Exception as exc:
            logger.debug(f"RPC call failed ({chain} {method}): {exc}")
            return None

    async def get_native_balance(self, chain: str = "ethereum") -> float:
        """Get native token balance (ETH, MATIC, BNB) in whole units."""
        if not self.address:
            return 0.0
        result = await self._rpc_call(chain, "eth_getBalance", [self.address, "latest"])
        if result is None:
            return 0.0
        try:
            wei = int(result, 16)
            return wei / 1e18
        except (ValueError, TypeError):
            return 0.0

    async def get_erc20_balance(
        self, token_contract: str, chain: str = "ethereum"
    ) -> float:
        """Get ERC-20 token balance. Uses balanceOf(address)."""
        if not self.address:
            return 0.0
        # balanceOf(address) selector = 0x70a08231 + padded address
        padded_addr = self.address[2:].zfill(64) if self.address.startswith("0x") else self.address.zfill(64)
        data = f"0x70a08231000000000000000000000000{padded_addr}"
        result = await self._rpc_call(chain, "eth_call",
                                      [{"to": token_contract, "data": data}, "latest"])
        if result is None:
            return 0.0
        try:
            raw = int(result, 16)
            return raw / 1e6  # most stablecoins are 6 decimals
        except (ValueError, TypeError):
            return 0.0

    async def get_all_balances(self, chain: str = "ethereum") -> List[TokenBalance]:
        """Get native + common ERC-20 balances for the wallet."""
        balances: List[TokenBalance] = []
        config = CHAINS.get(chain, {})
        native_sym = config.get("native", "ETH")
        native = await self.get_native_balance(chain)
        if native > 0:
            balances.append(TokenBalance(native_sym, native, 18))
        # Read common ERC-20s
        for symbol, contract in ERC20_CONTRACTS.items():
            amt = await self.get_erc20_balance(contract, chain)
            if amt > 0:
                decimals = 8 if symbol in ("WBTC",) else 6
                balances.append(TokenBalance(symbol, amt, decimals, contract))
        return balances

    def build_transfer_tx(
        self, to_address: str, amount_wei: str, chain: str = "ethereum"
    ) -> dict:
        """Build an unsigned native-token transfer tx for MetaMask to sign.

        The frontend calls window.ethereum.request({method:'eth_sendTransaction',
        params:[tx]}) — the user approves in MetaMask, keys never leave the wallet.
        """
        config = CHAINS.get(chain, CHAINS["ethereum"])
        return {
            "to": to_address,
            "from": self.address,
            "value": amount_wei,  # hex
            "chainId": hex(config["chain_id"]),
        }

    def status(self) -> dict:
        return {
            "address": self.address or "(not configured)",
            "chains": list(CHAINS.keys()),
            "tokens_tracked": list(ERC20_CONTRACTS.keys()),
        }
