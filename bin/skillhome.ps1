<#
.SYNOPSIS
  SkillHome 命令行入口 — 管理统一 skill 仓库。零硬编码，可移植。

.DESCRIPTION
  用法:
    skillhome init              首次初始化：自动扫描发现 skill 目录，生成 config.json
    skillhome discover          重新扫描发现 skill 目录（更新 config.json）
    skillhome sync              手动触发增量同步
    skillhome status            查看当前状态
    skillhome list              列出所有 skill 及其分布
    skillhome link <skill> <agent>    把 skill link 到 agent 目录
    skillhome unlink <skill> <agent>  从 agent 目录移除 junction
    skillhome watch             手动启动 watcher（调试用）
    skillhome stop              停止 watcher 进程
    skillhome config            查看当前配置
    skillhome help              显示帮助

  安装别名（可选）:
    在 PowerShell profile 里加: Set-Alias skillhome "<HomeRoot>\bin\skillhome.ps1"
#>
[CmdletBinding()]
param(
  [Parameter(Position = 0)]
  [string]$Command = 'help',

  [Parameter(Position = 1, ValueFromRemainingArguments = $true)]
  [string[]]$Args
)

$ErrorActionPreference = 'Continue'

. (Join-Path $PSScriptRoot 'Config.ps1')

# ============================================================
# init — 首次初始化
# ============================================================
function Invoke-Init {
  Write-Host "=== SkillHome 初始化 ===" -ForegroundColor Cyan
  Write-Host ""
  Initialize-SkillHomeDirs
  Write-Host "中央仓库: $CentralSkills" -ForegroundColor Gray
  Write-Host ""

  # 运行自检索
  $discoverScript = Join-Path $BinDir 'Discover-SkillDirs.ps1'
  & $PwshExe -NoProfile -ExecutionPolicy Bypass -File $discoverScript -Force 2>&1

  if (Test-Path $ConfigPath) {
    Write-Host ""
    Write-Host "初始化完成！下一步: skillhome sync" -ForegroundColor Green
  }
}

# ============================================================
# discover — 重新扫描
# ============================================================
function Invoke-Discover {
  $discoverScript = Join-Path $BinDir 'Discover-SkillDirs.ps1'
  & $PwshExe -NoProfile -ExecutionPolicy Bypass -File $discoverScript -Force 2>&1
}

# ============================================================
# sync — 手动触发增量同步
# ============================================================
function Invoke-Sync {
  $cfg = Get-SkillHomeConfig
  if (-not $cfg) { Write-Host "config.json 不存在，请先运行: skillhome init" -ForegroundColor Red; return }
  Write-Host "触发增量同步..." -ForegroundColor Cyan
  $syncScript = Join-Path $BinDir 'Sync-SkillHome.ps1'
  & $PwshExe -NoProfile -ExecutionPolicy Bypass -File $syncScript -Incremental -VerboseLog 2>&1
}

