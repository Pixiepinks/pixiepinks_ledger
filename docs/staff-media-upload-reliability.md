# Staff photo/video upload reliability

## Findings and scope

A short video can fail even if its duration is only twenty seconds: the previous video default was 8 MiB; validation accepted only a narrow MP4 brand/H.264/AAC combination, at most 1080p, and decoded the entire file with a five-second CPU limit. MOV, phone HEVC, large-camera images, high bitrates and legitimate longer full-file decoding could therefore fail. The browser rejected oversized files before the server could optimize them.

There is no intentional admin-only attachment policy. STAFF, SALES_AGENT, SALES_MANAGER and ADMIN may send with current WhatsApp Inbox access and conversation ownership or an explicit shared reply grant. Read-only shares cannot send. Tests cover Inbox-only staff sending photos/videos and shared reply access. Amanda's production error and current account/conversation configuration have not been inspected; the exact error is required before identifying her individual root cause. Do not grant administrator access as a workaround.

## New behavior

- Existing compatible JPEG/PNG and H.264/AAC MP4 files within output limits remain unchanged.
- Supported larger/otherwise incompatible photos and phone MP4/MOV/M4V/WebP inputs are processed locally before sending. Photos become a resized JPEG (up to 1600 pixels per side); video becomes H.264/AAC MP4, up to 1280×720 bounds, 30 fps, with a bitrate budget based on duration/output allowance. Small sources are not enlarged. Metadata/chapters are stripped during conversion. Unsupported, corrupt, animated-image, excessive-resolution or excessive-duration sources are rejected safely.
- Videos remain limited to 120 seconds and at most 4K input; photos at most 40 megapixels. Output is fully revalidated. Video duration is checked after conversion so size limits cannot silently truncate the clip. A file that cannot fit the configured output size is rejected rather than sent incorrectly.
- The client accepts supported image/video sources up to the configured upload allowance and explains automatic optimization. It shows upload/processing status and reports size savings after success. Original media is previewed before sending; conversion may alter quality. Document handling remains unchanged.
- The source upload allowance defaults to **64 MiB**. Default final image/video ceilings are now **5,000,000 / 16,000,000 bytes**, within the application's WhatsApp provider caps. A larger source allowance does not increase the provider's output limit. Explicit existing Railway values are respected; changing defaults does not override configured environment variables.
- No blind customer-send retry is added. Existing durable attachment receipts and uncertain-send recovery remain in place. Current account activity, Inbox grants, conversation reply permission and the service window are checked before sending, including after slow uploads. The UI provides clearer policy-load diagnostics and a sanitized numeric Meta upload error code where available.

## Railway/deployment

No database migration, paid service or new credential is needed. Deploy the code and static asset version update. Normalization reuses the existing pinned, hash-verified FFmpeg installed through `requirements.voice-diagnostics.txt` in the Nixpacks build. It never downloads an executable at request time. Other deployment pipelines must install that pinned dependency/build verification too; an unavailable converter produces a safe service-unavailable response, not a claim that the customer's file is corrupt.

| Setting | Default | Meaning |
| --- | --- | --- |
| `WHATSAPP_OUTBOUND_MEDIA_SOURCE_MAX_BYTES` | `67108864` | Maximum supported source image/video upload; bounded at 64 MiB |
| `WHATSAPP_OUTBOUND_IMAGE_MAX_BYTES` | `5000000` | Final image cap; cannot exceed 5 MB |
| `WHATSAPP_OUTBOUND_VIDEO_MAX_BYTES` | `16000000` | Final video cap; cannot exceed 16 MB |
| `WHATSAPP_OUTBOUND_DOCUMENT_MAX_BYTES` | unchanged, 8 MiB | Existing document cap |

If Railway already sets video to 8 MiB, conversion targets that value. An administrator may explicitly raise it to 16,000,000 after review; this implementation does not change production variables. File duration alone cannot determine its size or WhatsApp compatibility.

Uploads, conversion and validation use bounded concurrency, input/output bytes, dimensions, duration, CPU, memory and time limits. Conversion occurs in a separate process group with a sanitized environment, private temporary files deleted on completion, no external network media protocols and no caller-supplied shell commands. Timeouts kill the whole conversion process group. Upload/request time budgets allow for conversion; slow clients should wait for the recorded result and must not repeatedly resend after an uncertain response. Adequate Railway CPU/memory and proxy request timeouts are still needed for large inputs; there is no promise that every arbitrary phone format will convert.

## Staff troubleshooting / manual verification

1. Ensure Amanda's Staff & Access grants include WhatsApp Inbox and she is signed in after any access update.
2. Check that the conversation belongs to her, or that she has an explicit share with **can reply**. Taking over AI mode is not the same as being assigned the conversation.
3. Try a small JPEG in an assigned test chat while its reply window is open. Note the exact error/status if it fails. A 403/404 indicates an access/scope issue; 413 is an input-size issue; 422 indicates invalid/unsupported media or failed bounded conversion; a validation-unavailable 503 indicates a runtime dependency problem. Numeric Meta upload errors distinguish provider rejection from these local issues.
4. Test a twenty-second camera MP4/MOV, including HEVC where available, then a photo above the former output limit. Confirm orientation, audio, complete duration, readable quality and the actual customer-side result. Test mobile data/slow upload and the desktop attachment picker.
5. Confirm read-only/unassigned staff cannot send, and that a duplicate request key never sends twice. Global AI, Saved Flows and marketing consent are unaffected.

Automated tests use isolated databases and mocked Meta transports. They exercise real local twenty-second MOV conversion, HEVC → H.264, PCM → AAC, oversized-photo optimization, preserved compatible files, staff/shared permissions and existing corrupt-document/idempotency/browser regressions. No production account state, real customer file or live Meta API was used.

## Recorded verification

- Relevant attachment, formats, staff-access and collaboration regressions: **106 passed, 2 PostgreSQL-only skips**.
- Final media/conversion run after current-access checks: **64 passed, 2 PostgreSQL-only skips**; two additional tests confirm access/window changes after media upload prevent the customer send. Counts overlap.
- Both skipped PostgreSQL migration/concurrent-idempotency tests passed separately against disposable PostgreSQL 16 (**2 passed**).
- Real local conversion tests passed for twenty-second MOV, HEVC, PCM-to-AAC audio, oversized photos and unchanged compatible photos, using mocked Meta transports. Staff-only Inbox grants and shared reply permission passed; read-only access was denied. Existing Chromium media tests, syntax and whitespace checks passed.
- No production role/grant/variable changes, real customer uploads, live Meta requests, merge or deployment were performed. Production testing with Amanda's exact error remains necessary to confirm her account-specific issue.
