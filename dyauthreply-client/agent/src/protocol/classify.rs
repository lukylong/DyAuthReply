//! Conservative delivery classification for an already-observed send response.

use serde::{Deserialize, Serialize};

use super::im::SendMessageResponse;

const HARD_RISK_CODES: [i64; 4] = [60021, 7905, 7911, 8610];
const EXPIRED_MESSAGES: [&str; 2] = ["unexepcted session length", "unexpected session length"];

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DeliveryClass {
    Delivered,
    DeliveredSoft,
    LoginExpired,
    RiskControlled,
    ProtocolRejected,
    BusinessRejected,
    Inconclusive,
    Uncertain,
}

#[must_use]
pub fn classify_delivery(
    http_status: Option<u16>,
    result: &SendMessageResponse,
    expected_client_msg_id: &str,
) -> DeliveryClass {
    if http_status == Some(401) {
        return DeliveryClass::LoginExpired;
    }
    let acknowledged = result.server_msg_id != 0
        && !expected_client_msg_id.is_empty()
        && result.client_msg_id == expected_client_msg_id;
    // A hard business rejection remains authoritative. raw_check=2 alone is
    // not: the live Python sender historically accepts a matching platform
    // acknowledgement for non-hard business results. Require a non-zero
    // business status and no acknowledgement before latching account risk.
    let explicit_risk = HARD_RISK_CODES.contains(&result.biz_status_code)
        || (result.biz_raw_check_code == 2 && result.biz_status_code != 0 && !acknowledged);
    if http_status == Some(403) {
        return if explicit_risk {
            DeliveryClass::RiskControlled
        } else {
            DeliveryClass::LoginExpired
        };
    }
    if !http_status.is_some_and(|status| (200..300).contains(&status)) {
        return DeliveryClass::Inconclusive;
    }

    let status_message = result.status_msg.to_lowercase();
    if EXPIRED_MESSAGES
        .iter()
        .any(|marker| status_message.contains(marker))
    {
        return DeliveryClass::LoginExpired;
    }
    if !result.outer_status_present {
        return DeliveryClass::Uncertain;
    }
    if result.status_code != 0 {
        return DeliveryClass::ProtocolRejected;
    }
    if explicit_risk {
        return DeliveryClass::RiskControlled;
    }
    if !result.has_response_body
        || !result.has_inner_response
        || !result.business_payload_valid
        || result.server_msg_id == 0
        || expected_client_msg_id.is_empty()
        || result.client_msg_id != expected_client_msg_id
    {
        return DeliveryClass::Uncertain;
    }
    if result.biz_status_code == 8101 {
        return if result.biz_status_text.is_empty() {
            DeliveryClass::Delivered
        } else {
            DeliveryClass::BusinessRejected
        };
    }
    if result.biz_status_code != 0 {
        return DeliveryClass::DeliveredSoft;
    }
    DeliveryClass::Delivered
}

