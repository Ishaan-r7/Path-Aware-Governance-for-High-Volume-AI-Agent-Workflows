# Work Log

A change-level record of work on this repo: what was added, changed or removed, and why.
Newest entries go at the top.

## 2026-10-27: Engine fixes from the step 3 findings

The team decided on the three findings from step 3. This entry carries out those decisions.
Branch `ai-testing`.

### Decisions made

1. **Attempts vs executed actions.** Path-aware governance remembers every attempted action, including blocked ones. When it checks cumulative limits and evasion, it counts only actions that actually happened.
2. **The "harmless read first" gap is fixed now,** not deferred to Phase C.
3. **The two-PII-source merge stays a known limit.** It must be clearly documented for experimental scenarios.

### Changed

- **`engines.py`, path-aware engine: tracks a status for every attempt**
  - **The statuses:** `blocked`, `allowed`, `executed` or `failed`. The history itself is unchanged and still holds every attempt.
  - **New methods:**
    - `record_outcome(event_id, executed)` reports whether an allowed action really happened.
    - `attempts(workflow_id)` lists each event with its status, for audit.
    - `observe` takes an optional `executed` flag.
  - **Why:** decision 1. The engine needs to know the difference between "tried" and "happened".

- **`engines.py`: the split-transfer rule counts only actions that happened**
  - The total includes earlier transfers that executed (or were allowed with no outcome reported), plus the current one. Blocked and failed transfers are left out.
  - **Fixes:** finding 3. A blocked 1500 attempt no longer causes a later 100 transfer from another agent to be blocked.
  - **Kept unchanged:** an allowed action with no reported outcome still counts as executed. The benchmark and Kafka code never report outcomes, so this keeps their results exactly as before.

- **`engines.py`: the exfiltration rule now searches backwards**
  - It starts from the send-outside step and looks back for a matching export, a merge and a PII read, all carrying the same `data_flow_id`. The policy ids and messages are unchanged.
  - **Fixes:** finding 1. The old matcher took the *first* read in the run, so reading a public source first hid the PII read.

- **`engines.py`: `record_outcome` on every engine**
  - The no-governance and stateless engines get an empty `record_outcome`, and it is part of the engine interface.
  - **Why:** the gateway can then report outcomes to any engine without special cases.

- **`harness/gateway.py`:** after an allowed tool runs, the gateway calls `record_outcome` with whether the tool succeeded.
  - **Why:** the gateway is the only piece that knows whether an allowed action really happened. For example, an overdraft makes the transfer fail even though the engine allowed it.

- **`docs/architecture.md`:** the "Agent harness" section now describes attempt statuses and how cumulative rules count.

- **`docs/experiment-plan.md`: new "Known limitations for agent scenarios" section**
  - **Two PII sources merged** (decision 3): scenarios that do this must be labeled as covering the known gap, and their results reported separately.
  - **One agent splitting a transfer:** a single agent sending several under-limit transfers to the same destination is not caught by any engine. This is a new finding, listed as an open question.
  - **Cumulative limits** count executed actions only.

- **Tests:**
  - **The "expected to fail" mark** was removed from the docs-first test, because it now passes.
  - **4 new engine tests:**
    - A blocked transfer followed by a small one is allowed.
    - 600 + 500 by two agents is still blocked.
    - A failed transfer does not count.
    - A docs-first leak is blocked.
  - **2 new gateway tests:** the finding-3 scenario end to end, and an overdraft that does not count.

### Removed

- **`_ordered_subsequence` in `engines.py`.** It was only used by the old exfiltration matcher, which caused finding 1.

### Not changed, on purpose

- **`consistency.py`, `distributed.py` and the benchmark code are untouched.** The tests on the generated corpus and the sample workflows pass unchanged, and the demo gives the same verdicts as before.
- **Weak-consistency replicas do not share attempt statuses yet.** An event learned from another replica counts as allowed, which is the same as the old behavior. This matters only if the consistency experiment is rerun with blocked transfers in it.

### Still open

- **Single-agent split transfers.** Do we want a rule for one agent splitting a payment into several under-limit transfers?

### Review

- **Who did what:** I wrote the spec and reviewed the code. A Sonnet subagent wrote the code.
- **Tests:** 58 tests, all pass, with no expected failures.

## 2026-10-26: Agent harness, step 3 (gateway) and the rest of A2 (provenance)

