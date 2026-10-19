# Independent entry review — Supervisor controlled tool delegation

Date: 2026-10-19. Reviewer session: `/root/v1c004_validation`.

This reviewer did not implement business code or import the main test suite's helpers. The reviewer owns `tests/test_supervisor_tool_contract_review.py` and this report only. Original inputs were `tasks.yaml` V1C-004, the approved implementation plan, Accepted ADR-0015, AGENTS.md, the task-card-delivery skill and applicable runtime/tool/testing/security rules. Final4 independently reran the unchanged 49-case entry suite against the current business sources: 49 passed, exit 0, with no warnings. This is a bounded technical entry-review pass, not whole-task closure or user business acceptance.

## Actual entry coverage

Real ChatService ordinary and streaming paths, real LangGraph, separate temporary SQLite databases, real local tool functions, fresh Session reads of messages and tool audit rows. Synthetic models provide controlled proposals. The response-cache case uses the real OpenAICompatibleProvider and real BoundedCache with HTTP requests terminated by httpx.MockTransport; it makes no network calls and spends no model fees. This is E2 isolated integration evidence, not production model quality evidence.

Counterexamples include cross-research duplicate proposals, partial success then failure, missing/L2/NaN/Infinity/bool-timeout/bool-or-negative-retry/unaudited contracts, current User/Tenant/token/tenant reassignment without a skill, unknown tools, malicious tool output reaching control, stream close before actual execution, cancellation during execution, selected skill disable/version changes, revocation after tool.call is exposed, retry after skill version changes, actual provider response-cache hits across new requests, and direct legacy Supervisor construction without a delegate. Existing explicit resume and private isolation tests will also require regression in the main gate; this independent file does not duplicate checkpoint construction.

## Executed rounds and corrections

Interpreter: D:/DepTooL/anaconda3/envs/ai-assistant/python.exe, Windows; test databases are synthetic and isolated. All pytest commands run serially with the main agent.

1. `python -m pytest tests/test_supervisor_tool_contract_review.py --basetemp data/pytest-tmp/v1c004-independent-red -q --tb=short`: 25 failed, 8 passed in 11.96 seconds; pytest exit 1. Log: `data/v1c004-baseline-20261019/independent-red.txt`. Failures were business assertions, not missing imports. The eight original zero-call permission passes were vacuous while the baseline had no delegation; they are not authorization evidence until rerun with functioning delegation.
2. `python -m pytest tests/test_supervisor_tool_contract_review.py --basetemp data/pytest-tmp/v1c004-independent-green1 -q --tb=short`: 10 failed, 35 passed, 1 warning in 18.16 seconds; exit 1. Log: `data/v1c004-baseline-20261019/independent-green1.txt`. Some business source files changed near this run, so this is diagnostic history rather than final fingerprint-bound evidence.
3. Test setup corrections after green1: skill versions `1`/`2` were invalid under the existing version contract; replaced with valid `1.0`/`2.0`. A TOOL-mode skill binds tools but does not inject its prompt body, so the incorrect body assertion became an assertion that the proposal-time mutation really ran. The cache transport's decision script now recognizes deterministic `not_called` evidence instead of relying on the obsolete empty-research wording. These preserve business refusal/execution/cache assertions.
4. The initial close test incorrectly waited for a tool to start while the consumer was paused at tool.call. ADR-0015 requires the opposite: the tool must await consumer acknowledgement and reauthorization. The corrected close test asserts zero starts and explicit producer cleanup; separate ordinary/streaming cancellation tests assert cancellation of in-flight functions and audit evidence.

## Findings submitted to implementation agents

- P2: Direct legacy Supervisor with tools but no delegate returned only the model's success wording. ADR-0015 requires explicit no-tool disclosure; system-prompt advice alone does not satisfy deterministic user-visible disclosure. The two independent assertions are retained.
- P1/P2: Closing the outer ChatService stream at tool.call did not explicitly close its nested Supervisor iterator. A producer remained awaiting acknowledgement; subsequent GC emitted pending-task and coroutine-ignored-GeneratorExit warnings. The review added an explicit check for residual Supervisor producer tasks. Explicit nested async generator cleanup was repaired and passed green3, with no residual producer warning in that run.
- Main agent independently found and is testing partial-success error disclosure, observation-triggered second-tool calls, retry authorization changes and retry-backoff cancellation. These are not claimed as independent findings here.

## Second frozen-source diagnostic

`python -m pytest tests/test_supervisor_tool_contract_review.py --basetemp data/pytest-tmp/v1c004-independent-green2 -q --tb=short`: 3 failed, 46 passed in 21.78 seconds, exit 1, no warnings. Log: `data/v1c004-baseline-20261019/independent-green2.txt`.

