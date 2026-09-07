//! Explicit hosted-control integration probe. Acquires, verifies, installs and
//! releases leases; never opens a platform connection or starts account workers.
use dy_agent::{
    control_plane::{
        AccountLeaseOperation, GrantStatus, HostedControlClient, LeaseAction, LeaseSyncRequest,
    },
    identity,
    store::CoreStore,
};
use serde_json::json;
use std::{env, fs::File, io::Read, path::PathBuf};
use uuid::Uuid;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let args: Vec<_> = env::args().collect();
    anyhow::ensure!(
        args.len() == 9,
        "usage: lease-probe --server URL --public-key PEM --request JSON --data-dir DIR"
    );
    let arg = |name: &str| -> anyhow::Result<&str> {
        args.windows(2)
            .find(|v| v[0] == name)
            .map(|v| v[1].as_str())
            .ok_or_else(|| anyhow::anyhow!("missing argument"))
    };
    let mut file = File::open(arg("--request")?)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        anyhow::ensure!(
            file.metadata()?.permissions().mode().trailing_zeros() >= 6,
            "request credential file must be owner-only"
        );
    }
    let mut raw = String::new();
    Read::take(&mut file, 256 * 1024).read_to_string(&mut raw)?;
    let mut request: LeaseSyncRequest = serde_json::from_str(&raw)?;
    let public = std::fs::read_to_string(arg("--public-key")?)?;
    let data_dir = PathBuf::from(arg("--data-dir")?);
    let (owner, _lock) = identity::initialize(&data_dir)?;
    request.instance_id = owner.installation_id;
    request.boot_id = owner.boot_id;
    request.request_id = Uuid::new_v4();
    let client = HostedControlClient::new(arg("--server")?, &public)?;
    let verified = client.sync(&request).await?;
    anyhow::ensure!(
        verified
            .results()
            .iter()
            .all(|g| g.status == GrantStatus::Owned),
        "not all requested accounts were granted"
    );
    let store = CoreStore::open(&data_dir)?;
    let installed = verified
        .install_owned(&store)
        .into_iter()
        .collect::<Result<Vec<_>, _>>()?;
    let epochs: Vec<_> = installed.iter().map(|lease| lease.fence_epoch).collect();
    let release = LeaseSyncRequest {
        request_id: Uuid::new_v4(),
        sequence: request
            .sequence
            .checked_add(1)
            .ok_or_else(|| anyhow::anyhow!("sequence exhausted"))?,
        accounts: verified
            .results()
            .iter()
            .map(|grant| AccountLeaseOperation {
                platform: grant.platform.clone(),
                platform_account_id: grant.platform_account_id.clone(),
                local_account_id: grant.local_account_id.clone(),
                action: LeaseAction::Release,
                expected_epoch: grant.fence_epoch,
            })
            .collect(),
        ..request
    };
    let released = client.sync(&release).await?;
    anyhow::ensure!(
        released
            .results()
            .iter()
            .all(|g| g.status == GrantStatus::Released),
        "hosted release not confirmed"
    );
    for lease in &installed {
        store.release_account_lease(&lease.token())?;
    }
    println!(
        "{}",
        serde_json::to_string_pretty(
            &json!({"verified_grants":installed.len(),"installed_leases":installed.len(),
        "released_leases":released.results().len(),"fence_epochs":epochs,"platform_messages_sent":0})
        )?
    );
    Ok(())
}