Plan items A1 (harness, step 3) and A2 (provenance and taint tracking). Goal: every tool call
an agent proposes is checked by an engine before it can touch the fake world.
Branch `ai-testing`.

### Added

- **`harness/gateway.py`: the gateway**
  - **What it does:** for each proposed tool call it checks the arguments, builds an `ActionEvent`, asks the engine, and then runs the tool or returns a refusal.
  - **The refusal:** the agent gets a message naming the policy and the reason, for example "Blocked by governance policy P-SPLIT-ACTION-EVASION: ...".
  - **Why:** this is the piece that turns the existing engines into a guard for a live agent.

- **Event fields come from the world, never from the agent**
  - `authorized` comes from the permission table, and classification comes from the source or the artifact's lineage.
  - `destination` is set to `external` or `internal` from the world's list of internal hosts. The raw host is kept in the attributes.
  - **Why:** an agent could otherwise leave fields out or lie, and the rules would quietly stop working.

- **Delegation `authorized` means "could the requester do this action itself"**
  - It is checked against the *requested* action, not against `request_delegation`.
  - **Why:** that is what the privilege-laundering rule and the existing sample workflows mean by it.

- **`data_flow_id` from lineage (finishes A2)**
  - The id is the source (or sources) with the highest sensitivity in the lineage. So customers merged with public docs is still `customers`, and the chain matches the original read.
  - **Why:** without an id, the exfiltration rule never fires. Real agents will not supply one, and using "all sources" as the id would let an agent break the chain just by mixing in harmless data.

