# Progress

- 2026-04-24: Identified missing persistent verification state on `DouyinAccount`.
- 2026-04-24: Identified worker relogin loop as the main place to pause retries while manual verification is pending.
- 2026-04-24: Added `pending_verification_*` fields, migration, and API resets on manual login/logout.
- 2026-04-24: Worker now pauses auto-relogin during manual verification cooldown and demotes online accounts whose business pages are no longer usable.
- 2026-04-24: Login validity is no longer tied only to a narrow strong-cookie allowlist; business-shell checks now participate before final IM verification.
