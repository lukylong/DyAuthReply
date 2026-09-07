use super::*;
const A: &str = "00000000-0000-0000-0000-000000000001";
const B: &str = "00000000-0000-0000-0000-000000000002";
fn state(extra: &str) -> serde_json::Value {
    serde_json::json!({"cookies":[{"name":"sessionid","value":"fixture-private-cookie"},{"name":"msToken","value":"fixture-ms"}],"_dtrait":{"blob":extra}})
}
fn fixture() -> (tempfile::TempDir, PathBuf, PathBuf, Key) {
    let dir = tempfile::tempdir().unwrap();
    let root = dir.path().join("legacy");
    fs::create_dir_all(root.join("douyin/storage")).unwrap();
    let key = Key::generate();
    private_write(
        &root.join(".env"),
        format!(
            "# fixture\nexport DOUYIN_STORAGE_ENCRYPTION_KEY='{}' # comment\n",
            key.encoded()
        )
        .as_bytes(),
    )
    .unwrap();
    let db = rusqlite::Connection::open(root.join("db.sqlite3")).unwrap();
    db.execute_batch("CREATE TABLE core_douyin_account(id TEXT,sec_uid TEXT,nickname TEXT,user_agent TEXT,storage_state_path TEXT,is_deleted INTEGER)").unwrap();
    db.execute(
        "INSERT INTO core_douyin_account VALUES(?1,'scope-a','Fixture','Chrome/124.0.0.0',?2,0)",
        rusqlite::params![A, format!("storage/{A}.bin")],
    )
    .unwrap();
    drop(db);
    private_write(
        &root.join(format!("douyin/storage/{A}.bin")),
        &key.encrypt(&serde_json::to_vec(&state("v1")).unwrap())
            .unwrap(),
    )
    .unwrap();
    let dest = dir.path().join("native");
    (dir, root, dest, key)
}
#[test]
fn encrypted_snapshot_loads_without_legacy_and_reimport_is_idempotent() {
    let (_dir, root, dest, _key) = fixture();
    let before = fs::read(root.join(format!("douyin/storage/{A}.bin"))).unwrap();
    let first = import_legacy(&root, &dest).unwrap();
    assert_eq!(first.accounts, 1);
    assert!(!first.reused);
    let token = fs::read(dest.join(format!("{A}.fernet"))).unwrap();
    let manifest = fs::read(dest.join("manifest.json")).unwrap();
    assert!(!String::from_utf8_lossy(&token).contains("fixture-private-cookie"));
    assert!(!String::from_utf8_lossy(&manifest).contains("fixture-private-cookie"));
    assert!(import_legacy(&root, &dest).unwrap().reused);
    assert_eq!(fs::read(dest.join(format!("{A}.fernet"))).unwrap(), token);
    assert_eq!(
        fs::read(root.join(format!("douyin/storage/{A}.bin"))).unwrap(),
        before
    );
    fs::remove_dir_all(root).unwrap();
    let loaded = load_accounts(&dest, &[A.into()]).unwrap();
    assert_eq!(
        loaded[0].cookie("www.douyin.com", "sessionid"),
        "fixture-private-cookie"
    );
    assert_eq!(loaded[0].dtrait_blob, "v1");
    assert!(load_accounts(&dest, &[B.into()]).is_err());
}
#[test]
fn changed_non_binding_signing_fields_require_new_version_not_silent_stale_reuse() {
    let (_dir, root, dest, key) = fixture();
    import_legacy(&root, &dest).unwrap();
    let before = fs::read(dest.join("manifest.json")).unwrap();
    fs::write(
        root.join(format!("douyin/storage/{A}.bin")),
        key.encrypt(&serde_json::to_vec(&state("v2")).unwrap())
            .unwrap(),
    )
    .unwrap();
    assert!(import_legacy(&root, &dest).is_err());
    assert_eq!(fs::read(dest.join("manifest.json")).unwrap(), before);
    assert_eq!(
        load_accounts(&dest, &[A.into()]).unwrap()[0].dtrait_blob,
        "v1"
    );
    assert!(!dest.parent().unwrap().join(".native.pending").exists());
}
#[test]
fn invalid_source_or_path_leaves_no_published_or_partial_store() {
    let (_dir, root, dest, _key) = fixture();
    fs::write(root.join(format!("douyin/storage/{A}.bin")), b"broken").unwrap();
    assert!(import_legacy(&root, &dest).is_err());
    assert!(!dest.exists());
    assert!(!dest.parent().unwrap().join(".native.pending").exists());
    let db = rusqlite::Connection::open(root.join("db.sqlite3")).unwrap();
    db.execute(
        "UPDATE core_douyin_account SET storage_state_path='../outside'",
        [],
    )
    .unwrap();
    assert!(import_legacy(&root, &dest).is_err());
    assert!(!dest.exists());
}
#[test]
fn native_token_swap_and_manifest_corruption_are_rejected() {
    let (_dir, root, dest, key) = fixture();
    let db = rusqlite::Connection::open(root.join("db.sqlite3")).unwrap();
    db.execute(
        "INSERT INTO core_douyin_account VALUES(?1,'scope-b','Second','Chrome/124.0.0.0',?2,0)",
        rusqlite::params![B, format!("storage/{B}.bin")],
    )
    .unwrap();
    drop(db);
    private_write(
        &root.join(format!("douyin/storage/{B}.bin")),
        &key.encrypt(&serde_json::to_vec(&state("second")).unwrap())
            .unwrap(),
    )
    .unwrap();
    import_legacy(&root, &dest).unwrap();
    let other = fs::read(dest.join(format!("{B}.fernet"))).unwrap();
    fs::write(dest.join(format!("{A}.fernet")), other).unwrap();
    assert!(load_accounts(&dest, &[A.into()]).is_err());
    let mut manifest: serde_json::Value =
        serde_json::from_slice(&fs::read(dest.join("manifest.json")).unwrap()).unwrap();
    manifest["accounts"][0]["nickname"] = "changed".into();
    fs::write(
        dest.join("manifest.json"),
        serde_json::to_vec(&manifest).unwrap(),
    )
    .unwrap();
    assert!(load_accounts(&dest, &[B.into()]).is_err());
}
#[test]
fn missing_credentials_are_counted_and_pending_import_is_not_deleted() {
    let (_dir, root, dest, _key) = fixture();
    let db = rusqlite::Connection::open(root.join("db.sqlite3")).unwrap();
    db.execute(
        "INSERT INTO core_douyin_account VALUES(?1,'scope-b','Pending','','',0)",
        [B],
    )
    .unwrap();
    drop(db);
    let report = import_legacy(&root, &dest).unwrap();
    assert_eq!(report.skipped_without_credentials, 1);
    assert_eq!(report.accounts, 1);
    let pending = dest.parent().unwrap().join(".next.pending");
    fs::create_dir(&pending).unwrap();
    fs::write(pending.join("sentinel"), b"pending").unwrap();
    assert!(import_legacy(&root, &dest.parent().unwrap().join("next")).is_err());
    assert_eq!(fs::read(pending.join("sentinel")).unwrap(), b"pending");
}

#[cfg(unix)]
#[test]
fn legacy_default_permissions_import_but_native_key_must_stay_private() {
    use std::os::unix::fs::PermissionsExt;
    let (_dir, root, dest, _key) = fixture();
    fs::set_permissions(root.join(".env"), fs::Permissions::from_mode(0o644)).unwrap();
    import_legacy(&root, &dest).unwrap();
    assert_eq!(
        fs::metadata(dest.join("key.fernet"))
            .unwrap()
            .permissions()
            .mode()
            & 0o777,
        0o600
    );
    fs::set_permissions(dest.join("key.fernet"), fs::Permissions::from_mode(0o644)).unwrap();
    assert!(load_accounts(&dest, &[A.into()]).is_err());
}

#[test]
fn windows_legacy_relative_separator_is_normalized_without_path_escape() {
    let (_dir, root, dest, _key) = fixture();
    let db = rusqlite::Connection::open(root.join("db.sqlite3")).unwrap();
    db.execute(
        "UPDATE core_douyin_account SET storage_state_path=?1",
        [format!("storage\\{A}.bin")],
    )
    .unwrap();
    drop(db);
    assert_eq!(import_legacy(&root, &dest).unwrap().accounts, 1);
}
