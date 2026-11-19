# DCM-PERF-5: Isolated browser relay inspection latency

## A. Envelope

Tier 3. As part of the operator-approved INNO-FIX-61 package, reduce repeated
Docker CLI overhead in the isolated local browser relay so the existing
functional acceptance tests can run without relaxed timeouts. Preserve every
per-request ownership, runtime, mount, network, path, size and no-secret-logging
check; continue to fail closed. Do not expose ports or change authentication,
isolation, other consumers, or ordinary application traffic.

Run focused relay, consumer, synthetic-configuration, launcher, bridge and
isolation tests, including malformed inspection and fresh per-request evidence.
Verify the existing INNO-FIX-61 isolated F-1 and F-2 against the changed relay.

## B. Implementation map

Target `src/django_core_micha/test_management/local_proxy.py` and the directly
affected `tests/test_test_management_*` files. The relay calls
`validate_runtime` for each request; its inspection callback may query only
the existing non-secret runtime fields. No source outside this seam belongs to
this work order. Preserve concurrent foreign work in this repository.

## C. Orchestrator only

STOP — this section is not addressed to an implementer. The configured Luna
worker was stopped after over five minutes without output and no file changes;
the parent used the documented fallback. Independent Tier 3 security and code
reviews, affected tests and coordinated landing remain required. The user
explicitly approved this targeted Shared-Core extension on 2026-11-18.

## Completion

The per-request snapshot reads only named non-secret Docker fields and still
passes fresh evidence to the existing runtime validator. The affected relay,
consumer, synthetic, isolation, launcher and bridge suites passed 100 tests;
INNO-FIX-61 F-1 and F-2 passed against the local relay fix. The first review's
freshness, mapping, malformed-data and fail-closed test gaps were addressed.
The closing review confirmed no security finding and raised additional
non-blocking coverage and maintenance observations. Keyword invocation, partial
output, foreign mounts, inspection errors and call counts were additionally
checked; not every individual snapshot field has a separate mutation test.
The initial regression and duplication outputs were incomplete tool requests;
the closing reviewers supplied substantive results. No third review was run.
