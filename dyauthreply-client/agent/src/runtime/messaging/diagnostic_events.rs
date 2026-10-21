//! Diagnostic context only. Never changes classification, admission or retry policy.
use super::{OutboundBatch, WorkEnvelope, WorkKind};
use crate::{
    protocol::{live_sender::SendOutcome, DeliveryClass},
    store::OutboundSegment,
};

pub(super) fn message_source(kind: WorkKind) -> &'static str {
    if kind == WorkKind::AutomaticReply {
        "automatic"
    } else {
        "manual"
    }
}
fn content_kind(payload: &str) -> &'static str {
    let text = payload.trim();
    if !text.chars().any(char::is_whitespace)
        && text.parse::<wreq::Uri>().is_ok_and(|uri| {
            matches!(uri.scheme_str(), Some("http" | "https")) && uri.host().is_some()
        })
    {
        "link"
    } else {
        "text"
    }
}

/// Only format typed error codes and static stages, never an arbitrary error chain/body.
pub(super) fn receive_failure_detail(error: &anyhow::Error) -> String {
    use crate::protocol::account_session::AccountRequestError;
    match error.downcast_ref::<AccountRequestError>() {
        Some(AccountRequestError::AccountMismatch) => "登录账号身份不匹配".into(),
        Some(AccountRequestError::AuthenticationExpired { step, code }) => {
            format!("{step}：登录已失效（状态码 {code}）")
        }
        Some(AccountRequestError::Transport { step }) => format!("{step}：网络请求失败"),
        Some(AccountRequestError::Signing { step }) => format!("{step}：签名材料未就绪"),
        Some(AccountRequestError::Http { step, status }) => format!("{step}：HTTP {status}"),
        Some(AccountRequestError::Business { step, code }) => format!("{step}：业务状态 {code:?}"),
        Some(AccountRequestError::Decode { step }) => format!("{step}：响应结构异常"),
        None if error.downcast_ref::<crate::store::StoreError>().is_some() => {
            "本地消息存储操作失败".into()
        }
        None => "未归类的接收异常，请核对本地日志".into(),
    }
}
pub(super) fn log_send_outcome(
    work: &WorkEnvelope,
    batch: &OutboundBatch,
    segment: &OutboundSegment,
    outcome: &SendOutcome,
    elapsed_ms: u64,
) {
    let codes = outcome.response_codes.as_deref().unwrap_or("");
    macro_rules! emit {
        ($level:expr, $message:literal $(, $extra:ident = $value:expr)*) => {
            tracing::event!($level, account_id = %work.account_id, batch_id = %batch.id,
                message_source = message_source(work.kind), segment_index = segment.ordinal.saturating_add(1),
                segment_count = batch.segments.len(), content_kind = content_kind(&segment.payload),
                content_chars = segment.payload.chars().count(), attempt = segment.attempt_count.saturating_add(1),
                elapsed_ms, classification = ?outcome.classification, response_codes = codes, $($extra = $value,)* $message)
        };
    }
    match outcome.classification {
        DeliveryClass::Delivered | DeliveryClass::DeliveredSoft => {
            emit!(tracing::Level::INFO, "native send completed");
        }
        DeliveryClass::RiskControlled => emit!(
            tracing::Level::WARN,
            "platform risk control rejected the send; sending paused until recheck",
            recheck_minutes = crate::store::RISK_RECHECK_MS / 60_000
        ),
        _ => emit!(tracing::Level::WARN, "native send was not delivered"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn diagnostic_receive_error_reports_typed_status_not_private_error_body() {
        let error = anyhow::Error::new(
            crate::protocol::account_session::AccountRequestError::Http {
                step: "inbox",
                status: 403,
            },
        );
        assert_eq!(receive_failure_detail(&error), "inbox：HTTP 403");
        let secret = anyhow::anyhow!(
            "sessionid=PRIVATE_SECRET https://example.com/private?token=PRIVATE_TOKEN"
        );
        let message = receive_failure_detail(&secret);
        assert!(!message.contains("PRIVATE") && !message.contains("example.com"));
        assert!(message.contains("未归类"));
    }
    #[test]
    fn diagnostic_content_kind_never_guesses_cards_or_exports_payload() {
        assert_eq!(content_kind("https://example.com/private?a=secret"), "link");
        assert_eq!(content_kind("正文 https://example.com/private"), "text");
        assert_eq!(content_kind("file:///secret"), "text");
        assert_eq!(content_kind("ordinary reply"), "text");
        assert_eq!(message_source(WorkKind::AutomaticReply), "automatic");
        assert_eq!(message_source(WorkKind::ManualSend), "manual");
    }
}
