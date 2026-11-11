# Staff attachment delivery diagnostics

A successful upload/send response means WhatsApp accepted the request. Delivery can fail later through a status webhook. The attachment notice now distinguishes acceptance from delivery and displays a fixed explanation and numeric Meta error code when delivery fails. The conversation timeline retains the same explanation.

The supplied production access logs cover 09:35–09:47 UTC (15:05–15:17 Sri Lanka), after the reported failures at 14:39–14:41. HTTP 200 for `/webhook` confirms receipt of a webhook request, not customer delivery. These logs contain no Meta error code, so the exact production rejection remains unconfirmed.

Previously, staff attachment webhooks updated only the message status, discarding the error code, and the attachment footer continued to say sent. Migration `20261111_41` adds numeric-only media delivery evidence. Codes and subcodes are retained; upstream text, URLs and customer details are not added to diagnostic logs. Administrator/staff visibility continues to use existing conversation permissions. Duplicate callbacks preserve known error codes. Older callbacks cannot regress recorded delivery status. Accepted send claims remain accepted and are never reset for an automatic resend.

As a compatibility improvement, fragmented browser MP4 recordings and videos with a non-yuv420p pixel format now pass through existing normalization instead of bypassing it. Output uses nonfragmented fast-start MP4, H.264 baseline level 3.1, yuv420p and AAC-LC when audio exists. This behavior is verified with a real fragmented MP4 fixture; it is not proof that fragmentation caused the production failures.

Deploy with the normal Alembic upgrade to head before serving the new code. No new environment variables are required. The additive migration preserves accepted sends and messages; downgrade retains diagnostic evidence. Historical errors discarded by the older version cannot be reconstructed from access logs. After deployment, an authorized test-number send can reveal the numeric code if a failure recurs. No live messages were sent during development.

Validation includes signed failure callbacks, duplicate and out-of-order delivery events, safe API output, accepted-send idempotency, fragmented MP4 conversion, additive SQLite/PostgreSQL migration, and Chromium attachment notice updates without resending. Related media optimization, recorder and outbound-media regression tests are included.

Final verification: **79 passed** across `test_media_delivery_status.py`, `test_media_optimization.py`, `test_record_video.py`, `test_whatsapp_outbound_media.py` and `test_whatsapp_outbound_media_formats.py`, including the disposable PostgreSQL checks and Chromium UI test. JavaScript syntax and `git diff --check` also passed.
