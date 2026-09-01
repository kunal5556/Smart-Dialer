# Agent State Machine

The authoritative encoding lives in `app/state_machines/agent_sm.py` (`AGENT_TRANSITIONS` and
`TRANSITION_ACTORS`). This document mirrors those tables. The exhaustive matrix tests in
`tests/test_agent_state_machine.py` fail if code and this document drift apart.

```mermaid
stateDiagram-v2
    [*] --> OFFLINE

    OFFLINE --> AVAILABLE: login (AGENT)

    AVAILABLE --> RESERVED: atomic reserve (ALLOCATOR)
    AVAILABLE --> PAUSED: break (AGENT)

    RESERVED --> DIALING: provider accepted (ALLOCATOR)
    RESERVED --> AVAILABLE: setup failure / lease expiry (ALLOCATOR, RECOVERY)

    DIALING --> CONNECTED: ANSWERED (EVENT_PROCESSOR)
    DIALING --> AVAILABLE: call failed / originate rejected (ALLOCATOR, EVENT_PROCESSOR, RECOVERY)

    CONNECTED --> WRAP_UP: call completed (EVENT_PROCESSOR)
    CONNECTED --> AVAILABLE: call failed / cancelled (EVENT_PROCESSOR, RECOVERY)

    WRAP_UP --> AVAILABLE: wrap-up timer (WORKER_TIMER, RECOVERY)
    WRAP_UP --> PAUSED: break (AGENT)

    PAUSED --> AVAILABLE: resume (AGENT)

    AVAILABLE --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
    RESERVED --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
    DIALING --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
    CONNECTED --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
    WRAP_UP --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
    PAUSED --> OFFLINE: logout / heartbeat timeout (AGENT, RECOVERY)
```

## State meanings

| State | Meaning | Claimable | Counts as busy |
|---|---|---|---|
| `OFFLINE` | Not logged in, or the heartbeat expired. | no | no |
| `AVAILABLE` | Logged in and idle. The only claimable state. | **yes** | no |
| `RESERVED` | A worker holds an atomic lease; call setup in progress. | no | yes |
| `DIALING` | Call handed to the provider, not yet answered. | no | yes |
| `CONNECTED` | Agent is talking to a borrower. | no | yes |
| `WRAP_UP` | Post-call disposition, time-boxed. | no | yes |
| `PAUSED` | Deliberately unavailable. | no | no |

## Transition actors

`ALLOCATOR`, `EVENT_PROCESSOR`, `RECOVERY`, `AGENT`, `WORKER_TIMER`.

Actor enforcement is what stops one component doing another's job: an `EVENT_PROCESSOR` cannot
reserve an agent, and an `ALLOCATOR` cannot mark a call connected. `validate_transition` raises
`UnauthorizedTransitionActor` for those attempts, separately from `InvalidStateTransition`.

## Explicitly invalid transitions

`AVAILABLE -> CONNECTED`, `RESERVED -> CONNECTED`, `OFFLINE -> RESERVED`, `WRAP_UP -> DIALING`,
`PAUSED -> RESERVED`, and every self-transition. Any transition attempted by a worker that does
not hold the lease is rejected by the reservation layer rather than by this table.

## Validation is not concurrency safety

These functions are pure and answer only "is this transition legal?". They cannot answer "did I
win the race?". A legal transition applied with a plain read-then-write would still allow two
workers to reserve the same agent. Legality must always be paired with the atomic conditional
update in `app/repositories/agent_repo.py`, where the current state is part of the MongoDB filter.
