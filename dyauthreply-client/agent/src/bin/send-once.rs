//! Explicit single-command native delivery canary. It is not the automatic
//! worker or legacy UI adapter; every run still needs a genuine signed grant.
use anyhow::{Context, Result};
use dy_agent::{
    control_plane::{GrantStatus, HostedControlClient, LeaseAction, LeaseSyncRequest},
    identity,
    protocol::{
        account_session::NativeAccountSession,
        credentials::{AccountCredentials, MAX_CREDENTIAL_BYTES},
        live_http::ProtocolHttpClient,
        live_sender::{LiveSender, SendOperation},
        native_signer::NativeSigner,
        SendRequestInput,
    },
    runtime::{account::AccountControl, hosted::HostedSettings, model::WorkKind},
    state::{AccountRuntimeState, InboundState, LifecycleState, OwnershipState, SendCapability},
    store::{CoreStore, OutboundSegmentDraft, SegmentStatus},
};
use serde::Deserialize;
use serde_json::json;
use std::{
    env,
    fs::File,
    io::Read,
    path::Path,
    sync::Arc,
    time::{Instant, SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ManualCommand {
    request_id: Uuid,
    conversation_id: String,
    conversation_short_id: u64,
    text: String,
}
fn read_private(path: &str, limit: usize) -> Result<Vec<u8>> {
    let file = File::open(path)?;
    let meta = file.metadata()?;
    anyhow::ensure!(
        meta.is_file() && meta.len() <= limit as u64,
        "input file exceeds bound"
    );
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        anyhow::ensure!(
            meta.permissions().mode().trailing_zeros() >= 6,
            "input file must be owner-only"
        );
    }
    let mut raw = Vec::new();
    file.take((limit + 1) as u64).read_to_end(&mut raw)?;
    anyhow::ensure!(raw.len() <= limit, "input file exceeds bound");
    Ok(raw)
}

struct Inputs {
    credentials: AccountCredentials,
    config: HostedSettings,
    command: ManualCommand,
    data_dir: std::path::PathBuf,
}
fn load_inputs() -> Result<Inputs> {
    let args: Vec<_> = env::args().collect();
    anyhow::ensure!(
        args.len() == 9,
        "usage: send-once --credentials FILE --control FILE --send FILE --data-dir DIR"
    );
    let arg = |key: &str| -> Result<&str> {
        args.windows(2)
            .find(|p| p[0] == key)
            .map(|p| p[1].as_str())
            .context("missing argument")
    };
    let credentials = AccountCredentials::import_json(&read_private(
        arg("--credentials")?,
        MAX_CREDENTIAL_BYTES,
    )?)?;
    let mut config = HostedSettings::load(Path::new(arg("--control")?))?;
    config.refresh_activation()?;
    config
        .accounts
        .retain(|a| a.local_account_id == credentials.account_id.as_str());
    anyhow::ensure!(
        config.accounts.len() == 1
            && !credentials.expected_sec_uid.is_empty()
            && config.accounts[0].platform_account_id == credentials.expected_sec_uid,
        "credential/hosted platform identity mismatch"
    );
    let command: ManualCommand = serde_json::from_slice(&read_private(arg("--send")?, 16384)?)?;
    anyhow::ensure!(
        !command.request_id.is_nil()
            && !command.conversation_id.is_empty()
            && command.conversation_id.len() <= 256
            && command.conversation_short_id > 0
            && !command.text.trim().is_empty()
            && command.text.len() <= 4096,
        "invalid manual command"
    );
    Ok(Inputs {
        credentials,
        config,
        command,
        data_dir: Path::new(arg("--data-dir")?).to_path_buf(),
    })
}

#[tokio::main]
async fn main() -> Result<()> {
    let Inputs {
        credentials,
        config,
        command,
        data_dir,
    } = load_inputs()?;
    let (owner, _lock) = identity::initialize(&data_dir)?;
    let store = Arc::new(CoreStore::open(&data_dir)?);
    let http = ProtocolHttpClient::for_user_agent(2, &credentials.user_agent)?;
    let signer = NativeSigner::new(2)?;
    let mut session = NativeAccountSession::new(credentials, http.clone(), signer.clone());
    // Read-only preparation happens before taking the short write lease.
    session.verify_self().await?;
    let identity = session.identity().await?;
    let conversation = session
        .conversation_context(&command.conversation_id, command.conversation_short_id)
        .await?;
    let credentials = session.send_credentials(1)?;
    let client = HostedControlClient::new(&config.server, &config.public_key_pem)?;
    let request = LeaseSyncRequest {
        activation_id: config.activation_id,
        activation_token: config.activation_token,
        instance_id: owner.installation_id,
        boot_id: owner.boot_id,
        request_id: Uuid::new_v4(),
        sequence: 1,
        accounts: config.accounts,
    };
    let grant = client.sync(&request).await?;
    anyhow::ensure!(
        grant.allows_manual()
            && grant.results().len() == 1
            && grant.results()[0].status == GrantStatus::Owned,
        "manual ownership permission was not granted"
    );
    let lease = grant
        .install_owned(&store)
        .into_iter()
        .next()
        .context("no installed grant")??;
    let (state, control) = tokio::sync::watch::channel(AccountControl {
        actor_generation: 1,
        state: AccountRuntimeState {
            lifecycle: LifecycleState::Running,
            ownership: OwnershipState::Owned,
            inbound: InboundState::Disconnected,
            send: SendCapability::Unknown,
            credential_generation: 1,
            lease_epoch: u64::try_from(lease.fence_epoch)?,
        },
    });
    let prepared = PreparedManual {
        command,
        store: store.clone(),
        session,
        identity,
        conversation,
        credentials,
        signer,
        http,
    };
    store.restore_send_observation(
        &lease.token(),
        &prepared.credentials.canonical_sec_uid,
        &prepared.credentials.binding_digest,
    )?;
    let result = prepared.execute(lease.clone(), grant, control).await;

    state.send_modify(|c| c.state.ownership = OwnershipState::Lost);
    store.invalidate_account_lease(&lease.token())?;
    let mut release = request;
    release.sequence += 1;
    release.request_id = Uuid::new_v4();
    release.accounts[0].action = LeaseAction::Release;
    release.accounts[0].expected_epoch = u64::try_from(lease.fence_epoch)?;
    let remote_released = client
        .sync(&release)
        .await
        .is_ok_and(|g| g.results()[0].status == GrantStatus::Released);
    let mut report = result?;
    report["local_lease_released"] = json!(true);
    report["remote_lease_released"] = json!(remote_released);
    println!("{}", serde_json::to_string_pretty(&report)?);
    Ok(())
}

struct PreparedManual {
    command: ManualCommand,
    store: Arc<CoreStore>,
    session: NativeAccountSession,
    identity: dy_agent::protocol::account_session::IdentityCredentials,
    conversation: dy_agent::protocol::inbox::ConversationContext,
    credentials: dy_agent::protocol::live_sender::SendCredentials,
    signer: NativeSigner,
    http: ProtocolHttpClient,
}
impl PreparedManual {
    async fn execute(
        self,
        lease: dy_agent::store::AccountLease,
        grant: dy_agent::control_plane::VerifiedGrantBatch,
        control: tokio::sync::watch::Receiver<AccountControl>,
    ) -> Result<serde_json::Value> {
        let Self {
            command,
            store,
            session,
            identity,
            conversation,
            credentials,
            signer,
            http,
        } = self;

        let batch = store.prepare_outbound_batch(
            &lease.token(),
            &format!("manual:{}", command.request_id),
            &format!(
                "{}:{}",
                command.conversation_id, command.conversation_short_id
            ),
            &[OutboundSegmentDraft::text(command.text)],
        )?;
        let segment = &batch.segments[0];
        if !matches!(
            segment.status,
            SegmentStatus::Prepared | SegmentStatus::Retryable
        ) {
            return Ok(
                json!({"attempted":false,"durable_status":format!("{:?}",segment.status),
                "attempt_count":segment.attempt_count,"client_message_id":segment.client_message_id,
                "platform_message_id":segment.platform_message_id}),
            );
        }
        anyhow::ensure!(
            Instant::now() < grant.deadline(),
            "grant expired during command preparation"
        );
        let stime = SystemTime::now()
            .duration_since(UNIX_EPOCH)?
            .as_millis()
            .to_string();
        let operation = SendOperation {
            lease: lease.token(),
            lease_deadline: grant.deadline(),
            control,
            kind: WorkKind::ManualSend,
            batch_id: batch.id.clone(),
            segment_id: segment.id.clone(),
            request: SendRequestInput {
                conversation_id: command.conversation_id,
                conversation_short_id: command.conversation_short_id,
                ticket: conversation.ticket,
                text: String::new(),
                user_agent: session.credentials.user_agent.clone(),
                client_msg_id: String::new(),
                sequence_id: 10001,
                stime,
                message_type: 7,
                identity_security_token: identity.token,
                identity_security_device_id: identity.device_id,
                mentioned_users: vec![],
                ext: vec![],
            },
            credentials,
        };
        let outcome = LiveSender::new(store.clone(), signer, http)
            .send(operation)
            .await;
        let saved = store.outbound_batch(&batch.id)?;
        let segment = &saved.segments[0];
        Ok(
            json!({"attempted":segment.attempt_count>0,"classification":outcome.as_ref().ok().map(|o|o.classification),
            "error":outcome.err().map(|e|e.to_string()),"durable_status":format!("{:?}",segment.status),
            "attempt_count":segment.attempt_count,"client_message_id":segment.client_message_id,
            "platform_message_id":segment.platform_message_id}),
        )
    }
}
