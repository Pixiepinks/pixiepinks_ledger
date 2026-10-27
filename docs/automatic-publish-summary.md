# Automatic publication summary and live settings

Versions / History prefills an editable Change summary by comparing saved DRAFT
against the published snapshot. It names changed settings (including the product
maximum and automatic attachment switch), without copying business instruction or
policy contents. If there are no differences it says “Republish current configuration”.
Submitting an omitted/blank summary generates it server-side from current saved
snapshots. A custom summary is preserved. Typing PUBLISH and existing publication
validation are still required; nothing publishes automatically.

The page also shows the actual published version, automatic attachment switch,
image switch and product maximum. History summary text is user-authored and cannot
prove that a particular switch was saved. The editable instruction form is DRAFT.

The live-reply investigation reproduced a fall-through: automatic recommendations
were generated, then the independent legacy catalog/text chain overwrote the reply.
The automatic branch now exits that routing chain as soon as it handles a turn,
preserving clarification and verified product selection without another catalog or
AI call. Stock checks and at-most-once attachment delivery are unchanged.

Normal routing now logs configuration
version, the two effective published switches and whether bicycle routing applies.
No message contents or secrets are added to this log. With both published switches
on, the screenshot's “Do you have water bottle” request uses automatic discovery/
selection; zero-stock candidates are filtered and cannot become attachments. Without
production database/log access, a version-history screenshot alone cannot establish
the saved switches or prove a particular live worker's routing state.

Validation covers editable/blank summaries, unchanged
publish confirmation, saved version summaries, actual LIVE flag display, the exact
water-bottle routing regression and existing attachment/language tests. Python 3.12
production-environment import passed. No dependencies or migrations changed.
The related regression run passed 170 tests with one environment-gated skip;
the final strengthened attachment/publication tests passed all 33 checks.
