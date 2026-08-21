# Sxbot

A trading bot for [SX Bet](https://sx.bet), a peer-to-peer sports betting exchange. It does **not** copy wallets. It follows the market makers who actually set the line.

Nothing here is guaranteed edge. The useful output right now is (1) a paper log of what we *would* have done on live books, and (2) a flow log of where informed size showed up, pregame vs live.

## How it works

SX Bet is an exchange, not a sportsbook. The people who matter are the **makers** who keep two-sided quotes up. When they know something they reprice both sides, rotate size onto one outcome, or leave stale quotes through the new mid. Takers lifting the top look different: size disappears on one side *and* the tape prints.

Sxbot polls the book, classifies that microstructure, and either:

1. **Takes stale quotes** (`take_stale`, IOC) when leftover size is sitting through the new mid — betting *with* the makers.
2. **Joins as a maker** (`join_maker`, GTC) one tick behind the new best on the informed side.

It ignores taker hits. It does not dime the lead quote. Live (in-play) markets are watched for intel but **not traded** unless you set `SX_ALLOW_LIVE=true`.

### V2 now, V3 on August 25

V3 mainnet books are gated until **August 25, 2026, 10:00 AM EST**. Until then:

- Mainnet V3 snapshot/tape/order routes 403.
- Mainnet **V2** `GET /orders` and `GET /trades` still return the real books.
- Testnet V3 books are dummy/symmetric and useless for extracting anything.

The bot defaults to **mainnet**. It sums V2 resting orders by price/side into the same anonymous `Book` the V3 classifier uses, and throws away maker addresses. After cutover it switches to `GET /orderbook-v3/snapshot` on the same URL. The strategy does not change.

## Is it paper trading? Is it recording?

Only while `sxbot run` is actually running. There is no background daemon. Dry-run is the default (`SX_DRY_RUN=true`): intended orders go to `sxbot-paper.jsonl` and nothing is signed. Flow events go to `sxbot-flow.jsonl`. `sxbot summary` prints both.

Live orders additionally need `SX_API_KEY`, `SX_PRIVATE_KEY`, `pip install -e ".[trade]"`, a funded proxy, and `SX_DRY_RUN=false`. Keep paper mode on until that log looks like a strategy you actually want to fund.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
```

```bash
sxbot doctor          # connectivity + which book source (V2 vs V3)
sxbot scan            # rank live books by maker-size imbalance
sxbot flow            # classify steam / rotation / lag / taker / crossed
sxbot run             # paper-trade loop (writes jsonl)
sxbot summary         # what has been recorded so far
```

`sxbot flow --once` and `sxbot run --once` do two polls then exit.

Mainnet: `https://api.sx.bet` (default)  
Testnet: `https://api.toronto.sx.bet`

## What you can extract before V3 — and what still helps after

| Build now | Why it still matters after V3 |
| --- | --- |
| Paper-trade the same join/take rules on **real** mainnet books | Identical classifier; only the snapshot transport changes |
| Record `maker_steam`, `size_rotation`, `tob_lag`, `taker_hit`, `crossed` tagged **pregame vs live** | After V3 you still cannot see wallets; this *is* the smart-money tape |
| Calibrate which motives actually print (summary over days of jsonl) | Same labels on V3, so the sample is not thrown away |
| Do **not** build a wallet-copy product | Addresses die on August 25 |

Pregame is the cleaner tape (no betting delay, less toxic flow). Live is noisier; we log it so you can see where size is, but we do not join those books by default.

## Configuration

See `.env.example`. The knobs that matter:

| Variable | Meaning |
| --- | --- |
| `SX_SPORT_IDS` | Universe. `8,1,3,2,5` = football, basketball, baseball, hockey, soccer |
| `SX_ONLY_MAIN_LINE` | Skip alternate spreads/totals |
| `SX_WATCH_LIVE` | Include in-play books in the intel poll (default on) |
| `SX_ALLOW_LIVE` | Off by default. Allow paper/live *orders* on in-play |
| `SX_MIN_MID_MOVE_BPS` | How far the mid must move (100 bps = 1 implied-probability point) |
| `SX_MIN_IMBALANCE` | Size skew that counts as makers parking money on one side |
| `SX_JOIN_TICKS_BEHIND` | Rest behind the lead MM instead of penny-jumping them |
| `SX_STAKE_USDC` | Size per join/take. Mainnet minimum is currently 5 USDC |
| `SX_MAX_MARKETS` | Cap on markets polled each loop (soonest kickoff first, plus a live slice) |
| `SX_BOOK_SOURCE` | `auto` (V2 on mainnet until cutover), or force `v2` / `v3` |

## How sharp money is recovered without wallets

| What you see | What it usually means | What we do |
| --- | --- | --- |
| Both sides of the book reprice the same way, often with **no** tape print | Makers moved fair value (steam) | Join that side |
| Resting size pulled off one outcome and added to the other | Makers want to be long that outcome | Join that side |
| Depth-weighted mid leads the displayed mid | The body of the book is informed; the top is leftover or a probe | Join the body |
| Size disappears at the best **and** the tape printed | A taker lifted offers | Ignore — do not copy takers |
| Leftover quotes sitting through the new mid (crossed book) | Stale size after a steam | Take it — that *is* betting with the makers |

`sxbot flow` prints that classification live. `sxbot run` only papers the first four rows when they agree.

## How a signal is built

Each market is reduced to a two-sided view:

- **Bid** on outcome one = best maker odds resting on outcome one
- **Ask** on outcome one = `1 −` best maker odds resting on outcome two
- **Mid** = average of those
- **Imbalance** = `(size_one − size_two) / total_size`

A **maker steam** is both sides shifting in the same direction. A **size rotation** is makers adding on one outcome and pulling the other. A **taker hit** is a one-sided size drop that the public tape explains. Conflicting steam vs rotation is dropped. Live orders (when you turn them on) go through the SX V3 path: EIP-712 `Order` signatures, `GTC` to join, `IOC` to take, and a heartbeat so resting quotes die if the process does.

Docs used: [market making](https://docs.sx.bet/developers/market-making), [order book](https://docs.sx.bet/developers/order-book), [public trades](https://docs.sx.bet/developers/public-trades), [posting orders](https://docs.sx.bet/developers/posting-orders), [V3 rollout](https://docs.sx.bet/developers/new-in-v3).

## Tests

```bash
pytest
```

## Risk

This is experimental trading software for a peer-to-peer betting exchange, not a sportsbook. You can lose the entire stake. SX Bet is unavailable in some jurisdictions, including the United States. Paper trade first.
