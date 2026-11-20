# Railway Print Agent: isolation-backed ingress

## Decision and hard deployment boundary

PR #181's exact proxy IP policy is not appropriate for this Railway deployment. The owner confirms Railway supplies neither stable exclusive edge IPs nor signed edge-origin headers, and private workloads can supply forwarding headers. There is no application-only rule that distinguishes two otherwise identical HTTP requests from that shared network. Do not trust a private address class, Host, `X-Forwarded-Proto` or `FORWARDED_ALLOW_IPS=*` as proof.

The owner selected **prepare isolated ingress**. This change supports that option while preserving the existing public address:

```text
Windows agent (existing TLS-verifying client)
  -> HTTPS pixiepinksledger-production.up.railway.app
  -> Railway managed TLS edge (must sanitize transport headers)
  -> EXCLUSIVELY ISOLATED CRM service ingress
  -> railway_ingress:app (controlled ASGI entrypoint)
  -> existing native pairing/authentication routes
```

**Do not enable the new mode in the current shared environment.** Network isolation and edge header sanitation are prerequisites, not effects of an application setting. Until the administrator establishes them, production pairing correctly remains 403. A GitHub merge/deploy alone does not complete the infrastructure work.

No Railway management connection is available to Codex. Topology facts above come from the owner's discovery and the checked-in Procfile/settings/security code; they are not a claim that live private-network access, service configuration or provider guarantees were inspected. Actual public 401 and private-network exclusion still require administrator acceptance.

## Why this option

| Option | Decision |
| --- | --- |
| Trust all Railway/private sources or a guessed subnet | Rejected: private callers can impersonate the edge. |
| HMAC/signing sidecar on the same shared ingress | Insufficient: cryptography authenticates the signer, but it would sign an attacker-supplied transport assertion. |
| Authenticated TLS-terminating ingress that actually verifies external TLS and signs the complete method/path/body/authority with replay protection | Viable alternative infrastructure, but requires control of the TLS ingress and backend reachability. A gateway behind the same unsigned shared hop cannot supply that proof. Preserving this provider-owned generated hostname is not assumed possible with an unrelated external TLS endpoint. Requires a separate reviewed design if isolation cannot be obtained. |
| Isolate the existing service's ingress; consume sanitized Railway headers only inside that exclusive boundary | Selected. Retains the current hostname/client; no stable peer IPs or additional signing secrets required. |

The ASGI marker in this implementation is **not cryptographic Railway proof**. It only proves passage through our own controlled entrypoint inside one trusted process. Its security depends on the separately enforced network boundary.

## Implementation

- `Procfile` starts `uvicorn railway_ingress:app --host 0.0.0.0 --port $PORT --no-proxy-headers`.
- New `PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED=false` by default. The existing public-origin setting is reused. Exact proxy IP settings must be empty in this mode; mixing the two policies fails closed.
- When explicitly enabled, the entrypoint validates native API requests against the approved public Host, actual HTTPS or a single sanitized `X-Forwarded-Proto: https`, matching optional forwarded host/443, and no Origin or alternate/duplicate transport assertions. External pseudo-proof headers are rejected.
- It attaches a process-local identity object to a fresh ASGI scope. HTTP headers cannot mint it. The route guard requires that exact marker in this mode: bypassing the entrypoint by starting `main:app` directly is rejected even with forged HTTPS headers.
- The native route still performs existing token authentication, activation, one-use five-minute pairing, rate limiting, audit and no-replay checks. `X-Forwarded-For` never supplies authentication or rate-limit identity. Peer-keyed limits remain shared by callers using the same proxy hop; assess scale separately without trusting caller-provided IPs.
- No body proxying, second HTTP listener, buffering, logging, credential copying or global URL/client-IP rewriting. Other CRM routes and lifespan events pass through unchanged.
- `POS_CASH_DRAWER_AUTOMATION_ENABLED` remains default false. No hardware or financial operation is authorized by enabling ingress. No changes to Shopify, barcode synchronization, POS transactions, device credentials or hardware queues.

## Infrastructure changes requiring administrator approval

1. **Inventory every inbound path and workload.** Include other services, workers, databases, shells, preview deployments, public TCP proxies and users who can deploy arbitrary code into the environment. Merely naming a service “ingress” does not isolate it.
2. **Keep the existing public CRM service/domain intact.** Do not delete/recreate it or assume a generated `*.up.railway.app` domain can be transferred. Its public URL must remain exactly `https://pixiepinksledger-production.up.railway.app`.
3. **Exclude all other workloads from reaching its HTTP listener.** Use provider-enforced service-level isolation only if Railway explicitly supports and verifies it for this topology. Otherwise dedicate the existing service's environment/project to the trusted CRM process and move/reconfigure other workloads through a separately approved infrastructure plan. No other untrusted private caller may remain able to reach `$PORT`.
4. **Plan database/dependency connectivity before moving anything.** Existing private DB/worker connections may cross the proposed boundary. Obtain a reviewed secure connectivity plan and backups; do not move/recreate production databases, copy secrets, enable public DB access or change connection variables as an ad hoc pairing fix. This PR performs none of those operations. If dependencies prevent effective isolation while retaining the service/domain, stop and use a separately designed authenticated ingress.
5. **Verify Railway's public-edge contract.** The edge must set/overwrite the protocol assertion according to the real external connection, preserve the approved host and reject/redirect public HTTP even if a client sends `X-Forwarded-Proto: https`. It must not simply pass arbitrary client assertions. No signed edge header is assumed. Confirm optional header shapes against the strict validator.
6. **Review ongoing access/change controls.** Do not later add other services to this isolated network or expose a TCP/private ingress without disabling this mode and reviewing the boundary. A compromised trusted CRM process is outside this boundary; it already holds application privileges.

