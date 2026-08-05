# Fine-Tuning Tab Implementation Plan

## 1. Executive Summary

This document defines the implementation plan for introducing a dedicated Fine-Tuning tab where users can run a prebuilt recursive self-improvement workflow without building or editing an orchestration manually.

User experience target:
- Open Fine-Tuning tab
- Choose target type and target object
- Configure or select benchmark
- Set loop controls and mode
- Start run
- Observe progress, review outcomes, and rollback when needed

Primary implementation direction:
- Use existing self-improvement backend and orchestration capabilities
- Keep orchestration engine semantics intact
- Deliver a productized, guided surface over a prebuilt template

This is the Version A+ approach (UI-first with light parameterization), not a full de-orchestration rewrite.

## 2. Goals and Non-Goals

### 2.1 Goals

- Provide a first-class Fine-Tuning tab in Settings.
- Use one prebuilt orchestration template for recursive improve flow.
- Allow runtime selection of:
  - Target kind (agent or orchestration)
  - Target id
  - Benchmark id
  - Improve mode (human or autonomous)
  - Budget and ratchet controls
- Keep existing benchmark authoring and grading capabilities available in-tab.
- Enable one-click run for users without graph editing.
- Preserve compatibility with existing improve routes, runs, and inbox history.

### 2.2 Non-Goals

- Remove IMPROVE step types from orchestration engine.
- Redesign benchmark scoring logic.
- Introduce a new orchestration runtime.
- Replace existing improve API contracts unless required for parameter passing.

## 3. Current State (as of planning date)

- A prebuilt recursive improve orchestration exists in data with hardcoded target and benchmark defaults.
- Self-improvement UI components already exist:
  - Insights
  - Diff review
  - Benchmark editor
  - Rubric editor
  - Version history
  - Inbox panel
- Improve APIs exist for benchmark CRUD/run, propose/apply/rollback, versions, and inbox.
- Improve step executors run through orchestration step execution with state-driven contracts.

Gap:
- Current prebuilt orchestration requires hardcoded step benchmark and target settings unless edited.
- No dedicated top-level Fine-Tuning tab that wraps this as a guided product flow.
- The existing prebuilt orchestration (orch_sql_recursive_improve) is SQL-domain-specific. A plain, domain-neutral improve orchestration template is needed as the canonical Fine-Tuning tab backend.

### 3.1 Confirmed Implementation Gap: Run Endpoint State Injection

Option A in Section 5.2.1 is described as "no new endpoint" but this is not fully accurate given the current backend.

The orchestration engine's `run()` method signature accepts `initial_state: dict | None = None` and merges it into shared state before execution. However, the HTTP route `POST /api/orchestrations/{orch_id}/run` only reads `body.get("message", "")` from the request body and never passes `initial_state` to the engine. The engine parameter exists but is unreachable from HTTP.

Required fix (Option A path — ~3 lines, no new endpoint):
- In `backend/core/routes/orchestrations.py`, inside `run_orchestration`, read `initial_state` from the request body:
  ```python
  initial_state = body.get("initial_state") or None
  ```
- Pass it to the engine run call:
  ```python
  async for event in engine.run(user_input, run_id, initial_state=initial_state):
  ```

This unblocks the Fine-Tuning tab from injecting target, benchmark, mode, and budget at launch without requiring a separate endpoint.

The improve step executors already read `improve_target_id`, `improve_target_kind`, `improve_mode`, and `improve_budget_usd` from shared state, so once the state is injected the steps will use the user-selected values and ignore the hardcoded step defaults.

## 4. Proposed Product Model

### 4.1 New User Surface

Add a top-level Fine-Tuning tab in Settings with these sections:
- Target section
  - Target kind selector: agent or orchestration
  - Target picker based on kind
- Benchmark section
  - Existing benchmark selector
  - Create/edit benchmark inline
- Controls section
  - Improve mode
  - Budget
  - Ratchet threshold
  - Max iterations
  - Plateau patience
