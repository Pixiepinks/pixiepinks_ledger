# Windows Print Agent behind Railway HTTPS

## Diagnosis and scope

The reported public HTTPS `/api/print-agent/config` response is a TLS-related 403. The pre-fix Procfile invokes Uvicorn 0.30.6 without explicit proxy configuration. Its `ProxyHeadersMiddleware` trusts `127.0.0.1` by default (or `FORWARDED_ALLOW_IPS` if configured). A Railway TLS-terminating edge forwards HTTP to the container from a different socket peer, so the default middleware leaves `request.url.scheme` as `http`; the native-agent guard rejects the request before authentication. The added regression reproduces that behavior with an HTTP hop, a non-loopback peer and `X-Forwarded-Proto: https`.

This is consistent with the production evidence, not a claim that Codex inspected live Railway headers or settings. Direct access to the public deployment and Railway documentation was blocked by this execution environment's network boundary (403/CONNECT denial). The actual deployed Start Command, edge socket addresses and header sanitation contract still require operator verification. Do not infer trusted address ranges from examples in access logs.

Reference sources for administrator review:

- [Railway public networking](https://docs.railway.com/reference/public-networking): HTTPS public edge / TLS termination.
- [Railway private networking](https://docs.railway.com/reference/private-networking): private service connectivity is a separate boundary.
- [Uvicorn settings](https://www.uvicorn.org/settings/#http): proxy headers and forwarded allowlist.
- [Pinned Uvicorn 0.30.6 middleware](https://github.com/encode/uvicorn/blob/0.30.6/uvicorn/middleware/proxy_headers.py): inspected installed source and regression-tested default trust.

## Security design

The Procfile now explicitly passes `--no-proxy-headers`. This is mandatory: otherwise Uvicorn can replace the original socket peer/scheme before application authorization. It also prevents an inherited `FORWARDED_ALLOW_IPS=*` from implicitly authorizing arbitrary forwarding assertions. A dashboard Start Command override must use the same explicit flag.

`print_agent_security.require_native_https` is used only by the native print-agent routes. It does not globally rewrite request URLs, redirect schemes, cookies or client IPs. Other CRM, Shopify and webhook handlers are unchanged; the existing secure session cookie remains secure. A real Uvicorn regression confirms that an ordinary route's scheme is not rewritten.

- Direct HTTPS with no forwarding assertions remains supported.
- Direct HTTP with no trusted HTTPS assertion is rejected.
- Any Origin header, including empty, duplicate or `null`, is rejected.
- Forwarded HTTPS is accepted only from an **explicitly approved exact immediate TCP peer IP**, with a single `X-Forwarded-Proto: https` and the expected public Host.
- Optional `X-Forwarded-Host` must match the same public host; optional forwarded port must be 443. Duplicate, chained/conflicting scheme/host, RFC `Forwarded`, and unsupported forwarding assertions are rejected.
- There are no default trusted peers. Wildcards and CIDRs (including entire Railway/private network ranges) are rejected. Neighboring addresses and other private services are not implicitly trusted. Unknown or changed edge addresses fail closed.
- `X-Forwarded-For` is never used for peer authorization or rate-limit identity. Changing it cannot bypass pairing limits. Limits remain keyed to the hashed original peer; multiple agents behind one edge share those limits. Assess capacity before scaling beyond the initial workstation, without widening trust.
- Token checks, five-minute single-use pairing, activation/revocation, audit and permanent no-replay protections remain unchanged. No code logs forwarding headers, pairing codes, tokens or request bodies.

### Administrator approval required before trusting a Railway peer

An IP allowlist is safe only if each listed address identifies an authorized TLS-terminating proxy and cannot also be used by an untrusted private caller. Railway's public edge and its project/environment private network are not interchangeable trust domains. A private address, `*.railway.internal`, `100.64.*`, a valid public Host or possession of a forwarding header is **not** proof of edge identity.

The administrator must verify with Railway's current deployment/network documentation or support:

1. The exact immediate peer addresses seen by this service belong exclusively to the approved public edge, not shared source NAT also usable by project services/untrusted callers.
2. The public edge sanitizes/overwrites inbound `X-Forwarded-Proto` with the actual external transport, preserves the approved Host, and prevents HTTP callers from asserting HTTPS. Confirm the actual optional header shapes too; this implementation deliberately rejects ambiguous chains.
3. Only approved edge peers can reach the listening application with those source identities; inventory other project services, public TCP proxies and ingress paths. Do not expose the container port through an unreviewed TCP proxy.
4. Address stability/changes are managed. This intentionally uses exact IPs rather than assuming a stable, exclusive Railway subnet.

If exclusivity or sanitation cannot be established, **leave the allowlist empty**. Pairing remains blocked. Use a separately authenticated ingress or an operator-managed ingress with an established identity boundary in a future reviewed change; do not fall back to trusting every forwarded header. This task does not claim that host matching authenticates the proxy.

## Approved deployment procedure (not executed)

No migration, dependency update, Shopify permission, printer command or financial transaction is required.

1. Review/approve the PR, backup normal release records, and record current hardware flags and Start Command. Keep `POS_CASH_DRAWER_AUTOMATION_ENABLED=false`; do not change checkout/refund, inventory, barcode, Shopify or webhook settings. No new flag activates printing in this change.
2. Complete the boundary review above. Under authorized Railway administration, determine the actual immediate peer from existing access logs and Railway's exclusive ingress mapping. Do not request/log tokens or use a pairing code to discover the peer.
3. Only after approval, configure:

   ```text
   PRINT_AGENT_TRUSTED_PROXY_IPS=<approved exact peer IP>[,<another approved exact peer IP>]
   PRINT_AGENT_PUBLIC_ORIGIN=https://pixiepinksledger-production.up.railway.app
   ```

   Values are deployment-specific, not supplied as guessed Railway IPs here. These settings default to empty. Invalid configuration fails closed at the API. If the provider cannot guarantee suitable addresses, this allowlist mechanism cannot safely unblock production.
4. Deploy the reviewed commit through the normal approved process. Ensure the effective web command is:

   ```sh
   uvicorn main:app --host 0.0.0.0 --port "$PORT" --no-proxy-headers
   ```

   `Procfile` supplies this. Inspect any Railway dashboard Start Command override and running process. Do not add `--proxy-headers` or unrestricted forwarded trust. Do not print the process environment, which may contain secrets.
5. Perform the sanitized checks below without credentials. With an existing local-print gate enabled for approved pairing, public HTTPS config should return **401 Device authentication unavailable**, not TLS 403. If all hardware gates are off, the existing hardware-disabled 404 remains correct; do not enable any hardware gate merely to mask this condition.
6. Once the HTTPS/auth boundary is verified, an administrator may enroll a fresh five-minute code through the existing UI. Run the existing agent `pair` command under its dedicated Windows account and enter the code at the hidden prompt. Pairing alone remains inactive until separate administrator activation; do not activate/run/attest/print as part of this network fix. An expired/used/invalid code must return an authentication error, not bypass validation.

### Sanitized checks

An authorized operator may run these from a normal external workstation. They send no credentials, pairing codes, PDFs or hardware jobs:

```powershell
curl.exe --silent --show-error --include https://pixiepinksledger-production.up.railway.app/api/print-agent/config
curl.exe --silent --show-error --include -H "Origin: https://untrusted.example" https://pixiepinksledger-production.up.railway.app/api/print-agent/config
curl.exe --silent --show-error --include -H "X-Forwarded-Proto: https" http://pixiepinksledger-production.up.railway.app/api/print-agent/config
```

Expected: first 401 when hardware authentication is enabled; second 403; third a Railway HTTPS redirect/rejection or application 403, **never** an HTTP request authorized merely by the supplied header. Do not add `--location` for the third check, since following a redirect would hide the HTTP boundary. No live checks were executed successfully by Codex due network restrictions.

From an authorized **untrusted private service**, test the internal HTTP service with Host set to the public host and forwarded proto HTTPS, without credentials: it must return 403. Perform only the config GET; no pair code or mutation. If it returns 401, that private caller is being mistaken for a trusted ingress: clear the allowlist and stop rollout. Do not use the trusted edge itself as the negative-control source.

## Rollback and recovery

- Clear the two new proxy settings to fail closed and redeploy/restart through the authorized process. Existing pairing tokens and audit remain; do not delete them or reset action journals.
- Prefer retaining `--no-proxy-headers` when rolling back application code. Restoring the old Procfile restores its old scheme failure and can reintroduce environment-dependent forwarding trust; never compensate with wildcard trust.
- Keep cash-drawer automation disabled and stop any independently running agent before broader operational troubleshooting. Already queued hardware jobs retain their original expiry and authorization/no-replay fences; this fix does not create or reset them.
- No schema rollback or data edits are needed. Receipt/browser PDF fallback remains available. No Shopify orders, stock, Draft Orders or webhook changes form part of recovery.

## Verification record

Automated results are recorded in the PR and accompanying implementation report. Fixtures use isolated SQLite/PostgreSQL, fake Shopify and fake Windows hardware. Live proxy boundary/actual peer exclusivity, public header sanitation and production 401 remain operator verification requirements; they are not certified by mocked tests.
