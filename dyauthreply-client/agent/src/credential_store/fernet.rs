//! Fernet v0x80 compatibility. Authenticate before CBC decryption; no TTL for
//! migration, matching the legacy local-storage decoder. Login remains separately verified.
use aes::cipher::{block_padding::Pkcs7, BlockDecryptMut, BlockEncryptMut, KeyIvInit};
use anyhow::{anyhow, Result};
use base64::{
    engine::general_purpose::{URL_SAFE, URL_SAFE_NO_PAD},
    Engine,
};
use hmac::{Hmac, Mac};
use rand::{rngs::OsRng, RngCore};
use sha2::Sha256;

pub(super) const MAX_PLAINTEXT: usize = 1024 * 1024;
pub(super) const MAX_TOKEN: usize = 1_400_000;
pub(super) struct Key([u8; 32]);
fn invalid() -> anyhow::Error {
    anyhow!("invalid encrypted credential")
}
impl Key {
    pub(super) fn parse(raw: &[u8]) -> Result<Self> {
        let decoded = URL_SAFE
            .decode(raw)
            .or_else(|_| URL_SAFE_NO_PAD.decode(raw))
            .map_err(|_| invalid())?;
        Ok(Self(decoded.try_into().map_err(|_| invalid())?))
    }
    pub(super) fn generate() -> Self {
        let mut key = [0; 32];
        OsRng.fill_bytes(&mut key);
        Self(key)
    }
    pub(super) fn encoded(&self) -> String {
        URL_SAFE.encode(self.0)
    }
    pub(super) fn decrypt(&self, token: &[u8]) -> Result<Vec<u8>> {
        if token.len() > MAX_TOKEN {
            return Err(invalid());
        }
        let raw = URL_SAFE
            .decode(token)
            .or_else(|_| URL_SAFE_NO_PAD.decode(token))
            .map_err(|_| invalid())?;
        if raw.len() < 73 || raw[0] != 0x80 || (raw.len() - 57) % 16 != 0 {
            return Err(invalid());
        }
        let end = raw.len() - 32;
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.0[..16]).map_err(|_| invalid())?;
        mac.update(&raw[..end]);
        mac.verify_slice(&raw[end..]).map_err(|_| invalid())?;
        let plain = cbc::Decryptor::<aes::Aes128>::new_from_slices(&self.0[16..], &raw[9..25])
            .map_err(|_| invalid())?
            .decrypt_padded_vec_mut::<Pkcs7>(&raw[25..end])
            .map_err(|_| invalid())?;
        if plain.len() > MAX_PLAINTEXT {
            return Err(invalid());
        }
        Ok(plain)
    }
    pub(super) fn encrypt(&self, plain: &[u8]) -> Result<Vec<u8>> {
        let mut iv = [0; 16];
        OsRng.fill_bytes(&mut iv);
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)?
            .as_secs();
        self.encrypt_at(plain, now, iv)
    }
    fn encrypt_at(&self, plain: &[u8], now: u64, iv: [u8; 16]) -> Result<Vec<u8>> {
        if plain.len() > MAX_PLAINTEXT {
            return Err(invalid());
        }
        let encrypted = cbc::Encryptor::<aes::Aes128>::new_from_slices(&self.0[16..], &iv)
            .map_err(|_| invalid())?
            .encrypt_padded_vec_mut::<Pkcs7>(plain);
        let mut raw = vec![0x80];
        raw.extend(now.to_be_bytes());
        raw.extend(iv);
        raw.extend(encrypted);
        let mut mac = Hmac::<Sha256>::new_from_slice(&self.0[..16]).map_err(|_| invalid())?;
        mac.update(&raw);
        raw.extend(mac.finalize().into_bytes());
        Ok(URL_SAFE.encode(raw).into_bytes())
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn python_vectors_encrypt_and_decrypt_identically() {
        let corpus: serde_json::Value =
            serde_json::from_str(include_str!("../../tests/fixtures/fernet.json")).unwrap();
        let key = Key::parse(corpus["key"].as_str().unwrap().as_bytes()).unwrap();
        for row in corpus["cases"].as_array().unwrap() {
            let plain = row["plaintext"].as_str().unwrap().as_bytes();
            let token = row["token"].as_str().unwrap().as_bytes();
            assert_eq!(key.decrypt(token).unwrap(), plain);
            assert_eq!(
                key.encrypt_at(plain, 1_700_000_000, [7; 16]).unwrap(),
                token
            );
        }
    }
    #[test]
    fn tamper_wrong_key_truncation_and_oversize_fail_without_plaintext() {
        let key = Key::generate();
        let token = key.encrypt(b"fixture secret").unwrap();
        assert!(Key::generate().decrypt(&token).is_err());
        let raw = URL_SAFE.decode(&token).unwrap();
        for index in [0, 1, 9, 25, raw.len() - 1] {
            let mut bad = raw.clone();
            bad[index] ^= 1;
            assert!(key.decrypt(URL_SAFE.encode(bad).as_bytes()).is_err());
        }
        assert!(key.decrypt(&token[..token.len() - 4]).is_err());
        assert!(key.decrypt(&vec![b'A'; MAX_TOKEN + 1]).is_err());
        assert_ne!(
            key.encrypt(b"fixture").unwrap(),
            key.encrypt(b"fixture").unwrap()
        );
    }
}
