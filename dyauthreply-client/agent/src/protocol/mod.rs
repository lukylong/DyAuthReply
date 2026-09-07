//! Protocol implementation layers. Wire/request-plan modules remain pure and
//! offline. Explicit live modules provide in-process signing and fenced HTTP
//! execution; the default account worker remains shadow-disabled until hosted
//! lease, credentials, inbound transport and UI migration gates are complete.

pub mod classify;
pub mod fixtures;
pub mod http_fixtures;
pub mod http_plan;
pub mod im;
pub mod wire;

pub use account_session::{VerifiedSelf, WorkItem, WorksPage};
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

pub mod live_http;
pub mod live_sender;
pub mod native_signer;

pub mod account_session;
pub mod credentials;
pub mod dtrait;
pub mod inbox;

pub mod frontier;
