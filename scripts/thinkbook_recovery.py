"""Capture or unpack a private ThinkBook state package for the existing installer."""

import argparse
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from http.client import HTTPConnection
from http.cookies import SimpleCookie
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from urllib.parse import unquote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4
from zipfile import ZIP_DEFLATED, ZipFile


FORMAT = 'fishhome-thinkbook-recovery-v1'
ASTRO = 'dev/astrbot-4.29.4/data'
NAPCAT = 'dev/napcat-4.18.38'
RUNNER = ('DSH', 'DSH-Commission', 'm3-file-check', 'commission-files')
MANAGER = ('settings.json', 'credentials.bin', 'bili')
ACTIVITY = 'plugin_data/astrbot_plugin_bili_activity/activity.sqlite3'
WRITERS = ('astrbot', 'dsh_commission', 'dsh', 'bili', 'budget')
PROACTIVE = 'plugins/astrbot_plugin_proactive_chat'


def verify(archive, destination=None):
    from scripts.mock_backup import check_database, staged_directory
    from scripts.native_archive import path_parts

    with ZipFile(archive) as bundle:
        names = bundle.namelist()
        if len({name.casefold() for name in names}) != len(names):
            raise ValueError('还原包存在重复文件。')
        manifest = json.loads(bundle.read('manifest.json'))
        files = set(names) - {'manifest.json'}
        required = {f'install/{ASTRO}/cmd_config.json', f'install/{ASTRO}/data_v4.db',
                    f'install/{ASTRO}/{ACTIVITY}', 'manager/settings.json', 'manager/credentials.bin',
                    'runner/m3-file-check/.m3-root'}
        if manifest.get('format') != FORMAT or not required <= files or set(manifest.get('files', ())) != files:
            raise ValueError('ThinkBook 还原包清单不完整。')
        prefixes = (f'install/{ASTRO}/', f'install/{NAPCAT}/napcat/config/', 'manager/bili/',
                    *(f'runner/{name}/' for name in RUNNER))
        for name in names:
            path_parts(name)
            if name not in required | {'manifest.json', f'install/{NAPCAT}/config.json'} and not name.startswith(prefixes):
                raise ValueError('还原包包含未知路径。')
        if not all(any(name.startswith(prefix) for name in files) for prefix in
                   (f'install/{NAPCAT}/napcat/config/', *(f'runner/{name}/' for name in RUNNER))):
            raise ValueError('还原包缺少 NapCat 或文件任务状态。')
        if bundle.testzip() is not None:
            raise ValueError('ThinkBook 还原包文件校验失败。')
        if destination is not None:
            with staged_directory(destination) as stage:
                bundle.extractall(stage)
                for file in stage.rglob('*'):
                    if file.is_file() and file.suffix in {'.db', '.sqlite3'}:
                        with file.open('rb') as stream:
                            is_sqlite = stream.read(16) == b'SQLite format 3\0'
                        if is_sqlite:
                            check_database(file)
    return manifest


