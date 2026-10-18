# 一键卸载 DSH 弹幕姬
# 用法: pwsh -File tools\uninstall.ps1          # 保留 ~/.dsh/bullet 数据
#       pwsh -File tools\uninstall.ps1 -Purge   # 连数据一起删
param([switch]$Purge)
$ErrorActionPreference = 'Stop'
$profile  = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
$patch    = Join-Path $profile 'cordis.patch.yml'
$link     = Join-Path $profile 'node_modules\@local\dsh-bullet'
$bulletDir= Join-Path $env:USERPROFILE '.dsh\bullet'

# 路径断言：确认目标真的在预期位置，否则中止
if (-not (Test-Path $patch)) { throw "中止：找不到 $patch" }
if ($bulletDir -notlike '*\.dsh\bullet') { throw "中止：bulletDir 异常 $bulletDir" }

# 1) 收掉浮窗进程（新版记 pids.json，旧版记 overlay.pid，两种都认）
$stopped = 0
$pidsFile = Join-Path $bulletDir 'pids.json'
if (Test-Path $pidsFile) {
    try {
        $obj = Get-Content $pidsFile -Raw | ConvertFrom-Json
        foreach ($p in $obj.PSObject.Properties) {
            $n = [int]$p.Value
            if ($n -gt 0) {
                Stop-Process -Id $n -Force -ErrorAction SilentlyContinue
                $stopped++
            }
        }
    } catch { Write-Host "pids.json 解析失败，跳过" -ForegroundColor Yellow }
    Remove-Item $pidsFile -Force -ErrorAction SilentlyContinue
}
$legacy = Join-Path $bulletDir 'overlay.pid'
if (Test-Path $legacy) {
    $opid = (Get-Content $legacy -Raw).Trim()
    foreach ($part in $opid -split ',') {
        if ($part -match '^\d+$') {
            Stop-Process -Id ([int]$part) -Force -ErrorAction SilentlyContinue
            $stopped++
        }
    }
    Remove-Item $legacy -Force -ErrorAction SilentlyContinue
}
Remove-Item (Join-Path $bulletDir 'port.txt') -Force -ErrorAction SilentlyContinue
Write-Host "已停止 $stopped 个浮窗进程"

# 2) 摘掉补丁层里那一整段（它是追加在文件末尾的）
$lines = Get-Content $patch
$cut = ($lines | Select-String -Pattern '^# ── 弹幕姬' | Select-Object -First 1).LineNumber
if ($null -ne $cut) {
    Copy-Item $patch "$patch.bak-uninstall-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    ($lines[0..($cut - 2)] -join "`n") + "`n" |
        Set-Content $patch -Encoding UTF8 -NoNewline
    Write-Host "patch layer cleaned"
} else {
    Write-Host "patch already clean"
}

# 3) 删链接（junction 用 rmdir，不跟随目标删内容）
if (Test-Path $link) {
    cmd /c rmdir "$link" | Out-Null
    Write-Host "link removed"
}

if ($Purge) {
    Remove-Item -Recurse -Force $bulletDir
    Write-Host "runtime data purged: $bulletDir"
}
Write-Host ""
Write-Host "重启 DSH 后生效。" -ForegroundColor Yellow
