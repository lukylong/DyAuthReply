//! Refreshes only the captured device-trait envelope, never invents traits.
use super::native_signer::SigningError;
use aes::cipher::{block_padding::Pkcs7, BlockEncryptMut, KeyIvInit};
use base64::{engine::general_purpose::STANDARD, Engine};
use rand::{rngs::OsRng, RngCore};
use rsa::{pkcs1::DecodeRsaPublicKey, Pkcs1v15Encrypt, RsaPublicKey};
use serde::Serialize;

pub struct DtraitSession {
    key: [u8; 16],
    encrypted_key: Vec<u8>,
}
#[derive(Serialize)]
struct Payload<'a> {
    dtrait: &'a str,
    timestamp: u64,
    #[serde(rename = "sdkVersion")]
    sdk_version: &'static str,
    path: &'a str,
}
impl DtraitSession {
    /// # Errors
    /// Reports bounded cryptographic setup errors.
    pub fn new() -> Result<Self, SigningError> {
        use std::fmt::Write;
        let mut key = [0; 16];
        OsRng.fill_bytes(&mut key);
        let hex = key.iter().fold(String::new(), |mut s, b| {
            let _ = write!(s, "{b:02x}");
            s
        });
        let public = RsaPublicKey::from_pkcs1_pem(include_str!("dtrait_public.pem"))
            .map_err(|_| SigningError::InvalidServerKey)?;
        let encrypted_key = public
            .encrypt(&mut OsRng, Pkcs1v15Encrypt, hex.as_bytes())
            .map_err(|_| SigningError::Engine)?;
        Ok(Self { key, encrypted_key })
    }
    /// # Errors
    /// Rejects empty, oversized, or malformed captured device material.
    pub fn header(&self, path: &str, blob: &str, timestamp: u64) -> Result<String, SigningError> {
        let mut iv = [0; 16];
        OsRng.fill_bytes(&mut iv);
        self.header_with_iv(path, blob, timestamp, iv)
    }
    fn header_with_iv(
        &self,
        path: &str,
        blob: &str,
        timestamp: u64,
        iv: [u8; 16],
    ) -> Result<String, SigningError> {
        if !path.starts_with('/') || path.len() > 512 || blob.is_empty() || blob.len() > 65536 {
            return Err(SigningError::InputLimit);
        }
        let bytes = serde_json::to_vec(&Payload {
            dtrait: blob,
            timestamp,
            sdk_version: "1.0.0.16",
            path,
        })
        .map_err(|_| SigningError::InputLimit)?;
        let encrypted = cbc::Encryptor::<aes::Aes128>::new(&self.key.into(), &iv.into())
            .encrypt_padded_vec_mut::<Pkcs7>(&bytes);
        let mut data = iv.to_vec();
        data.extend(encrypted);
        Ok(format!(
            "d0_{}_{}",
            STANDARD.encode(&self.encrypted_key),
            STANDARD.encode(data)
        ))
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn path_envelope_matches_python_aes_reference() {
        let fixture: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/account_session.json"))
                .unwrap();
        let mut key = [0_u8; 16];
        for (i, v) in key.iter_mut().enumerate() {
            *v = u8::try_from(i).unwrap();
        }
        let session = DtraitSession {
            key,
            encrypted_key: vec![5; 256],
        };
        assert_eq!(
            session
                .header_with_iv(
                    "/passport/safe/get_identity_security_token/",
                    "captured-synthetic-设备",
                    1_700_000_000,
                    [42; 16]
                )
                .unwrap(),
            fixture["dtrait"]
        );
    }

    #[test]
    fn randomized_envelopes_are_path_bound_and_bounded() {
        let session = DtraitSession::new().unwrap();
        let a = session
            .header("/identity", "captured-synthetic", 1_700_000_000)
            .unwrap();
        let b = session
            .header("/identity", "captured-synthetic", 1_700_000_000)
            .unwrap();
        assert_ne!(a, b);
        assert!(a.starts_with("d0_"));
        assert!(session.header("not-a-path", "blob", 1).is_err());
    }
}