def capture(root, archive):
    sys.path.insert(0, str(root / 'repo'))
    import psutil
    import win32profile
    from deploy.desktop_manager.windows import Credentials, impersonate

    port = 6196
    pid = next((row.pid for row in psutil.net_connections('tcp') if row.status == 'LISTEN'
                and row.laddr and row.laddr.port == port and row.laddr.ip == '127.0.0.1'), None)
    if pid is None:
        raise RuntimeError('请先打开 ThinkBook 托盘。')
    process = psutil.Process(pid)
    args = process.cmdline()
    if ('deploy.desktop_manager.manager' not in args or '--dev' not in args or '--state' not in args
            or Path(args[args.index('--dev') + 1]).resolve() != root / 'dev'):
        raise RuntimeError('ThinkBook 托盘与安装目录不匹配。')
    state = Path(args[args.index('--state') + 1])
    token = process.environ()['FISH_MANAGER_TOKEN']
    try:
        credentials = Credentials(state / 'credentials.bin')
    except Exception as exc:
        raise RuntimeError(f'无法读取 ThinkBook 控制台凭据（Windows {getattr(exc, "winerror", "未知")}）。') from None
    password = credentials.values.get('runner_password')
    if not password:
        raise RuntimeError('ThinkBook 控制台尚未保存 Runner 凭据。')
    data = root / ASTRO

    def runner_profile(handle):
        return Path(win32profile.GetUserProfileDirectory(handle)) / 'AppData/Local/FishHome'

    def fresh_cookie(dsh_port, folder):
        with impersonate('FishHomeRunner', password) as handle:
            log = (runner_profile(handle) / folder / 'web-start.log').read_text(encoding='utf-8', errors='replace')
        tickets = re.findall(rf'http://127\.0\.0\.1:{dsh_port}/\?token=([A-Za-z0-9._~%-]+)', log)
        if not tickets:
            raise ValueError('DSH ticket')
        query = urlencode({'token': unquote(tickets[-1]), 'interactions': 'none'})
        with closing(HTTPConnection('127.0.0.1', dsh_port, timeout=5)) as conn:
            conn.request('GET', '/connect?' + query)
            response = conn.getresponse()
            cookie = SimpleCookie(response.getheader('Set-Cookie', ''))
            if response.status != 302 or len(cookie) != 1:
                raise ValueError('DSH login')
            name, morsel = next(iter(cookie.items()))
        return f'{name}={morsel.value}'

    def api(route, action=False):
        req = Request(f'http://127.0.0.1:{port}{route}', headers={'Authorization': 'Bearer ' + token},
                      data=b'' if action else None)
        with urlopen(req, timeout=20) as response:
            return json.load(response)

    def services():
        return {row['id']: row for row in api('/api/status')['services']}

    def idle(status):
        with closing(sqlite3.connect((data / ACTIVITY).as_uri() + '?mode=ro', uri=True)) as db:
            pending = db.execute("""SELECT 1 FROM settings WHERE
              (key LIKE 'commissioned-file:v1:%' AND json_extract(value,'$.state') IN ('prepared','submitting','submitted')) OR
              (key LIKE 'owner-reminder:v1:%' AND json_extract(value,'$.status')='sending') LIMIT 1""").fetchone()
            billed = db.execute('SELECT 1 FROM fish_cost_calls WHERE actual IS NULL LIMIT 1').fetchone()
        if pending or billed:
            raise RuntimeError('ThinkBook 有待处理的委托、提醒投递或计费请求，请稍后重试。')
        for service, dsh_port, folder in (('dsh', 59934, 'DSH'), ('dsh_commission', 59935, 'DSH-Commission')):
            if status[service]['state'] != 'running':
                continue
            rpc = json.dumps({'type': 'client-request', 'rpcId': str(uuid4()), 'method': 'session/list',
                              'payload': {'args': {'_request': {}}}}).encode()
            try:
                req = Request(f'http://127.0.0.1:{dsh_port}/api/session/list', data=rpc, headers={
                    'Cookie': fresh_cookie(dsh_port, folder), 'Content-Type': 'application/json'})
                with urlopen(req, timeout=8) as response:
                    result = json.load(response)['result']
                items = result['value']['items'] if result['ok'] else None
                if not isinstance(items, list):
                    raise ValueError('session.list')
            except (OSError, ValueError, KeyError, TypeError) as exc:
                status = getattr(exc, 'code', type(exc).__name__)
                raise RuntimeError(f'无法确认 {service} 已空闲（{status}），请检查 ThinkBook 上的 DSH 连接。') from None
            if any(item.get('running') for item in items):
                raise RuntimeError('ThinkBook 正在处理文件任务，请结束后重试。')

    def wait_for(names, target):
        deadline = time.monotonic() + (120 if target == 'running' else 20)
        while time.monotonic() < deadline:
            current = services()
            waiting = [name for name in names if current[name]['state'] != target]
            if not waiting:
                return
            time.sleep(.5)
        phase = '恢复' if target == 'running' else '停止'
        raise RuntimeError(f'ThinkBook 服务未能{phase}：{", ".join(waiting)}。')

    def add(bundle, source, name, skip=()):
        info = source.lstat()
        if source.is_symlink() or getattr(info, 'st_file_attributes', 0) & 1024:
            raise RuntimeError('还原范围内存在 Windows 链接，请先检查该目录。')
        if stat.S_ISDIR(info.st_mode):
            for child in sorted(source.iterdir()):
                if child.name not in skip and child.name != '__pycache__' and not child.name.endswith('-shm'):
                    add(bundle, child, f'{name}/{child.name}')
        elif stat.S_ISREG(info.st_mode):
            bundle.write(source, name)
        else:
            raise RuntimeError('还原范围内存在非常规文件。')

    status = services()
    if any(status[name]['state'] not in {'running', 'stopped'} or
           status[name]['state'] == 'running' and not status[name]['recognized'] for name in WRITERS):
        raise RuntimeError('请等 ThinkBook 服务启动完成并检查有无端口冲突。')
    idle(status)
    stopped = []
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    partial = archive.with_suffix('.partial')
    try:
        try:
            for name in WRITERS:
                if status[name]['state'] == 'running':
                    stopped.append(name)
                    api(f'/api/services/{name}/stop', True)
            wait_for(WRITERS, 'stopped')
            idle(services())
            with ZipFile(partial, 'x', compression=ZIP_DEFLATED, compresslevel=3) as bundle:
                add(bundle, data, f'install/{ASTRO}', skip=('plugins', 'temp'))
                if (data / PROACTIVE).is_dir():
                    add(bundle, data / PROACTIVE, f'install/{ASTRO}/{PROACTIVE}', skip=('.git',))
                cat = root / NAPCAT
                add(bundle, cat / 'napcat/config', f'install/{NAPCAT}/napcat/config')
                if (cat / 'config.json').is_file():
                    add(bundle, cat / 'config.json', f'install/{NAPCAT}/config.json')
                for name in MANAGER:
                    source = state / name
                    if name != 'bili' or source.exists():
                        add(bundle, source, f'manager/{name}')
                try:
                    with impersonate('FishHomeRunner', password) as handle:
                        profile = runner_profile(handle)
                        for name in RUNNER:
                            add(bundle, profile / name, f'runner/{name}')
                except OSError as exc:
                    raise RuntimeError(f'无法备份 ThinkBook 文件工作区（Windows {exc.winerror}）。') from None
                bundle.writestr('manifest.json', json.dumps({'format': FORMAT, 'captured_utc': stamp,
                    'source_root': str(root), 'source_account': os.environ['USERNAME'], 'files': bundle.namelist()}, indent=2))
        finally:
            restart_failed = []
            for name in reversed(stopped):
                try:
                    api(f'/api/services/{name}/start', True)
                except (OSError, ValueError):
                    restart_failed.append(name)
            wait_for(stopped, 'running')
            if restart_failed:
                raise RuntimeError(f'ThinkBook 恢复服务时发生错误：{", ".join(restart_failed)}。')
        partial.rename(archive)
    finally:
        partial.unlink(missing_ok=True)
    with archive.open('rb') as stream:
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'name': f'thinkbook-recovery-{stamp}.zip', 'sha256': digest}


