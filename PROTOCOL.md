# Strategy Protocol v1

Newline-delimited JSON over stdin/stdout between the engine (parent) and a
strategy (child process). Any language that can read stdin and write stdout can
implement a strategy.

`"proto": 1`. The engine refuses to run a strategy whose `proto` it does not
recognise, and says so loudly rather than guessing.

## Ground rules

1. **stdout is protocol only.** One JSON object per line, no pretty-printing, no
   banners, no progress text. Anything a strategy writes to **stderr** is
   captured into the run log and shown in the TUI — that is where debug output
   belongs.
2. **The strategy replies exactly once per engine message.** `{"t":"noop"}` is a
   valid reply.
3. **Bars arrive in batches** (default 500, tunable). Per-bar round-trips do not
   survive multi-million-bar sweeps.
4. **The engine never sends future data.** A strategy that can see ahead is an
   engine bug, not a strategy bug, and the look-ahead tests exist to catch it.
5. **Strategies never compute PnL.** They emit orders. The engine is the single
   source of truth for money.
6. **Failure is loud.** A crash, a timeout, or malformed JSON fails the run and
   reports the step, the exact command, the exit code and the stderr tail. A
   strategy never silently produces zero trades.
7. **Absent bars are absent.** A minute in which nothing traded has no bar. The
   engine does not synthesise one and a strategy must not assume contiguity.

## Discovery — `--describe`

Every strategy supports a `--describe` flag that prints one JSON object and
exits 0. This is what makes the app config-file-free: the TUI reads the schema
and builds the optimisation grid itself.

```json
{"proto":1,"name":"orb_breakout","version":"1.0","params":[
  {"name":"or_minutes","type":"int","default":30,"min":5,"max":120,"step":5},
  {"name":"stop_ticks","type":"int","default":40,"min":10,"max":200,"step":5}
]}
```

`type` is one of `int`, `float`, `bool`, `choice`. A `choice` param carries
`options`; numeric params carry `min`, `max`, `step`.

## Run loop

```
engine -> {"t":"init","proto":1,"params":{...},
           "instrument":{"symbol":"NQ","tick_size":0.25,"tick_value":5.0},
           "session":{"tz":"America/Chicago","rth":["08:30","15:00"]}}
strat  -> {"t":"ready"}

engine -> {"t":"bars","bars":[[ts,o,h,l,c,v], ...],"pos":0,"equity":100000.0}
strat  -> {"t":"orders","orders":[{"action":"buy","type":"stop","qty":1,
            "price":15234.25,"stop_loss":15214.25,"take_profit":15294.25,
            "tag":"orb_long"}]}
engine -> {"t":"fills","fills":[...]}
...
engine -> {"t":"end"}
strat  -> {"t":"bye"}
```

`ts` is an integer Unix epoch in **seconds, UTC**. Timezone presentation is the
engine's problem; the wire is always UTC. `session.tz` is supplied so a strategy
can reason about session-relative time without doing its own conversion.

`action` is `buy` | `sell` | `close` | `cancel`. `type` is `market` | `limit` |
`stop`. `stop_loss` and `take_profit` are optional absolute prices.

**Every order carries `bar`** -- the absolute index of the bar the strategy was
looking at when it decided. Because bars arrive in batches, the engine cannot
otherwise tell which bar inside a batch a decision belongs to, and would have to
assume. The engine will not fill an order earlier than `bar + 1`, and rejects an
order whose `bar` falls outside the batch the strategy was just shown. That rule
is the whole of the look-ahead defence.

A strategy therefore cannot react to a fill in the middle of a batch. That is
what `stop_loss` and `take_profit` are for: brackets are managed by the engine,
so an entry and its protective exits are decided together and no round trip is
needed to exit mid-batch.

## Fill semantics — resolved against the strategy

Intrabar ambiguity is always resolved **against** the strategy. These are engine
guarantees, not strategy responsibilities:

- **Market** orders fill at the **next bar's open**.
- **Stop** orders fill at the stop price plus slippage. If the bar gapped
  through the stop, the fill is at the **bar open**, not the stop price.
- **Limit** orders fill only if price trades **at least one tick beyond** the
  limit.
- When a single bar could have hit both the stop-loss and the take-profit, the
  **stop-loss is taken**.
- Slippage is applied against the strategy on every side, default 1 tick.

## Conformance

The reference opening-range-breakout strategy ships in three implementations:
vectorised in-process Python, Python over this protocol, and a Java jar. All
three must produce **identical trade logs** on the same input slice.

The protocol itself is language-agnostic -- nothing in it is Python or Java --
so a fourth implementation joins by satisfying the same test.

"Identical" means identical after canonical serialisation with fixed decimal
quantisation — prices to the instrument tick, quantities as integers, timestamps
as integer epoch seconds. Raw IEEE-754 byte equality across separate language
runtimes is not achievable and is not the contract. The canonical trade log is.

The in-process fast path for Python strategies must produce a trade log
identical to the same strategy run over the subprocess protocol. That
equivalence is a test, not an aspiration.

## Bracket timing

A bracket becomes live on the bar AFTER entry, not on the entry bar itself.

On the entry bar only O/H/L/C are known, not the path after the fill. Testing
the bracket against that bar's full range would assume the whole range was
still available once filled, which is an assumption rather than a measurement.

Deferring is conservative in both directions -- a stop that fires a bar late
costs more, and a target that fires a bar late earns less -- so it does not
quietly flatter a strategy. It is a setting (`brackets_live_next_bar`) for
anyone who wants the other convention, and it is recorded in run metadata.
