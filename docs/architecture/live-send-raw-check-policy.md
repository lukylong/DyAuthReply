# Live send policy: raw_check_code=2

The current live Python transport patch treats a valid raw_check_code=2 as a
stop-send signal even when the platform returns a server message ID. The Rust
live sender uses `classify_live_delivery` to apply the same conservative policy.

This is an application admission policy, not evidence of a new upstream wire
protocol, and not proof that the entire account is banned or that every such
message was undelivered. Transport success and server-assigned IDs are not
recipient-visible delivery proof.

`classify_delivery` remains the frozen migration/reference classifier. The
original request/response corpus, HTTP-plan digests and upstream revision are
unchanged. Historical acknowledged raw_check=2 cases may remain classified as
`delivered`/`delivered_soft` there, while current live admission stops sending.
Both layers have separate tests; reference parity alone does not validate the
current business policy.

Live behavior:
- Valid parsed business raw_check=2 on an accepted envelope, or parsed HTTP 403
  business rejection, becomes RiskControlled regardless of message IDs.
- HTTP 401 still means login expiry. Network failure/5xx remain inconclusive.
- Malformed business payloads are not invented risk evidence.
- Rejected attempts persist the numeric codes and do not expose a success ID.
- Existing runtime handling stops the unsent tail and retains receive-only
  operation. No credential reset, auto-retry, or risk-state clearing is added.

Tests use synthetic loopback HTTP responses only. No real messages are sent.
Do not release on offline results alone: remote authorization, recipient-visible
receipts and platform-specific runtime behavior need separate live validation.
