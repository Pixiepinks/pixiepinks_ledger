# Railway isolation-backed Print Agent ingress — report

## Delivered

Default-off controlled ASGI ingress for the existing Railway URL and unchanged current Windows Python agent. It requires exclusive network isolation plus Railway's sanitized public-edge HTTPS assertion; it does not assume stable edge IPs or signed provider headers.

Changed areas:

- `railway_ingress.py` and `Procfile`: dedicated controlled entrypoint, native-only validation, fresh process-local scope proof, normal CRM/lifespan pass-through, automatic Uvicorn forwarded trust disabled.
- `print_agent_security.py`, `settings.py`: new explicit isolation-approved mode, default false; empty exact-IP policy in this mode; raw backend/start-command bypass rejects headers without the in-process marker; public host, protocol and Origin validation retained.
- `scripts/validate_print_agent_ingress.py`: credential-free public/negative-private checks. Any reachable private entrypoint fails; timeouts never automatically certify isolation. No pairing or print/financial/Shopify mutation.
- `tests/test_railway_print_ingress.py`: application and real Uvicorn socket tests, unchanged native auth/pairing behavior, forged proof headers, backend bypass, disabled/shared mode, conflicting modes and probe detection of reachable private ingress.
- Documentation/README: selected architecture, rejected signing-sidecar shortcut, administrator isolation/connectivity approvals, exact release/rollback steps and reviewed Python-source use without buying a code-signing certificate.

No database migration, dependency upgrade, new client protocol or new secret. `hardware_service.py`, Windows TLS verification, device pairing/authentication/activation, audit, five-minute expiry, worker/printer coordination, drawer replay fences, financial/Shopify/barcode logic and existing write flags are unchanged. Cash-drawer automation remains default false. No live configuration, infrastructure or data was changed.

## Security assumptions and blocker

The owner selected “Prepare isolated ingress”; this authorizes preparing code/runbooks, **not enabling the mode on the current shared deployment**. The public CRM service/domain must remain intact while administrator-approved infrastructure excludes every other workload from its HTTP listener and verifies Railway overwrites transport assertions. Existing DB/worker connectivity must be planned safely before any infrastructure changes. No Railway management connector is available here; production topology/configuration facts are owner-supplied plus repository inspection, not a live provider audit.

A scope marker is not cryptographic edge proof. If any private attacker can still reach the approved entrypoint, it can imitate unsigned edge headers. A deliberate regression demonstrates this counterexample and the deployment probe rejects the reachable-private case. This is why enabling an application flag alone is not a secure fix. If the provider cannot establish isolation while preserving the current service/URL and dependency connectivity, keep this mode off and obtain a separately reviewed authenticated TLS ingress; do not substitute a signing sidecar that trusts the same unsigned input.

## Verification record

- Final ingress-specific run: **25 passed, 0 failed**, including actual Procfile/Uvicorn startup, negative-control probe behavior and the unchanged Windows Transport pairing/configuration at the existing HTTPS origin. The socket test uses a fake backend so no background financial worker runs.
- Broad hardware/POS/accounting, Retail/scanner, inventory, Shopify Draft Order/payment/fulfillment, staff and WhatsApp regression: **475 passed, 2 skipped, 0 failed**. Both skipped PostgreSQL-engine-only cases passed separately on disposable PostgreSQL (**1 Draft Order concurrency + 1 fulfillment concurrency**). PostgreSQL hardware/no-replay, POS and inventory tests are also included in the broad run.
- Python compile checks, CLI help and staged whitespace checks passed. Existing deprecation warnings remain. Counts overlap and must not be summed as unique tests. All databases are disposable; Shopify and Windows hardware are mocked.

Live acceptance remains pending: isolated network exclusion, correct target mappings, provider edge sanitation, preserved public domain, public config **401**, private negative controls, and actual pairing from the Windows workstation after approval. No production HTTP/physical acceptance or deployment is claimed. See [operational guide and rollback](print-agent-railway-ingress.md).
