import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

import django

os.environ.setdefault("CLIENT_DATA_DIR", "/private/tmp/dyauthreply-test-client")
os.environ.setdefault("ZQ_ENV", "server")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "application.settings")
Path(os.environ["CLIENT_DATA_DIR"]).mkdir(parents=True, exist_ok=True)
django.setup()

from scheduler.service import SchedulerService


class SchedulerServiceTests(TestCase):
    def test_should_skip_hosted_account_job_when_disabled(self):
        service = SchedulerService()
        job = SimpleNamespace(task_func="scheduler.tasks.douyin_probe_credentials")

        with patch("scheduler.service.settings.ENABLE_HOSTED_ACCOUNT_SCHEDULER_JOBS", False):
            assert service._should_skip_job(job) is True

    def test_should_allow_hosted_account_job_when_enabled(self):
        service = SchedulerService()
        job = SimpleNamespace(task_func="scheduler.tasks.douyin_probe_credentials")

        with patch("scheduler.service.settings.ENABLE_HOSTED_ACCOUNT_SCHEDULER_JOBS", True):
            assert service._should_skip_job(job) is False

    def test_should_not_skip_regular_scheduler_job(self):
        service = SchedulerService()
        job = SimpleNamespace(task_func="scheduler.tasks.cleanup_old_logs")

        with patch("scheduler.service.settings.ENABLE_HOSTED_ACCOUNT_SCHEDULER_JOBS", False):
            assert service._should_skip_job(job) is False

    def test_prepare_job_kwargs_skips_job_code_for_plain_tasks(self):
        service = SchedulerService()
        job = SimpleNamespace(code="reset_db", task_func="scheduler.tasks.load_initial_data")

        kwargs = service._prepare_job_kwargs(
            service._import_task_func(job.task_func),
            job,
            {},
        )

        self.assertEqual(kwargs, {})

    def test_prepare_job_kwargs_includes_job_code_for_decorated_tasks(self):
        service = SchedulerService()
        job = SimpleNamespace(
            code="cleanup_logs",
            task_func="scheduler.module.executor.cleanup_old_logs",
        )

        kwargs = service._prepare_job_kwargs(
            service._import_task_func(job.task_func),
            job,
            {},
        )

        self.assertEqual(kwargs["job_code"], "cleanup_logs")