# ============================================================
# status — 查看当前状态
# ============================================================
function Show-Status {
  $cfg = Get-SkillHomeConfig
  if (-not $cfg) { Write-Host "config.json 不存在，请先运行: skillhome init" -ForegroundColor Red; return }

  $centralCount = (Get-ChildItem -Path $CentralSkills -Directory -ErrorAction SilentlyContinue).Count
  $totalJunctions = 0
  $totalReals = 0
  $agentStatus = @()

  foreach ($agentName in $cfg.agentDirs.Keys) {
    $dir = $cfg.agentDirs[$agentName]
    if (-not (Test-Path $dir)) {
      $agentStatus += [PSCustomObject]@{ Agent = $agentName; Junctions = 0; Reals = 0; Status = 'NOT_FOUND' }
      continue
    }
    $children = Get-ChildItem -Path $dir -Directory -Force -ErrorAction SilentlyContinue |
      Where-Object { $cfg.skipNames -notcontains $_.Name }
    $junctions = ($children | Where-Object { $_.LinkType -in @('Junction','SymbolicLink') }).Count
    $reals = ($children | Where-Object { -not $_.LinkType }).Count
    $totalJunctions += $junctions
    $totalReals += $reals
    $agentStatus += [PSCustomObject]@{ Agent = $agentName; Junctions = $junctions; Reals = $reals; Status = if($reals -eq 0){'OK'}else{'NEEDS_SYNC'} }
  }

  Write-Host "SkillHome 状态" -ForegroundColor Cyan
  Write-Host "  中央仓库: $CentralSkills" -ForegroundColor Gray
  Write-Host "  skill 总数: $centralCount" -ForegroundColor Gray
  Write-Host "  junction 总数: $totalJunctions" -ForegroundColor Gray
  Write-Host "  残留真实目录: $totalReals" -ForegroundColor $(if($totalReals -eq 0){'Gray'}else{'Yellow'})
  Write-Host "  agent 目录数: $($cfg.agentDirs.Count)" -ForegroundColor Gray
  Write-Host ""
  $agentStatus | Format-Table -AutoSize
}

# ============================================================
# list — 列出所有 skill 及其分布
# ============================================================
function Show-List {
  if (-not (Test-Path $CentralSkills)) { Write-Host "中央仓库不存在" -ForegroundColor Red; return }
  $skills = Get-ChildItem -Path $CentralSkills -Directory -ErrorAction SilentlyContinue | Sort-Object Name
  $rows = @()
  foreach ($s in $skills) {
    $metaPath = Join-Path $s.FullName '.skillhome.json'
    $sources = @()
    $merged = $false
    if (Test-Path $metaPath) {
      try {
        $meta = Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json
        $sources = @($meta.sources)
        $merged = $meta.merged
      } catch {}
    }
    $rows += [PSCustomObject]@{
      Skill = $s.Name
      Sources = ($sources -join ', ')
      Merged = if($merged){'Y'}else{''}
    }
  }
  $rows | Format-Table -AutoSize
  Write-Host "共 $($rows.Count) 个 skill" -ForegroundColor Gray
}

# ============================================================
# link <skill> <agent>
# ============================================================
function Invoke-Link {
  param([string]$SkillName, [string]$AgentName)
  $cfg = Get-SkillHomeConfig
  if (-not $cfg) { Write-Host "请先运行: skillhome init" -ForegroundColor Red; return }
  if (-not $SkillName -or -not $AgentName) {
    Write-Host "用法: skillhome link <skill> <agent>" -ForegroundColor Red
    Write-Host "agent 名称: $($cfg.agentDirs.Keys -join ', ')" -ForegroundColor Gray
    return
  }
  if (-not $cfg.agentDirs.Contains($AgentName)) {
    Write-Host "未知 agent: $AgentName" -ForegroundColor Red
    Write-Host "可选: $($cfg.agentDirs.Keys -join ', ')" -ForegroundColor Gray
    return
  }
  $centralPath = Join-Path $CentralSkills $SkillName
  if (-not (Test-Path $centralPath)) {
    Write-Host "中央仓库没有此 skill: $SkillName" -ForegroundColor Red
    return
  }
  $linkPath = Join-Path $cfg.agentDirs[$AgentName] $SkillName
  if (Test-Path $linkPath) {
    $item = Get-Item $linkPath -Force
    if ($item.LinkType -in @('Junction','SymbolicLink')) {
      Write-Host "已存在 junction: $linkPath -> $($item.Target)" -ForegroundColor Yellow
      return
    } else {
      Write-Host "目标位置已有真实目录，不覆盖: $linkPath" -ForegroundColor Red
      return
    }
  }
  cmd /c mklink /J "$linkPath" "$centralPath" 2>&1 | Out-Null
  if (Test-Path $linkPath) {
    Write-Host "已创建 junction: $AgentName/$SkillName -> central" -ForegroundColor Green
    $metaPath = Join-Path $centralPath '.skillhome.json'
    if (Test-Path $metaPath) {
      $meta = Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $sources = @($meta.sources) + @($AgentName) | Sort-Object -Unique
      $meta.sources = $sources
      $json = $meta | ConvertTo-Json -Depth 5 -Compress
      [System.IO.File]::WriteAllText($metaPath, $json, (New-Object System.Text.UTF8Encoding $false))
    }
  } else {
    Write-Host "创建 junction 失败" -ForegroundColor Red
  }
}