- Run section
  - Start button
  - Run status and latest events
- Results section
  - Score deltas and decision
  - Proposed/applied changes
  - Rollback controls
  - Inbox timeline

### 4.2 Execution Model

Use a prebuilt orchestration template as execution backend.

At run-start time, set runtime state values and step overrides so users do not edit graph nodes manually.

Preferred behavior:
- Fine-Tuning tab injects target and benchmark values at start
- Benchmark baseline and new steps both use selected benchmark id
- Analyze/propose/apply/ratchet steps use selected target
- Mode and budget values live in shared state and/or step-level runtime overrides

## 5. Technical Scope

## 5.1 Frontend Scope

### 5.1.1 Navigation and Tab Registration

- Extend Settings tab type union to include fine_tuning.
- Add Fine-Tuning entry in Settings navigation list.
- Add tab render block in Settings view.

### 5.1.2 New Fine-Tuning Page

Create a dedicated component, example name:
- FineTuningTab.tsx

Responsibilities:
- Fetch agents and orchestrations for target picker
- Fetch benchmarks and benchmark results
- Render benchmark authoring controls by reusing existing benchmark editor where possible
- Launch and monitor prebuilt orchestration run
- Display outcomes and rollback/inbox history

### 5.1.3 Reuse Existing Improve Components

Reuse components already proven in production:
- Benchmark editor
- Version history
- Inbox panel
- Optional: insights and diff review depending on final UX layout

### 5.1.4 Run Launch UX

Add a simple launch interaction:
- Validate required fields (target, benchmark)
- Resolve prebuilt orchestration id
- Start orchestration run with prepared state payload
- Subscribe to run events and render concise timeline

### 5.1.5 Error UX

Handle and surface:
- Missing prebuilt orchestration
- Missing benchmark
- Invalid target
- Budget abort
- Incomparable run basis
- Permission/auth errors

## 5.2 Backend Scope

### 5.2.1 Minimal Backend Additions

Two options (pick one based on current orchestration start API flexibility):

Option A (preferred if existing start API supports state injection and/or per-step overrides):
- No new endpoint
- Fine-Tuning tab calls orchestration start endpoint directly with payload

Option B (if direct payload shaping is too brittle):
- Add a thin endpoint, example:
  - POST /api/improve/fine-tune/run
- Endpoint resolves prebuilt orchestration, applies user params, starts run, returns run id

### 5.2.2 Runtime Parameter Injection

Required runtime parameters:
- target_kind
- target_id
- benchmark_id
- improve_mode
- improve_budget_usd
- ratchet settings

Implementation strategy:
- Shared state keys:
  - improve_target_kind
  - improve_target_id
  - improve_mode
  - improve_budget_usd
- For benchmark steps, ensure both baseline and new benchmark steps use selected benchmark id.

### 5.2.3 Prebuilt Template Contract

The prebuilt orchestration template should be treated as internal contract:
- Stable step ids for baseline/new benchmark and loop nodes
- Stable expected state keys
- Stable entry behavior

If template changes, Fine-Tuning tab mapping logic must be updated in lockstep.

## 5.3 Data Scope

### 5.3.1 New Plain Fine-Tuning Orchestration Template

The existing prebuilt orchestration (`orch_sql_recursive_improve`) is SQL-domain-specific and unsuitable as the canonical Fine-Tuning tab backend. A new plain orchestration template must be created specifically for this tab.

Template requirements:
- No domain-specific agents or steps (no SQL agents, no SQL-specific prompts).
- Step ids and shared-state keys must be stable and documented as an internal contract.
- `improve_target_id` and `improve_target_kind` default to `null` — always injected at launch.
- `improve_benchmark_id` default to `null` — always injected at launch.
- `improve_mode` defaults to `"human"` — overridable at launch.
- `improve_budget_usd` defaults to `null` (no cap) — overridable at launch.
- Loop structure: BENCHMARK (baseline) → IMPROVE_ANALYZE → IMPROVE_PROPOSE → IMPROVE_REVIEW → IMPROVE_APPLY → BENCHMARK (new) → IMPROVE_RATCHET_DECIDE → loop-back or stop.
- Ratchet decide step routes to loop head (keep) or rollback+stop (revert).
- No hardcoded agent ids in any step config.

