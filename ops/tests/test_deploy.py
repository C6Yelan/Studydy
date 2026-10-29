"""部署失敗後不可用舊映像啟動已升級的資料庫。"""
import importlib.machinery
import importlib.util
import argparse
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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


class ForcedQueueTests(unittest.TestCase):
    def test_snapshot_classifies_accounts_without_recording_private_identity(self):
        accounts = {name: {'email': name+'@example.test', 'password': 'private'}
                    for name in ('demo', 'try', 'c6yelan')}
        rows = [{'table': 'material_notes', 'id': {'material_id': str(i), 'kind': 'summary',
                  'structure_revision': 'revision'}, 'status': 'running',
                 'owner_key': hashlib.sha256((name+'@example.test').encode()).hexdigest()}
                for i, name in enumerate(('demo', 'try', 'c6yelan', 'other'))]
        with patch.object(deploy.Path, 'read_text', return_value=json.dumps(accounts)), \
             patch.object(deploy, 'sql', return_value='\n'.join(json.dumps(row) for row in rows)) as query:
            snapshot = deploy.queue_snapshot()
        self.assertEqual(snapshot['active_jobs'], 4)
        self.assertEqual(snapshot['tables']['material_notes'], 4)
        self.assertEqual([row['account'] for row in snapshot['jobs']], ['demo', 'try', 'c6yelan', None])
        self.assertNotIn('owner_key', json.dumps(snapshot))
        self.assertNotIn('@example.test', json.dumps(snapshot))
        statement = query.call_args.args[0]
        self.assertIn('JOIN podcasts p ON p.podcast_id=j.podcast_id', statement)
        self.assertIn('JOIN voice_conversations c', statement)
        self.assertIn("t.status='approved' AND t.material_id IS NULL", statement)

    def test_force_skips_both_waits_but_keeps_backup_and_release_record(self):
        snapshot = {'active_jobs': 1, 'tables': {'podcast_videos': 1}, 'wait_seconds': 0,
                    'jobs': [{'table': 'podcast_videos', 'id': {'video_id': 'video'},
                              'status': 'running', 'account': 'demo'}]}
        sha = 'a'*40
        def run(command, **kwargs):
            if command[:2] == ['git', 'status']:
                return ''
            if command[:2] == ['git', 'branch']:
                return 'dev'
            if command[:2] == ['git', 'rev-parse']:
                return sha
            return 'old-image'
        with tempfile.TemporaryDirectory() as temporary:
            original = Path.cwd()
            os.chdir(temporary)
            try:
                state = Path('private-state')
                state.write_text('{}')
                args = argparse.Namespace(sha=sha, name='forced', dry_run=False,
                    storage_state=str(state), min_free_gib=20, no_queue_wait=True, wait_timeout=10)
                with patch.object(deploy, 'LOCK', Path('deploy.lock')), \
                     patch.object(deploy, 'run', side_effect=run), \
                     patch.object(deploy, 'disk', return_value={}), \
                     patch.object(deploy, 'schema', return_value=30), \
                     patch.object(deploy, 'verify'), patch.object(deploy, 'recover'), \
                     patch.object(deploy, 'queue_snapshot', return_value=snapshot) as capture, \
                     patch.object(deploy, 'drain') as drain, patch.object(deploy, 'queues') as queues, \
                     patch.object(deploy.subprocess, 'run', side_effect=RuntimeError('BACKUP_PROBE')) as backup:
                    with self.assertRaisesRegex(RuntimeError, 'BACKUP_PROBE'):
                        deploy.deploy(args)
                capture.assert_called_once_with()
                drain.assert_not_called()
                queues.assert_not_called()
                self.assertIn('pg_dump', backup.call_args.args[0][-1])
                record = json.loads(next(Path('data/deployments').glob('*/release.json')).read_text())
                self.assertTrue(record['queue']['wait_skipped'])
                self.assertEqual(record['queue']['preflight'], snapshot)
                self.assertIn('db_backup', record)
            finally:
                os.chdir(original)


class NetworkStabilityTests(unittest.TestCase):
    def monitor(self):
        monitor = MagicMock()
        monitor.poll.return_value = None
        return monitor

    def test_address_event_restarts_full_quiet_period(self):
        monitor = self.monitor()
        clock = [0.0]
        observed_waits = []
        def ready(readers, writers, errors, timeout):
            observed_waits.append(timeout)
            clock[0] += .5 if len(observed_waits) == 1 else timeout
            return (readers if len(observed_waits) == 1 else [], [], [])
        with patch.object(deploy.subprocess, 'Popen') as process, \
             patch.object(deploy.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(deploy.select, 'select', side_effect=ready), \
             patch.object(deploy.os, 'read', return_value=b'address changed'):
            process.return_value.__enter__.return_value = monitor
            result = deploy.wait_network_stable()
        self.assertEqual(result, {'wait_seconds': 3.5, 'quiet_seconds': 3, 'event_batches': 1})
        self.assertEqual(observed_waits, [3, 3])
        monitor.terminate.assert_called_once()
        monitor.wait.assert_called_once_with(timeout=5)

    def test_continuous_events_fail_at_total_deadline(self):
        monitor = self.monitor()
        clock = [0.0]
        def ready(readers, writers, errors, timeout):
            clock[0] += min(.5, timeout)
            return readers, [], []
        with patch.object(deploy.subprocess, 'Popen') as process, \
             patch.object(deploy.time, 'monotonic', side_effect=lambda: clock[0]), \
             patch.object(deploy.select, 'select', side_effect=ready), \
             patch.object(deploy.os, 'read', return_value=b'link changed'):
            process.return_value.__enter__.return_value = monitor
            with self.assertRaisesRegex(RuntimeError, 'NETWORK_STABILITY_TIMEOUT'):
                deploy.wait_network_stable(timeout=2, quiet=1)
        self.assertEqual(clock[0], 2)
        monitor.terminate.assert_called_once()

    def test_monitor_failure_is_not_treated_as_stable(self):
        monitor = self.monitor()
        monitor.poll.return_value = 1
        with patch.object(deploy.subprocess, 'Popen') as process:
            process.return_value.__enter__.return_value = monitor
            with self.assertRaisesRegex(RuntimeError, 'NETWORK_MONITOR_FAILED'):
                deploy.wait_network_stable()


if __name__ == '__main__':
    unittest.main()
