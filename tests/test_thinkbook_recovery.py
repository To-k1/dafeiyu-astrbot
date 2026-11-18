import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from contextlib import closing, nullcontext
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from scripts import thinkbook_recovery as recovery


class WorkspaceRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.home = Path(temp.name).resolve()
        self.root = self.home / 'installation'
        self.workspace = self.home / 'workspaces'
        self.data = self.root / recovery.ASTRO
        self.state = self.home / 'ops' / 'FishHome/manager'
        self.runner = self.home / 'runner' / 'AppData/Local/FishHome'
        for name in recovery.WORKSPACE_DIRS:
            (self.workspace / name).mkdir(parents=True)
        self.files = {
            'shared/qq-inbox/received.txt': 'full QQ attachment\n',
            'dsh/life/result.md': 'life output\n',
            'dsh/commission/request.csv': 'source,value\nbackup,1\n',
        }
        for name, content in self.files.items():
            path = self.workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')
        (self.workspace / 'dsh/projects/empty-project').mkdir()
        cache = self.workspace / 'dsh/projects/empty-project/.fishhome-wheels'
        cache.mkdir()
        (cache / 'rebuildable.whl').touch()
        (self.data / 'plugins').mkdir(parents=True)
        (self.root / 'dev/.thinkbook-setup').touch()
        (self.data / 'plugins/installed.py').write_text('# keep', encoding='utf-8')
        (self.data / 'cmd_config.json').write_text('{}', encoding='utf-8')
        bridge = self.data / recovery.BRIDGE
        bridge.parent.mkdir(parents=True)
        bridge.write_text(json.dumps({'dsh_workspaces_root': str(self.workspace)}), encoding='utf-8')
        for file in (self.data / 'data_v4.db', self.data / recovery.ACTIVITY):
            file.parent.mkdir(parents=True, exist_ok=True)
            with closing(sqlite3.connect(file)) as db, db:
                db.execute('create table saved (note text)')
        cat = self.root / recovery.NAPCAT / 'napcat/config'
        cat.mkdir(parents=True)
        (cat / 'onebot.json').write_text('{}', encoding='utf-8')
        self.state.mkdir(parents=True)
        (self.state / 'settings.json').write_text('{}', encoding='utf-8')
        (self.state / 'credentials.bin').write_bytes(b'test-fixture')
        for name in recovery.RUNNER:
            (self.runner / name).mkdir(parents=True)
            (self.runner / name / '.m3-root').touch()

    def archive(self, *, legacy=False, added=None):
        archive = self.home / 'backup.zip'
        with ZipFile(archive, 'w') as bundle:
            recovery.add_to_archive(bundle, self.data, f'install/{recovery.ASTRO}', skip=('plugins',))
            recovery.add_to_archive(bundle, self.root / recovery.NAPCAT, f'install/{recovery.NAPCAT}')
            recovery.add_to_archive(bundle, self.state, 'manager')
            recovery.add_to_archive(bundle, self.runner, 'runner')
            manifest = {'format': recovery.LEGACY_FORMAT if legacy else recovery.FORMAT,
                        'source_root': str(self.root), 'source_account': 'FishHomeOps'}
            if not legacy:
                manifest['workspace_dirs'] = recovery.add_workspaces(bundle, self.workspace)
            if added:
                bundle.writestr(added, 'unexpected content')
            manifest['files'] = bundle.namelist()
            bundle.writestr('manifest.json', json.dumps(manifest))
        return archive

    def test_v2_exact_path_restore_and_rollback_copy_include_external_workspaces(self):
        archive = self.archive()
        for name in self.files:
            (self.workspace / name).write_text('previous live version', encoding='utf-8')
        acl_calls = []

        def acl(path, *args):
            if '/inheritance:r' in args:
                self.assertFalse(list(path.iterdir()))
            acl_calls.append((path.name, args))

        fake_windows = {
            'win32profile': SimpleNamespace(GetUserProfileDirectory=lambda _: str(self.home / 'runner')),
            'deploy.desktop_manager.windows': SimpleNamespace(
                Credentials=lambda _: SimpleNamespace(values={'runner_password': 'test-fixture',
                                                               'compute_password': 'test-fixture'}),
                impersonate=lambda *_: nullcontext()),
            'deploy.desktop_manager.compute': SimpleNamespace(ACCOUNT='FishHomeCompute', acl=acl, sid=str),
        }
        with patch.dict(sys.modules, fake_windows), patch.dict(os.environ, {
                'USERNAME': 'FishHomeOps', 'LOCALAPPDATA': str(self.home / 'ops')}), \
                patch('psutil.net_connections', return_value=[]):
            recovery.restore_installation(archive, self.root)
        for name, content in self.files.items():
            self.assertEqual((self.workspace / name).read_text(encoding='utf-8'), content)
        self.assertTrue((self.workspace / 'dsh/projects/empty-project').is_dir())
        self.assertFalse((self.workspace / 'dsh/projects/empty-project/.fishhome-wheels').exists())
        self.assertTrue((self.data / 'plugins/installed.py').is_file())
        [prior] = self.home.glob('workspaces.before-restore-*')
        self.assertEqual((prior / 'shared/qq-inbox/received.txt').read_text(), 'previous live version')
        self.assertIn('*FishHomeRunner:(OI)(CI)M', acl_calls[0][1])
        self.assertEqual(acl_calls[1], ('projects', ('/grant:r', '*FishHomeCompute:(OI)(CI)M')))

    def test_old_package_can_still_be_unpacked(self):
        destination = self.home / 'unpacked'
        self.assertEqual(recovery.verify(self.archive(legacy=True), destination)['format'], recovery.LEGACY_FORMAT)
        self.assertFalse((destination / 'workspaces').exists())

    def test_archive_rejects_undeclared_workspace_paths(self):
        with self.assertRaisesRegex(ValueError, 'DSH 工作区'):
            recovery.verify(self.archive(added='workspaces/private.txt'))