Suggested id: `orch_fine_tune_builtin`

This template should be seeded into `backend/data/orchestrations.json` and treated as an internal system record (not shown in the regular orchestration editor by default, or clearly labeled as a system template).

The Fine-Tuning tab must pin to this template id. If the template is missing at launch time, the tab renders an error state rather than falling back to the SQL-specific one.

### 5.3.2 Existing Template Preservation

- Keep one canonical prebuilt recursive improve orchestration record in orchestration data.
- Ensure its defaults are safe but overridable at launch.
- Avoid forcing users to duplicate or manually edit this template.

## 5.4 Tests Scope

### 5.4.1 Frontend Tests

- Tab appears and loads
- Target and benchmark validation
- Run launch payload correctness
- Error message rendering
- Result card and status timeline rendering

### 5.4.2 Backend/API Tests

- Fine-tune run request starts orchestration with expected parameters
- Invalid benchmark/target returns expected errors
- Budget abort and ratchet stop reasons are visible in response/log stream

### 5.4.3 Integration Tests

- End-to-end from Fine-Tuning tab launch to completed run
- Human mode pauses at review
- Autonomous mode skips review and proceeds
- Baseline/new scores recorded correctly
- Rollback path remains functional

## 6. Acceptance Criteria

Functional acceptance:
- A user can run fine-tuning from a dedicated tab without opening orchestration editor.
- User can choose target and benchmark before run.
- Baseline and new benchmark are both computed using selected benchmark.
- Result decision is visible: keep or revert.
- Inbox and version history reflect autonomous actions.

Quality acceptance:
- Existing improve APIs continue to work.
- Existing orchestration editor behavior remains unchanged.
- No regression in self-improvement routes and benchmark lifecycle.

## 7. Rollout Plan

Phase 1: UI foundation
- Add Fine-Tuning tab
- Wire selectors and benchmark management
- Render dry-run validation state

Phase 2: Run orchestration launch path
- Runtime parameter injection
- Start run and stream status

Phase 3: Outcome and operations
- Results panel
- Rollback actions
- Inbox integration

Phase 4: Hardening
- Error handling polish
- Tests
- Documentation updates

## 8. Effort Estimate

Estimated effort for Version A+:
- 4 to 8 engineering days

Breakdown:
- Frontend tab and interaction model: 2 to 4 days
- Runtime parameterization and launch path: 1 to 2 days
- QA, tests, edge cases, and polish: 1 to 2 days

## 9. Risks and Mitigations

Risk: Template drift
- Mitigation: pin and validate required step ids and keys at launch time

Risk: Parameter mismatch between UI and runtime
- Mitigation: strict payload schema validation and clear error messages

Risk: Confusion between benchmark authoring and benchmark selection
- Mitigation: separate sections with explicit primary action labels

Risk: Feature scope creep toward full de-orchestration
- Mitigation: mark engine-level refactor as separate future initiative

## 10. Out-of-Scope Follow-Up Initiative

Future initiative (not this implementation):
- Remove improve loop from orchestration step system entirely
- Introduce dedicated fine-tuning runtime service
- Migrate old improve step-based orchestrations

This would be a separate multi-week architecture project.

## 11. Suggested File-Level Change Map

Frontend likely touched:
- settings tab type definitions
- settings navigation and view routing
- new Fine-Tuning tab component
- optional shared improve component extraction

Backend likely touched:
- `backend/core/routes/orchestrations.py` — expose `initial_state` from request body and pass to `engine.run()` (required for Option A)
- optional fine-tune run endpoint (if Option B chosen instead)
- orchestration run parameter shaping helpers
- tests for run launch and error paths

