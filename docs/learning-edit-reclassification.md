# Correcting an edited lesson's category

Baseline: verified main `935faa1b499639858302e762fa813f6c14248619`.

The supplied test error is consistent with a lesson edited into product-category
content but published with GLOBAL scope and no product handle. The shared matcher
correctly refuses unscoped product knowledge, even in forced preview. Entering
an arbitrary test handle cannot repair missing lesson scope.

The editor called `classify(new_content, previous_category)`. The classifier
conservatively treats the suggested product/correction category as product
knowledge, so later neutral communication revisions stayed stuck in that category.
Edits now classify the revised content afresh. Product/policy words still trigger
the existing conservative fact rules. Every edit still resets to DRAFT, revalidates
and requires manager approval. Existing product handles are not removed or changed;
product-scoped entries remain scoped. Publication guards and force-preview scope
checks are not relaxed. Historical versions and evidence are preserved.

The lesson test now distinguishes unbound product classification from a legitimate
scoped product needing verified catalog context. It explains that an unrelated
handle is not a fix and that general communication guidance should be revised,
revalidated, approved and published again.

No production database was accessed or modified, and existing records are not
automatically reclassified. No migration, dependency, live flag change, merge or
deployment is included. The exact live row could not be read; the failure was
reproduced through the documented edit/publish/test sequence in isolated tests.

## Interim recovery on the existing deployment

Keep live retrieval and automatic publication disabled. A manager can reset the
category using an existing reviewed communication phrase, without publishing that
intermediate draft:

1. Replace Proposed lesson with `Could you tell me a little more about what you need?`
   and click Save edit & revalidate. Do not approve/publish this temporary draft.
2. Replace it with the following neutral guidance and save/revalidate again:

   > Clarification is useful when the customer's intended item is unclear. When
   > existing context already identifies the item, a helpful reply answers the
   > customer's question about that item without repeating the identification
   > question.

3. Check communication category, evaluation and evidence. Independently verify,
   approve and publish the final revision. Repeat the forced quoted-context test.

The sequence was checked against the existing classifier: product -> communication
LOW for the existing phrase -> communication REVIEW for the final guidance. Both
texts pass the unchanged privacy filter. Avoid the word “addresses” in this example:
the current conservative privacy regex treats it as address-related content. This
change does not weaken that privacy rule or any product-fact safeguards.

After deploying the fix, the intermediate reset is unnecessary: a neutral revision
is classified on its current wording directly. The broad product keyword rules
remain conservative; this is not a semantic classifier redesign.

## Validation

114 focused learning, lesson-test and worker-diagnostics checks passed; one
PostgreSQL-only comparison check was skipped because no dedicated PG URL was
provided for this small correction. Chromium checks were included. New regressions
cover the reproduced classification trap, draft/approval gates, preserved immutable
history, successful neutral-lesson testing, factual-risk classification and retained
product bindings. Python 3.12.14 production-dependency import passed. The full suite
and production startup/restart were not rerun for this focused backend correction.
