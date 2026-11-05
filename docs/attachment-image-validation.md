# PNG/JPEG attachment rejection investigation

## Confirmed findings and remaining uncertainty

The reported message originates in outbound_media_service.validate_file when the
disposable local content-validation worker fails, times out or returns invalid
metadata. It happens before perform_send creates a send attempt or calls Meta.
It does not establish that Meta rejected the image, nor does a browser preview
prove full file integrity.

The original PNG was not supplied. A generated valid RGB PNG of 2,418,198 bytes
passes validation and reaches the mocked upload/send path. RGB, RGBA, grayscale,
indexed and 16-bit fixtures also decode locally. The file-specific root cause
(pixel limit, CRC/structure, decoding, animation or a processing failure) is
therefore NOT confirmed. Do not describe this patch as repairing that exact PNG.
The original file, preserving its bytes, is required to reproduce its rejection.

A confirmed diagnostics defect was fixed: unrelated local failures were collapsed
into one message that blamed corrupt/unsupported/encrypted content. Worker failures
now return allowlisted reason codes. Users get specific, safe guidance for wrong
signature/extension, PNG structure/CRC, animation, pixel count, color/encoding,
image decoding, memory, timeout and process interruption. No upstream or decoder
exception text, filenames, credentials or image content is logged or returned.

## Complete flow and limits

1. The mobile attachment picker makes a browser object-URL preview. This is only a
   preview. Client format/size checks use the existing server media-policy endpoint.
2. send-media enforces login, section access, conversation reply permission, CSRF,
   a unique key, multipart fields and a 45-second bounded request read. It admits
   at most two concurrent attachment operations in a process.
3. The service sanitizes the filename, uses its allowlisted extension to choose
   image MIME, and checks nonempty bytes and caption (at most 1024 characters).
   Supplied multipart MIME is not trusted. Signatures and actual decoder codec
   must match the filename. Browser MIME spoofing cannot bypass this check.
4. Image bytes are capped at min(configured image limit, 5,000,000). The default
   configured cap remains 4 MiB (4,194,304 bytes), deliberately unchanged. 2.42 MB
   is below either threshold. The bounded multipart request may be larger because
   the shared route also supports larger documents/video, but image validation
   still applies its image-specific cap.
5. PNG structure and every chunk CRC are checked. Animated PNG is rejected. Both
   PNG and JPEG dimension headers are now checked before decoder allocation; the
   existing 20,000,000-pixel maximum remains. PNG standard bit-depth/color-type
   combinations are validated. Actual image decoding still runs with one thread
   and strict error detection, and decoded dimensions must match the stream.
6. The isolated worker keeps its existing 512 MiB virtual-address limit, five CPU
   seconds, zero file-write budget, pipe-only decoder protocols and no app secrets.
   The parent retains a ten-second wall-clock timeout. A small compressed image
   can still represent an excessive decoded image; the byte limit alone is not
   a decompression-bomb defense. None of these protections were raised or removed.
7. After validation, reply-window checks, fingerprint/idempotency and committed
   send claims precede the fixed-Graph media upload. Only a successful media ID
   can progress to the separate customer-message submission. Both operations
   recheck permissions; uncertain sends are not replayed automatically. Existing
   bounded responses, 30-second HTTP/5-second connect timeouts, no redirects and
   safe upload-error diagnostics remain unchanged.

## Cloud API compatibility

The existing JPEG/PNG MIME allowlist and 5,000,000-byte maximum are conservative
against WhatsApp Cloud API's documented 5 MB image limit. A configured lower cap
is an intentional application policy, not the Meta maximum. Browser appearance or
local decode success is not proof of Meta acceptance; export conventional 8-bit
RGB/RGBA PNG or JPEG when troubleshooting unusual image encodings.

Reference: https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media/#supported-media-types

This environment has no allowed Meta documentation host or production credentials,
so current provider documentation and this specific image's Meta compatibility
were not independently checked live. No Meta upload or message was attempted.
This particular reported error occurred before that provider-compatibility stage.

## Deployment and rollback

Deploy the reviewed application commit normally; no migration, dependency, image
size setting or audio setting change is required. Keep Global AI OFF. The parent
and worker files must be deployed together. Do not resend to a customer to test
validation; use generated local fixtures or inspect the original file offline.
Rollback restores the preceding application version; there is no database change.

Saved Voices, browser audio, production encoding and provider send logic are
untouched. Existing image limits, import-failure handling and sanitized native
library environment are preserved. Regression coverage uses mocked provider
transport and verifies that rejected images cause no upload or customer send.

The owner later supplied dimensions of 1080×1350 (~1.46 MP) and reported reducing
compressed file size from roughly 2.42 MB to 0.6 MB. Both sizes and those dimensions
are within the normal local caps. Another generated 1080×1350 PNG near 2.42 MB
passed the staff and admin paths using exactly the same bytes. Re-saving may alter
structure, CRCs, encoding or metadata, so the smaller copy does not establish a
size-limit root cause. The original bytes still have not been provided.

Validation results: 98 unique checks passed and 3 optional integration checks were
skipped across generated image validation, existing image/document/video/media
flows, Saved Voices and staff Variant C. Regressions cover 2.42 MB images, exact
reported dimensions, staff/admin parity, valid near-5 MB JPEGs when configured,
configured byte limits, pixel bombs, CRC/structure, color modes, simulated memory/
wall-time/process interruption and no provider calls for rejected content.
