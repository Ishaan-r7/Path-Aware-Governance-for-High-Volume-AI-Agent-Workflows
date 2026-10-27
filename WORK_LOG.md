# Work Log

A change-level record of work on this repo: what was added, changed or removed, and why.
Newest entries go at the top.

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