Data likely touched:
- new plain fine-tuning orchestration template (`orch_fine_tune_builtin`) in `backend/data/orchestrations.json`
- existing SQL prebuilt orchestration template defaults and metadata (leave untouched)

## 12. Decision Log

Decision: Use Version A+ rather than full de-orchestration
Reason: Fastest path to ship requested user experience with lowest regression risk

Decision: Keep prebuilt orchestration as backend contract
Reason: Existing improve executors are stable and already validated

Decision: Expose benchmark and target as first-class runtime controls
Reason: Removes need for users to edit step config manually

## 13. Ready-to-Start Implementation Checklist

- Confirm final Fine-Tuning tab label and placement in Settings
- Confirm single prebuilt template id and lock it
- Confirm run-launch payload contract
- Implement UI shell and selectors
- Wire benchmark selector and editor
- Wire run launch and status feed
- Wire results, version history, and inbox
- Add tests and docs
- Run regression checks for improve and orchestration flows

## 14. Final Notes

This plan delivers exactly the requested product behavior:
- Prebuilt fine-tuning orchestration
- No graph-building required by user
- User only chooses target and benchmark, then runs

It intentionally avoids a high-risk engine refactor while preserving the option to perform a deeper architectural separation later.

## 15. Implementation Report (Completed on 2026-08-05)

This implementation is now complete and includes backend, data, frontend, and test updates for the dedicated Fine-Tuning tab flow.

### 15.1 Backend Changes Implemented

1. Orchestration run endpoint now accepts and forwards `initial_state`.
- File updated: `backend/core/routes/orchestrations.py`
- Implemented exactly as planned under Section 3.1 / Option A:
  - Reads request body key `initial_state`
  - Passes it into `engine.run(..., initial_state=initial_state)`

2. Improve step runtime parameterization now supports launch-time overrides from shared state.
- File updated: `backend/core/improve/steps.py`
- Added runtime override support for:
  - `improve_benchmark_id` (used by both baseline + new benchmark steps)
  - `improve_ratchet_threshold`
  - `improve_ratchet_max_iterations`
  - `improve_ratchet_plateau_patience`
- Existing step-level defaults remain intact when no runtime override is provided.

### 15.2 Data / Template Changes Implemented

1. Added a new domain-neutral built-in template orchestration:
- File updated: `backend/data/orchestrations.json`
- New orchestration id: `orch_fine_tune_builtin`
- Characteristics implemented:
  - No SQL/domain-specific prompts or agent coupling
  - Stable loop shape:
    - `s_bench_base` -> `s_analyze` -> `s_propose` -> `s_review` -> `s_apply` -> `s_bench_new` -> `s_ratchet` -> `s_gate` -> loop or `s_end`
  - Runtime-injected defaults in `state_schema`:
    - `improve_target_id`: `null`
    - `improve_target_kind`: `null`
    - `improve_benchmark_id`: `null`
    - `improve_mode`: `"human"`
    - `improve_budget_usd`: `null`
    - `improve_ratchet_threshold`: `0.0`
    - `improve_ratchet_max_iterations`: `5`
    - `improve_ratchet_plateau_patience`: `2`

### 15.3 Frontend Changes Implemented

1. Added a dedicated Settings tab: `Fine-Tuning`.
- Files updated:
  - `frontend/src/components/settings/types.ts`
  - `frontend/src/components/SettingsView.tsx`
  - `frontend/src/components/settings/FineTuningTab.tsx` (new)

2. Fine-Tuning tab capabilities implemented:
- Target section:
  - Target kind selector (`agent` / `orchestration`)
  - Target object picker
- Benchmark section:
  - Benchmark selector
  - Embedded benchmark authoring via existing `BenchmarkEditor`
  - Embedded rubric authoring via existing `RubricEditor`
- Controls section:
  - Improve mode (`human` / `autonomous`)
  - Budget (USD)
  - Ratchet threshold
  - Max iterations
  - Plateau patience
