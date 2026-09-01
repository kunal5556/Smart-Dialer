# Interview Preparation

Short answers to the ten questions the assignment says the project must be able to answer, each
with the file that backs it up.

---

## 1. Concurrency — two workers try to reserve the same agent at exactly the same time. What happens?

Exactly one wins, and it is the database that decides.

Both call `try_reserve_agent`, which is a single `find_one_and_update` whose **filter contains the
expected state**:

```python
filter={"_id": agent_id, "state": "AVAILABLE"}
update={"$set": {"state": "RESERVED", "reserved_by": worker_id, ...}, "$inc": {"state_version": 1}}
```

MongoDB guarantees single-document updates are atomic, so there is no window between the check and
the write. The loser gets `None` back — a normal outcome, not an error. It records a contention
counter and moves to the next candidate; it never retries the same document, because that is how
contention storms start.

**Evidence:** `tests/test_agent_reservation_concurrency.py` — 20 concurrent workers, exactly 1 wins,
`state_version` incremented exactly once. Also verified under load: 44,000 concurrent attempts,
zero double-claims (`loadtest/test_reservation_throughput.py`).

**Code:** `app/repositories/agent_repo.py`

---

## 2. Source of truth — the database says AVAILABLE but the cache says RESERVED. Which wins?

There is no cache, deliberately, so the conflict cannot arise.

Every safety-relevant decision reads MongoDB directly. In-memory state exists only for provider mock
internals, rolling health windows and observability counters — none of which is authoritative, and
the Safety Controller never trusts any of it.

If a cache were ever added, the database would win unconditionally, because only the database can
enforce an atomic conditional claim. But the better answer is that this design removes the question
rather than answering it.

**Code:** `app/db.py`, `app/safety/safety_controller.py` (`_read_capacity`)

---

## 3. Provider events — the provider sends ANSWERED, the worker crashes, then COMPLETED arrives. What happens?

`ANSWERED` was processed normally: the call moved to `ANSWERED`, the agent to `CONNECTED`.

The worker dying changes nothing about that, because the state lives in MongoDB, not in the worker.
When `COMPLETED` arrives — possibly at a different worker — the Event Processor applies it: the call
becomes `COMPLETED`, the agent moves to `WRAP_UP`, and the borrower is released as `CONTACTED`.

Meanwhile the crashed worker's *lease* expires, and the recovery worker's stuck-wrap-up sweep
eventually returns the agent to `AVAILABLE`.

**Evidence:** `tests/test_worker_crash_recovery.py::test_crash_after_answered_still_processes_the_later_completed_event`

**Code:** `app/services/event_processor.py`, `app/workers/recovery_worker.py`

---

## 4. Prediction failure — the system predicted a 70 % answer rate and it drops to 10 %. What protects the system?

Four independent things, in order:

1. **The clamp.** `effective_answer_rate` is clamped to `[0.05, 0.95]`, so a collapsing rate cannot
   make `calls_needed` explode toward infinity.
2. **The blend.** 70 % recent / 30 % baseline means a handful of unlucky calls cannot swing pacing
   wildly.
3. **The volatility factor.** A sharp move in the answer rate multiplies the request by 0.6.
4. **The Safety Controller.** Even if all of the above failed and the engine asked for 500, the
   controller caps at `AVAILABLE − RESERVED` read fresh from the database.

And if the pacing engine throws an exception outright, it returns `requested = 0`. A broken
optimizer degrades to *no extra calls*, never unbounded ones.

**Evidence:** `tests/test_pacing_engine.py` (clamps, volatility, error path),
`tests/test_safety_controller.py::test_controller_ignores_inflated_request_inputs`

---

## 5. Pacing — why did the algorithm decide to initiate 17 calls instead of 10?

Because a stored document says so, in words:

> 12 agents free + 3 soon-free (weighted 1.5) = 13.5 capacity; at 32 % estimated answer rate that
> needs 42 calls; 21 already in flight leaves 21; ×0.85 safety margin ×1 health ×1 volatility =
> **17 requested**.

Every pacing tick writes a `pacing_decisions` document containing every input, every intermediate
value, and that generated sentence. The dashboard's Pacing panel renders it directly.

The function is pure, so the same inputs always give the same answer — which is what makes the
worked example above an actual assertion in the test suite.

**Evidence:** `tests/test_pacing_engine.py::test_worked_example_from_the_roadmap_requests_seventeen`,
`tests/test_pacing_explainability.py`

**Code:** `app/pacing/pacing_engine.py`

---

## 6. Safety — can the predictive engine bypass the Safety Controller?

