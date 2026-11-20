# Print Agent trusted-proxy fix — implementation report

## Change

The former native TLS guard used `request.url.scheme` alone. The deployed Procfile did not configure Railway peers as trusted Uvicorn proxies. The default Uvicorn middleware therefore preserves HTTP on an HTTP hop from a non-loopback TLS edge, explaining the reported pre-authentication TLS 403. This is reproduced in regression tests; actual live peer/header/runtime inspection was unavailable from Codex.

Files:

- `Procfile`: explicitly disables Uvicorn proxy rewriting so original transport peer/scheme remain available for authorization, including when an inherited forwarded-allowlist environment setting is a wildcard.
- `print_agent_security.py`: native-only HTTPS/proxy boundary validation, exact peer allowlist, public authority binding, strict forwarding/origin checks. Never trusts forwarded client IPs for authorization or throttling.
- `settings.py`: empty-by-default `PRINT_AGENT_TRUSTED_PROXY_IPS` and `PRINT_AGENT_PUBLIC_ORIGIN`; no hardware/Shopify write-gate changes.
- `hardware_routes.py`: same TLS helper for pairing and authenticated native routes; authentication, pairing rate limiting, expiry, activation and authorization remain in the existing service.
- `tests/test_print_agent_proxy.py`: real-route pairing/auth tests plus a real Uvicorn HTTP-hop/startup regression. No Windows I/O, financial checkout or Shopify call is used by the new tests.
- `docs/print-agent-trusted-proxy.md` and README: security assumptions, rollout, sanitized checks and rollback.

No database migration or dependency changes. `hardware_service.py`, device token lifecycle, immutable hardware intent/audit and financial/Shopify integration logic are unchanged. Existing no-replay protections remain exercised by regression tests.

## Verification

- Proxy-focused final run: **34 passed, 0 failed**. Covers proxied HTTPS reaching 401 authentication, no credentials, valid/invalid/expired/used pairing, inactive device rejection/admin activation, native HTTPS, direct HTTP, arbitrary/private neighboring peers, Origin (including empty/null), duplicate/chained/conflicting headers, wrong public Host, wildcard/CIDR rejection, original-peer rate-limit identity, forwarded-IP rotation, and the actual Procfile flags.
- Initial combined hardware/agent/proxy/Windows API run: **88 passed** before the final two proxy regressions were added. Counts overlap; do not sum them as unique coverage.
- Final broad hardware/POS/accounting, Retail/scanner, inventory, Shopify Draft Orders/payments/fulfillment, staff and WhatsApp regression run: **451 passed, 2 skipped, 0 failed**. Both skipped engine-specific cases passed separately on PostgreSQL (**1 Draft Order concurrency + 1 fulfillment concurrency**). The run also includes PostgreSQL hardware/no-replay, POS and inventory tests. Existing deprecation warnings remain.
- Python compile checks and staged whitespace checks passed. All test databases are disposable; Shopify and Windows hardware are mocked. Counts overlap across runs and must not be summed as unique coverage.

## Production verification outstanding

The task environment could not reach Railway documentation or the deployment (network 403/CONNECT denial). There is no claim that production now returns 401. The code is deliberately blocked until an administrator approves exclusive edge TCP peer identities and sanitization of forwarded transport headers, supplies the exact addresses/public origin, and deploys with the explicit startup flag. If Railway uses a shared/unverifiable source identity, **do not configure it as trusted**; a separately authenticated ingress solution is required instead.

The Windows account, dependencies and queues are owner-verified context; they were not modified or retested physically here. No pairing code, credential, production environment variable, Shopify object, financial transaction, printer or cash drawer was changed. Cash-drawer automation remains default false and no code enables it. No merge or deployment is part of this task.

See [deployment and rollback instructions](print-agent-trusted-proxy.md). There is no schema rollback. Clearing the new settings fails closed without deleting pairings/audit/action fences; retaining `--no-proxy-headers` avoids restoring implicit environment-dependent trust.