# ============================================================
# unlink <skill> <agent>
# ============================================================
function Invoke-Unlink {
  param([string]$SkillName, [string]$AgentName)
  $cfg = Get-SkillHomeConfig
  if (-not $cfg) { Write-Host "请先运行: skillhome init" -ForegroundColor Red; return }
  if (-not $SkillName -or -not $AgentName) {
    Write-Host "用法: skillhome unlink <skill> <agent>" -ForegroundColor Red
    return
  }
  if (-not $cfg.agentDirs.Contains($AgentName)) {
    Write-Host "未知 agent: $AgentName" -ForegroundColor Red
    return
  }
  $linkPath = Join-Path $cfg.agentDirs[$AgentName] $SkillName
  if (-not (Test-Path $linkPath)) {
    Write-Host "路径不存在: $linkPath" -ForegroundColor Red
    return
  }
  $item = Get-Item $linkPath -Force
  if ($item.LinkType -in @('Junction','SymbolicLink')) {
    cmd /c rmdir "$linkPath" 2>&1 | Out-Null
    Write-Host "已移除 junction: $AgentName/$SkillName" -ForegroundColor Green
    $centralPath = Join-Path $CentralSkills $SkillName
    $metaPath = Join-Path $centralPath '.skillhome.json'
    if (Test-Path $metaPath) {
      $meta = Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json
      $sources = @($meta.sources) | Where-Object { $_ -ne $AgentName }
      $meta.sources = $sources
      $json = $meta | ConvertTo-Json -Depth 5 -Compress
      [System.IO.File]::WriteAllText($metaPath, $json, (New-Object System.Text.UTF8Encoding $false))
    }
  } else {
    Write-Host "这不是 junction，不删除真实目录: $linkPath" -ForegroundColor Red
  }
}

# ============================================================
# global <skill> [on|off] — 设置/取消全局共享标记
# ============================================================
function Set-Global {
  param([string]$SkillName, [string]$Action)
  $cfg = Get-SkillHomeConfig
  if (-not $cfg) { Write-Host "请先运行: skillhome init" -ForegroundColor Red; return }
  if (-not $SkillName) {
    Write-Host "用法: skillhome global <skill> [on|off]" -ForegroundColor Red
    Write-Host "  on  — 标记为全局，sync 时自动扩散到所有 agent" -ForegroundColor Gray
    Write-Host "  off — 取消全局标记，仅保留来源 agent 的 junction" -ForegroundColor Gray
    # 列出当前已标记 global 的 skill
    $globals = @()
    Get-ChildItem $CentralSkills -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $mp = Join-Path $_.FullName '.skillhome.json'
      if (Test-Path $mp) {
        $m = Get-Content $mp -Raw -Encoding UTF8 | ConvertFrom-Json
        if ($m.global) { $globals += $_.Name }
      }
    }
    if ($globals.Count -gt 0) {
      Write-Host ""
      Write-Host "当前全局 skill:" -ForegroundColor Cyan
      foreach ($g in $globals) { Write-Host "  $g" -ForegroundColor Green }
    }
    return
  }
  $centralPath = Join-Path $CentralSkills $SkillName
  if (-not (Test-Path $centralPath)) {
    Write-Host "中央仓库没有此 skill: $SkillName" -ForegroundColor Red
    return
  }
  $metaPath = Join-Path $centralPath '.skillhome.json'
  $meta = $null
  if (Test-Path $metaPath) {
    $meta = Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json
  }
  $turnOn = ($Action -ne 'off' -and $Action -ne 'false' -and $Action -ne '0')
  if (-not $meta) {
    $meta = [PSCustomObject]@{ name=$SkillName; sources=@(); merged=$false; created_at=(Get-Date).ToString('o') }
  }
  $meta | Add-Member -NotePropertyName global -NotePropertyValue $turnOn -Force
  $json = $meta | ConvertTo-Json -Depth 5 -Compress
  [System.IO.File]::WriteAllText($metaPath, $json, (New-Object System.Text.UTF8Encoding $false))
  if ($turnOn) {
    Write-Host "$SkillName 已标记为全局，下次 sync 将扩散到所有 agent" -ForegroundColor Green
  } else {
    Write-Host "$SkillName 已取消全局标记" -ForegroundColor Yellow
    Write-Host "注意：已存在的 junction 不会自动移除，需要手动 unlink 或跑完整 sync" -ForegroundColor Gray
  }
}