def restore_installation(archive, root):
    import psutil
    import win32profile
    from deploy.desktop_manager.windows import Credentials, impersonate

    # Existing absolute workspace references and DPAPI state belong to this installation and Windows user.
    protected_ports = {6185, 6196, 6197, 6199, 6099, 59934, 59935, 8011, 8767}
    if any(row.laddr and row.laddr.port in protected_ports and row.status == 'LISTEN'
           for row in psutil.net_connections('tcp')):
        raise RuntimeError('还原前请停止 FishHome 服务并退出托盘。')
    state = Path(os.environ['LOCALAPPDATA']) / 'FishHome/manager'
    data = root / ASTRO
    marker = root / 'dev/.thinkbook-setup'
    if not marker.is_file() or not (data / 'cmd_config.json').is_file():
        raise RuntimeError('请先运行安装器完成程序初始化。')
    with tempfile.TemporaryDirectory(dir=root, prefix='restore-') as temp:
        staged = Path(temp) / 'verified'
        manifest = verify(archive, staged)
        if Path(manifest['source_root']).resolve() != root or manifest['source_account'] != os.environ['USERNAME']:
            raise RuntimeError('请使用原 ThinkBook 的 FishHomeOps 账户和原安装目录还原。')
        creds = Credentials(staged / 'manager/credentials.bin')
        password = creds.values['runner_password']
        tag = '.before-restore-' + uuid4().hex[:10]
        moves = []

        def replace(target, prepare):
            prior = target.with_name(target.name + tag)
            pending = target.with_name(target.name + tag + '.new')
            prepare(pending)
            existed = target.exists()
            if existed:
                target.rename(prior)
            moves.append((target, prior if existed else None, pending))
            pending.rename(target)

        def undo(entries):
            for target, prior, pending in reversed(entries):
                if target.exists() and not pending.exists():
                    target.rename(pending)
                if prior is not None:
                    prior.rename(target)

        def prepare_data(target):
            restored = staged / 'install' / ASTRO
            shutil.copytree(restored, target)
            shutil.copytree(data / 'plugins', target / 'plugins', dirs_exist_ok=True)
            if (restored / PROACTIVE).is_dir():
                shutil.copytree(restored / PROACTIVE, target / PROACTIVE, dirs_exist_ok=True)

        # Read the ZIP as FishHomeOps, then write individual workspace files as Runner to retain ownership.
        with ZipFile(archive) as bundle:
            try:
                replace(data, prepare_data)
                cat = root / NAPCAT
                replace(cat / 'napcat/config', lambda dest: shutil.copytree(staged / 'install' / NAPCAT / 'napcat/config', dest))
                if (staged / 'install' / NAPCAT / 'config.json').is_file():
                    replace(cat / 'config.json', lambda dest: shutil.copyfile(staged / 'install' / NAPCAT / 'config.json', dest))
                state.mkdir(parents=True, exist_ok=True)
                for name in MANAGER:
                    source = staged / 'manager' / name
                    if source.exists():
                        copy = shutil.copytree if source.is_dir() else shutil.copyfile
                        replace(state / name, lambda dest: copy(source, dest))
                runner_start = len(moves)
                with impersonate('FishHomeRunner', password) as handle:
                    profile = Path(win32profile.GetUserProfileDirectory(handle)) / 'AppData/Local/FishHome'

                    def prepare_runner(name, target):
                        target.mkdir()
                        prefix = f'runner/{name}/'
                        for item in manifest['files']:
                            if item.startswith(prefix):
                                destination = target / item.removeprefix(prefix)
                                destination.parent.mkdir(parents=True, exist_ok=True)
                                with bundle.open(item) as source, destination.open('xb') as output:
                                    shutil.copyfileobj(source, output)
                        if (target / 'sample.csv').is_file():
                            (target / 'sample.csv').chmod(stat.S_IREAD)

                    try:
                        for name in RUNNER:
                            replace(profile / name, lambda dest: prepare_runner(name, dest))
                    except Exception:
                        undo(moves[runner_start:])
                        del moves[runner_start:]
                        raise
            except Exception:
                undo(moves)
                raise
    print('Recovery applied. Previous directories have a .before-restore suffix.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    take = commands.add_parser('capture')
    take.add_argument('root', type=Path)
    take.add_argument('archive', type=Path)
    take.add_argument('--receipt', type=Path)
    unpack = commands.add_parser('unpack')
    unpack.add_argument('archive', type=Path)
    unpack.add_argument('destination', type=Path)
    restore = commands.add_parser('restore-installation')
    restore.add_argument('archive', type=Path)
    restore.add_argument('root', type=Path)
    args = parser.parse_args()
    if args.command == 'unpack':
        verify(args.archive.resolve(), args.destination.resolve())
        print('Recovery package verified and unpacked.')
        return 0
    if args.command == 'restore-installation':
        restore_installation(args.archive.resolve(), args.root.resolve())
        return 0
    try:
        result = capture(args.root.resolve(), args.archive.resolve())
    except Exception as exc:
        error = str(exc) if type(exc) is RuntimeError else f'ThinkBook 备份失败（{type(exc).__name__}）。'
        result = {'error': error}
    payload = json.dumps(result)
    if args.receipt:
        pending = args.receipt.with_suffix('.tmp')
        pending.write_text(payload, encoding='ascii')
        pending.rename(args.receipt)
    else:
        print(payload)
    return int('error' in result)


if __name__ == '__main__':
    raise SystemExit(main())
