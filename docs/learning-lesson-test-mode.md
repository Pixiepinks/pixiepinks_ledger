# Staff-only published-lesson comparisons

Baseline: verified GitHub main `1b25216c21e9268fc4bd0ec151577743ad0ff3c4`.

## How to use

Keep `LEARNING_RETRIEVAL_ENABLED=false` and automatic publication off. No
environment change, migration or new dependency is required for this feature.
Existing OpenAI configuration is needed to generate test replies; existing
Shopify credentials are needed only when a product handle is supplied.

1. Sign in as ADMIN or SALES_MANAGER and open **AI Learning Center → Suggestions**.
2. Review a currently **PUBLISHED**, enabled lesson. Click **Test learned lesson**.
   Unpublished, disabled, archived, withdrawn or superseded lessons cannot be
   forced into a test. Existing privacy/notice/provider reviews remain required.
3. Enter a synthetic customer question, or fill a preset. Selecting a preset
   never sends a request. Do not copy customer conversations or private evidence.
4. Choose one mode:
   - **Normal matching for this selected lesson** uses the same keyword/scope
     selection rules as production retrieval. If no match is found, the lesson
     is not included and the trace explicitly says `NO_KEYWORD_MATCH`.
   - **Force selected lesson into preview** includes it despite a keyword miss,
     clearly labelled `FORCED_PREVIEW`. It does not override product scope,
     publication state, privacy checks or live settings. This is an experiment,
     not evidence that live retrieval would select that lesson.
5. Optionally add a synthetic quoted-product, image or ad description. These
   are unverified text, not image analysis, actual webhook context or proof of
   advertised product identity. No images or arbitrary URLs are fetched.
6. Optionally supply a Shopify **handle**, not a URL or product ID. The server
   resolves it read-only and supplies verified catalog data to both comparison
   arms. Product-scoped lessons require matching server-verified Shopify scope.
7. Click **Run comparison** once. Review both replies, provider status, reported
   token usage, selected version, shared matching result and whether the lesson
   was actually supplied in the model request.

The trace proves inclusion in the request, not whether the model internally
followed it or whether it improved customer outcomes. Replies can vary because
the two generation calls are independent. There is no automatic pass/fail grade
and no automatic publication, retrieval activation or promotion of test results.

## What this tests—and what it does not

This compares AI generation with and without one selected published lesson.
Both arms use identical question, context, verified catalog data, published
administrator policy data and security instructions; the selected example is
the difference. Inputs and displayed replies pass the existing conservative
privacy filter, so redaction can alter the sample or displayed wording. The
trace notes when input changed.

The selected-lesson matcher intentionally runs independently of the live
retrieval gate, which stays unchanged/off. It does not reproduce competition
among every published lesson, the four-example production selection cap, the
entire webhook router, deterministic bicycle/order routing, actual quoted media
resolution or image vision. The existing Test Bot simulator is unchanged.
Use the relevant integration/browser tests and an explicitly authorized test
number later for full end-to-end customer behavior; this comparison alone
does not establish that enabling live retrieval is safe.

Suggested checks for clarification lessons:

- An ambiguous request with no identified product: a polite clarification is
  appropriate if the lesson matches or is explicitly forced into preview.
- A quoted product plus a verified matching Shopify handle: the reply should
  use known context and avoid asking the customer to identify the product again.
- A synthetic image/ad description without a verified product: the reply must
  not invent a Shopify match, price or availability from generic text.
- A forced, unrelated lesson: inspect whether the model still follows the
  customer request and authoritative facts. Force mode is never live routing.

## Isolation and security

`POST /crm/bot-management/learning/api/lesson-test` requires manager access,
the existing session and CSRF token, strict bounded JSON and a current published
candidate. Arbitrary conversation IDs, media URLs, model overrides and extra
fields are rejected. Responses are private/no-store and displayed as text.

The service uses SELECT queries only. It does not write settings, candidates,
evaluations, evidence, feedback, jobs, audits, messages, orders, leads or
conversation timestamps. It never calls WhatsApp send functions or reads a
customer conversation/source evidence into the model prompt. The selected
published lesson is the only learning content supplied; normal customer replies
continue on their original retrieval path.

`ai_service.ReplyTestContext` is a request-local object, not a global flag,
environment mutation or monkeypatch. It supplies the explicit comparison payload
and suppresses live retrieval/telemetry only for those two function calls.
Production callers pass no test context and retain their existing behavior.
The pure shared matcher was extracted without changing production defaults.

Current lesson/publication/privacy state is rechecked after each provider wait;
withdrawal or a superseding version stops further calls/invalidates the result.
Provider failures are shown as failed arms with no generated reply, rather than
presenting the customer fallback text as a successful comparison. Provider error
bodies, tokens and private learning evidence are not returned or logged.

## Cost and limits

Every valid comparison can make **two billable AI requests** using the configured
`OPENAI_MODEL`. The panel warns before submission and displays provider-reported
tokens when available. These manual requests are not learning-worker runs and
do not change the worker's budget counters or History cost estimates. Check
provider billing/usage for actual costs.

Bounds: ten admitted comparison attempts per manager per hour per web process,
two concurrent comparisons per process, 2,000-character questions, 1,000-character
context descriptions and 200-character handles. Existing privacy filtering can
further shorten input. Each reply uses at most 300 output tokens and a 12-second
provider timeout. Test requests disable SDK retries and use Responses
`store=false`; provider retention policies still apply. The browser does not
automatically retry a failed/timed-out comparison. In-memory admission counters
reset on restart and are not a global billing cap across multiple processes.

Only transient request/response objects and count/timestamp admission entries
are kept in the application. No simulation prompts or results are permanently
stored. Nothing is sent to customers and live flags are not modified.

## Changed files

`ai_service.py`, `learning_runtime.py`, `learning_routes.py`,
`learning_test_service.py`, `templates/learning_center.html`,
`templates/_learning_lesson_test.html`, `static/learning_center.js`,
`static/learning_center.css`, `static/learning_lesson_test.js`,
`tests/test_learning_lesson_test.py` and this guide.


## Validation

- Complete suite: **1,077 passed, 2 failed, 2 skipped**. The payment-claim and
  Shopify-webhook concurrency failures both reproduced on an unchanged archive
  of baseline `1b25216c21e9268fc4bd0ec151577743ad0ff3c4`. No tests were disabled.
- The two primary-PostgreSQL-only skips (fulfillment preflight concurrency and
  Shopify draft retry claiming) passed separately in isolated schemas.
- **110 focused checks passed**, followed by **81 final focused checks** after
  setting Responses `store=false`; these counts overlap each other and the suite.
- A final Chromium check also passed for clearing stale results when inputs
  change, without automatically making another request.
- Requirements-only Python **3.12.14**, dependency validation, real startup and
  restart on SQLite/PostgreSQL, authenticated pages/assets, disabled-worker and
  read-only diagnostics checks passed. Alembic head remains `20261026_16`.
- New tests cover manager authentication and CSRF; strict bounds; current
  publication, rollback/privacy and product scope; match versus forced preview;
  verified versus descriptive context; failures, limits and tokens; read-only
  SQL on SQLite/PostgreSQL; no customer messages/feedback; concurrent live-call
  isolation; and actual mobile Chromium behavior. Providers are mocked.
- No real AI/Shopify calls, customer messages, production writes, live flag
  changes, merge or deployment were performed during implementation.