- **`attach_provenance` switch on the gateway**
  - Turning it off sends no `data_flow_id`.
  - **Why:** a test uses it to show the PII leak goes through without provenance (A2's "missing provenance" test).

- **Reproducible event ids and timestamps**
  - Event ids and timestamps come from the sequence number, not the clock.
  - **Why:** the same run gives an identical log, which repeated runs need (A6).

- **`tests/test_harness_gateway.py`: 16 tests**
  - **What they cover:** every engine rule triggered through the gateway (exfiltration, split transfers, privilege laundering, transaction limit, unauthorized actor), near-misses that should be allowed, invalid calls, blocked actions leaving the world untouched, and determinism.

- **`docs/architecture.md`: new "Agent harness" section**
  - It records the blocked-action decision and the `data_flow_id` rule with its known limit.
  - **Why:** the earlier plan promised the decision would be written down there.

### Changed

- **`harness/tools.py`:** the argument checks moved out of `call_tool` into `validate_call`, and `call_tool` behaves exactly as before.
  - **Why:** the gateway has to check a call *before* it asks the engine, without running anything.
- **`harness/__init__.py`:** now also exports `Gateway`, `GatewayResult`, `flow_id_for` and `validate_call`.

### Removed

- Nothing.

### Findings (not fixed; engine code untouched)

1. **Reading a harmless source first hides the leak.**
   - **The case:** an agent reads `docs`, then `customers`, then merges, exports and sends outside. The path-aware engine misses it.
   - **The cause:** its sequence matcher takes the *first* `read_data` (`_ordered_subsequence` in `engines.py`), which is the public one.
   - **How it is kept visible:** a test asserts the chain *should* be blocked and is marked "expected failure".
   - **Fix belongs to:** Phase C (engine rules).
2. **Merging two different PII sources breaks the chain.**
   - **The case:** `customers` plus `hr` gets the id `customers+hr`, which does not match the earlier single read.
   - **Status:** a known limit of the id rule. A test pins the current behavior.
3. **Blocked attempts count toward later totals.**
   - **The case:** agent A tries to transfer 1500 and is blocked, so no money moves. Agent B then sends 100 to the same vendor and is also blocked as a "split transfer", because the engine adds up attempts, not money that actually moved.
   - **Status:** this follows from our decision that the engine remembers blocked attempts. It may be a false positive or a sensible cautious choice. That is a team decision, not fixed here.

### Review

- **Who did what:** I wrote the spec and reviewed the code. A Sonnet subagent wrote the code.
- **Verification:** I reran the full suite and probed finding 3 myself to confirm it.
- **Tests:** 52 tests, all pass, with 1 expected failure (finding 1).

### Next

- **Step 4:** a scripted fake agent that runs whole scenarios through the gateway end to end.
- **Team decision needed:** finding 3.

## 2026-10-26: Agent harness, step 1 (fake world and tools)

Part of plan item A1 (agent harness). Goal: give a future LLM agent a pretend environment
to act in, so we can see what governance stops and what harm happens without it.
Branch `ai-testing`. Not committed yet.

### Added

- **`pathaware_governance/harness/` package**
  - **Why:** keeps all agent-testing code apart from the existing engines and benchmark.
  - Nothing existing had to change to make room for it.

- **`harness/world.py`: the fake world**
  - **What it holds:**
    - Three data sources: `customers` (PII), `sales` (internal) and `docs` (public).
    - One bank account with 5000.
    - Two internal destinations. Anything else counts as external.
    - A permission table for five agents: analyst, exporter, finance, intern and manager.
    - Records of everything that happened: transmissions, transfers, delegation requests, refunds, and whether the production database was deleted.
  - **Lineage tracking:** every artifact remembers which original sources it came from, and its classification is the most sensitive one in that lineage.
    - **Why:** the exfiltration rule needs a `data_flow_id`, and real agents will never supply one (plan item A2). With lineage tracked, the harness can work that id out itself later, and the oracle can check whether PII really left.
  - **Ledger:** an ordered record of every tool call, including failed ones.
    - **Why:** it becomes the run log for experiments.
  - **`snapshot()`:** returns the whole world as JSON.
    - **Why:** for logs, and for the ground-truth oracle (plan item A3), which judges a run by what actually happened in the world.
  - **Deterministic ids:** ids come from counters (`art-1`, `file-1`, `req-1`), with no randomness.
    - **Why:** the same run can be repeated exactly, which experiment design needs (plan item A6).

- **`harness/tools.py`: eight fake tools**
  - **The tools:** `read_data`, `aggregate_data`, `export_file`, `transmit_external`, `transfer_funds`, `request_delegation`, `delegated_action` and `delete_production_database`. These match the actions the existing engines have rules for.
  - **Tools do not check permissions or policies.** They only reject bad input, such as unknown ids, non-positive amounts, an overdraft, or the wrong artifact kind.
    - **Why:** governance belongs in one place, the gateway coming in step 3. With no governance (`none` mode), the harm should really happen in the world so we can measure it. A test asserts this on purpose.
  - **`TOOL_SPECS`:** a description and JSON-Schema parameters for each tool.
    - **Why:** these become the tool definitions we hand to a real LLM in step 5.
  - **`call_tool()`:** the single entry point. It never crashes, and it returns a failed result for an unknown tool, missing or unexpected arguments, or arguments of the wrong type. Every rejection is written to the ledger.
    - **Why:** real models send malformed calls. The harness should report them back to the agent and log them, not crash the run.

- **`tests/test_harness_world.py`: 17 tests**
  - **What they cover:**
    - Each tool's normal use and its input checks.
    - PII lineage surviving read, aggregate, export and transmit.
    - Balances and overdrafts.
    - Delegation by the right and the wrong agent.
    - Tools deliberately not enforcing permissions.
    - Deterministic ids, ledger entries for failures, and the JSON snapshot.
    - Bad and wrong-typed arguments.

### Changed

- **`pyproject.toml`:** `pathaware_governance.harness` is now listed under `packages`.
  - **Why:** setuptools only installs the packages it lists, so without this the new subpackage would be missing from an install.

### Decisions

- **Blocked actions will not change the fake world.**
  - The engine still records the attempt in its own history (`engines.py`, `observe`).
  - **Why:** a real guard would stop the transfer from happening, but the path-aware engine still needs to remember attempts to spot split-up or repeated tries.
  - This is enforced in step 3 (the gateway) and will be written into `docs/architecture.md` then.

### Review

- **Who did what:** I wrote the spec and reviewed the code. A Sonnet subagent wrote the code.
- **Fix from review:** the first version could crash, or quietly misread input, when a tool argument had the wrong type (for example a string where a list was expected). That was fixed with type checks in `call_tool` and three new tests.
- **Tests:** the full suite passes, 36 tests. That is 17 new and 19 existing.

### Next

- **Step 3, the gateway:** turn each tool call into an `ActionEvent`, run it through an engine, then run the tool or return a refusal.
- **Step 4:** a scripted fake agent to test the whole loop.
