# OMEGA — Setup Guide

Complete setup to go from zero to live trading on OKX.

---

## Step 1 — Install

```bash
git clone https://github.com/AFKmoney/omega-hedge-fund.git
cd omega-hedge-fund
pip install -r requirements.txt
python tests/test_smoke.py   # verify 12 tests pass
```

---

## Step 2 — Generate your TOTP secret (wallet security)

The TOTP secret protects your withdrawals. **A leaked API key alone cannot withdraw without it.** Generate it ONCE, locally, never in git:

```bash
python -c "
import secrets, base64
raw = secrets.token_bytes(20)
secret = base64.b32encode(raw).decode()
print('Your TOTP secret:', secret)
print('Save it to: ~/.omega/data/totp_secret.txt')
"
```

Then add it to Google Authenticator on your phone:
1. Open Google Authenticator
2. Tap **+** → **Enter setup key**
3. Account name: `OMEGA trader`
4. Key: paste your secret
5. Type: **Time-based**
6. Tap Add

Set the env var:
```bash
set OMEGA_TOTP_SECRET=your_secret_here
```

> ⚠️ **Never commit this secret. Never put it in a cloud sync. If compromised, regenerate immediately.** The secret lives in `~/.omega/` which is outside the git repo.

---

## Step 3 — Create your OKX API keys

1. Log into OKX → **API Management**
2. Create a key with permissions:
   - ✅ **Read**
   - ✅ **Trade**
   - ✅ **Withdraw** (only if you want the wallet manager to work)
3. Note the 3 values: **API Key**, **Secret**, **Passphrase**
4. Set them:
```bash
set OKX_API_KEY=your_key
set OKX_API_SECRET=your_secret
set OKX_PASSPHRASE=your_passphrase
set OKX_DEMO=true          # START WITH DEMO (paper trading)!
```

---

## Step 4 — Train the PPO agents (optional but recommended)

```bash
# Generate multi-regime synthetic data
python scripts/gen_synthetic_data.py

# Train both agents
python scripts/train_ppo.py --data tests/_train_data.csv --episodes 50 --mode-type trend
python scripts/train_ppo.py --data tests/_train_data.csv --episodes 25 --mode-type meanrev
```

Checkpoints save to `checkpoints/ppo_<mode>_latest.pt`. Validated: trend +11.9%, meanrev +21.1% vs random −1.1%.

---

## Step 5 — Run live (demo first!)

```bash
# Demo mode (paper trading, zero risk)
set OKX_DEMO=true
python scripts/live_trade.py --symbols BTCUSDT,ETHUSDT --load-checkpoints
```

When you're ready for real money:
```bash
set OKX_DEMO=false
python scripts/live_trade.py --symbols BTCUSDT --load-checkpoints
```

Press `Ctrl+C` to stop. The kill switch cancels open orders on shutdown.

---

## Step 6 — Dashboard + wallet commands

```bash
# Live dashboard (balance, positions, PnL)
python -m omega.cli.app dashboard

# Wallet status
python -m omega.cli.app status

# Withdraw (requires TOTP code from your phone)
python -m omega.cli.app withdraw 100 USDT 0xAbc... ETH-ERC20 123456

# Panic — freeze ALL withdrawals instantly
python -m omega.cli.app panic

# Unfreeze (requires TOTP)
python -m omega.cli.app unfreeze 123456

# Change daily withdrawal cap
python -m omega.cli.app set-cap 2000 123456
```

---

## Wallet security layers (read this)

Your money is protected by 4 independent layers:

| Layer | What it does | What defeats it |
|-------|-------------|----------------|
| **TOTP** | Every withdrawal needs a 6-digit code from your phone | Attacker needs your physical phone |
| **Daily cap** | Max $500/day default | Limits damage to the cap |
| **Panic switch** | `omega panic` freezes all withdrawals | Instant kill |
| **Audit log** | Every attempt logged (chained hash) | Tamper-evident trail |

**The only way to lose funds via API is if BOTH your API key AND your TOTP secret are compromised.** Keep them separate:
- API key: in env vars or a password manager
- TOTP secret: on your phone (Google Authenticator) + `~/.omega/` local backup

---

## Troubleshooting

**"OKXExecutor in DRY-RUN mode"** → your 3 OKX credentials aren't set. Check `OKX_API_KEY`, `OKX_API_SECRET`, `OKX_PASSPHRASE`.

**"kill_switch triggered flash_crash"** → the kill switch detected a real crash OR you're on a very volatile moment. Wait or reset.

**"size_too_small"** → your account equity is too small for the Kelly-sized position. Raise equity or lower `OMEGA_RISK_PER_TRADE_PCT`.

**PP0 agents make no sense** → did you train them? Run `--load-checkpoints` flag. Check `checkpoints/ppo_*_latest.pt` exists.

**Crowd engine emits nothing** → that's correct behavior when signals diverge. It only emits on coherent extremes. Check `omega.crowd_engine.stats()` to see live signal values.