# ============================================================
# config — 查看配置
# ============================================================
function Show-Config {
  if (-not (Test-Path $ConfigPath)) {
    Write-Host "config.json 不存在，请先运行: skillhome init" -ForegroundColor Red
    return
  }
  Write-Host "配置文件: $ConfigPath" -ForegroundColor Cyan
  Write-Host ""
  $raw = Get-Content $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
  Write-Host "  userProfile: $($raw.userProfile)" -ForegroundColor Gray
  Write-Host "  centralSkills: $($raw.centralSkills)" -ForegroundColor Gray
  Write-Host "  similarityThreshold: $($raw.similarityThreshold)" -ForegroundColor Gray
  Write-Host "  skipNames: $($raw.skipNames -join ', ')" -ForegroundColor Gray
  Write-Host "  agentDirs:" -ForegroundColor Gray
  foreach ($prop in $raw.agentDirs.PSObject.Properties) {
    Write-Host "    $($prop.Name): $($prop.Value)" -ForegroundColor Gray
  }
}

# ============================================================
# help
# ============================================================
function Show-Help {
  Write-Host @"

  SkillHome — 统一 skill 管理系统（可移植，零硬编码）

  命令:
    skillhome init              首次初始化：自动扫描发现 skill 目录，生成 config.json
    skillhome discover          重新扫描发现 skill 目录
    skillhome sync              手动触发增量同步
    skillhome status            查看当前状态
    skillhome list              列出所有 skill 及其分布
    skillhome link <skill> <agent>    把 skill link 到 agent 目录
    skillhome unlink <skill> <agent>  从 agent 目录移除 junction
    skillhome global <skill> [on|off] 设置/取消全局共享（sync 时自动扩散到所有 agent）
    skillhome config            查看当前配置
    skillhome help              显示此帮助

  中央仓库: $CentralSkills
  配置文件: $ConfigPath

  安装别名（可选，在 PowerShell profile 里加）:
    Set-Alias skillhome "$BinDir\skillhome.ps1"

  首次使用:
    1. skillhome init     (自动扫描发现 skill 目录)
    2. skillhome sync     (迁移到中央仓库 + 创建 junction)
    3. skillhome status   (检查状态)

"@ -ForegroundColor Cyan
}

# ============================================================
# 主分发
# ============================================================
switch ($Command.ToLower()) {
  'init'     { Invoke-Init }
  'discover' { Invoke-Discover }
  'sync'     { Invoke-Sync }
  'status'   { Show-Status }
  'list'     { Show-List }
  'link'     { Invoke-Link -SkillName $Args[0] -AgentName $Args[1] }
  'unlink'   { Invoke-Unlink -SkillName $Args[0] -AgentName $Args[1] }
  'global'   { Set-Global -SkillName $Args[0] -Action $Args[1] }
  'config'   { Show-Config }
  'help'     { Show-Help }
  default    { Write-Host "未知命令: $Command" -ForegroundColor Red; Show-Help }
}
