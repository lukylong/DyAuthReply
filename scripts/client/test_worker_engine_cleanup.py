"""Offline actual-worker lifecycle gate regression; no account/protocol I/O."""
import os,sys,tempfile,asyncio,json
from pathlib import Path
from unittest.mock import AsyncMock,patch

def main():
    root=Path(__file__).resolve().parents[2];sys.path.insert(0,str(root/'backend-django'))
    with tempfile.TemporaryDirectory(prefix='dy-worker-gate-') as directory:
     os.environ.update(ZQ_ENV='client',CLIENT_DATA_DIR=directory,DJANGO_SETTINGS_MODULE='application.settings')
     import django;django.setup()
     from core.douyin.runtime.worker import DouyinWorker
     from core.client.engine_gate import LegacyEngineLease,EngineGateError
     async def check():
      worker=DouyinWorker(transport_factory=lambda:None)
      entered=asyncio.Event();release=asyncio.Event();cleaned=asyncio.Event()
      async def failed():
       await entered.wait();raise RuntimeError('injected loop failure')
      async def blocked():
       entered.set()
       try:await asyncio.Event().wait()
       finally:
        await release.wait();cleaned.set()
      worker._loop_refresh_accounts=failed;worker._loop_db_commands=blocked
      async def waiting():await asyncio.Event().wait()
      worker._loop_heartbeat=waiting;worker._loop_redis_commands=waiting
      worker._loop_renew_leases=waiting;worker._loop_credential_probe=waiting
      worker._connect_redis=AsyncMock()
      with patch('core.douyin.runtime.account_status.reconcile_send_restrictions',new=AsyncMock(return_value=0)),patch('core.douyin.runtime.worker._log_event',new=AsyncMock()),patch('core.douyin.runtime.worker._mark_worker_sessions_stopped',new=AsyncMock()):
       task=asyncio.create_task(worker.run());await entered.wait();await asyncio.sleep(.05)
       try:LegacyEngineLease.acquire(Path(directory))
       except EngineGateError:pass
       else:raise AssertionError('engine lock released while old consumer cleanup still active')
       assert not task.done() and not cleaned.is_set()
       release.set()
       try:await task
       except RuntimeError as e:assert str(e)=='injected loop failure'
       else:raise AssertionError('failure swallowed')
       assert cleaned.is_set()
       with LegacyEngineLease.acquire(Path(directory)):pass
      # Real worker implementation never enters owned work on sticky Rust selection.
      (Path(directory)/'message-engine.json').write_text(json.dumps({'version':1,'selected':'rust','native_data_dir':str(Path(directory)/'native')}))
      other=DouyinWorker(transport_factory=lambda:None);other._run_owned=AsyncMock()
      try:await other.run()
      except EngineGateError:pass
      else:raise AssertionError('legacy started under native selection')
      other._run_owned.assert_not_called()
     asyncio.run(check())
     print('LEGACY_FAILURE_JOINS_ALL_CONSUMERS_BEFORE_ENGINE_RELEASE_PASS')
     print('LEGACY_RUN_BLOCKS_BEFORE_OWNED_WORK_PASS')

if __name__ == "__main__":
    main()
