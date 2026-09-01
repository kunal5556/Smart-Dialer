# Architecture Decision Record

Each entry: the decision, why, and honestly what it costs.

---

## 1. MongoDB atomic conditional updates instead of transactions, Redis, or a lock service

**Decision.** Agent and borrower reservation use a single-document `find_one_and_update` whose
filter contains the expected state.

**Why.** MongoDB guarantees single-document updates are atomic, so the read of `state: AVAILABLE`
and the write of `state: RESERVED` are one indivisible operation. It needs no extra infrastructure,
no lock service, and no multi-document transaction. It maps exactly to the problem: "claim this
document if and only if it is still free." A college student can explain it in one sentence, which
matters more here than sophistication.

**Rejected alternatives.** Multi-document transactions work on a replica set but add contention and
complexity for a guarantee we can get per-document. A Redis lock introduces a second source of truth
and a whole new failure mode (lock held, process dead). A read-then-write with an application-level
check is the actual bug the assignment tests for.

**Cost.** No cross-document atomicity — see ADR 3.

---

## 2. Leases, not locks

**Decision.** Every reservation carries `reserved_by`, `reserved_at` and `lease_expires_at`. There
is no lock that must be explicitly released.

**Why.** A lock held by a dead process is held forever. A lease held by a dead process expires. This
single property is what makes crash recovery possible at all: the recovery worker only has to find
things whose lease has passed. Release is *also* implemented as a fast path, but correctness never
depends on it running.

**Cost.** A crashed worker's agent stays unavailable until the TTL expires (default 30 s). That is
lost capacity, and it is the safe direction.

---

## 3. Claim-then-compensate instead of a multi-document transaction

**Decision.** Claim the agent, then the borrower; on any failure, release what was already claimed.

**Why.** The pair is two documents so it cannot be one atomic update. A transaction would work but
adds contention and a harder failure story. Ordered claiming with explicit compensation is simple to
read, and the lease is a second, independent safety net.

**Cost.** There is a real window between claiming the agent and claiming the borrower where a crash
leaves the agent reserved. It is bounded by the TTL and cleaned up by recovery. This is stated in
the README rather than hidden.

---

## 4. The Safety Controller re-reads the database

**Decision.** The controller never trusts numbers carried in the `PacingRequest`; it recomputes
capacity itself. The request's numbers are recorded only for comparison.

**Why.** This is the entire safety argument. If the controller trusted the optimizer's view, a bug
or a malicious input in the optimizer would become a safety failure. There is a test that hands the
controller a request claiming 500 free agents when the database has 3, and asserts `approved == 3`.

**A subtlety worth defending in an interview.** Approval is an *upper bound*, not a guarantee.
Between the controller reading capacity and the allocator reserving agents, another worker may take
some. That is fine — the atomic reservation layer enforces the real limit. This is exactly why the
safety guarantee does not depend on the snapshot being perfectly fresh, which is what makes the
design robust rather than fragile.

**Cost.** Extra database reads per tick, which is part of the measured first bottleneck.

---

## 5. Building the Safety Controller *before* the Pacing Engine

**Decision.** Phase ordering put the guard first and the optimizer second.

**Why.** Build the optimizer first and the guard becomes something you add *around* it — which is
how guards end up optional. Building the boundary first meant prediction was born inside a system it
structurally cannot escape.

**Cost.** None. This is the decision I would defend hardest.

---

## 6. No cache, deliberately

**Decision.** There is no caching layer for agent, borrower or call state.

**Why.** The assignment asks what happens when "the database says AVAILABLE but the cache says
RESERVED". Rather than document a conflict-resolution rule, this design removes the conflict: the
database is read directly for every safety-relevant decision. Rolling metrics buffers exist in
memory, but they are *inputs to an optimizer*, and the Safety Controller never trusts them.

**Cost.** More reads. Acceptable, and the measurements say the tick loop breaks before read volume
does.

---

## 7. No message broker

**Decision.** No Kafka, no RabbitMQ, no Celery.

**Why.** There is one producer and a bounded worker set. The "exactly one worker gets this" semantic
is already provided by the atomic claim. A broker would be a second system to deploy, explain and
debug for a guarantee we already have.

**Cost.** Event processing is a single serialized path, which is the measured bottleneck at 10,000
agents. The fix is partitioning by `hash(provider_call_id)`, not adding a broker.

---

## 8. Streamlit instead of a JavaScript SPA

**Decision.** The dashboard is a Streamlit app in the same language as the backend.

**Why.**
- One language for the whole project, so every line is defensible in a technical discussion.
- No Node toolchain, bundler or npm dependency tree inside a 4–6 hour prototype budget.
- The API key lives in **server-side** secrets and never reaches a browser, which means mutating
  endpoints can be genuinely key-protected. With an SPA, any key in the bundle is public, so control
  endpoints would have had to stay unauthenticated.