- Confirmed repaired: all skill revocation/version and retry version cases, no-delegate disclosure, nested GeneratorExit cleanup, task cancellation/audit, malformed contracts and principal changes now pass.
- New P2: partial-success then legitimate second same-tool call with different arguments was denied. The injection guard received the entire formatted observation, including the trusted `[review_read]` envelope; thus the normal previous tool name was misread as an instruction naming the next tool. The synthetic tool body `PARTIAL_READ_OK` contains no instruction/name. The original partial-success assertions remain unchanged; implementation must distinguish trusted envelope metadata from untrusted tool-result text.
- Test expectation correction: the streaming provider cache showed a real hit (second request made four HTTP calls instead of five), but the test demanded that every model use be a cache hit. The original contract requires actual cache hits to coexist with fresh tool execution, not a 100% hit rate. The test now spies only on the real BoundedCache.get return values, preserving actual behavior: require a real second-request cache hit and two tool executions/two persisted audits. The ordinary case additionally requires a cached tool-call proposal and zero second-request HTTP calls. The streaming case requires fewer requests and an actual model cache hit. The existing stream lease lifetime safely bypasses cache for other producer calls; optimizing that hit rate is not claimed or implemented here.

## Successful independent run and subsequent input audit

Actual command: `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_supervisor_tool_contract_review.py --basetemp data/pytest-tmp/v1c004-independent-green3 -q --tb=short`.

Actual result: **49 passed in 19.52 seconds**, pytest exit **0**, no warnings. The reviewer ran this command directly and its terminal output explicitly recorded `PYTEST_EXIT=0`. This is E2 evidence for the source version in the input manifest, not a claim about later source edits. The main agent had exclusive control of the other pytest windows; this run did not overlap another pytest process.

The successful cases independently rechecked the three discovered business defects: deterministic no-delegate disclosure, nested stream/producer cleanup, and legitimate same-tool/different-argument continuation after an earlier success. All passed. All selected-skill disable/version/event-yield/retry checks, live principal changes, malformed contracts, cancellation/audit, and real cache-hit execution assertions also passed. Ordinary cache evidence includes cached tool-call proposals; streaming evidence includes a real model/control cache hit and fresh tool/audit writes, and does not prove streaming action-proposal caching or zero second-request HTTP calls.

Recorded baseline HEAD: `e005b4d08b5139080423f780db8c60f92d5d219e`. Platform: Windows 11 build 22621. Interpreter: `D:/DepTooL/anaconda3/envs/ai-assistant/python.exe`. Configuration comes from the isolated test fixture; all tools, actors and databases are synthetic, with no external HTTP/model calls. The manifest records all 216 application Python files plus this test, conftest and pyproject (219 inputs).

Historical read-only inventory before final4, subsequently superseded by the rerun below: **217 of 219 hashes match**, including the review test and the runtime delegation/executor files. Two changed inputs mean this 49-case result is now **historical evidence for the earlier inputs**, not a current final-source pass:

| Input | Recorded SHA256 | Current SHA256 at audit |
|---|---|---|
| app/agents/supervisor.py | e04b8194e18f0af6d4e31eb834b363a0c98da7306013907c730e109d8df34901 | d8ca7b41df1d5a33abf9700f0cdd56986bf382b7a239f4cb86fcdd4e9473f71d |
| app/services/chat_service.py | 0b88fbdba2b63e355fcdf1feb60d95f8234e8010a1dd2a645f152b6955bcfc07 | 19b856eba077595bf56e7a4a1224156a493fee235d1a1c1e0159b6524cffa4a0 |

Log: `data/v1c004-baseline-20261019/independent-green3.txt`, SHA256 `4fd4a46ea500295ed32719eddbc055f02e1749e08da3df5eead1efbff1b0bd3b`. Input manifest: `docs/reviews/2026-10-19-v1c-004-entry-inputs.json`, SHA256 `99808a95c099957458e72eb083e763b6176af54efe6f06f401155324f31d1663`. The manifest is preserved, not overwritten to make later source changes appear verified.

## Current final4 evidence and repair closure

The reviewer independently reran the unchanged suite after the main agent confirmed the source freeze. Actual command:

`D:/DepTooL/anaconda3/envs/ai-assistant/python.exe -m pytest tests/test_supervisor_tool_contract_review.py --basetemp data/pytest-tmp/v1c004-independent-final4 -q --tb=short`

