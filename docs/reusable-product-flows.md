# Reusable product flows

Configure any product from **Quick Messages → Customer Question Automation**. Create a flow, or duplicate one from the Flow library. Existing bicycle flows retain their behavior; customer runs retain the definition captured when they started.

## Stand fan example

Add these steps using **Add step** and the **Answer format** menu:

1. **Send selected products**. Give it the answer key `fan`. Select the actual fan and variant from the catalogue. Select only one product and enable **Continue to the next step after products / customer choice**. Leave customer choice off for a single fan. The sent card supplies the current price and product identity; no price is copied from the details table.
2. **Send Saved Voice**. Search, preview and select your prepared explanation recording.
3. **Send product details**. Enter a heading in Question text, then label/value rows. For example Brand / ELEVATE, Size / 16 inches, Model / ELSF1152, Power / 45W, Voltage / 220–240V, Frequency / 50Hz, Blades / 5, Speeds / 4, Timer / 60 minutes, Colour / Black, Warranty / 1 year, Assembly / Sri Lanka, Delivery / Free. The rows send as readable text, one detail per line; WhatsApp does not render an HTML table.
4. **Collect order details / checkout**. Select `fan` as the Product step answer key. Customize the delivery-address question for this product. Name, address, optional nearest city, Phone 1, optional Phone 2, and Confirm order / Edit details run one at a time. The quote has quantity 1 and free delivery, with no greasing question or service charge.

Enable payment instructions inside Checkout to choose a payment Saved Voice and upload your bank-details PNG/JPEG. Order confirmation is followed by the payment voice, bank image and deposit-slip handoff. Existing staff invoice/order preparation, bank verification and delivery tracking continue as before. A submitted slip never automatically marks payment verified.

Checkout must be the last step. For multiple products, enable customer choice (maximum 10) and Continue on the source product step, so checkout uses the card the customer selected. Sending multiple cards without customer choice is supported for information-only flows but cannot supply an unambiguous checkout product.

Use **Move up / Move down** to reorder steps and **Remove question** to remove a step. Buttons/List questions retain their per-answer routing. An **Ask typed question** step sends its question and waits for a signed text response of 1–500 characters before proceeding; use Checkout for the validated name/address/telephone fields. Save settings, review the Flow preview, then start it from the customer's chat. Ad-based mappings must be reviewed again after the saved flow version changes, as before.

## Execution and deployment

Actions continue on the server after the editor is closed. Existing reply-window, automation switch, pause/stop, conversation visibility and Saved Voice permissions apply. A durable claim is committed before each external send; uncertain outcomes stop for staff review and are not automatically replayed. A single unavailable product or missing LKR quote blocks checkout progression. Changed/unavailable recordings and payment images require reselection. Existing definitions without Continue finish as before.

No new environment variables or database migration are needed. Existing JSON flow definitions and durable send rows store the new step types. Deploy web and worker code together: older workers do not understand new action types. Keep the upgraded code while any new flows remain active; finish/stop those runs and remove the new step types before rolling back to an older application version. No live customer sends or production flow edits are performed by this implementation.

## Validation

The related saved-flow, product, bicycle greasing, delivery collection, payment follow-up, staff payment review, library and ad-flow regression run passed **177 checks**. Additional focused tests cover the final source-product selector and inbox preview, multiple-product selection, missing products and failed voice delivery. Browser checks exercise mobile editing, voice lookup, detail rows, step reordering, save/reload and inbox previews. All outbound transports are mocked.

Final focused verification passed **21 editor/browser checks** and **25 generic-flow/product checks** (these overlap with each other and the 177-check run). JavaScript syntax checks, Python compilation and `git diff --check` passed.
