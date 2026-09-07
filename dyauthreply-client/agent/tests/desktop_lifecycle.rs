//! Real native child process lifecycle. Fixtures contain no account credentials or platform sends.
use std::{
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    process::{Command, Stdio},
    time::{Duration, Instant},
};
fn port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}
fn call(port: u16, path: &str, token: Option<&str>, body: Option<&str>) -> String {
    let mut socket = TcpStream::connect(("127.0.0.1", port)).unwrap();
    socket
        .set_read_timeout(Some(Duration::from_secs(3)))
        .unwrap();
    let auth = token.map_or(String::new(), |t| format!("Authorization: Bearer {t}\r\n"));
    let body = body.unwrap_or("");
    write!(socket,"{} {path} HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n{auth}Content-Type: application/json\r\nContent-Length: {}\r\n\r\n{body}",if body.is_empty(){"GET"}else{"POST"},body.len()).unwrap();
    let mut output = String::new();
    socket.read_to_string(&mut output).unwrap();
    output
}
fn scenario(method: &str) {
    let root = tempfile::tempdir().unwrap();
    let data = root.path().join("agent-v2");
    let port = port();
    let mut child = Command::new(env!("CARGO_BIN_EXE_dy-agent"))
        .env("CLIENT_DATA_DIR", root.path())
        .env("DY_AGENT_DATA_DIR", &data)
        .env("DY_AGENT_BIND", format!("127.0.0.1:{port}"))
        .env("DY_AGENT_PARENT_STDIN", "1")
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let deadline = Instant::now() + Duration::from_secs(15);
    while TcpStream::connect(("127.0.0.1", port)).is_err() {
        assert!(Instant::now() < deadline);
        assert!(child.try_wait().unwrap().is_none());
        std::thread::sleep(Duration::from_millis(30));
    }
    let identity = call(
        port,
        "/api/agent/v1/identity?nonce=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        None,
        None,
    );
    assert!(identity.starts_with("HTTP/1.1 200"));
    let raw = identity.split("\r\n\r\n").nth(1).unwrap();
    let identity: serde_json::Value = serde_json::from_str(raw).unwrap();
    if method == "pipe" {
        drop(child.stdin.take());
    } else {
        let token = std::fs::read_to_string(data.join("native-api-token")).unwrap();
        let wrong = call(
            port,
            "/api/agent/v1/lifecycle/shutdown",
            Some(&token),
            Some(&serde_json::json!({"boot_id":uuid::Uuid::new_v4()}).to_string()),
        );
        assert!(wrong.starts_with("HTTP/1.1 409"));
        assert!(child.try_wait().unwrap().is_none());
        let response = call(
            port,
            "/api/agent/v1/lifecycle/shutdown",
            Some(&token),
            Some(&serde_json::json!({"boot_id":identity["boot_id"]}).to_string()),
        );
        assert!(response.starts_with("HTTP/1.1 202"));
    }
    let deadline = Instant::now() + Duration::from_secs(12);
    loop {
        if let Some(status) = child.try_wait().unwrap() {
            assert!(status.success());
            break;
        }
        if Instant::now() > deadline {
            let _ = child.kill();
            panic!("native child failed to drain");
        }
        std::thread::sleep(Duration::from_millis(30));
    }
    assert!(TcpListener::bind(("127.0.0.1", port)).is_ok());
}
#[test]
fn parent_pipe_eof_drains_and_releases_listener() {
    scenario("pipe");
}
#[test]
fn authenticated_boot_bound_shutdown_drains_while_parent_stays_alive() {
    scenario("http");
}
