# Captured web IM contract (0.1.40)

## Sender and signing

V2 capture retains raw host-scoped Cookie headers and browser profile metadata.
The current main-site sender uses SDK 0.1.8, build `0d50935:feat/pc-im-group`,
refer 1, and ordinary text aweType 700. Sign the complete ordered query
`verifyFp`, `fp`, `msToken` before appending `a_bogus`; do not append unsigned
fingerprint fields afterward. Sign with the actual saved UA and captured geometry.

The observed current signing context uses minor 0, page 6241, aid 6383 and
flags 2/2/2. Fixed-clock synthetic vectors compare against the independently
pinned Python oracle. The legacy signer remains for legacy capture; missing
geometry is not filled with invented device values.

Capture producers (native quick login and extension 2.3.1) export the same actual
geometry, platform, screen, CPU, memory, language and timezone fields. Existing
captures without those fields may need recapture; an application update alone
cannot reconstruct absent browser state.

## Delivery and account state

A positive platform message ID is not proof of recipient-visible content.
The experimental creator send path returned 8101/raw=1 and IDs while the recipient
body was empty. That path is not the released sender. Live 8101 is a message-level
business rejection; frozen historical corpus interpretation stays independent.
7467 is recipient-specific, not account-wide expiry. Do not automatically resend
failed or uncertain historical attempts. Do not infer sendability from a healthy
receive channel or profile lookup.

Keep the verified 6383 push gateway. Matching sender business aid to a different
creator gateway was experimentally rejected; gateway and send envelope are not
interchangeable.

## Inbound recovery

The protocol request limit is not the returned event count. The bounded inbound
spool accepts up to 512 events within its existing 1 MiB bound, with fair-queue
pagination and replay/empty-page termination. Expose reconciliation errors
separately from login expiry.

Opening a conversation performs a bounded read repair: latest 20 messages,
4-second timeout, 5-second throttle, at most 16 throttle keys per account.
Validate conversation identity, protect credential rotation/deletion across
await, and project messages without advancing the inbox cursor or triggering
historical automatic replies. Retain cached content on failure.

## Acceptance

Before this release, local macOS acceptance verified exact recipient plaintext
in both directions, an automatically received reverse message, and a further
successful send after identity-cache expiry. A separate 245-second token-refresh
probe and 12 healthy samples over 660 seconds passed. This does not establish
universal account compatibility or long-term zero reconnects.

CI also runs captured-signature vectors, UA regression, extension isolation and
completeness, UI status wording, Rust tests/Clippy, shared protocol corpus,
bounded runtime simulations, and platform package/install checks.
