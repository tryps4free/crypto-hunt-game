# Crypto Hunt — local multiplayer starter

A small local PWA-style crypto treasure-hunt game starter. It includes:
- profiles with username/password login
- unique page claims across users
- Litecoin → Ethereum → Bitcoin level gates
- XP, virtual rewards, leaderboard
- balance-check integration placeholder for PUBLIC addresses / authorized test puzzles
- SQLite storage and backup instructions

## Safety boundary
This starter does not extract, store, or distribute private keys discovered on third-party wallets.
The `check_public_address_balance()` integration point accepts a public address only. For playable
rewards, use testnet wallets, keys you own, or intentionally-created puzzle challenges.

## Run locally (Python 3.11+)

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://127.0.0.1:8000

First registered account is not automatically admin; set `ADMIN_USERNAME` before starting if you
want a local admin username, e.g.:
- Windows PowerShell: `$env:ADMIN_USERNAME="yourname"`
- macOS/Linux: `export ADMIN_USERNAME="yourname"`

For a small private group, don't expose this development server directly to the public internet.
Use HTTPS, strong unique passwords, rate limiting, and a proper production deployment before hosting.

## Database backup

Stop the server, then copy `crypto_hunt.db` to a safe location. For a live SQLite backup, use:
```bash
sqlite3 crypto_hunt.db ".backup 'crypto_hunt-backup.db'"
```
Do not put passwords, seed phrases, or private keys in the database.

## Where to add public balance providers

In `main.py`, find `check_public_address_balance(network, address)`.
It is intentionally a placeholder. Implement it using a reputable explorer/API provider, with API
keys stored in environment variables. Never ask users for seed phrases or private keys.


## Render deploy (flat GitHub layout)
This package is arranged with files directly in the repository root.
- Build Command: `pip install -r requirements.txt`
- Start Command: `uvicorn main:app --host 0.0.0.0 --port $PORT`


## Game loop (updated)
- Each account receives 20 hunt clicks per UTC day.
- A 100 VCOIN in-game purchase adds 10 extra clicks for that day.
- After finding a page, the player can immediately hunt another page on the same network from the result screen.
- The “Buy next level with ETH” button is DEMO ONLY: it advances one level and records a demo purchase, but does not connect to a wallet or collect real ETH. Do not advertise it as a real-money purchase until a real payment flow is implemented and independently verified.
- Real blockchain payments and real wallet/private-key handling are not implemented.
