//! Offline, side-effect-free parity for the frozen Douyin PC IM send wire format.
//!
//! This module deliberately contains no HTTP client, credential loader, account
//! storage, or retry loop.  It can only encode synthetic request values, decode
//! response bytes, classify an already-observed result, and verify the embedded
//! corpus.

pub mod classify;
pub mod fixtures;
pub mod http_fixtures;
pub mod http_plan;
pub mod im;
pub mod wire;

pub use classify::{classify_delivery, DeliveryClass};
pub use fixtures::{verify_embedded_corpus, ParityReport};
pub use http_plan::{
    finalize_send_request, parse_plan_digest, percent_encode_rfc3986, prepare_send_request,
    FingerprintInput, OrderedHeader, RequestPlan, RequestPlanError, SendHttpPlanInput,
    SignerOutputs, TicketGuardCredential, UnsignedRequestPlan,
};
pub use im::{
    decode_send_message_response, encode_send_message_request, ExtensionInput, SendMessageResponse,
    SendRequestInput, BUILD_ID, SDK_VERSION,
};