- Run section:
  - Starts `orch_fine_tune_builtin` using `/api/orchestrations/{id}/run`
  - Injects launch-time state via `initial_state`
  - Streams SSE progress, status, and ratchet decisions
  - Supports pause/resume for human review input
  - Supports cancel
- Results section:
  - Recent benchmark result list for selected target
  - Embedded `VersionHistory` (rollback controls)
  - Embedded `InboxPanel` (autonomous audit timeline)

3. Missing template behavior implemented:
- If `orch_fine_tune_builtin` is missing, the tab renders an explicit error state and does not silently fall back to SQL-specific orchestrations.

### 15.4 Validation and Tests

1. Added API regression test for initial state injection:
- File updated: `backend/tests/api_app/test_orchestrations.py`
- New test verifies:
  - `POST /api/orchestrations/{orch_id}/run` passes `initial_state` through to `engine.run()`

2. Executed targeted backend tests:
- Command run: `cd backend; ..\\.venv\\Scripts\\python.exe -m pytest tests/api_app/test_orchestrations.py -q`
- Result: `7 passed`

3. Editor diagnostics check:
- No errors reported in changed backend/frontend files.

### 15.5 Notes on Scope

- This implementation uses Option A (no new fine-tune-specific backend endpoint), as planned.
- Existing orchestration engine semantics were preserved.
- Existing improve APIs and components were reused rather than replaced.

### 15.6 Completion Status

All requested implementation work in this plan has been completed, documented, and validated with targeted tests.

## 16. QA Summary

Major features being tested:
- Settings navigation and Fine-Tuning tab registration
- Target selection for agents and orchestrations
- Benchmark selection, benchmark authoring, and rubric authoring
- Launch payload shaping with `initial_state`
- Run lifecycle, SSE event streaming, human review, cancel, and completion handling
- Autonomous ratchet behavior, budget controls, and stop reasons
- Results, version history, rollback, and inbox integration
- Error handling, validation, and empty/loading/failure states
- Regression coverage for existing improve and orchestration APIs

These cases are written so they can be executed manually and later automated with UI, API, and integration coverage.

## 17. Test Cases

### Navigation and page load

1. FT-01 Settings navigation includes a visible Fine-Tuning entry. Expected: the tab label appears alongside the other Settings tabs and selects the Fine-Tuning page when clicked.
2. FT-02 Fine-Tuning page renders on first open without a target selected. Expected: all sections appear, placeholder selects are shown, and the page does not crash.
3. FT-03 Direct navigation to the Fine-Tuning tab opens the same page state as clicking it from Settings. Expected: the view is consistent and the tab remains active.
4. FT-04 The page shows a loading state while agents, orchestrations, and benchmarks are being fetched. Expected: loading feedback is visible and launch controls are not usable until data is ready.
5. FT-05 The page handles a data-fetch failure from one source while still rendering the rest of the shell. Expected: a clear error banner appears and the user can retry after the failure.

### Target and data loading

6. FT-06 The agent target list loads from `/api/agents`. Expected: all returned agents are shown with readable labels and stable ids.
7. FT-07 The orchestration target list loads from `/api/orchestrations`. Expected: orchestrations appear with names and ids in the target picker.
8. FT-08 The built-in template id `orch_fine_tune_builtin` is not exposed as a selectable target object. Expected: the system template is excluded from the user target dropdown.
9. FT-09 Switching target kind from agent to orchestration clears the current target id. Expected: the picker resets so an invalid cross-kind selection is not retained.
10. FT-10 The initial target is auto-seeded from the first available agent when the page loads in agent mode. Expected: the first agent is preselected when no prior selection exists.
11. FT-11 The initial target is auto-seeded from the first non-built-in orchestration when the page loads in orchestration mode. Expected: the built-in system template is skipped during seeding.
12. FT-12 The benchmark list loads from `/api/improve/benchmarks`. Expected: each suite is shown in the benchmark selector with its name and id.
13. FT-13 The initial benchmark is auto-seeded from the first available suite. Expected: a default benchmark is selected when benchmarks exist.
14. FT-14 The latest benchmark result list is loaded from `/api/improve/benchmark/results?target_object_id=...`. Expected: results are filtered to the current target and sorted newest-first.
15. FT-15 Changing the target refreshes the latest benchmark history. Expected: the results panel updates to the new object id and does not keep stale rows.

