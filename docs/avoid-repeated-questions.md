# Avoid repeated requirement questions

Singlish customer answers `Duwata` / `Puthata` (and `duwa` / `putha`,
`duwage` / `puthage`) are recognised as girl / boy preferences in both the
bicycle helpers and general recommendation requirement extraction.

The existing server-side protection against repeated age/preference discovery
now includes bicycles. When the customer has supplied category, age and
preference, a model response that merely repeats those questions is replaced by
catalogue search and verified selection. Distinct essential questions remain
allowed, and existing stock/fit checks still apply. No products are invented
when there are no suitable matches.

Generated replies are also instructed to use prior answers, avoid paraphrasing
previous questions, and proceed with known information or explain limitations
when an answer is missing. This instruction reduces other repetition but is
not a deterministic semantic guarantee for every possible question. The
server-side protection covers the recognised age/preference shopping exchange.

No migrations, environment variables or configuration publishing are required;
merge and deploy the code. No existing customer messages are changed or sent.
