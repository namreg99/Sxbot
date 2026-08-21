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

Classify **sharp vs taker flow** from the book — this is the V3-safe view of "what is the informed money doing":

```bash
sxbot flow
```

Paper-trade the strategy. This is the default — `SX_DRY_RUN=true` logs intended orders to `sxbot-paper.jsonl` and never signs anything:

```bash
sxbot run
```

Live trading additionally needs `SX_API_KEY`, `SX_PRIVATE_KEY`, `pip install -e ".[trade]"`, a deployed proxy wallet, and `SX_DRY_RUN=false`. Keep paper mode on until that log looks like a strategy you actually want to fund.

Mainnet API: `https://api.sx.bet`  
Testnet API: `https://api.toronto.sx.bet`

**V3 is testnet-only until August 25, 2026, 10:00 AM EST** ([SX Bet docs](https://docs.sx.bet/developers/new-in-v3): *"Do not point a production integration at V3 until August 25th at 10:00 AM EST"*). Until then, mainnet's V3 routes — `GET /orderbook-v3/snapshot`, `GET /trades-v3/public`, `POST /orders-v3` — return `403`/`401`, while always-on reference routes like `GET /metadata/obv3` and `GET /markets/active` keep working. That split (some mainnet routes fine, V3-specific ones not) is the tell that this is a rollout gate, not an IP block. This bot is built entirely on V3, so it **defaults to testnet** (`SX_API_BASE=https://api.toronto.sx.bet`) until the cutover. Switch to `https://api.sx.bet` after it passes.

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

## How sharp money is recovered without wallets

V3 strips addresses on purpose. The replacement is book microstructure, which does not need an identity:

| What you see | What it usually means | What we do |
| --- | --- | --- |
| Both sides of the book reprice the same way, often with **no** tape print | Makers moved fair value (steam) | Join that side |
| Resting size pulled off one outcome and added to the other | Makers want to be long that outcome | Join that side |
| Depth-weighted mid leads the displayed mid | The body of the book is informed; the top is leftover or a probe | Join the body |
| Size disappears at the best **and** the anonymized tape printed | A taker lifted offers | Ignore — do not copy takers |
| Leftover quotes sitting through the new mid (crossed book) | Stale size after a steam | Take it — that *is* betting with the makers |

`sxbot flow` prints that classification live. `sxbot run` only trades the first four rows when they agree. Events are appended to `sxbot-flow.jsonl` so you can see, after the fact, which motives actually made money.

Wallet copy-trading is a V2 leftover and is not used. After August 25 the same `flow` command is how you read the market on mainnet.

## How a signal is built

Each market is reduced to a two-sided view:

- **Bid** on outcome one = best maker odds resting on outcome one
- **Ask** on outcome one = `1 −` best maker odds resting on outcome two
- **Mid** = average of those
- **Imbalance** = `(size_one − size_two) / total_size`

A **maker steam** is both sides shifting in the same direction. A **size rotation** is makers adding on one outcome and pulling the other. A **taker hit** is a one-sided size drop that the public tape explains — those are not followed. Conflicting tape is ignored. Live orders go through the usual SX V3 path: EIP-712 `Order` signatures, `GTC` to join, `IOC` to take, and a heartbeat so resting quotes die if the process does.

Docs used: [market making](https://docs.sx.bet/developers/market-making), [order book](https://docs.sx.bet/developers/order-book), [public trades](https://docs.sx.bet/developers/public-trades), [posting orders](https://docs.sx.bet/developers/posting-orders).

## Tests

```bash
pytest
```

## Risk

This is experimental trading software for a peer-to-peer betting exchange, not a sportsbook. You can lose the entire stake. SX Bet is unavailable in some jurisdictions, including the United States. Paper trade first.
