# Exact-ad first replies and gift-pack follow-up context

## Activate the approved offer

After deployment, open **AI Bot Management → Ad Replies**. The form is prefilled
with the business-approved offer supplied in this task:

- Ad reference: `52608663935249`
- Pack: Baby Care Gift Pack
- Advertised pack price: Rs. 3,750
- Delivery: Rs. 400
- Offer valid through December 31, 2026, inclusive in Sri Lanka time
- The eight pack contents supplied by the business

The first reply is sent literally, without an AI request:

> Hi Dear, me pack eka wenawa Rs.3,750. Mekata delivery add wenawa Rs.400.
>
> Order eka proceed karannada?

Review the reply, leave its checkbox enabled and click **Save Draft**, then publish
in **Versions / History**. Nothing is activated just by opening the form or deploying
the code. Existing snapshots have no ad rules until a manager saves and publishes
one. The automatic change summary includes “Ad replies updated”. No new environment
variables, dependencies or migrations are needed; schema head remains `20261027_17`.

Only administrators and sales managers can access/save ad rules. The form uses a
session CSRF token. Saved draft rules can be edited or disabled, then republished.
Keep the literal first-reply prices and the structured price fields consistent.
Each source ID is unique; rules have bounded text, validated amounts and expiry.

## Trigger and continuation

Only a stored Meta referral with `source_type=ad` and the exact published source ID
can match. IDs typed in customer messages, caption keywords and click-tracking IDs
are not matches. Existing signed-webhook referral validation/storage is reused.
This feature does not fetch Facebook ads or infer identity from the ad image.

The first incoming **text** message carrying the reference gets the configured
greeting. Subsequent text messages in that conversation retain the published pack
name, approved contents, advertised price, delivery charge and single-pack total
(Rs. 4,150), instead of returning to general boys/girls/age product discovery.
After interest, the AI may ask a useful next question, such as quantity or delivery
town. Published global language preferences still apply to generated follow-ups;
the first reply remains exactly the manager-approved text.

Context is bounded to the latest referral in the last 24 hours for that phone and
only references at/before the current inbound message. A newer unmatched ad/post
stops the old offer context. A recognizable switch to another product category
also ends that offer context for later follow-ups. Human handover and HUMAN mode
retain priority. Image/voice processing and unrelated general product routing keep
their existing behavior. This does not retroactively contact old ad respondents.

## Pricing and action boundaries

The quoted amounts are explicit advertiser-approved **offer terms** supplied by the
business, not prices inferred from an image, referral caption or model output.
Their authority is scoped to this specific published gift-pack campaign. Normal
Shopify pricing rules for other products are unchanged. An offer quote is not proof
of current stock, a reservation, order acceptance, payment or dispatch.

No Shopify product identity was supplied for this pack. The system therefore does
not invent that mapping, automatically send unrelated gift products, create an
order, confirm stock or payment, invent diaper sizes, make medical claims or promise
delivery dates. Exact product/stock/order fulfillment still requires verification.
Raw referral captions, images, source URLs and private click IDs are not passed as
approved campaign data to the AI.

## Retry and race behavior

Each inbound campaign reply is claimed in the existing `whatsapp_manual_sends`
table before AI/provider work, with a unique `adc_` key scoped to conversation and
inbound message ID. Repeated or concurrent processing cannot replay it. The
customer-service window, AI mode and unchanged active rule are checked before
the provider send; expiry uses Sri Lanka's calendar date. Rule edits/disable or
human takeover while generation is running stop sending the old response.

A missing/ambiguous provider ID leaves UNKNOWN; there is no automatic retry or
fallback message that could duplicate the greeting. Database failure after provider
I/O preserves the committed claim, preventing replay. As with other manual-send
claims, interrupted attempts require checking actual customer delivery before any
deliberate new send. No marketing broadcast or proactive scheduled send is added.

## Verification

357 related checks passed with two environment-gated PostgreSQL skips in that run.
All 13 campaign-specific checks passed against PostgreSQL using disposable schemas,
including exact matching, manager publication, human/window guards, concurrency,
unknown outcomes and database failure after a send. Tests also check follow-up
price/content context without raw tracking/caption data and normal language/action
guardrails. Providers are mocked; no real customers were contacted.

Requirements-only Python 3.12 startup/restart, authenticated Ad Replies/inbox/library
pages, schema head and an empty default published ad list passed on SQLite and
PostgreSQL. Viewing the prefilled editor did not create a send or activate the offer.
