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
        None => receive_invariant_detail(error).into(),
    }
}

// Exact internal invariant names only. Never forward arbitrary error chains,
// SQL arguments, response bodies, account identifiers or credentials.
fn receive_invariant_detail(error: &anyhow::Error) -> &'static str {
    for cause in error.chain() {
        let description = match cause.to_string().as_str() {
            "conflicting server message projection" => {
                Some("聊天展示同步冲突：同一消息的方向或文本与已有记录不一致")
            }
            "legacy route conflict" => Some("旧聊天记录的会话映射与平台消息不一致"),
            "too many projection events" => Some("聊天展示同步失败：单批消息数量超过处理上限"),
            "invalid projection event" | "invalid message projection" => {
                Some("聊天展示同步失败：消息结构或长度不符合要求")
            }
            "projection byte counter mismatch" => Some("聊天展示存储的容量计数不一致"),
            "inbox cursor regressed" => Some("消息补拉游标发生回退，未推进接收位置"),
            "inbox page exceeds safe receive bounds at minimum limit" => {
                Some("消息补拉单组数据仍超过处理上限，已保留接收位置等待排查")
            }
            "invalid receipt evidence page" => Some("消息补拉结果不完整或超过单页处理上限"),
            "inbox response is not a valid success" => Some("消息补拉响应未通过完整性检查"),
            "invalid message identity" => Some("接收消息缺少有效的消息编号或会话编号"),
            "history baseline has no usable cursor" => Some("首次消息补拉缺少有效游标"),
            "stale receive lease" | "stale receive result" => {
                Some("接收期间账号状态或托管授权已变化，本轮结果未采用")
            }
            _ => None,
        };
        if let Some(description) = description {
            return description;
        }
    }
    if error.downcast_ref::<rusqlite::Error>().is_some() {
        "聊天展示数据库操作失败"
    } else if error.downcast_ref::<serde_json::Error>().is_some() {
        "接收消息的结构化内容解析失败"
    } else {
        "未归类的接收异常，请核对本地日志"
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
    fn receive_invariant_diagnostics_name_only_known_safe_failures() {
        let error = anyhow::anyhow!("conflicting server message projection")
            .context("private outer details");
        assert_eq!(
            receive_failure_detail(&error),
            "聊天展示同步冲突：同一消息的方向或文本与已有记录不一致"
        );
        assert!(!receive_failure_detail(&anyhow::anyhow!(
            "conflicting server message projection PRIVATE_TOKEN"
        ))
        .contains("PRIVATE"));
        assert!(
            receive_failure_detail(&anyhow::anyhow!("inbox cursor regressed")).contains("游标")
        );
    }
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
