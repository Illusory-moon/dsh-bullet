# 重启弹幕姬两层并验证单实例互斥体
$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py  = "$env:LOCALAPPDATA\Programs\Python\Python312\pythonw.exe"
$src = Join-Path $root 'src'
$D   = "$env:USERPROFILE\.dsh\bullet"

function Get-Bullet {
    # 按"命令行里含本项目路径"认，项目挪地方也不会误杀/漏杀
    Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
        Where-Object { $_.CommandLine -like "*$root*" }
}

foreach ($p in (Get-Bullet)) { Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

$o = Start-Process -FilePath $py -ArgumentList "$src\overlay.py"  -WorkingDirectory $src -PassThru
$i = Start-Process -FilePath $py -ArgumentList "$src\inputbar.py" -WorkingDirectory $src -PassThru
Start-Sleep -Milliseconds 1200

@{ overlay = $o.Id; inputbar = $i.Id } | ConvertTo-Json -Compress | Set-Content "$D\pids.json" -Encoding UTF8
"$($o.Id)" | Set-Content "$D\overlay.pid" -Encoding UTF8 -NoNewline

# 故意多拉 3 对：互斥体应该把它们全部挡掉
for ($k = 0; $k -lt 3; $k++) {
    Start-Process -FilePath $py -ArgumentList "$src\overlay.py"  -WorkingDirectory $src | Out-Null
    Start-Process -FilePath $py -ArgumentList "$src\inputbar.py" -WorkingDirectory $src | Out-Null
}
Start-Sleep -Milliseconds 2000

$list = Get-Bullet
Write-Host ("多拉 6 个后进程数（应为 2）: {0}" -f $list.Count)
$list | Select-Object ProcessId, @{n = 'Script'; e = { ($_.CommandLine -split '\\')[-1] } } | Format-Table -AutoSize
