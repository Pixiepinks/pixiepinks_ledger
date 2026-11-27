# Monara audio format validation — controlled recovery

## Evidence and limits

The owner observed `response_validation / WorkerFailure / UNSUPPORTED_PCM_FORMAT`.
This proves the worker rejected an inline part's MIME declaration; it does not identify
that historical MIME or establish the validity/codec of its data. The old handler did
not retain it. Google AI Studio showed no matching new log; missing logs do not prove
no request or charge occurred.

The supplied Railway excerpt shows one CRM generation POST accepted at
2026-11-27 11:10:36 UTC (**4:40:36 PM Asia/Colombo**) and subsequent HTTP 200 job/status
requests. CRM HTTP 200 is not provider success or READY audio. The excerpt has no
provider MIME metadata. No raw Railway log/customer data is included in this PR.

## Source-supported correction

The original worker required one literal PCM parameter order. It rejected benign
variants such as `audio/L16;rate=24000;codec=pcm`. It also rejected WAV, although the
official Google 3.8 TTS sample's audio helper explicitly handles RIFF/WAV or PCM.
See the already reviewed official cookbook reference in `monara-ai-voice.md`.

This patch adds bounded, semantic MIME parsing and header-validated WAV support:

- PCM remains restricted to the existing Google 24 kHz, mono, signed 16-bit profile.
  MIME case, parameter order/quoting and the redundant `codec=pcm` parameter may vary.
  Conflicting duplicate parameters, unknown profile parameters, unsupported codec,
  sample rate, channels or bit depth are rejected. Existing raw PCM byte interpretation
  is unchanged; no new byte-order assumption is introduced.
- WAV is accepted only for known WAV MIME aliases with a complete RIFF/WAVE container,
  exact declared container length and verified 24 kHz/mono/16-bit uncompressed PCM.
  The header is stripped before passing exact PCM samples to the existing encoder.
- The complete bounded stream is assembled before WAV parsing. Split headers/data
  work; concatenated standalone WAV files, truncated, compressed/unsupported-profile
  or big-endian containers are rejected instead of silently truncating or guessing.
- Conflicting declared parameters cannot be erased by later chunks lacking them.
  Actual WAV headers must agree with all declared numeric profile information.
- Existing output byte/duration bounds, MP3/OGG conversion, model/voice/style/language,
  API key handling, admission budgets, no-retry state and send protections are retained.

For rejected formats, the existing safe diagnostic can now retain **only** a known MIME
enum (or OTHER), bounded numeric rate/channel/bit-depth values and controlled reasons.
No arbitrary MIME string, unknown parameters, transcript, audio or secret is logged.
The parent revalidates the diagnostic allowlist before persistence.

This establishes defects in the old validator and covers verified PCM/WAV formats.
It does **not** establish the original response was WAV, nor claim live generation is
fixed before an actual accepted preview. No provider request was made in development.

## Owner execution after review

1. Review/merge/deploy this draft only through normal owner approval. No migration or
   new variable is needed. Keep WhatsApp sending and Global AI OFF; no job is replayed
   by deployment. Existing failed jobs and reserved usage remain intact.
2. Run `python -m scripts.validate_monara_readonly` to confirm SDK/FFmpeg and effective
   gates. This does not validate live compatibility or issue provider calls.
3. Within the owner's existing generation-test approval, admit only one further short
   request through the CRM. Raise existing daily/reserved admission limits only enough
   for that request if required; never delete failed jobs, reset history or call the
   SDK directly to bypass the normal claim/budget path.
4. Use **Regenerate** once with the same controlled short test, language and style.
   Do not send or share it. If READY, listen and evaluate exact words, sound and duration.
   If rejected again, share only the new safe diagnostic (including its audio enum and
   numeric profile). Do not repeatedly regenerate to guess a format.
5. The visible AI disclosure must be present before any later approved customer send;
   it is enforced on sending, not the cause of the generation format failure.

Approval to tolerate generation costs does not authorize WhatsApp sending, automatic
replies, changing the existing voice/model, or production database cleanup.

## Rollback

Retain revision `20261127_62`, jobs, budgets and audit. Disable generation if necessary
and redeploy the prior accepted artifact under release approval. It will retain the
old stricter format rejection; no table/data downgrade or deletion is required. The
new additive diagnostic fields can remain and are ignored by older code. Sending
must stay disabled during this recovery.

## Tests

**99 distinct cases passed**, including 96 combined Monara/diagnostic/PostgreSQL/browser
cases and the final 31 format cases (28 overlapping cases counted once). No failures
or skips in retrieved final reports. See `monara-format-validation-results.json`.

Synthetic PCM/WAV fixtures verify MIME syntax variants, split streams, exact sample
preservation, size/duration limits, unsupported profiles, malformed/truncated/duplicate
containers and metadata redaction. The actual isolated SDK interpreter loads the
standard-library format helper without credentials. Existing diagnostics, Monara,
PostgreSQL and browser acceptance tests use mocked providers only. Local encoding
fixtures are synthetic tones, not actual Monara speech. Live validation remains an
operator step, not a claimed test result.