**No. The architecture prevents it**, in three independent ways:

1. **Module level.** `app/pacing/` imports no provider module and no allocator module. It has no
   way to reach a telephone.
2. **Signature level.** `CallAllocator.allocate` takes a `SafetyDecision` and nothing else. There is
   no overload accepting a plain integer, so there is no way to allocate without a decision object.
3. **Behavioural level.** The controller recomputes capacity from the database and ignores the
   numbers the request carries.

All three are asserted by tests, so a future shortcut fails CI rather than shipping.

**Evidence:** `tests/test_architecture_boundaries.py`,
`tests/test_acceptance_criteria.py::test_safety_the_predictive_engine_cannot_bypass_the_safety_controller`

---

## 7. Scaling — what breaks first at 1,000 → 10,000 agents?

**The dialer tick exceeds its budget.** Measured p95 tick duration: 273 ms at 100 agents, 438 ms at
1,000, and **1,901 ms at 10,000** against a 1,000 ms interval.

Once a tick takes longer than the interval, ticks stop being periodic: the loop falls behind, the
pacing snapshot ages, and the Safety Controller's staleness guard begins rejecting requests. The
system stays *safe* — it dials less — but stops being useful. That is the correct failure direction.

Throughput tells the same story: reservations hold ~4,000 ops/sec at every scale while p95 latency
goes 28 ms → 273 ms → 2,623 ms. The database is not saturated; work is queueing behind one
serialized loop.

The fix is per-campaign pacing loops plus incrementally maintained counter documents instead of
re-aggregating every tick — which introduces a hot counter document needing reconciliation. Adding
servers does **not** help: bottleneck 1 is a serialized loop, and bottleneck 2 is contention, which
more workers make worse.

**Evidence:** `docs/scalability.md`, `loadtest/run_all.py`

---

## 8. Architecture — why did you choose this architecture?

Because the assignment's real question is about a *boundary*, not about dialing. So the design is
organised around making that boundary impossible to cross:

- Prediction is a **pure function** producing a number. It has no I/O and no dependencies that could
  reach a phone line.
- Safety is a **separate module** that re-reads the database and returns a decision object.
- Allocation **only accepts that decision object**.

Everything else follows from wanting that to be true and testable. MongoDB was chosen because its
single-document atomicity gives the concurrency primitive with no extra infrastructure. Leases were
chosen over locks because a lease held by a dead process expires. Streamlit was chosen so the whole
project is one language I can defend line by line.

**Evidence:** `docs/adr.md`

---

## 9. Trade-offs — what does your architecture make harder?

- **No cross-document atomicity.** Agent + borrower reservation is compensate-and-lease, so there is
  a real window where a crash leaves an agent reserved. Bounded by the TTL, cleaned by recovery.
- **The pacing snapshot is approximate.** Several aggregations that are not mutually consistent.
  This is only acceptable because the Safety Controller re-reads authoritative counts — optimizer
  may be approximate, guard must be exact.
- **Streamlit would not scale to a real agent-facing UI.** It suits an operations dashboard.
- **Single-instance deployment**, so multi-worker guarantees are proven by tests and simulation
  rather than production traffic.
- **In-process counters undercount** across instances.
- **No message broker** means event processing is one serialized path — the measured bottleneck at
  10,000 agents.

---

## 10. Future work — what would you change with another week?

**First, the honest finding.** Measured over 900 simulated seconds at a 20 % answer rate,
progressive and predictive placed *exactly the same* 41 calls. Predictive genuinely asked for more —
every tick produced a `REDUCED` verdict — but in this architecture every call reserves an agent
before dialing, and safety constraint 1 caps approvals at `AVAILABLE − RESERVED`. The optimizer
works, the boundary works, and the boundary wins.

So the first change is the one that would actually deliver the utilization win: **let the dialer
claim agents in `WRAP_UP` or nearly finished on a call**, so call setup overlaps wrap-up and the
call is already ringing when the agent frees up. That is a change to the claimable-state set in the
agent state machine, not a tuning tweak, and it keeps the 1-agent-per-call guarantee intact.

Then, in order:

2. Per-campaign pacing loops and incremental counters, to fix the measured first bottleneck.
3. Partitioned agent claiming by `hash(agent_id) % worker_count` to cut contention.
4. Partitioned event processing by `hash(provider_call_id)`, preserving per-call ordering.
5. A real provider adapter behind the existing `TelecomProvider` interface.
6. Abandoned-call-rate tracking as a ninth safety constraint — the regulatory one.
7. Answer-rate modelling by time of day, replacing the single rolling window.