- Streamlit ships `AppTest`, so the UI has real automated tests rather than "manually verified".
- The dashboard calls the API server-to-server, so **CORS is not involved at all** — one entire
  class of deployment bug disappears.

**Cost.**
- The whole script re-runs on every interaction, so the API client must be cached and calls cheap.
- Coarser layout control than real front-end code.
- ~2 s refresh granularity rather than a live stream.
- It would not scale to a real agent-facing softphone UI. This is an operations dashboard.
- It is Python, so it *could* import the backend — which is why ADR 9 exists.

---

## 9. The dashboard is forbidden from importing `app/`, and that is enforced mechanically

**Decision.** `dashboard/` may not import `app.*`, `motor`, `pymongo` or `fastapi`. A test walks
every dashboard module's AST and fails if it does.

**Why.** With an SPA the language barrier enforced this for free. With Streamlit, one convenient
`from app.services.call_allocator import allocate` would let the UI dial without passing the Safety
Controller and make it a second source of truth. Convention is not enough for something this
important, so it is a test — and in deployment the dashboard's environment has no `MONGODB_URI` and
its requirements file installs no driver, making the boundary a deployment fact too.

**Cost.** State names are duplicated as display strings in `dashboard/formatting.py`. That looks
like a DRY violation and is a deliberate trade: a little duplication in exchange for a boundary that
cannot rot.

---

## 10. Fragment auto-refresh instead of push

**Decision.** Live panels use `@st.fragment(run_every="2s")`.

**Why.** The dashboard is a periodic snapshot view. Fragment-scoped refresh re-runs only the live
panels, so controls stay responsive and a whole-script rerun does not reset widget state mid-use. No
socket lifecycle code, and it survives free-tier hosts that sleep.

**Cost.** Not truly real-time; a burst faster than 2 s is visible in the data but not animated.

---

## 11. The pacing formula: simple statistics, no model

**Decision.** `calls_needed = free_capacity / effective_answer_rate`, with a blended and clamped
answer rate and three multiplicative factors.

**Why.** It has to be explainable live. Every intermediate value is captured and a human-readable
sentence is generated and stored, so "why 17 and not 10?" is answered by fetching one document. It
is a pure function, so it is trivially testable and deterministic. And progressive dialing turns out
to be the same formula with the answer rate forced to 1.0 — a genuinely satisfying thing to be able
to say.

**Cost.** It will underperform a well-tuned production pacer under unusual call-length
distributions.

---

## 12. The failure-rate guard counts system faults, not no-answers

**Decision.** Safety constraint 8 counts calls that failed for provider reasons, excluding
`no_answer` and `busy`.

**Why.** This was a real bug found by running a simulation. At a 20 % answer rate, 80 % of calls end
`FAILED` with reason `no_answer` — a completely normal telephony outcome. Counting those meant the
failure-rate guard tripped permanently and forced predictive mode into progressive fallback on every
single tick, silently disabling the entire predictive feature. A no-answer is an outcome; a carrier
rejection or timeout is a fault.

**Cost.** The guard now depends on providers setting an honest failure reason.

---

## 13. Reporting that predictive does not beat progressive here

**Decision.** The simulation comparison test asserts that predictive *requests* more and that safety
invariants hold in both modes — not that predictive wins on utilization.

**Why.** Measured over 900 simulated seconds at a 20 % answer rate, both modes placed exactly 41
calls. In this architecture every call reserves an agent before dialing, and safety constraint 1
caps approvals at `AVAILABLE − RESERVED`, so predictive's extra requests cannot be realised. The
optimizer works (12 `REDUCED` verdicts prove it), the boundary works, and the boundary wins.

Writing a test that asserts a win the architecture cannot deliver would have been dishonest. The
real fix — letting the dialer claim `WRAP_UP` and nearly-finished agents so setup overlaps wrap-up —
is a genuine state-machine change and is the first item in future work.

**Cost.** The project does not currently demonstrate the utilization win it set out to. It
demonstrates precisely why, with measurements, which is the more useful engineering result.

---

## What this architecture makes harder

Worth saying out loud:

- **No cross-document atomicity.** Agent + borrower is compensate-and-lease, not a transaction.
- **The pacing snapshot is approximate by design.** Several aggregations that are not mutually
  consistent. This is acceptable *only* because the Safety Controller re-reads authoritative counts —
  optimizer may be approximate, guard must be exact.
- **A single-instance deployment.** Multi-worker guarantees are proven by tests and simulation, not
  production traffic.
- **An extra network hop** from dashboard to API compared with a browser calling the API directly.
- **In-process counters undercount** across multiple instances; the persisted metrics samples are
  the cross-process source.
