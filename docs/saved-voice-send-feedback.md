# Saved Voices send investigation and visible feedback

Local desktop and compact-mobile browser tests successfully submit Saved Voices
through mocked Meta transport, including lost-response recovery. This does not
establish why a particular production message did not send: production logs and
the affected attempt/status were not available during this investigation.

A UI issue was found: send outcomes were displayed above the scrollable library,
while Send Voice was in the sticky footer. On a long recording list/transcript,
a returned failed/uncertain/in-progress result could be out of view. Pending sends
also lacked an explicit preparing/checking message.

The picker now shows status beside the sticky Send Voice controls and displays
preparing/checking progress immediately. Unknown/in-progress attempt protection,
idempotency keys, reply-window enforcement, encoding, transport and global settings
are unchanged. Clicking Send Voice with an existing unknown attempt still checks
that attempt; it does not automatically resend. Check send status is read-only.

To diagnose the production case, obtain the exact picker status or the redacted
status response for the existing attempt. A closed service window, failed media
preparation, a pending/unknown attempt or Meta delivery failure require different
responses; none is established solely by a click producing no received message.
Do not clear pending attempt storage or force another send to diagnose uncertainty.

Deploy normally and reload picker assets; no migration or setting change is needed.
Rollback restores the preceding frontend version. Keep Global AI and production
voice settings unchanged. No live messages were sent during investigation.

Validation: 20 existing Saved Voices checks passed with 1 optional integration
check skipped; 2 additional mobile feedback checks passed. Coverage includes desktop
and mobile lost acknowledgments, a long transcript, closed-window feedback and
repeated clicks on UNKNOWN producing only one mocked message submission.

The owner subsequently reported the production message "Delivery is uncertain"
on both desktop and mobile. This confirms the picker received UNKNOWN for the
existing attempt, so its recovery guard checks that attempt instead of issuing
a duplicate POST. It does not establish the underlying cause. UNKNOWN can be an
explicit transport outcome (timeout/connection error, retryable Meta HTTP failure
or missing response message ID), or the read-only presentation of an old UPLOADING/
SENDING claim after an interrupted process.

The existing admin Voice delivery diagnostics page can read the affected attempt
without sending: /crm/bot-management/voice-diagnostics. Use Recent attempt IDs,
then Read trace. Compare the stored status with trace.stages.send_http and
send_exception. Do not share recipient fields, credentials or WhatsApp message IDs.
No recovery send or stale-claim clearing is authorized by this diagnostic patch.
