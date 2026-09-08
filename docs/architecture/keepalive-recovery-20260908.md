# Native keepalive recovery contract

## Scope and findings

The platform login, platform send observation, client license, account lease and
socket health are separate states. A successful immediate send is not a
long-running acceptance test.

The September 8 investigation found:

1. Account lease HTTP 401/403 terminated the controller while the independent
   client-license task later renewed successfully. Both accounts stayed offline.
2. A retired local epoch could still exist on the server. Acquiring before
   releasing it could return the same epoch, which the local store must reject.
3. An expired license at startup could omit the hosted controller entirely,
   leaving nothing to recover after license renewal.
4. A five-minute restore-only expiry of send evidence silently disabled automatic
   replies after idle restarts, unlike uninterrupted operation.
5. Account cards could display historical sendability while ownership was lost.

## Required behavior

- Immediately withdraw permissions on authorization denial; do not keep sending
  with the previously granted rights.
- Retain a dormant controller which observes the bounded owner-only authorization
  snapshot. Do not repeatedly submit the rejected token.
- Require the same activation and authority, an active/grace snapshot, a changed
  authorization token after denial, and a newly verified signed server grant.
- Retain queued release operations across subsequent authorization denials.
  Release retired server epochs before reacquiring; never revive a retired local
  epoch or clear account credentials to manufacture recovery.
- Expired, correctly signed licenses may create dormant recovery configuration;
  that configuration is not an entitlement. Revoked/invalid activation does not
  grant business access. Signature and account binding checks remain mandatory.
- Successful delivery is historical evidence bound to the exact credentials.
  Restoring it does not refresh its timestamp. Current verified platform identity,
  valid lease, user automation policy and business admission still gate sends.
  Changed credentials, newer explicit rejection and authentication expiry are not
  overwritten by old success.
- Show lease recovery separately from platform login errors.

## Release acceptance

Run the agent all-target tests, Clippy, frontend tests/build, and real local
client-to-remote license renewal. Verify unchanged boot and credential generation
across renewal, and matching sender/recipient message IDs on both accounts.
Also test authorization denial/rotation, expired startup, release/reacquisition,
idle restart, uncertain delivery deduplication and revoked authorization.

Windows installer smoke testing is not Windows business testing. Windows real
login/renewal/send and physical sleep/wake still require their own evidence.
Do not publish a tag merely because the compiler or immediate send succeeded.

## Regression commands

```sh
cargo test --manifest-path dyauthreply-client/agent/Cargo.toml --all-targets
cargo clippy --manifest-path dyauthreply-client/agent/Cargo.toml --all-targets -- -D warnings
node --test dyauthreply-client/client-ui/src/utils/accountLease.test.mjs
npm run ui:build --prefix dyauthreply-client
```
