"""Offline checks: python3 -m unittest discover -s tests -v"""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
import zipfile

spec = importlib.util.spec_from_file_location('manager', Path(__file__).resolve().parents[1] / 'modloaders/_manager.py')
manager = importlib.util.module_from_spec(spec)
spec.loader.exec_module(manager)
REAL_STOPPED = manager.stopped


def jar_bytes(profile=None):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('META-INF/MANIFEST.MF', 'Manifest-Version: 1.0\n')
        if profile:
            archive.writestr('install_profile.json', json.dumps(profile))
    return output.getvalue()


class LoaderTests(unittest.TestCase):
    def setUp(self):
        # All deletion checks are confined to this newly-created test directory.
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name)
        self.root = self.repo / 'minecraft-server'
        self.root.mkdir()
        (self.root / 'minecraft-version.txt').write_text('1.21.1\n')
        (self.root / 'start.sh').write_text('old custom start\n')
        (self.root / 'server.properties').write_text('keep me\n')
        (self.root / 'eula.txt').write_text('eula=true\n')
        (self.root / 'world').mkdir()
        (self.root / 'world/level.dat').write_bytes(b'world')
        (self.root / '.playit').mkdir()
        (self.root / '.playit/playit.toml').write_text('secret')
        (self.root / '.java-21/bin').mkdir(parents=True)
        (self.root / '.java-21/bin/java').write_bytes(b'fake java')
        self.jar = jar_bytes({'minecraft': '1.21.1'})
        self.sha1 = hashlib.sha1(self.jar).hexdigest()
        self.sha256 = hashlib.sha256(self.jar).hexdigest()
        self.manifest = {'versions': [{'id': '1.21.1', 'type': 'release', 'url': 'https://example.test/version'}]}
        self.details = {'javaVersion': {'majorVersion': 21}, 'downloads': {'server': {'url': 'https://example.test/server.jar', 'sha1': self.sha1}}}
        self.addCleanup(patch.stopall)
        patch.object(manager, 'stopped', lambda root: contextlib.nullcontext()).start()
        patch.object(manager, 'fetch', self.fetch).start()
        patch.object(manager.subprocess, 'run', self.installer).start()

    def fetch(self, url):
        if 'version_manifest' in url:
            payload = self.manifest
        elif url.endswith('/version'):
            payload = self.details
        elif 'promotions_slim' in url:
            payload = {'promos': {'1.21.1-recommended': '52.1.0'}}
        elif 'maven-metadata' in url:
            return io.BytesIO(b'<metadata><versioning><versions><version>21.1.2</version><version>21.1.10</version><version>21.1.11-beta</version><version>21.2.20</version></versions></versioning></metadata>')
        elif url.endswith('.sha1'):
            return io.BytesIO(self.sha1.encode())
        elif 'fill.papermc' in url:
            payload = [{'id': 134, 'channel': 'EXPERIMENTAL'}, {'id': 133, 'channel': 'STABLE', 'downloads': {'server:default': {'url': 'https://example.test/paper.jar', 'checksums': {'sha256': self.sha256}}}}]
        elif url.endswith('/versions/installer'):
            payload = [{'stable': True, 'version': '1.1.2'}]
        elif url.endswith('/versions/loader/1.21.1'):
            payload = [{'loader': {'stable': False, 'version': '0.20.0'}}, {'loader': {'stable': True, 'version': '0.19.5'}}]
        else:
            return io.BytesIO(self.jar)
        return io.BytesIO(json.dumps(payload).encode())

    def installer(self, command, cwd, check):
        self.assertTrue(check)
        self.assertEqual(command[-1], '--installServer')
        # Installer file name is generic, inspect the selected provider through this test's field.
        group = 'net/neoforged/neoforge/21.1.10' if self.kind == 'neoforge' else 'net/minecraftforge/forge/1.21.1-52.1.0'
        args = Path(cwd) / 'libraries' / group / 'unix_args.txt'
        args.parent.mkdir(parents=True)
        args.write_text('-cp libraries/example.jar\n')
        (Path(cwd) / 'libraries/example.jar').write_bytes(b'library')
        if self.kind == 'forge':
            (Path(cwd) / 'forge-1.21.1-52.1.0-shim.jar').write_bytes(b'shim')

    def assert_preserved(self):
        self.assertEqual((self.root / 'world/level.dat').read_bytes(), b'world')
        self.assertEqual((self.root / 'server.properties').read_text(), 'keep me\n')
        self.assertEqual((self.root / 'eula.txt').read_text(), 'eula=true\n')
        self.assertEqual((self.root / '.playit/playit.toml').read_text(), 'secret')

    def test_all_loaders_activate_correct_entry_point(self):
        expected = {'fabric': '-jar fabric-server-launch.jar', 'paper': '-jar paper.jar', 'forge': '@libraries/net/minecraftforge/forge/1.21.1-52.1.0/unix_args.txt', 'neoforge': '@libraries/net/neoforged/neoforge/21.1.10/unix_args.txt'}
        for kind, entry in expected.items():
            with self.subTest(kind=kind):
                self.kind = kind
                manager.switch(self.repo, kind)
                self.assertIn(entry, (self.root / 'start.sh').read_text())
                self.assertIn('flock -n 9', (self.root / 'start.sh').read_text())
                self.assertEqual(json.loads((self.root / 'server-type.json').read_text())['type'], kind)
                if kind == 'forge':
                    self.assertEqual((self.root / 'forge-1.21.1-52.1.0-shim.jar').read_bytes(), b'shim')
                self.assert_preserved()

    def test_vanilla_removes_only_mods_plugins_and_restores_jar(self):
        for name in ('mods', 'plugins'):
            (self.root / name).mkdir()
            (self.root / name / 'example.jar').write_bytes(b'old')
        (self.root / 'server.jar').write_bytes(b'old jar')
        manager.switch(self.repo, 'vanilla')
        self.assertFalse((self.root / 'mods').exists())
        self.assertFalse((self.root / 'plugins').exists())
        self.assertEqual((self.root / 'server.jar').read_bytes(), self.jar)
        self.assertIn('-jar server.jar', (self.root / 'start.sh').read_text())
        self.assert_preserved()
        self.assertTrue(list((self.root / '.loader-backups').glob('start-*.sh')))

    def test_reset_deletes_only_the_server(self):
        (self.repo / 'install.sh').write_text('keep')
        manager.reset(self.repo)
        self.assertFalse(self.root.exists())
        self.assertEqual((self.repo / 'install.sh').read_text(), 'keep')
        manager.reset(self.repo)

    def test_minecraft_version_change_refused_before_any_network(self):
        with patch.object(manager, 'fetch', side_effect=AssertionError('network should not run')):
            with self.assertRaisesRegex(RuntimeError, 'never upgrade'):
                manager.switch(self.repo, 'fabric', '1.20.1')

    def test_checksum_failure_does_not_change_launcher_or_delete_mods(self):
        (self.root / 'mods').mkdir()
        self.details['downloads']['server']['sha1'] = '0' * 40
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            manager.switch(self.repo, 'vanilla')
        self.assertEqual((self.root / 'start.sh').read_text(), 'old custom start\n')
        self.assertTrue((self.root / 'mods').is_dir())

    def test_failed_official_installer_does_not_activate(self):
        with patch.object(manager.subprocess, 'run', side_effect=manager.subprocess.CalledProcessError(1, 'java')):
            with self.assertRaises(manager.subprocess.CalledProcessError):
                manager.switch(self.repo, 'forge')
        self.assertEqual((self.root / 'start.sh').read_text(), 'old custom start\n')
        self.assert_preserved()

    def test_stable_selection_and_explicit_beta(self):
        self.assertEqual(manager.select_fabric('1.21.1', '')[0], '0.19.5')
        self.assertEqual(manager.select_neoforge('1.21.1', '')[0], '21.1.10')
        self.assertEqual(manager.select_neoforge('1.21.1', '21.1.11-beta')[0], '21.1.11-beta')
        with self.assertRaisesRegex(RuntimeError, 'stable paper'):
            manager.select_paper('1.21.1', '134')

    def test_redirected_server_root_is_refused(self):
        with patch.object(Path, 'is_symlink', return_value=True):
            with self.assertRaisesRegex(RuntimeError, 'symlinked'):
                manager.reset(self.repo)
        self.assertTrue(self.root.is_dir())

    def test_active_lock_prevents_reset(self):
        def busy(*args):
            raise BlockingIOError()
        fcntl = types.SimpleNamespace(LOCK_EX=2, LOCK_NB=4, flock=busy)
        with patch.object(manager, 'stopped', REAL_STOPPED), patch.dict('sys.modules', {'fcntl': fcntl}):
            with self.assertRaisesRegex(RuntimeError, 'stop the server'):
                manager.reset(self.repo)
        self.assertTrue(self.root.is_dir())
        self.assert_preserved()


if __name__ == '__main__':
    unittest.main()
