# 安装 DSH 弹幕姬（幂等：重复跑不会重复挂）
# 用法: pwsh -File tools\install.ps1
$ErrorActionPreference = 'Stop'
$root     = Split-Path -Parent $PSScriptRoot   # 项目根：跟着脚本走，别硬编码盘符
$profile  = Join-Path $env:USERPROFILE '.dsh\profiles\desktop'
$patch    = Join-Path $profile 'cordis.patch.yml'
$linkDir  = Join-Path $profile 'node_modules\@local'
$link     = Join-Path $linkDir 'dsh-bullet'
$bulletDir= Join-Path $env:USERPROFILE '.dsh\bullet'

# 路径断言：只碰这两个已知目录
foreach ($p in @($profile, $bulletDir)) {
    if (-not (Test-Path $p)) { throw "路径不存在，中止: $p" }
}

# 1) 链接插件包
#    ⚠️ 必须是真的 junction。`mklink /J` 在某些 shell 里会静默退化成"复制一份"，
#    结果改了源文件宿主却还在跑旧副本（踩过一次，白重启两轮）。
if (Test-Path $link) {
    $existing = Get-Item $link -Force
    if (-not $existing.LinkType) {
        Write-Host "发现旧副本（不是链接），删除后重建" -ForegroundColor Yellow
        if ($link -notlike '*\.dsh\profiles\desktop\node_modules\@local\dsh-bullet') { throw "断言失败：$link" }
        Remove-Item -Recurse -Force $link
    }
}
if (-not (Test-Path $link)) {
    New-Item -ItemType Directory -Force -Path $linkDir | Out-Null
    New-Item -ItemType Junction -Path $link -Target "$root\plugin" | Out-Null
}
$after = Get-Item $link -Force
if ($after.LinkType -ne 'Junction') { throw "链接建立失败（LinkType=$($after.LinkType)）" }
Write-Host "linked: $link -> $($after.Target)"

# 自检：链接里的代码必须就是当前源码，否则宿主会跑旧版本
$needle = 'startCommandChannel'
if (-not (Select-String -Path "$link\index.js" -Pattern $needle -SimpleMatch -Quiet)) {
    throw "链接内容与源码不一致：$link\index.js 里找不到 $needle"
}

# 2) 补丁层插入（已有就跳过）
$text = Get-Content $patch -Raw -Encoding UTF8
if ($text -notmatch 'bullet-bridge') {
    Copy-Item $patch "$patch.bak-bullet-$(Get-Date -Format 'yyyyMMdd-HHmmss')"
    $block = @"

# ── 弹幕姬（dsh-bullet）──────────────────────────────────────────────
# 把会话事件压成 ~/.dsh/bullet/state.json，并起停桌面浮窗。
# 卸载：删掉这一整段（或恢复 cordis.patch.yml.bak-bullet-*）后重启。
- insert:
    - id: bullet-bridge
      name: "@local/dsh-bullet"
"@
    Add-Content -Path $patch -Value $block -Encoding UTF8
    Write-Host "patch layer updated"
} else {
    Write-Host "patch already contains bullet-bridge"
}

# 3) 运行时目录 + 配置
New-Item -ItemType Directory -Force -Path $bulletDir | Out-Null
$py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python312\pythonw.exe'
if (-not (Test-Path $py)) { throw "找不到 pythonw: $py" }
@{ python = $py; overlay = "$root\src\overlay.py" } |
    ConvertTo-Json | Set-Content (Join-Path $bulletDir 'config.json') -Encoding UTF8
Write-Host "config written"

Write-Host ""
Write-Host "下一步：重启 DSH 桌面端（插件只在启动时组合）。" -ForegroundColor Yellow
Write-Host "重启后随便发一句话，浮窗应出现在左上角。"
