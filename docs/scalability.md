# Scalability Analysis

Every number below was measured with `python -m loadtest.run_all` against a local MongoDB 8.3
standalone, Motor connection pool size 100, on a single developer machine. Raw output is written
to `loadtest_results/*.json`.

Reproduce with:

```bash
python -m loadtest.run_all --scales 100 1000 10000
```

## Measured results

| Measurement | Scale | Ops | ops/sec | p50 ms | p95 ms | p99 ms |
|---|---:|---:|---:|---:|---:|---:|
| agent_reservation | 100 | 200 | 3194.8 | 28.7 | 54.3 | 58.0 |
| borrower_reservation | 100 | 200 | 3587.7 | 22.0 | 34.7 | 35.6 |
| call_creation | 100 | 100 | 4216.9 | 18.0 | 20.5 | 21.6 |
| event_processing | 100 | 110 | 903.4 | 115.5 | 119.8 | 120.3 |
| **dialer_tick** | 100 | 5 | 15.9 | 10.8 | 272.6 | 272.6 |
| agent_reservation | 1 000 | 2 000 | 4052.8 | 273.5 | 426.6 | 431.4 |
| borrower_reservation | 1 000 | 2 000 | 4210.5 | 270.6 | 425.3 | 426.2 |
| call_creation | 1 000 | 1 000 | 4374.1 | 124.1 | 185.1 | 186.7 |
| event_processing | 1 000 | 1 100 | 702.0 | 1424.2 | 1470.3 | 1473.4 |
| **dialer_tick** | 1 000 | 5 | 2.4 | 410.6 | 437.5 | 437.5 |
| agent_reservation | 10 000 | 20 000 | 3918.7 | 2623.2 | 4053.2 | 4189.4 |
| borrower_reservation | 10 000 | 20 000 | 2646.8 | 2969.8 | 5749.7 | 6003.0 |
| call_creation | 10 000 | 10 000 | 1764.0 | 2530.0 | 4408.9 | 4521.9 |
| event_processing | 10 000 | 11 000 | 266.5 | 38528.0 | 39578.7 | 39679.5 |
| **dialer_tick** | 10 000 | 5 | 0.7 | 1395.8 | 1901.2 | 1901.2 |

The concurrency guarantee held at **every** scale: across 44,000 concurrent reservation attempts,
the load test asserted zero double-claims. That assertion is inside
`loadtest/test_reservation_throughput.py`, so a regression fails the run rather than going unnoticed.

## What breaks first

**The dialer tick exceeds its budget somewhere between 1,000 and 10,000 agents.**

`DIALER_TICK_SECONDS` is 1.0 s. Measured p95 tick duration:

- 100 agents — 273 ms (27 % of budget)
- 1 000 agents — 438 ms (44 % of budget)
- 10 000 agents — **1 901 ms (190 % of budget — the loop can no longer keep up)**

Once a tick takes longer than the interval, ticks stop being periodic: the dialer falls behind,
the pacing snapshot ages, and the Safety Controller's staleness guard (`MAX_SNAPSHOT_AGE_SECONDS`)
starts rejecting requests. The system stays *safe* — it dials less — but it stops being useful.
That is the correct failure direction, and it is why the staleness guard exists.

Throughput tells the same story from the other side: reservations hold ~4 000 ops/sec at every
scale, but p95 latency rises 28 ms → 273 ms → 2 623 ms. The database is not running out of
capacity; work is queueing behind a single serialized loop.

## Bottleneck table