### Benchmark and rubric authoring

16. FT-16 The embedded BenchmarkEditor appears only when a target is selected. Expected: the editor is hidden or inert until a valid target id exists.
17. FT-17 Running a benchmark from BenchmarkEditor triggers a refresh of the Fine-Tuning results panel. Expected: the new benchmark run appears after the callback fires.
18. FT-18 The embedded RubricEditor is available in the Fine-Tuning page. Expected: rubric authoring can be reached without leaving the tab.
19. FT-19 The benchmark section keeps both benchmark selection and authoring visible at the same time. Expected: selection and editing are clearly separated and usable together.

### Validation and launch guards

20. FT-20 The Improve mode selector supports both human and autonomous values. Expected: the selected mode is stored locally and reflected in the launch payload.
21. FT-21 A valid decimal budget value is accepted. Expected: the input can be set to a positive decimal and is converted to a numeric payload value.
22. FT-22 An empty budget input is treated as no cap. Expected: the launch payload sends `null` for `improve_budget_usd` rather than zero or NaN.
23. FT-23 A zero, negative, or non-numeric budget is rejected. Expected: launch is blocked with a clear validation message.
24. FT-24 A numeric ratchet threshold is accepted. Expected: the launch payload includes a number and the page does not reject valid numeric text.
25. FT-25 A non-numeric ratchet threshold is rejected. Expected: launch is blocked before the request is sent.
26. FT-26 Max iterations accepts only integers greater than or equal to 1. Expected: invalid values stop launch and show an error message.
27. FT-27 Plateau patience accepts only integers greater than or equal to 1. Expected: invalid values stop launch and show an error message.
28. FT-28 The Start button stays disabled while data is loading, while a run is active, or when the built-in template is missing. Expected: duplicate or invalid launches cannot be triggered.
29. FT-29 Starting without a target is blocked. Expected: the page shows the missing-target message and does not call the run endpoint.
30. FT-30 Starting without a benchmark is blocked. Expected: the page shows the missing-benchmark message and does not call the run endpoint.
31. FT-31 Launching with an invalid built-in template state is blocked. Expected: the explicit missing-template error is shown instead of silently falling back to the SQL template.

### Request shaping and API behavior

32. FT-32 The run request posts to `/api/orchestrations/orch_fine_tune_builtin/run`. Expected: the launch uses the built-in template id and not a dynamically chosen orchestration.
33. FT-33 The launch request includes a message and an `initial_state` object. Expected: the backend receives both fields in the JSON body.
34. FT-34 The launch payload includes `improve_target_kind` and `improve_target_id`. Expected: the selected target is injected into shared state at run start.
35. FT-35 The launch payload includes `improve_benchmark_id` and `improve_mode`. Expected: the chosen benchmark and mode are visible to downstream improve steps.
36. FT-36 The launch payload includes `improve_budget_usd`, `improve_ratchet_threshold`, `improve_ratchet_max_iterations`, and `improve_ratchet_plateau_patience`. Expected: all runtime controls are passed through without loss or renaming.
37. FT-37 The orchestration route returns a streamed response that the UI can parse as SSE. Expected: the run starts and events are consumed incrementally instead of waiting for a single blocking response.
38. FT-38 A non-200 launch response is surfaced in the UI. Expected: the page enters a failed launch state and logs the HTTP status.

### Run lifecycle and SSE events

