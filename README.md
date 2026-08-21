# Sxbot

A trading bot for [SX Bet](https://sx.bet), a peer-to-peer sports betting exchange. It does **not** copy wallets. It follows the market makers who actually set the line.

Nothing here is guaranteed edge. The useful output right now is (1) a paper log of what we *would* have done on live books, and (2) a flow log of where informed size showed up, pregame vs live.

## How a trade works (plain English)

SX Bet is not a sportsbook like DraftKings. It is a marketplace. People post prices, other people take them — the same idea as a stock exchange, but the "stock" is "will this team cover / will this total go over."

**Market makers** are the people who keep a price up on *both* sides of a game (Team A *and* Team B, or Over *and* Under). When they change their mind they move those prices. That move is the "smart money" this bot is trying to follow. It does **not** copy random winning wallets.

Every couple of seconds the bot looks at the live prices and asks:

1. Did the makers just move both sides the same way? → sit on that side, one tick worse than them (so we are not jumping in front).
2. Did they pull money off one side and pile it on the other? → same thing, join the heavy side.
3. Is leftover junk sitting at a stale price? → take it, because that *is* betting with the makers.
4. Did a random bettor just smash the top of the book? → ignore it.

In **paper mode** (the default) it writes "I would have bet $5 on X at 49%" to a file. It does **not** send an order and it does **not** spend money. Real orders only happen if you later turn dry-run off and add keys.

**We cannot backtest last season.** SX does not keep old order books, so there is nothing to rewind. What we *can* do is leave paper mode running, then after SX *reports* the market run `sxbot grade`. A TV/scoreboard final is not the same as an SX `outcome` — totals often report first; moneylines and spreads can stay pending for hours. That scores those paper quotes *if they had been filled*. Joining as a maker often does not fill, so graded P&L is the optimistic case.

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
sxbot radar           # sweep ~30s, print ranked "where is the money right now" (read it, bet yourself)
sxbot run             # paper-trade loop (writes jsonl)
sxbot summary         # what has been recorded so far
sxbot grade           # after SX reports the outcome (TV final is not enough)
sxbot scoreboard      # grade the SIGNAL itself vs. the price already in the book
sxbot sharp           # fingerprint known wallets (V2 only, until Aug 25)
sxbot archive         # pull winter + summer fill history into SQLite
sxbot profiles        # sport / odds / live-vs-pregame style report
sxbot overlap         # V2 only: who was quoting when our classifier fired?
sxbot mimic           # paper-copy new fills from those wallets (V2 only)
```

`sxbot flow --once` and `sxbot run --once` do two polls then exit.

Mainnet: `https://api.sx.bet` (default)  
Testnet: `https://api.toronto.sx.bet`

## The tool for a human bettor, not an autobot

`sxbot radar --seconds 30` is the direct answer to "where is the smart money right now": it sweeps the live book for a window (one instant rarely catches anything — most markets do not reprice every poll), then prints every market that showed a maker steam, size rotation, top-of-book lag, or a crossed/stale quote, ranked by confidence, with the reasoning spelled out and the kickoff time. Nothing gets bet automatically — you read it and decide.

Confidence there is a hand-tuned heuristic, not proof. `sxbot scoreboard` is how you find out if it's real: it grades the **signal itself** — not paper orders — against real settled results. It reports two numbers per motive and per confidence bucket:

- **hit rate** — how often the flagged side won. On its own this is close to useless: a signal that only ever fires on heavy favorites "hits" most of the time for free, because the price already said so.
- **avg edge** — the actual result (1/0) minus the implied probability the book was *already quoting* for that side the moment the signal fired. This is the number that can't be faked by picking favorites. Near 0% means the signal adds nothing beyond the price that was already there; positive and durable across a real sample is the only thing that would make this a genuine "better bettor" tool.

Run `sxbot run` (or just `sxbot flow`) for a while so `sxbot-flow.jsonl` accumulates, then `sxbot scoreboard` once some of those games have finished. Treat anything under ~100 settled events as noise — small samples in sports betting lie constantly, in both directions.

### Is historical wallet data worth building on?

Mostly no, and that skepticism is correct. `archive`/`sharp`/`mimic` read wallet-attributed V2 fills, which vanish from the public API after the V3 cutover (Aug 25) — anything built to depend on a specific address stops working that day. What those addresses *can* still tell you, while V2 lasts, is which sports/leagues/bet-types tend to have the most informed two-sided quoting (e.g. `cypherprod` above is a real, currently-profitable maker, concentrated in soccer) — that's a hint about where to point `radar`/`run`, not a wallet-copy product. The durable instrument is `flow.py` + `scoreboard`: it never depended on addresses, works identically on mainnet today (via the V2→book adapter) and on V3 after cutover, and its only real validation is the settled-outcome evidence `scoreboard` produces — not a story about named wallets.

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
| `SX_SPORT_IDS` | Universe. `8,1,3,2,5,6` = football, basketball, baseball, hockey, soccer, tennis |
| `SX_ONLY_MAIN_LINE` | Skip alternate spreads/totals |
| `SX_WATCH_LIVE` | Include in-play books in the intel poll (default on) |
| `SX_ALLOW_LIVE` | Off by default. Allow paper/live *orders* on in-play |
| `SX_MIN_MID_MOVE_BPS` | How far the mid must move (100 bps = 1 implied-probability point) |
| `SX_MIN_IMBALANCE` | Size skew that counts as makers parking money on one side |
| `SX_JOIN_TICKS_BEHIND` | Rest behind the lead MM instead of penny-jumping them |
| `SX_STAKE_USDC` | Size per join/take. Mainnet minimum is currently 5 USDC |
| `SX_MAX_MARKETS` | Cap on markets polled each loop (soonest kickoff first, plus a live slice) |
| `SX_FOLLOW_STYLE` | `join` (sit behind makers), `take` (hit now), `mixed` (take strong steam, else join) |
| `SX_SHARP_WALLETS` | Extra addresses on top of the four already paired (Gary, cypherprod, BotswanaMC, HedgeHog) |
| `SX_MIMIC_MAX_DECIMAL` | Mimic skips longer shots than this (default 3.5 — do not clone 7.84 alts) |
| `SX_BOOK_SOURCE` | `auto` (V2 on mainnet until cutover), or force `v2` / `v3` |

## How sharp money is recovered without wallets

| What you see | What it usually means | What we do |
| --- | --- | --- |
| Both sides of the book reprice the same way, often with **no** tape print | Makers moved fair value (steam) | Join that side |
| Resting size pulled off one outcome and added to the other | Makers want to be long that outcome | Join that side |
| Depth-weighted mid leads the displayed mid | The body of the book is informed; the top is leftover or a probe | Join the body |
| Size disappears at the best **and** the tape printed | A taker lifted offers | Ignore — do not copy takers |
| Leftover quotes sitting through the new mid (crossed book) | Stale size after a steam | Take it — that *is* betting with the makers |

`sxbot flow` prints that classification live. `sxbot run` papers maker-driven motives. `SX_FOLLOW_STYLE=take` hits the informed side immediately instead of resting; `mixed` takes only on strong steam/rotation.

## Fingerprinting wallets before V3

Known profitable addresses are useful until **August 25**, then they vanish from the public API. Four wallets are already paired from unique fills (GambleGuruGary, cypherprod, BotswanaMC, HedgeHog). `sxbot archive` pulls the biggest V2 sample the API will give us across **winter (Dec/Jan NFL/NBA/NHL)** and **summer (soccer, baseball, tennis)** windows — not one endless scrape from December 1, which never reaches January because Gary prints hundreds of fills a day.

`sxbot profiles` turns that SQLite into a style card: maker vs taker, sport mix (tennis included), live vs pregame, odds buckets (the ≤1.12 hammer vs 7.84 lottery tickets), scale-in, and **net** P&L vs the gross “Won” leaderboard. `sxbot mimic` paper-copies **new** taker fills at `SX_STAKE_USDC` (and HedgeHog-style resting quotes) while V2 still attributes them. It does not dump history into the paper log; the first poll only primes seen fill ids. Longshots above `SX_MIMIC_MAX_DECIMAL` (3.5) are skipped.

`sxbot overlap` is the last labeled check before cutover: each flow signal is tagged with which of the four wallets were *resting* on the flagged side (`quoted_by`) and which *took* it on the same poll (`takers`). Leave `sxbot flow` or `sxbot run` going on V2, then `sxbot overlap`. After August 25 that column is gone; keep the jsonl and grade it against SX outcomes.

After V3, drop mimic. Keep `SX_FOLLOW_STYLE=take` for a Gary-like soccer/tennis steam taker, `join` for a HedgeHog-like MM, and do not mix them.

A ~150k-fill sample (Dec/Jan + Jun + late July, including tennis) is what `sxbot profiles` is for. In that sample: **BotswanaMC** is the only labeled wallet with large **net** P&L, mostly pregame soccer and NFL at pick’em prices (1.80–2.20). **GambleGuruGary** prints millions of gross “Won” and is still **net negative** — the 1.13 hammer is a weapon, not the book. **Tennis lost money** for both takers. **cypherprod** is mixed maker/taker and the only one who is net-positive *live*. **HedgeHog** is the two-sided MM (`join`). Do not clone all four as one bot.

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