| # | Breaks at | Bottleneck | Why it happens | How to detect it | How to fix it | Trade-off the fix introduces |
|---|---|---|---|---|---|---|
| 1 | **~1 000 → 10 000 agents (measured)** | Centralized pacing tick | One loop builds a whole-campaign snapshot (agent counts, call counts, two aggregations for answer rate, two averages) then allocates serially. Cost grows with the agent and call population, and every campaign shares one loop. | Tick duration p95 approaching `DIALER_TICK_SECONDS`; rising `snapshot_age_ms` on safety decisions; `REJECTED` verdicts with `stale_state` binding. | Run one pacing loop per campaign; maintain incrementally-updated counter documents instead of re-aggregating each tick; allocate concurrently within a tick. | The counters document becomes its own hot spot needing `$inc` plus periodic reconciliation, and per-campaign loops multiply connection usage. |
| 2 | ~1 000 agents | Candidate-set contention on claims | Every worker queries the same "oldest AVAILABLE agents" window and races for the same documents. The 3× candidate window (`CANDIDATE_WINDOW_MULTIPLIER`) softens but does not remove this. | Contention ratio in the reservation load test; `reservation_contention` counter climbing faster than allocations. | Partition agents across workers by `hash(agent_id) % worker_count`, or randomize the candidate window. | Uneven distribution: one worker's shard can starve while another is saturated. |
| 3 | ~2 000+ agents | MongoDB connection pool | Every worker, API request and sweep needs a connection. Pool size was 100 for these runs; Atlas M0 caps connections hard. | Driver pool wait time; latency rising while server-side op time stays flat. | Raise pool size; batch per-tick reads instead of per-agent reads; separate read and write pools. | More memory, and batching adds snapshot staleness the Safety Controller must account for. |
| 4 | **~10 000 agents (measured)** | Single event-processing path | Events arrive proportional to call volume and are processed one coroutine at a time against one database. p95 went 120 ms → 1.5 s → **39.6 s**. | Event processing lag; growing gap between `received_at` and `processing_status` being set. | Partition event handling by `hash(provider_call_id)` across workers — per-call ordering is preserved because all events for one call land on one partition. | Rebalancing complexity; ordering guaranteed only within a partition. |
| 5 | ~10 000 agents | Recovery sweep cost | Five sweeps scan collections every 5 s. Unindexed or unbounded sweeps grow linearly with population. | Sweep duration metric; recovery falling behind lease TTL. | Indexes on `lease_expires_at` and `state_changed_at` (already present); bound each sweep with `limit` (already done via `RECOVERY_SWEEP_LIMIT`); lengthen the interval. | Slower recovery — reservations stay stuck longer, so capacity is temporarily understated. That is the safe direction. |
| 6 | Any scale, provider-dependent | Provider rate limits | Real carriers cap concurrent originations; mocks do not. | Provider 429s / rising rejection rate in `provider_health`. | A per-provider token-bucket limiter added as a ninth Safety Controller constraint. | Lower peak throughput and more state to maintain. |
| 7 | ~10 000 agents | Metrics and decision write volume | Every tick persists a pacing decision and a safety decision; the sampler writes a metrics document every 5 s. | Write IOPS; `pacing_decisions` collection growth. | Sample decision persistence (keep all rejections, sample approvals); TTL indexes (already on `metrics_samples`). | Reduced forensic detail — but 100 % of *safety* decisions must be kept, since they are the audit trail. |

## What this is explicitly not

"Add more servers" does not fix bottleneck #1 or #2. Bottleneck #1 is a single serialized loop —
more servers running the same loop duplicates work rather than dividing it. Bottleneck #2 is
*contention*: more workers racing for the same candidate agents makes the contention ratio worse,
not better. Both need the work partitioned before more machines help.

## Honest limitations of these numbers

- Measured against a local standalone MongoDB, not a replica set, so no replication lag is included.
- One machine, so client and server compete for CPU; absolute ops/sec would differ on separate hosts.
- Atlas M0 (the free tier used for deployment) has a much lower connection cap and shared CPU, so
  the 10 000-agent run would degrade earlier there. The shape of the curve, not the absolute
  numbers, is the transferable result.
- Load tests call the service layer directly. Testing through HTTP would measure the web stack
  rather than the operations that actually matter here.

## Deliberately not done

No performance optimization was attempted in this phase. Fixing bottleneck #1 (per-campaign pacing
loops and incremental counters) is real work with real trade-offs and belongs in the future-work
list, not in a rushed change made after seeing one benchmark.