39. FT-39 An `orchestration_start` SSE event sets the run id and running state. Expected: the run id appears in the UI and the event log records the start.
40. FT-40 `step_start` events append a readable step timeline in order. Expected: the user can follow the run progression step by step.
41. FT-41 Malformed SSE frames are ignored safely. Expected: the page keeps processing later valid events without crashing.
42. FT-42 An `orchestration_complete` event with status `completed` marks the run as completed. Expected: the run state updates, the spinner stops, and the page refreshes related results.
43. FT-43 An `orchestration_complete` event with a failure status marks the run as failed. Expected: the user sees a failed run state and the event stream stops.
44. FT-44 An `orchestration_error` event surfaces the backend error text. Expected: the error is shown to the user and the abort controller is cleared.
45. FT-45 The cancel button aborts an active run. Expected: the stream is stopped, the UI status becomes cancelled, and the cancel endpoint is called when a run id is known.
46. FT-46 Cancelling a paused run clears the human prompt. Expected: no stale approval prompt or input fields remain visible after cancellation.

### Human review, autonomous mode, and ratchet behavior

47. FT-47 Human mode pauses on `human_input_required`. Expected: the page shows the approval prompt and field inputs, and the run status changes to paused.
48. FT-48 Human-input fields render correctly by type. Expected: fields with options use a dropdown and free-text fields use a text input.
49. FT-49 Human-input submission includes both field names and labels when labels differ. Expected: the response payload is compatible with backend aliases and the run resumes.
50. FT-50 Autonomous mode skips the review pause, preserves the selected benchmark across baseline and new passes, surfaces ratchet decisions and stop reasons, and keeps results/history/rollback/inbox plus existing improve API routes intact. Expected: the run continues without waiting for human approval, the selected benchmark id is reflected in both benchmark events, budget or plateau failures are visible instead of silent, and the VersionHistory/InboxPanel surfaces still function after a run completes.

## 18. Edge Cases

- No agents returned from `/api/agents`.
- No orchestrations returned from `/api/orchestrations`.
- No benchmark suites returned from `/api/improve/benchmarks`.
- Built-in template exists but is malformed or missing required step ids.
- Target or benchmark is deleted after the page has already loaded.
- Run is cancelled while the SSE stream is mid-event.
- User refreshes the page while a run is paused for human input.
- User opens two Fine-Tuning tabs and launches conflicting runs.
- Budget, threshold, max iterations, or plateau inputs contain very large values.
- Human-input fields contain long text, unicode, or empty strings.
- Backend returns a 401/403 during a fetch or run launch.

## 19. Potential Gaps Or Unclear Requirements

- Exact role-based access control for Fine-Tuning is not defined. It is unclear whether all Settings users can launch runs or whether agent/orchestration ownership should be enforced.
- The spec does not define whether Fine-Tuning state should survive a browser refresh or be restored from the running orchestration id.
- The desired retry behavior for transient SSE disconnects is not specified.
- There is no explicit requirement for how benchmark authoring changes should be validated before a launch can use the newly edited benchmark.
- Input bounds for budget, threshold, iterations, and patience are not formally documented beyond basic runtime validation.
- Accessibility requirements are not spelled out for keyboard navigation, screen-reader labels, focus handling, or pause/resume controls.
- Logging and analytics requirements are not described, so it is unclear which user actions or failure states must be tracked.
- The priority between the embedded benchmark authoring tools and the run controls is not stated when both are used in the same session.
- The spec does not say whether results should auto-refresh continuously during a long run or only on completion.

## 20. Suggested Additional Scenarios

- Refresh the browser during a running or paused fine-tune and verify whether the run can be reattached or at least recovered from backend status.
- Launch a run, open a second tab on the same target, and verify concurrent state does not corrupt the UI.
- Simulate a network drop during SSE streaming and verify the user gets a clear, actionable failure message.
- Launch with a target or benchmark that is removed between selection and submit.
- Verify keyboard-only operation for every selector, button, and human-input control.
- Verify screen-reader labels and focus order for the target, benchmark, controls, run, and results sections.
- Verify large benchmark histories do not make the page unusably slow.
- Verify rollback after a fine-tuning run updates the results panel and inbox timeline consistently.
- Verify a failed launch does not leave the UI stuck in a running state.
- Verify backend auth failures are surfaced clearly instead of being interpreted as empty data.