Network exclusion and domain preservation must be demonstrated by provider configuration and positive/negative controls, not inferred from one timeout or an empty list of currently visible services. An approval ticket should record the mappings and reviewed provider behavior, without tokens, pairing codes or secrets.

## Release sequence (not executed)

1. Review the PR, backups and infrastructure plan. Keep drawer automation false and existing hardware/Shopify/checkout/refund/inventory flags unchanged. Do not activate a device or run a print test as part of this network change.
2. Deploy reviewed code with the new mode still **false**. Verify the effective Start Command, including any Railway dashboard override, uses `railway_ingress:app` and `--no-proxy-headers` exactly as above. Deployment of the wrapper with the mode off preserves PR #181's fail-closed behavior.
3. Complete and accept the isolation and public-edge requirements above. Test reachability from each formerly co-located private workload. Any reachable private HTTP entrypoint is a stop condition. Do not enable the mode merely to make a probe pass.
4. Only after recorded approval, set:

   ```text
   PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED=true
   PRINT_AGENT_PUBLIC_ORIGIN=https://pixiepinksledger-production.up.railway.app
   PRINT_AGENT_TRUSTED_PROXY_IPS=
   POS_CASH_DRAWER_AUTOMATION_ENABLED=false
   ```

   This is an administrator action; Codex did not change production variables. No migration or dependency update is required. Existing receipt/label gates must already be separately approved for pairing; all-disabled hardware retains its existing 404 behavior.
5. Run the credential-free public check from an external workstation:

   ```sh
   python -m scripts.validate_print_agent_ingress
   ```

   Expected public HTTPS config: **401 `Device authentication unavailable`**. Origin requests: 403. Public HTTP with forged HTTPS assertion: 403 or a verified same-host HTTPS redirect, never 401. The script never follows redirects or sends credentials/codes. It never performs a pairing, print, financial or Shopify mutation. Ordinary server rate counters may advance.
6. From authorized negative-control sources outside the isolated boundary, probe the actual, independently verified internal URL:

   ```sh
   python -m scripts.validate_print_agent_ingress --private-url http://VERIFIED-INTERNAL-SERVICE:PORT
   ```

   **Any HTTP response from that private entrypoint means isolation failed**, including 401. Disable the mode and stop. An unreachable address is only `UNREACHABLE_NOT_CERTIFIED`: DNS mistakes/outages also look unreachable. Corroborate the target mapping and network rules with an authorized positive control/provider configuration. The script intentionally returns exit 2 for operator review when no failure was detected; it never certifies isolation automatically.
7. Only after network acceptance, enroll a new five-minute code using the existing CRM UI. Under the already configured dedicated Windows account, run the **unchanged** current agent against the same address:

   ```powershell
   python -m windows_print_agent pair --server https://pixiepinksledger-production.up.railway.app
   ```

   Enter the code at its hidden prompt; do not put it in command arguments, logs or chat. Pairing remains inactive until separate administrator activation. This task does not authorize running the agent or opening the drawer. Invalid, used and expired codes remain rejected.

## No purchased code-signing certificate

Railway's existing public TLS certificate and the agent's normal certificate verification are retained. Transport TLS certificates and Windows executable code-signing certificates solve different problems; this pairing fix requires no purchased code-signing certificate, new Shopify app, agent credential or custom certificate authority.

The already prepared reviewed Python source/venv can run the pairing command. Keep the reviewed source and venv in administrator-owned directories, hash-verify their trusted release, give the dedicated agent user read/execute and normal profile storage only, and keep OS protections enabled. If a later startup task uses this source path, use Windows Task Scheduler's normal UI with the dedicated identity, the approved venv Python executable, arguments `-s -m windows_print_agent run`, and **Start in** the reviewed source directory after separate hardware acceptance. Do not store account passwords/codes in scripts or arguments. The existing packaged-executable installer retains its signature requirement; do not bypass that installer or claim an unsigned executable was signed. Source deployment does not require buying a certificate.

## Rollback

- Disable `PRINT_AGENT_RAILWAY_ISOLATED_INGRESS_ENABLED` first. With an empty exact-IP setting, native pairing fails closed again. Keep `--no-proxy-headers`, drawer automation false and normal browser/PDF fallbacks.
- Preserve network isolation until the mode is confirmed off. **Never restore shared private reachability while this mode is enabled.** An emergency rollback can turn the mode off without moving databases or changing service identity.
- Keep device tokens, pairing/audit history and local/server action fences intact. Do not reset a print or drawer attempt or re-pair to recover an uncertain action. No schema downgrade is needed.
- No merge, deployment, infrastructure move, production flag/secret change or live device action was performed during development.

## Acceptance limits

Automated tests demonstrate route/entrypoint controls and current agent protocol compatibility, not provider network isolation. A deliberate shared-network counterexample is included: an attacker who can still reach the approved entrypoint can imitate unsigned headers, and the deployment probe reports that topology as unsafe. This limitation is not hidden behind a signing sidecar or a green unit test. Actual isolation, edge sanitation, preserved URL and live public 401 remain administrator acceptance requirements.
