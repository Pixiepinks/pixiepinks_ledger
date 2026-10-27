# Recognize answers to product requirement questions

The general product planner previously received raw conversation turns and relied
entirely on the model to interpret short replies. It could repeat its age question
after “6”, then merely acknowledge “6 years” without starting a Shopify search.

`product_requirements` now supplies grounded conversational requirements to both
discovery and selection requests. Age/preference come only from customer text. A
bare number immediately following an English, Singlish or Sinhala age question is
age in years. Explicit forms such as “6 years”, “age 6” and “awurudu 6” also work.
A number following a budget/size question is not automatically an age. The topic
is context for shopping intent, not proof of product existence or child suitability.

For water bottles, school bags, toys and gifts, a completed category/age/preference
exchange advances to catalog search if the model otherwise repeats or merely
acknowledges those known facts. A genuinely different detail question (capacity,
size, budget, colour, brand or height) can remain. Cancellation is never overridden.
Other categories retain normal AI discovery; known age/preference are still supplied.
Category changes clear extracted requirements, and AI prose never supplies a child's
age or preference. The existing bounded conversation history remains the source;
no new customer records, schema, model training or configuration changes are needed.

English age questions in a non-bicycle conversation are no longer mistaken for
inferred bicycle discovery. Actual persisted bicycle states and category-switch
handling keep their existing priority.

Selection receives the known requirements and must select suitable verified matches,
ask a specific different essential question or explain that no suitable available
matches were found. It cannot infer suitability from colours or invent age/fit facts.
All positive-stock, exact-variant, service-window, human-mode and duplicate-send
guards remain in place. Completing age discovery does not guarantee stock exists.

Validation: 164 related tests passed, one environment-gated PostgreSQL test skipped.
The checks cover the reported water-bottle exchange, “6”/“6 years”/“age 6”/“awurudu 6”,
both repeated-question and acknowledgement-only model decisions, actual live routing,
budget ambiguity, distinct missing details, cancellation, bicycle category switches
and existing simulator/attachment/publication behavior. Providers were mocked.
Requirements-only Python 3.12 production imports passed.
