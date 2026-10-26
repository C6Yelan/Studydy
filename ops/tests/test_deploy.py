"""部署失敗後不可用舊映像啟動已升級的資料庫。"""
import importlib.machinery
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch

path = Path(__file__).resolve().parents[1] / 'deploy'
spec = importlib.util.spec_from_loader('deploy_command', importlib.machinery.SourceFileLoader('deploy_command', str(path)))
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)


class RecoveryTests(unittest.TestCase):
    def record(self, after):
        return {'schema_before': 20, 'schema_after': after, 'db_backup': 'data/backups/test/database.dump',
                'previous': {'backend': {'image_id': 'old-backend'}, 'frontend': {'image_id': 'old-frontend'}}}

    def test_committed_migration_requires_forward_fix(self):
        # migrate 即使後續失敗，只要已有一版提交，就不能退回。
        record = self.record(21)
        with patch.object(deploy, 'run') as run, patch.object(deploy, 'verify') as verify:
            deploy.recover(record, True, ['compose', 'rollback'], 'private-state')
        run.assert_not_called()
        verify.assert_not_called()
        self.assertEqual(record['status'], 'manual_forward_fix_required')
        self.assertIn(record['db_backup'], record['rollback'])

    def test_unreadable_ledger_does_not_guess_no_migration(self):
        record = self.record(None)
        with patch.object(deploy, 'run') as run:
            deploy.recover(record, True, ['compose', 'rollback'], 'private-state')
        run.assert_not_called()
        self.assertEqual(record['status'], 'manual_forward_fix_required')

    def test_unchanged_schema_restores_and_verifies_previous_images(self):
        record = self.record(20)
        with patch.object(deploy, 'run') as run, patch.object(deploy, 'verify', return_value={'ready': 200}) as verify:
            deploy.recover(record, True, ['compose', 'rollback'], 'private-state')
        self.assertIn('--no-build', run.call_args.args[0])
        verify.assert_called_once_with({'backend': 'old-backend', 'frontend': 'old-frontend'}, 'private-state')
        self.assertEqual(record['status'], 'failed_restored_previous')


class QueueDeadlineTests(unittest.TestCase):
    def test_empty_result_after_deadline_does_not_allow_deployment(self):
        with patch.object(deploy.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(deploy, 'queues', return_value={'material_notes': 0}) as query:
            with self.assertRaisesRegex(RuntimeError, 'QUEUE_TIMEOUT'):
                deploy.drain(1)
        query.assert_called_once_with(timeout=1)

    def test_stalled_query_stops_at_remaining_deadline(self):
        with patch.object(deploy.time, 'monotonic', side_effect=[0, .25]), \
             patch.object(deploy, 'queues', side_effect=deploy.subprocess.TimeoutExpired('psql', .75)) as query:
            with self.assertRaisesRegex(RuntimeError, 'QUEUE_TIMEOUT'):
                deploy.drain(1)
        query.assert_called_once_with(timeout=.75)


if __name__ == '__main__':
    unittest.main()
