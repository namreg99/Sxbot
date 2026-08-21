# Sxbot

A trading bot for [SX Bet](https://sx.bet), a peer-to-peer sports betting exchange. It does **not** copy wallets. It follows the market makers who actually set the line.

## Why makers, not copy-trading

SX Bet V3 made wallet copy-trading a dead end:

- The public trade tape is anonymized. There is no public per-user fill query.
- The order book is aggregated by price. Levels have size, not maker addresses.

What you can still see is the **maker book moving**. Market makers have to keep two-sided quotes live. When they know something, they do not wait to be copied — they pull one side and lift the other, and they park more size on the outcome they want. That is the informed money.

Sxbot watches consecutive book snapshots and:

1. **Takes stale quotes** when makers have already repriced one side and leftover size is still sitting through the new mid (a crossed book). Taking that size *is* betting with the makers.
2. **Joins as a maker** one tick behind the new best on the informed side. You support the same line, earn the spread / [maker rewards](https://docs.sx.bet/user-guides/rewards/maker-rewards), and avoid diming the lead quote.

A mid that only moved because a taker swept the top is ignored unless makers also shifted the other side or flipped their size.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

Check the exchange and your IP:

```bash
sxbot doctor
```

Rank current books by maker-size imbalance (no orders):

```bash
sxbot scan
```

Watch line moves as they happen (still no orders):

```bash
sxbot watch
```

Paper-trade the strategy. This is the default — `SX_DRY_RUN=true` logs intended orders to `sxbot-paper.jsonl` and never signs anything:

```bash
sxbot run
```

Live trading additionally needs `SX_API_KEY`, `SX_PRIVATE_KEY`, `pip install -e ".[trade]"`, a deployed proxy wallet, and `SX_DRY_RUN=false`. Keep paper mode on until that log looks like a strategy you actually want to fund.

Mainnet API: `https://api.sx.bet`  
Testnet API: `https://api.toronto.sx.bet`

Some cloud IPs get `403` on mainnet book and public-tape routes. Testnet is unrestricted and is the right place to develop. Set `SX_API_BASE=https://api.toronto.sx.bet`.

## Configuration

See `.env.example`. The knobs that matter:

| Variable | Meaning |
| --- | --- |
| `SX_SPORT_IDS` | Universe. `8,1,3,2,5` = football, basketball, baseball, hockey, soccer |
| `SX_ONLY_MAIN_LINE` | Skip alternate spreads/totals |
| `SX_ALLOW_LIVE` | Off by default. In-play has a betting delay and toxic flow |
| `SX_MIN_MID_MOVE_BPS` | How far the mid must move (100 bps = 1 implied-probability point) |
| `SX_MIN_IMBALANCE` | Size skew that counts as makers parking money on one side |
| `SX_JOIN_TICKS_BEHIND` | Rest behind the lead MM instead of penny-jumping them |
| `SX_STAKE_USDC` | Size per join/take. Mainnet minimum is currently 5 USDC |
| `SX_MAX_MARKETS` | Cap on markets polled each loop (soonest kickoff first) |

## How a signal is built

Each market is reduced to a two-sided view:

- **Bid** on outcome one = best maker odds resting on outcome one
- **Ask** on outcome one = `1 −` best maker odds resting on outcome two
- **Mid** = average of those
- **Imbalance** = `(size_one − size_two) / total_size`

A **maker reprice** is both sides shifting in the same direction. A **size flow** is makers adding on one outcome and pulling the other. Conflicting tape is ignored. Live orders go through the usual SX V3 path: EIP-712 `Order` signatures, `GTC` to join, `IOC` to take, and a heartbeat so resting quotes die if the process does.

Docs used: [market making](https://docs.sx.bet/developers/market-making), [order book](https://docs.sx.bet/developers/order-book), [public trades](https://docs.sx.bet/developers/public-trades), [posting orders](https://docs.sx.bet/developers/posting-orders).

## Tests

```bash
pytest
```

## Risk

This is experimental trading software for a peer-to-peer betting exchange, not a sportsbook. You can lose the entire stake. SX Bet is unavailable in some jurisdictions, including the United States. Paper trade first.
