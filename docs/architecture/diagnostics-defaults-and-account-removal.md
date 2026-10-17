# Diagnostics defaults and account deletion contracts

## Diagnostics

- No saved preference: enable the existing redacted uploader after configuration load.
- A saved boolean `false` is an explicit opt-out and remains off across upgrades/restarts.
- Invalid/unreadable preference data stays off; initialization before configuration load stays inert.
- Keep the existing allow-listed snapshot/events, account hashing, bounded spool and activation authentication. Do not upload message bodies or credentials for a more readable UI.
- Management UI separates data freshness from device/account state. More than 35 minutes without a report is **unknown/stale**, not proof of a logged-out account.
- Runtime, identity/ownership, receive, send and automatic-reply states stay separate. Historical error totals do not mean an active incident. An enabled automatic-reply task is not proof of delivery.
- Operational metrics are optional: missing values display unknown, not zero. Raw diagnostic payloads remain available only as collapsed technical details.

## Account removal

- Every explicit rule binding blocks account deletion, even for disabled rules. Legacy single-account references also count.
- Global rules with no explicit binding and bindings to other accounts do not block deletion.
- A rejected deletion must leave credentials, rules, policy and runtime unchanged. Users must unbind deliberately; deletion never silently edits a bound rule.
- Native checks run inside the same configuration write gate used by rule edits, before the registry tombstone and policy removal. Cover both loaded and dormant accounts. Removed account policies prevent rebinding after deletion.
- Hosted deletion and binding changes lock the account row in transactions, so a new binding cannot race past deletion. Both single and batch deletion enforce the guard; batch responses retain blocked IDs.
- UI preflight is advisory and may become stale. Server/native service checks are authoritative.

## Regression coverage

Native: missing preference / explicit off / corrupt preference, disabled and global rules, loaded/dormant account removal, and rebinding deleted accounts. Hosted: disabled/legacy bindings, single/batch deletion without side effects, and missing accounts. Presentation: stale clocks, incomplete metrics, send pending vs receive reconnect vs auto pause. All destructive tests use synthetic temporary stores, not real accounts.