Process environment explicitly set `DATABASE_URL=sqlite:///./data/v1c004-regression-test.db`; each entry case additionally owns a temporary SQLite database. Result: **49 passed in 20.86 seconds**, exit **0**, no warnings. The terminal recorded `PYTEST_EXIT=0`. No other pytest process was started in parallel by the main agent; the reviewer handed back the test window before the main full regression.

This run binds to `docs/reviews/2026-10-19-v1c-004-entry-inputs-final4.json`: all 216 application Python files, this independent test, conftest and pyproject (219 inputs). Compared with green3, the only changed suite inputs are Supervisor and ChatService; the independent test is unchanged. The original input manifest remains preserved as history. The newly approved `tests/test_rag_028_model_capability.py` assertion is not imported by this independent suite and is validated separately by its owner and the full regression.

Repair closure was checked against the actual current code and formal regression cases, rather than treating the old engineering summary as proof:

| Finding | Current repair and verification | Bounded conclusion |
|---|---|---|
| E-01: completed tool facts lost after later error | Delegate collects actual ToolOutput in finally; Supervisor error exit prefixes actual summary. Independent final4 verifies partial tool success followed by real tool failure; main formal ordinary/streaming follow-up-model-failure cases were read and their main-green3 34-case log is historical corroboration, not this reviewer's own run. | Source repair confirmed; independent partial-result preservation passes. Final full regression still required for current main cases. |
| E-02: retry-backoff cancellation omitted audit | Executor's outer cancellation handler now covers the entire while loop and retry sleep. The main formal backoff-cancellation test checks a new Session's single cancelled audit. Independent final4 verifies ordinary/streaming cancellation during the actual tool with cancellation/audit and no further draft or call. | Source repair confirmed; related independent cancellation paths pass. Current full regression must include the exact backoff case. |
| E-03: no delegate or empty registry lacked deterministic disclosure | Supervisor's `_tool_summary` returns fixed not_called without a delegate and is prepended on normal/error final replies. | Independent final4 direct legacy ordinary/streaming assertions pass; empty-registry main assertions remain in full gate. |
| E-04: tool observation could authorize another action | Only the actual external result body becomes UNTRUSTED_SOURCE for the existing execution rejection guard; control receives deterministic status summary. Original user authorization remains separate. | Independent control-isolation cases pass. Main formal A-returning-B instruction case is retained; current full regression required. No claim of universal semantic-injection resistance. |
| E-05: retry reused revoked actor/skill authority | The new delegate's before_attempt callback checks fresh actor and selected skill before each func attempt; failure records rejection audit before propagating. | Independent final4 live User/Tenant/role/token changes and retry-time skill version mutation all pass; actual first-attempt count and new Session audit verified. |
| E-06: outer stream close left nested producer | ChatService explicitly awaits nested events.aclose; Supervisor cancels and awaits the producer. | Independent final4 closes at tool.call, verifies zero actual starts and zero residual Supervisor producers. No GC/pending-coroutine warnings. |
| Guard over-rejection of same tool with different arguments | Trusted observation envelope is excluded from the untrusted-body scan. The original independent partial-success assertions were preserved. | Both ordinary/streaming same-tool continuation cases pass; second real function call and both audits are present. |

Final4 also passes invalid/missing/high-risk contract rejection, malformed numeric limits, unknown tools, cross-research duplicates, selected-skill event-yield revocation, actual model cache hits with fresh per-request execution/audit, and the no-delegate compatibility cases. Ordinary cache evidence proves an actual cached tool-call proposal; streaming proves a real model/control cache hit and fresh tool execution, not streaming action-proposal caching or a perfect hit rate. No source/test behavior was changed by this reviewer to obtain final4.

Current independent test SHA256: `06f27868e36b0a6ea026bfff424164313720a8a8c45d13ad7611c4c3c80d6cd0`. Final4 log SHA256: `36d11e84458022db5bacc23cc8a045492204585f584f19fbda9da6c802bff7e5`. Final4 input manifest SHA256: `850d383e73cb51ffad093afe07cff861f301c61659a0c81ac92aa171475958a3`.

## Current bounded verdict and remaining delivery gates

**Independent entry-review technical pass for the final4 inputs. No remaining reproduced P1/P2 was found in this review's cases or current repair-chain inspection.** E-01 through E-06 and the guard over-rejection have concrete code repairs and retained formal tests; exact main-owned cases are subject to the current final full regression and are not misrepresented as independently executed here.

The full regression, Ruff/mypy, fresh final audit agent, user business acceptance and final-candidate CI remain separate delivery gates. Existing explicit resume/private isolation cases require their applicable main regression; this reviewer did not duplicate checkpoint fixtures. This report author did not implement business code, approve user business acceptance, close the task, or submit/publish Git changes.
