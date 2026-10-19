"""Queue routing and retention switches without a broker or production database."""

import unittest
from unittest.mock import AsyncMock, patch

from app.config import settings
from app.core.enums import JobType
from app.core.errors import NotEnabled
from app.workers import celery_app as workers


class WorkerRoutingTests(unittest.TestCase):
    def test_each_job_uses_its_isolated_queue(self):
        self.assertNotEqual(
            workers.TASK_QUEUES[JobType.FILE_SCAN],
            workers.TASK_QUEUES[JobType.FILE_DERIVE],
        )
        self.assertNotEqual(
            workers.TASK_QUEUES[JobType.FILE_DERIVE],
            workers.TASK_QUEUES[JobType.NOTIFICATION_DISPATCH],
        )
        for kind, task in workers.TASKS_BY_TYPE.items():
            with patch.object(task, "apply_async") as send:
                self.assertTrue(workers.submit("synthetic-job", kind.value))
                send.assert_called_once_with(
                    args=["synthetic-job"], queue=workers.TASK_QUEUES[kind]
                )

    def test_broker_failure_is_not_reported_as_delivery(self):
        with patch.object(
            workers.file_scan,
            "apply_async",
            side_effect=ConnectionError("synthetic broker failure"),
        ):
            self.assertFalse(workers.submit("synthetic-job", JobType.FILE_SCAN.value))


class RetentionSwitchTests(unittest.IsolatedAsyncioTestCase):
    async def test_disabled_never_runs_cleanup(self):
        with (
            patch.object(settings, "RETENTION_SCHEDULE_ENABLED", False),
            patch(
                "app.modules.identity.retention._run",
                new_callable=AsyncMock,
            ) as accounts,
        ):
            with self.assertRaises(NotEnabled):
                await workers.run_retention_cleanup()
            accounts.assert_not_awaited()

    async def test_enabled_uses_existing_transactional_cleanup_functions(self):
        with (
            patch.object(settings, "RETENTION_SCHEDULE_ENABLED", True),
            patch(
                "app.modules.identity.retention._run",
                new_callable=AsyncMock,
                return_value=2,
            ) as accounts,
            patch(
                "app.modules.search.retention._run",
                new_callable=AsyncMock,
                return_value=3,
            ) as search,
        ):
            self.assertEqual(
                await workers.run_retention_cleanup(),
                {"accounts": 2, "search_queries": 3},
            )
            accounts.assert_awaited_once()
            search.assert_awaited_once()
