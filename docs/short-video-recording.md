# Short video recording in the CRM

Open a reply-enabled customer chat → **paperclip → Record video → Start recording**. Allow camera and microphone access in current Android Chrome/PWA. The recorder prefers the rear camera, requests up to 720p/24 fps (maximum 30 fps), and targets 750 kbps video plus 64 kbps audio. Camera constraints/bitrate requests are browser hints, not a guarantee about the hardware encoder.

Recording automatically stops after approximately **30 seconds**, or earlier near the **4 MiB** source-byte allowance. Incoming recording chunks are measured; recording stops at 85% of the allowance to reserve room for the last chunk/container. The final file must be nonempty and within the hard allowance or it is rejected locally with a retake message. A browser that ignores bitrate requests cannot upload an oversized recording through this control. Existing configured upload limits can lower the allowance.

The recorded clip is previewed locally. Staff can cancel or retake it. **Use this video** attaches it to the same customer; staff must still press **Send attachment**. Permission requests, recording, preview and selection never upload/send a customer message automatically. Switching conversations, closing the recorder, hiding the app or leaving the page stops/discards the capture and releases the camera/microphone. Retakes and asynchronous callbacks are fenced against stale conversations/recording sessions. This is not background camera recording.

This sends a **standard WhatsApp video message**, not the native application's circular instant-video-note format. The standard Cloud API path and existing staff ownership/shared reply, service-window, CSRF and idempotency checks are reused. No new customer message type, staff grants, Global AI setting, saved-flow behavior or marketing consent behavior is introduced.

## Browser and server behavior

Current Android Chrome over HTTPS is recommended. The browser chooses supported MP4 or WebM recording. Live browser files may omit duration metadata; the bounded conversion worker derives their duration by decoding instead of trusting a client-provided length. It converts browser WebM/VP8/Opus as needed to validated H.264/AAC MP4 before sending, using the existing pinned FFmpeg. Duration, resolution, memory, CPU, process timeout and output-size guards remain in force. No external media URL or WhatsApp access token is exposed to the browser.

The file is small **before upload**, addressing the initial phone-to-Railway delay of a 59 MB camera-library file. The existing compression/upload controls still apply to files selected from Videos; this recorder does not automatically compress an arbitrary old video in the browser.

If camera/microphone permission is blocked, use Android/Chrome site settings to allow access and reopen the recorder. Unsupported browsers show an error rather than silently opening an unlimited camera-file picker. If Android interrupts permission/camera access or the app goes into the background, return and record again. For long videos, continue to use the normal attachment picker and its size/duration limits.

## Deployment and testing

No database migration, Railway variable, credential, paid service or new runtime dependency is required. Deploy backend and versioned frontend assets together; reload the installed PWA so it receives the updated policy/scripts. The existing verified FFmpeg dependency is still required for browser codecs that need conversion.

Automated tests use actual Chromium MediaRecorder with fake camera/microphone devices and mocked Meta sends, plus a real duration-less WebM fixture. They cover local preview, cancel, deliberate send, mobile/desktop layout, permission failure, hard size rejection and camera cleanup on hide. Real Android hardware, orientation/audio quality, Chrome permissions and recording/lock interruptions still need a test-number check after deployment.

Recorded verification: broad media/conversion/mobile run **73 passed, 1 failed, 2 skipped**. The sole failure was an obsolete asset-version assertion; it was updated and passed in the final focused run (**7 passed**, including all six recorder checks). Thus no test failure remains unresolved from that run. The two existing PostgreSQL-only checks were skipped because their test URL was not configured; this feature adds no database schema changes. JavaScript/Python syntax and whitespace checks passed. No live provider sends, production changes, merge or deployment were performed.
