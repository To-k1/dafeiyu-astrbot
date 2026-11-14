"""Pull ThinkBook recovery state over its existing administrator SSH connection."""

import base64
import json
import re
import shutil
import subprocess
from uuid import uuid4

from scripts.mock_backup import checksum
from scripts.thinkbook_recovery import verify

from .thinkbook import select_root, ssh_args


def capture_and_pull(destination):
    from scripts import thinkbook_recovery

    job = 'fishhome-recovery-' + uuid4().hex
    ssh = [*ssh_args(), 'fishbook', 'powershell.exe', '-NoProfile', '-NonInteractive', '-EncodedCommand']
    scp = [shutil.which('scp') or 'scp', '-q', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
           '-o', 'ConnectTimeout=8']

    def run(args, timeout):
        return subprocess.run(args, check=False, capture_output=True, encoding='utf-8', errors='replace',
                              timeout=timeout, creationflags=subprocess.CREATE_NO_WINDOW)

    def remote(script, timeout):
        encoded = base64.b64encode(script.encode('utf-16le')).decode('ascii')
        return run([*ssh, encoded], timeout)

    filename = job + '.py'
    if run([*scp, thinkbook_recovery.__file__, f'fishbook:{filename}'], 30).returncode:
        raise RuntimeError('无法通过 fishbook 上传备份程序，请检查 ThinkBook 的 SSH 连接。')
    partial = destination / (job + '.partial')
    pulled = False
    try:
        result = remote("$ErrorActionPreference='Stop';$ProgressPreference='SilentlyContinue';"
                        + select_root() + f"""
$job='{job}'
$script=Join-Path $HOME '{filename}'
$archive=Join-Path $HOME "$job.zip"
$receipt=Join-Path $HOME "$job.json"
$scheduler=New-Object -ComObject 'Schedule.Service'
$scheduler.Connect()
$folder=$scheduler.GetFolder('\\')
$task=$scheduler.NewTask(0)
$task.Principal.UserId=[Security.Principal.WindowsIdentity]::GetCurrent().Name
$task.Principal.LogonType=3
$task.Settings.DisallowStartIfOnBatteries=$false
$task.Settings.StopIfGoingOnBatteries=$false
$task.Settings.ExecutionTimeLimit='PT12M'
$action=$task.Actions.Create(0)
$action.Path=Join-Path $root 'dev\\astrbot-4.29.4\\.venv\\Scripts\\pythonw.exe'
$action.WorkingDirectory=Join-Path $root 'repo'
$action.Arguments='-B "' + $script + '" capture "' + $root + '" "' + $archive + '" --receipt "' + $receipt + '"'
$registered=$folder.RegisterTaskDefinition($job,$task,2,$task.Principal.UserId,$null,3,$null)
try {{
    $running=$registered.Run($null)
    for ($i=0; $i -lt 540; $i++) {{
        if (Test-Path -LiteralPath $receipt) {{ Get-Content -LiteralPath $receipt -Raw; exit 0 }}
        if ($i -gt 3 -and $registered.State -notin @(2,4)) {{ exit 5 }}
        Start-Sleep -Seconds 1
    }}
    exit 6
}} finally {{
    $folder.DeleteTask($job,0)
}}
""", 600)
        try:
            receipt = json.loads(result.stdout.strip())
        except ValueError:
            raise RuntimeError('ThinkBook 未返回备份结果，请检查安装目录和控制台。') from None
        if result.returncode or 'error' in receipt:
            raise RuntimeError(receipt.get('error', 'ThinkBook 备份失败。'))
        name = receipt.get('name', '')
        if not re.fullmatch(r'thinkbook-recovery-\d{8}T\d{6}\.\d{6}Z\.zip', name):
            raise RuntimeError('ThinkBook 返回了无效的还原包名称。')
        destination.mkdir(parents=True, exist_ok=True)
        if run([*scp, f'fishbook:{job}.zip', str(partial)], 600).returncode:
            raise RuntimeError('ThinkBook 已生成还原包，但拉回台式机失败。')
        if checksum(partial) != receipt.get('sha256'):
            raise RuntimeError('还原包传输校验失败。')
        verify(partial)
        final = destination / name
        partial.rename(final)
        pulled = True
        return final
    finally:
        partial.unlink(missing_ok=True)
        paths = [filename, job + '.json', *([job + '.zip'] if pulled else [])]
        try:
            remote(''.join(f"Remove-Item -LiteralPath (Join-Path $HOME '{path}') -ErrorAction SilentlyContinue;"
                           for path in paths), 15)
        except (OSError, subprocess.SubprocessError):
            pass
