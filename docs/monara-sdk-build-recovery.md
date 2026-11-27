# Monara isolated SDK — selected Nixpacks configuration repair

## Confirmed cause

Production read-only evidence reported `NIXPACKS_CONFIG_FILE=nixpacks.voice-diagnostics.toml`,
no custom build command, requirements/worker files present, and the default isolated
interpreter absent. PR #218 installed the SDK only in `nixpacks.toml`; the explicitly
selected alternative configuration omitted it. This was a build-configuration omission,
not a missing API key. No settings or production files were changed to diagnose it.

Both supported configurations now install the same locked isolated SDK and execute
its offline `--capabilities` check **before** recording build provenance. A missing
interpreter, wrong SDK version or unsupported custom voice makes that build step fail.
The check prints public capability data only, with no key or provider request.

No application, authentication, provider payload, dependency pin, database migration
or feature-flag behavior changes. Existing FFmpeg installation/verification is retained.
No Railway variable change is needed to select a different build configuration.

## Owner-approved release

1. Review and merge this small PR only after approval. Both Monara gates, Global AI
   and existing production settings must remain as currently configured. Do not add
   the Gemini key or enable generation/sending for this repair.
2. Authorize deployment of the **new merged commit**. Merely restarting/redeploying
   the previous artifact will not add the missing environment. Retain the selected
   `nixpacks.voice-diagnostics.toml` configuration; no Console `pip install` workaround.
3. Check the build log for the isolated install followed by:

   ```text
   .monara-venv/bin/python monara_sdk_worker.py --capabilities
   ```

   Expected: SDK `3.6.1`, custom voice support true, exact approved voice, no SDK
   retries. No API key is required for this check.
4. After the deployment is healthy, run in the authorized Railway Console:

   ```bash
   cd /app
   python -m scripts.validate_monara_readonly
   ```

   Expected: migration `20261127_62`, SDK available/exact voice/custom-voice support
   true, verified FFmpeg, generation/send gates and policy false, Global AI false,
   no provider calls or sends. Missing API key remains expected at this stage.
5. Stop for review of the sanitized result before secure key configuration and any
   separately approved billable preview. This repair authorizes no production mutation,
   live generation, sending or activation.

## Failure and rollback

If installation or the offline capability check fails, preserve the failed build log
without secrets. Do not bypass the check, install packages interactively into the live
container, switch voices/models, or upgrade CRM Pydantic/httpx. Keep the currently
healthy deployment serving the CRM.

Rollback uses the prior accepted artifact through normal owner-approved controls.
It restores the previous missing-SDK limitation; Monara must remain disabled. There
is no database or data rollback and no new migration. Existing migration 62 remains.

Tests cover both configuration selectors, required step ordering, retained FFmpeg and
setup settings, locked requirements and the actual isolated SDK's credential-free
capability command. A local test is not proof of Railway final-image contents; the
post-deployment read-only verification is still required.
