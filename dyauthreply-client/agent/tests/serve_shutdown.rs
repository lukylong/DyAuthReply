#![cfg(unix)]

use std::{
    io::{Read, Write},
    net::{SocketAddr, TcpListener, TcpStream},
    process::{Child, Command, Stdio},
    thread,
    time::{Duration, Instant},
};

use dy_agent::{
    health::{HealthResponse, RuntimeHealthPhase},
    state::LifecycleState,
};

struct ChildGuard(Child);

impl Drop for ChildGuard {
    fn drop(&mut self) {
        if self.0.try_wait().ok().flatten().is_none() {
            let _ = self.0.kill();
            let _ = self.0.wait();
        }
    }
}

fn reserve_loopback_address() -> SocketAddr {
    let listener = TcpListener::bind("127.0.0.1:0").expect("reserve loopback port");
    listener.local_addr().expect("reserved address")
}

fn await_live_health(child: &mut Child, address: SocketAddr) -> HealthResponse {
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        if let Some(status) = child.try_wait().expect("inspect Agent process") {
            panic!("Agent exited before health became ready: {status}");
        }
        if let Ok(mut stream) = TcpStream::connect_timeout(&address, Duration::from_millis(100)) {
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .expect("health read timeout");
            stream
                .write_all(b"GET /health HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
                .expect("write health request");
            let mut response = String::new();
            stream
                .read_to_string(&mut response)
                .expect("read health response");
            if let Some((headers, body)) = response.split_once("\r\n\r\n") {
                assert!(headers.starts_with("HTTP/1.1 200"), "{headers}");
                return serde_json::from_str(body).expect("valid live health JSON");
            }
        }
        assert!(Instant::now() < deadline, "Agent health start timed out");
        thread::sleep(Duration::from_millis(20));
    }
}

fn await_clean_exit(child: &mut Child) -> std::process::ExitStatus {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = child.try_wait().expect("inspect Agent shutdown") {
            return status;
        }
        if Instant::now() >= deadline {
            child.kill().expect("force-stop timed out Agent");
            panic!("Agent did not drain before the SIGTERM deadline");
        }
        thread::sleep(Duration::from_millis(20));
    }
}

#[test]
fn serve_reports_live_runtime_and_sigterm_releases_the_listener() {
    let data_root = tempfile::tempdir().expect("isolated Agent data directory");
    let data_dir = data_root.path().join("agent-v2");
    let address = reserve_loopback_address();
    let child = Command::new(env!("CARGO_BIN_EXE_dy-agent"))
        .env("DY_AGENT_DATA_DIR", &data_dir)
        .env("DY_AGENT_BIND", address.to_string())
        .env("RUST_LOG", "off")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .expect("start Agent serve process");
    let mut child = ChildGuard(child);

    let health = await_live_health(&mut child.0, address);
    let expected_protocol_mode = if option_env!("CLIENT_LICENSE_SERVER_URL").is_some()
        && option_env!("LICENSE_LEASE_PUBLIC_KEY_B64").is_some()
    {
        "native-registry"
    } else {
        "shadow-disabled"
    };
    assert_eq!(health.protocol_mode, expected_protocol_mode);
    assert_eq!(health.lifecycle, LifecycleState::Running);
    assert_eq!(health.runtime.phase, RuntimeHealthPhase::Running);
    assert_eq!(health.runtime.central_timer_tasks, 1);
    assert!(health.runtime.timer_alive);
    assert!(health.runtime.dispatcher_alive);
    assert_eq!(
        health.runtime.signer_workers_alive,
        health.runtime.signer_concurrency
    );
    assert!(health.runtime.accepting_work);
    assert!(health.ready);

    let signal_status = Command::new("kill")
        .args(["-TERM", &child.0.id().to_string()])
        .status()
        .expect("send SIGTERM to Agent");
    assert!(signal_status.success());
    let exit_status = await_clean_exit(&mut child.0);
    assert!(exit_status.success(), "Agent SIGTERM status: {exit_status}");

    let rebound = TcpListener::bind(address).expect("Agent listener must be released after drain");
    drop(rebound);
}
