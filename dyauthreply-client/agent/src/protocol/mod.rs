//! Offline, side-effect-free parity for the frozen Douyin PC IM send wire format.
//!
//! This module deliberately contains no HTTP client, credential loader, account
//! storage, or retry loop.  It can only encode synthetic request values, decode
//! response bytes, classify an already-observed result, and verify the embedded
//! corpus.

pub mod classify;
pub mod fixtures;
pub mod im;
pub mod wire;

pub use classify::{classify_delivery, DeliveryClass};
pub use fixtures::{verify_embedded_corpus, ParityReport};
pub use im::{
    decode_send_message_response, encode_send_message_request, ExtensionInput, SendMessageResponse,
    SendRequestInput, BUILD_ID, SDK_VERSION,
};
