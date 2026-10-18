# 冒烟测试：伪造 state → 起浮窗 → 截屏 → 收工
# 用法: pwsh -File smoke-overlay.ps1
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$dir  = Join-Path $env:USERPROFILE '.dsh\bullet'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$state = Join-Path $dir 'smoke-state.json'
$shot  = Join-Path $root 'probe\smoke.png'
$py    = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'

# 1) 假的 agent 状态
@'
{
  "v": 1,
  "status": "thinking",
  "title": "给 DSH 做弹幕姬",
  "step": 12,
  "thought": "先确认 live-chunk 究竟带不带 thinking 标记",
  "said": "我把浮窗拆成上下两层：上面穿透，下面可点",
  "tool": "ops_bash",
  "toolDetail": "python smoke.py --dry-run",
  "seq": 1
}
'@ | Set-Content -Path $state -Encoding UTF8

# 2) 清掉旧实例（按命令行认，别用"最近 N 分钟"——那会漏掉跑久了的，互斥体又挡住新实例）
Get-CimInstance Win32_Process -Filter "Name='pythonw.exe'" |
    Where-Object { $_.CommandLine -like "*$root*" } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
Start-Sleep -Milliseconds 500

# 3) 起两层（独立进程，不阻塞）
# BULLET_CAPTURABLE=1：临时允许被截屏，否则 WDA_EXCLUDEFROMCAPTURE 会把它从截图里抹掉
$env:BULLET_STATE = $state
$env:BULLET_CAPTURABLE = '1'
$env:BULLET_PORT = '9'   # 冒烟时不连真插件
$env:BULLET_ALWAYS_SHOW = '1'   # 输入条平时只在鼠标悬停时出现，截图时要强制显示
Start-Process -FilePath $py -ArgumentList (Join-Path $root 'src\overlay.py') -WorkingDirectory (Join-Path $root 'src')
Start-Sleep -Milliseconds 700
Start-Process -FilePath $py -ArgumentList (Join-Path $root 'src\inputbar.py') -WorkingDirectory (Join-Path $root 'src')
Start-Sleep -Milliseconds 1200

# 4) 截图（左上角 760x420）
Add-Type -AssemblyName System.Drawing
$bmp = New-Object System.Drawing.Bitmap 760, 420
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen(0, 0, 0, 0, $bmp.Size)
$bmp.Save($shot, [System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose(); $bmp.Dispose()
Write-Host "saved: $shot"

# 5) 收工
Get-Process pythonw -ErrorAction SilentlyContinue | Where-Object { $_.StartTime -gt (Get-Date).AddMinutes(-5) } | Stop-Process -Force -ErrorAction SilentlyContinue
Write-Host "overlay stopped"
