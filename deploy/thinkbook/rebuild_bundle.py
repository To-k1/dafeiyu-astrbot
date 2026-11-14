"""Rebuild the ThinkBook installer from local source and a verified installer base."""

import argparse
from pathlib import Path, PurePosixPath
import shutil
import subprocess
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ('Install-FishHome.cmd', 'Install-FishHome.ps1', 'Prepare-Runner.cmd', 'Prepare-Runner.ps1')
SOURCE_DIRS = {'assets', 'config', 'deploy', 'deploy-kit', 'deploy-macos', 'docs', 'persona', 'plugin', 'scripts', 'tests'}
SOURCE_FILES = {'.gitattributes', '.gitignore', 'AGENTS.md', 'CHANGELOG.md', 'LICENSE', 'M0_REPORT.md',
                'M1_REPORT.md', 'QUICKSTART.md', 'README.md'}
PENDING_SOURCE = ('deploy/desktop_manager/ThinkBookTray.cs', 'deploy/desktop_manager/install-thinkbook-tray.ps1',
                  'deploy/desktop_manager/backup_sync.py', 'deploy/desktop_manager/recovery.py',
                  'scripts/thinkbook_recovery.py', 'docs/THINKBOOK_RECOVERY_CHECKPOINT.md',
                  'deploy/thinkbook/rebuild_bundle.py')
EXTRAS = {'PhoneMCP-v0.5.0-source.zip', 'LivingMemory-2.9.1.zip', 'DSH-Bridge-1.0.0.zip',
          'PhoneMCP-Windows.patch', 'Start-DSH-M3.cmd', 'dsh/package.json', 'dsh/package-lock.json',
          'NapCat.Shell.Windows.Node.zip'}


def rebuild(base, output):
    if base.resolve() == output.resolve() or output.exists():
        raise ValueError('Choose a new installer output path.')
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode('utf-8').split('\0')
    source = {name for name in (*tracked, *PENDING_SOURCE) if name and (ROOT / name).is_file()
              and (PurePosixPath(name).parts[0] in SOURCE_DIRS or name in SOURCE_FILES)}
    required = {'deploy/desktop_manager/backup_sync.py', 'plugin/astrbot_plugin_bili_activity/life.py',
                'scripts/thinkbook_recovery.py', 'persona/fish-home-v2.3.1.txt'}
    if not required <= source:
        raise ValueError('Current FishHome source is incomplete.')
    partial = output.with_name(f'{output.name}.{uuid4().hex}.partial')
    try:
        with ZipFile(base) as prior, ZipFile(partial, 'x', ZIP_DEFLATED) as bundle:
            for entry in prior.infolist():
                path = PurePosixPath(entry.filename)
                if entry.is_dir() or not entry.filename.startswith('extras/'):
                    continue
                if path.is_absolute() or '..' in path.parts or '\\' in entry.filename or ':' in entry.filename:
                    raise ValueError('Invalid installer asset path.')
                if entry.filename.removeprefix('extras/') not in EXTRAS:
                    continue
                with prior.open(entry) as origin, bundle.open(entry, 'w') as target:
                    shutil.copyfileobj(origin, target)
            bridge = ROOT / 'deploy/dsh_bridge'
            bundle.writestr('extras/m5-file-only.yml', (bridge / 'm3-file-only.yml').read_bytes().rstrip() +
                            b'\n\n' + (bridge / 'm5-gateway.yml').read_bytes())
            for name in sorted(source):
                bundle.write(ROOT / name, 'repo/' + name)
            for name in SCRIPTS:
                bundle.write(ROOT / 'deploy/thinkbook' / name, name)
            bundle.write(ROOT / 'deploy/thinkbook/README.md', '先看这里.md')
        with ZipFile(partial) as bundle:
            if bundle.testzip() or len(set(bundle.namelist())) != len(bundle.namelist()):
                raise ValueError('Installer archive integrity check failed.')
            if not {f'extras/{name}' for name in EXTRAS} <= set(bundle.namelist()):
                raise ValueError('Installer base is missing a required dependency.')
            if any(bundle.read('repo/' + name) != (ROOT / name).read_bytes() for name in source):
                raise ValueError('Installer does not match the selected source.')
        partial.rename(output)
    finally:
        partial.unlink(missing_ok=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('base', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    rebuild(args.base, args.output)
    print(args.output)