/// Current send-admission policy, separate from the frozen migration classifier.
/// A decoded `raw_check=2` stops further sends even if the server allocated an
/// ID. This conservative policy mirrors the live Python transport; it does not
/// assert that every such historical acknowledgement was undelivered.
#[must_use]
pub fn classify_live_delivery(
    http_status: Option<u16>,
    result: &SendMessageResponse,
    expected_client_msg_id: &str,
) -> DeliveryClass {
    let historical = classify_delivery(http_status, result, expected_client_msg_id);
    let valid_risk_marker = result.has_response_body
        && result.has_inner_response
        && result.business_payload_present
        && result.business_payload_valid
        && result.biz_raw_check_code == 2;
    let accepted_envelope = result.outer_status_present
        && result.status_code == 0
        && matches!(
            historical,
            DeliveryClass::Delivered
                | DeliveryClass::DeliveredSoft
                | DeliveryClass::BusinessRejected
                | DeliveryClass::Uncertain
        );
    if valid_risk_marker && (http_status == Some(403) || accepted_envelope) {
        DeliveryClass::RiskControlled
    } else {
        historical
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn complete_response() -> SendMessageResponse {
        SendMessageResponse {
            status_code: 0,
            status_msg: "OK".to_owned(),
            server_msg_id: 1,
            client_msg_id: "client".to_owned(),
            biz_status_code: 0,
            biz_status_text: String::new(),
            biz_raw_check_code: 0,
            outer_status_present: true,
            has_response_body: true,
            has_inner_response: true,
            business_payload_present: false,
            business_payload_valid: true,
        }
    }

    #[test]
    fn live_policy_raw_check_two_precedes_business_success_and_acknowledgement() {
        for business in [0, 8101, 8513, 9999] {
            for server_id in [0, 12345] {
                for client_id in ["client", "mismatched", ""] {
                    let mut response = complete_response();
                    response.business_payload_present = true;
                    response.biz_status_code = business;
                    response.biz_raw_check_code = 2;
                    response.server_msg_id = server_id;
                    client_id.clone_into(&mut response.client_msg_id);
                    assert_eq!(
                        classify_live_delivery(Some(200), &response, "client"),
                        DeliveryClass::RiskControlled
                    );
                }
            }
        }
    }

    #[test]
    fn live_policy_retains_auth_transport_and_malformed_response_boundaries() {
        let mut response = complete_response();
        response.business_payload_present = true;
        response.biz_raw_check_code = 2;
        assert_eq!(
            classify_live_delivery(Some(401), &response, "client"),
            DeliveryClass::LoginExpired
        );
        assert_eq!(
            classify_live_delivery(Some(503), &response, "client"),
            DeliveryClass::Inconclusive
        );
        assert_eq!(
            classify_live_delivery(None, &response, "client"),
            DeliveryClass::Inconclusive
        );
        assert_eq!(
            classify_live_delivery(Some(403), &response, "client"),
            DeliveryClass::RiskControlled
        );
        response.business_payload_valid = false;
        assert_eq!(
            classify_live_delivery(Some(200), &response, "client"),
            DeliveryClass::Uncertain
        );
        response.business_payload_valid = true;
        response.biz_raw_check_code = 0;
        assert_eq!(
            classify_live_delivery(Some(200), &response, "client"),
            DeliveryClass::Delivered
        );
        response.biz_status_code = 8513;
        assert_eq!(
            classify_live_delivery(Some(200), &response, "client"),
            DeliveryClass::DeliveredSoft
        );
    }

    #[test]
    fn http_auth_precedes_transport_inconclusive() {
        let response = complete_response();
        assert_eq!(
            classify_delivery(Some(401), &response, "client"),
            DeliveryClass::LoginExpired
        );
        assert_eq!(
            classify_delivery(None, &response, "client"),
            DeliveryClass::Inconclusive
        );
        assert_eq!(
            classify_delivery(Some(503), &response, "client"),
            DeliveryClass::Inconclusive
        );
    }

    #[test]
    fn protocol_expiry_precedes_outer_protocol_rejection() {
        let mut response = complete_response();
        response.status_code = 1;
        response.status_msg = "unexepcted session length".to_owned();
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::LoginExpired
        );
    }

    #[test]
    fn risk_control_precedes_missing_acknowledgement() {
        let mut response = complete_response();
        response.server_msg_id = 0;
        response.biz_status_code = 8610;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::RiskControlled
        );
        assert_eq!(
            classify_delivery(Some(403), &response, "client"),
            DeliveryClass::RiskControlled
        );
    }

    #[test]
    fn raw_check_two_does_not_override_a_matching_acknowledgement() {
        let mut response = complete_response();
        response.biz_raw_check_code = 2;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::Delivered
        );

        response.biz_status_code = 8513;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::DeliveredSoft
        );
    }

    #[test]
    fn raw_check_two_requires_rejection_without_acknowledgement() {
        let mut response = complete_response();
        response.server_msg_id = 0;
        response.biz_status_code = 8513;
        response.biz_raw_check_code = 2;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::RiskControlled
        );

        response.biz_status_code = 0;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::Uncertain
        );
    }

    #[test]
    fn missing_or_mismatched_acknowledgement_is_uncertain() {
        let mut response = complete_response();
        response.server_msg_id = 0;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::Uncertain
        );
        response.server_msg_id = 1;
        assert_eq!(
            classify_delivery(Some(200), &response, "different"),
            DeliveryClass::Uncertain
        );
        assert_eq!(
            classify_delivery(Some(200), &response, ""),
            DeliveryClass::Uncertain
        );
    }

    #[test]
    fn soft_and_idempotent_business_results_are_distinct() {
        let mut response = complete_response();
        response.biz_status_code = 8513;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::DeliveredSoft
        );
        response.biz_status_code = 8101;
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::Delivered
        );
        response.biz_status_text = "rejected".to_owned();
        assert_eq!(
            classify_delivery(Some(200), &response, "client"),
            DeliveryClass::BusinessRejected
        );
    }
}
