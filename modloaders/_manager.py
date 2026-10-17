"""Loader installers shared by the small shell entry points. Python stdlib only."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
import zipfile

REPO = Path(__file__).resolve().parents[1]
USER_AGENT = 'mc-server/1.0 (https://github.com/techmaster2013/mc-server)'


def fetch(url):
    if urllib.parse.urlparse(url).scheme != 'https':
        raise RuntimeError('refusing a non-https download')
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
            return urllib.request.urlopen(request, timeout=120)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)


def text(url):
    with fetch(url) as response:
        return response.read().decode('utf-8')


def metadata(url):
    return json.loads(text(url))


def download(url, target, algorithm=None, checksum=None):
    digest = hashlib.new(algorithm or 'sha256')
    with fetch(url) as response, target.open('wb') as output:
        for chunk in iter(lambda: response.read(1024 * 1024), b''):
            output.write(chunk)
            digest.update(chunk)
    if checksum and digest.hexdigest().lower() != checksum.lower():
        raise RuntimeError('download checksum mismatch: ' + target.name)
    if not zipfile.is_zipfile(target):
        raise RuntimeError('download is not a jar: ' + target.name)


def safe_root(repo):
    # Intentionally ignore SERVER_DIR: these tools manage only this repo's folder.
    target = repo / 'minecraft-server'
    if target.is_symlink() or target.resolve() != repo.resolve() / 'minecraft-server':
        raise RuntimeError('refusing a symlinked or redirected minecraft-server folder')
    if target.exists() and not target.is_dir():
        raise RuntimeError('minecraft-server exists but is not a directory')
    return target


def inside(root, path):
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise RuntimeError('refusing a symlink or path outside minecraft-server: ' + str(path))
    return path


def running_java(root):
    # Also catches old start.sh copies that predate the shared lock.
    for process in Path('/proc').glob('[0-9]*'):
        try:
            if (process / 'cwd').resolve() == root.resolve():
                command = (process / 'cmdline').read_bytes().split(b'\0')[0]
                if Path(os.fsdecode(command)).name == 'java':
                    return True
        except (OSError, RuntimeError):
            continue
    return False


@contextlib.contextmanager
def stopped(root):
    import fcntl
    with contextlib.ExitStack() as stack:
        locks = [root / '.server.lock']
        if (root / '.playit').exists():
            locks.append(root / '.playit/launcher.lock')
        for path in locks:
            inside(root, path)
            handle = stack.enter_context(path.open('a'))
            try:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError('stop the server/tunnel before switching loaders or resetting') from None
        if running_java(root):
            raise RuntimeError('minecraft is still running; type stop first')
        yield


def reset(repo):
    root = safe_root(repo)
    if not root.exists():
        print('minecraft-server/ is already gone')
        return
    with stopped(root):
        # Verify the exact target again immediately before recursive deletion.
        if root != safe_root(repo):
            raise RuntimeError('server folder changed during reset')
        shutil.rmtree(root)
    print('deleted minecraft-server/ (including worlds, settings and playit credentials)')


def version_key(value):
    return tuple(int(part) for part in value.split('.'))


def select_fabric(mc, requested):
    rows = metadata('https://meta.fabricmc.net/v2/versions/loader/' + mc)
    row = next((r for r in rows if r['loader']['version'] == requested), None) if requested else next(
        (r for r in rows if r['loader']['stable']), None)
    if row is None:
        raise RuntimeError('no matching stable fabric loader for minecraft ' + mc)
    installer = next(r for r in metadata('https://meta.fabricmc.net/v2/versions/installer') if r['stable'])
    loader = row['loader']['version']
    url = 'https://meta.fabricmc.net/v2/versions/loader/' + mc + '/' + loader + '/' + installer['version'] + '/server/jar'
    # Fabric's generated launcher endpoint has no published checksum; use official HTTPS.
    return loader, url


def select_paper(mc, requested):
    rows = metadata('https://fill.papermc.io/v3/projects/paper/versions/' + mc + '/builds')
    if not isinstance(rows, list):
        raise RuntimeError('paper returned no builds for minecraft ' + mc)
    row = next((r for r in rows if str(r['id']) == requested), None) if requested else next(
        (r for r in rows if r['channel'] == 'STABLE'), None)
    if row is None or row['channel'] != 'STABLE':
        raise RuntimeError('no matching stable paper build for minecraft ' + mc)
    return str(row['id']), row['downloads']['server:default']


def select_forge(mc, requested):
    if requested:
        build = requested.removeprefix(mc + '-')
    else:
        promos = metadata('https://files.minecraftforge.net/net/minecraftforge/forge/promotions_slim.json')['promos']
        build = promos.get(mc + '-recommended') or promos.get(mc + '-latest')
    if not build or not re.fullmatch(r'[0-9]+(?:\.[0-9]+)+', build):
        raise RuntimeError('no matching forge release for minecraft ' + mc)
    full = mc + '-' + build
    return full, 'https://maven.minecraftforge.net/net/minecraftforge/forge/' + full + '/forge-' + full + '-installer.jar'


def select_neoforge(mc, requested):
    parts = mc.split('.')
    if parts[0] == '1':
        prefix = parts[1] + '.' + (parts[2] if len(parts) > 2 else '0') + '.'
    else:
        prefix = mc + '.'
    versions = ET.fromstring(text('https://maven.neoforged.net/releases/net/neoforged/neoforge/maven-metadata.xml'))
    available = [v.text for v in versions.findall('./versioning/versions/version') if v.text]
    candidates = [v for v in available if v.startswith(prefix) and re.fullmatch(r'\d+(?:\.\d+)+', v)]
    build = requested or (max(candidates, key=version_key) if candidates else None)
    if not build or build not in available or not build.startswith(prefix):
        raise RuntimeError('no matching neoforge release for minecraft ' + mc + '; neo supports 1.20.2+ here')
    if not re.fullmatch(r'\d+(?:\.\d+)+(?:-beta)?', build):
        raise RuntimeError('invalid neoforge version')
    return build, 'https://maven.neoforged.net/releases/net/neoforged/neoforge/' + build + '/neoforge-' + build + '-installer.jar'


def install_forge_family(kind, mc, requested, java, stage):
    build, url = select_forge(mc, requested) if kind == 'forge' else select_neoforge(mc, requested)
    installer = stage / 'installer.jar'
    checksum = text(url + '.sha1').strip().split()[0]
    download(url, installer, 'sha1', checksum)
    with zipfile.ZipFile(installer) as archive:
        profile = json.loads(archive.read('install_profile.json'))
    if profile.get('minecraft') != mc:
        raise RuntimeError('installer targets a different minecraft version; refusing to install it')
    print('running official ' + kind + ' installer ' + build, flush=True)
    subprocess.run([str(java), '-jar', str(installer), '--installServer'], cwd=stage, check=True)
    group = 'net/minecraftforge/forge' if kind == 'forge' else 'net/neoforged/neoforge'
    args = Path('libraries') / group / build / 'unix_args.txt'
    if not (stage / args).is_file():
        raise RuntimeError('installer did not create modern unix_args.txt; use forge for minecraft 1.18+ or neo 1.20.2+')
    return build, args


def copy_libraries(stage, root):
    # Newer Forge args launch a shim jar from the server root, not just libraries/.
    sources = list((stage / 'libraries').rglob('*'))
    sources += list(stage.glob('forge-*.jar')) + list(stage.glob('neoforge-*.jar'))
    for source in sorted(sources):
        if source.is_symlink():
            raise RuntimeError('unexpected symlink in installer output')
        destination = inside(root, root / source.relative_to(stage))
        if source.is_dir():
            destination.mkdir(parents=True, exist_ok=True)
        elif source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            # Replace library files atomically, after the official installer succeeds.
            with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as handle:
                temporary = Path(handle.name)
            try:
                shutil.copyfile(source, temporary)
                temporary.replace(destination)
            finally:
                temporary.unlink(missing_ok=True)


def launcher(major, arguments):
    return '''#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
exec 9>.server.lock
flock -n 9 || { echo 'server or maintenance is already running' >&2; exit 1; }
if ! grep -Eq '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true[[:space:]]*$' eula.txt; then
  echo 'read https://aka.ms/MinecraftEULA, then set eula=true in eula.txt' >&2
  exit 1
fi
memory="${MC_MEMORY:-2G}"
[[ "$memory" =~ ^[1-9][0-9]*[MG]$ ]] || { echo 'MC_MEMORY must look like 2G or 1536M' >&2; exit 1; }
exec "./.java-MAJOR/bin/java" -Xms512M "-Xmx$memory" ARGS nogui "$@"
'''.replace('MAJOR', str(major)).replace('ARGS', shlex.join(arguments))


def activate(root, kind, major, arguments, build):
    start = inside(root, root / 'start.sh')
    if start.exists():
        backups = inside(root, root / '.loader-backups')
        backups.mkdir(exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix='start-', suffix='.sh', dir=backups, delete=False) as handle:
            handle.write(start.read_bytes())
    temporary = root / '.start-new.sh'
    inside(root, temporary)
    temporary.write_text(launcher(major, arguments))
    temporary.chmod(0o755)
    temporary.replace(start)
    state = inside(root, root / 'server-type.json')
    state.write_text(json.dumps({'type': kind, 'version': build}, indent=2) + '\n')


def switch(repo, kind, requested_mc='', requested_loader=''):
    root = safe_root(repo)
    if not (root / 'minecraft-version.txt').is_file():
        raise RuntimeError('run bash install.sh [minecraft-version] first, then run this loader script')
    mc = (root / 'minecraft-version.txt').read_text().strip()
    if not re.fullmatch(r'\d+(?:\.\d+)+', mc):
        raise RuntimeError('invalid minecraft version record')
    if requested_mc and requested_mc != mc:
        raise RuntimeError('installed minecraft is ' + mc + '; these scripts never upgrade or downgrade your world')
    if kind == 'vanilla' and requested_loader:
        raise RuntimeError('vanilla.sh does not take a loader version')
    with stopped(root):
        manifest = metadata('https://piston-meta.mojang.com/mc/game/version_manifest_v2.json')
        entry = next((v for v in manifest['versions'] if v['id'] == mc and v['type'] == 'release'), None)
        if entry is None:
            raise RuntimeError('unknown minecraft release: ' + mc)
        details = metadata(entry['url'])
        major = details.get('javaVersion', {}).get('majorVersion', 21)
        java = root / ('.java-' + str(major)) / 'bin/java'
        if not java.is_file():
            raise RuntimeError('missing java runtime; rerun bash install.sh first')
        with tempfile.TemporaryDirectory(prefix='.loader-', dir=root) as temporary:
            stage = Path(temporary)
            if kind == 'vanilla':
                server = details['downloads']['server']
                download(server['url'], stage / 'server.jar', 'sha1', server['sha1'])
                inside(root, root / 'server.jar')
                (stage / 'server.jar').replace(root / 'server.jar')
                for name in ('mods', 'plugins'):
                    folder = root / name
                    if folder.is_symlink():
                        folder.unlink()  # never follow a symlink outside the server
                    elif folder.is_dir():
                        shutil.rmtree(folder)
                    elif folder.exists():
                        folder.unlink()
                build, arguments = mc, ['-jar', 'server.jar']
            elif kind == 'fabric':
                build, url = select_fabric(mc, requested_loader)
                download(url, stage / 'fabric-server-launch.jar')
                inside(root, root / 'fabric-server-launch.jar')
                (stage / 'fabric-server-launch.jar').replace(root / 'fabric-server-launch.jar')
                # The executable launcher manages its own Fabric libraries on first start.
                arguments = ['-jar', 'fabric-server-launch.jar']
            elif kind == 'paper':
                build, artifact = select_paper(mc, requested_loader)
                download(artifact['url'], stage / 'paper.jar', 'sha256', artifact['checksums']['sha256'])
                inside(root, root / 'paper.jar')
                (stage / 'paper.jar').replace(root / 'paper.jar')
                arguments = ['-jar', 'paper.jar']
            else:
                build, args = install_forge_family(kind, mc, requested_loader, java, stage)
                copy_libraries(stage, root)
                arguments = ['@' + args.as_posix()]
            if kind in ('fabric', 'forge', 'neoforge', 'paper'):
                inside(root, root / ('plugins' if kind == 'paper' else 'mods')).mkdir(exist_ok=True)
            activate(root, kind, major, arguments, build)
    print('installed ' + kind + ' ' + build + ' for minecraft ' + mc)
    print('start it from the repo root: cd minecraft-server/ && bash start-public.sh')
    if kind == 'vanilla':
        print('mods/ and plugins/ deleted; world, settings, eula and playit kept')
    else:
        print('world/settings kept. use mods/plugins matching this loader and minecraft version.')


def main():
    kind, *arguments = sys.argv[1:]
    if kind == 'reset':
        if arguments:
            raise RuntimeError('reset takes no arguments')
        reset(REPO)
    elif kind in ('fabric', 'forge', 'neoforge', 'paper', 'vanilla') and len(arguments) <= 2:
        switch(REPO, kind, *arguments)
    else:
        raise RuntimeError('unknown loader or too many arguments')


if __name__ == '__main__':
    try:
        main()
    except (RuntimeError, OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile) as error:
        print('error: ' + str(error), file=sys.stderr)
        sys.exit(1)
