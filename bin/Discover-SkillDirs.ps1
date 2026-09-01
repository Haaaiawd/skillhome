<#
.SYNOPSIS
  SkillHome 自检索 — 扫描用户目录，自动发现所有 skill 存放地，写入 config.json。

.DESCRIPTION
  识别特征（满足任一即认为是 skill 目录）：
  1. 目录名包含 "skill" 且目录下有子目录含 SKILL.md
  2. 目录名包含 "skill" 且目录下有子目录含 .skill-metadata.yaml
  3. 目录名包含 "skill" 且目录自身有 SKILL.md

  扫描策略：
  - 第一阶段：快速探测已知模式（~30 个常见路径），命中即纳入
  - 第二阶段：深度扫描用户目录下所有 . 开头的目录（深度 3），发现未知 skill 目录

  找到的目录写入 config.json，Sync/Watcher 从中读取。

.PARAMETER ScanDepth
  深度扫描的递归深度，默认 3。

.PARAMETER Force
  即使 config.json 已存在也重新扫描。

.PARAMETER Interactive
  交互模式：每个新发现的目录询问是否纳入。
#>
[CmdletBinding()]
param(
  [int]$ScanDepth = 3,
  [switch]$Force,
  [switch]$Interactive
)

$ErrorActionPreference = 'Continue'

# dot-source 配置模块
. (Join-Path $PSScriptRoot 'Config.ps1')

Initialize-SkillHomeDirs

# ============================================================
# 工具函数
# ============================================================
function Test-IsSkillDir {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $false }
  # 子目录有 SKILL.md
  $children = Get-ChildItem -Path $Path -Directory -Force -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    if (Test-Path (Join-Path $child.FullName 'SKILL.md')) { return $true }
    if (Test-Path (Join-Path $child.FullName '.skill-metadata.yaml')) { return $true }
  }
  # 自身有 SKILL.md
  if (Test-Path (Join-Path $Path 'SKILL.md')) { return $true }
  return $false
}

function Get-SkillDirInfo {
  param([string]$Path)
  $real = 0; $junction = 0; $empty = 0
  $children = Get-ChildItem -Path $Path -Directory -Force -ErrorAction SilentlyContinue
  foreach ($c in $children) {
    if ($c.LinkType -in @('Junction','SymbolicLink')) { $junction++ }
    elseif ((Get-ChildItem -Path $c.FullName -Recurse -Force -ErrorAction SilentlyContinue).Count -eq 0) { $empty++ }
    else { $real++ }
  }
  return @{ Real = $real; Junction = $junction; Empty = $empty }
}

# ============================================================
# 推导 agent 名称
# ============================================================
function Get-AgentName {
  param([string]$Path)
  # .devin\skills => devin
  # .cursor\skills-cursor => cursor
  # .gemini\antigravity\skills => gemini
  # .codeium\windsurf\skills => windsurf
  # .config\devin\skills => config-devin
  $rel = $Path.Substring($UserProfile.Length).TrimStart('\','/')
  $parts = $rel -split '[\\/]'
  # 取第一个非 skills 的部分作为 agent 名
  $nameParts = @()
  foreach ($p in $parts) {
    if ($p -match '^skill') { continue }
    $nameParts += $p
  }
  if ($nameParts.Count -eq 0) { return 'unknown' }
  # 如果有多个部分（如 .config\devin），用 - 连接
  $name = ($nameParts -join '-')
  # 去掉前导点
  $name = $name -replace '^\.', ''
  return $name
}

# ============================================================
# 阶段 1：快速探测已知模式
# ============================================================
Write-Host "=== SkillHome 自检索 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1/2] 探测已知 skill 目录模式..." -ForegroundColor Gray

$found = [ordered]@{}

foreach ($pattern in $KnownSkillDirPatterns) {
  $path = Join-Path $UserProfile $pattern
  if (Test-IsSkillDir -Path $path) {
    $agentName = Get-AgentName -Path $path
    # 处理重名（如 .cursor\skills 和 .cursor\skills-cursor 都存在）
    if ($found.Contains($agentName)) {
      # 用完整路径区分
      $rel = $path.Substring($UserProfile.Length).TrimStart('\','/').Replace('\','-').Replace('/','-')
      $agentName = $rel -replace '^\.', '' -replace '-skills$','' -replace 'skills-',''
    }
    $info = Get-SkillDirInfo -Path $path
    $found[$agentName] = $path
    $status = if ($info.Real -gt 0) { "real=$($info.Real)" } elseif ($info.Junction -gt 0) { "junction=$($info.Junction)" } else { "empty" }
    Write-Host "  [OK] $agentName => $path ($status)" -ForegroundColor Green
  }
}

# ============================================================
# 阶段 2：深度扫描发现未知目录
# ============================================================
Write-Host ""
Write-Host "[2/2] 深度扫描用户目录（depth=$ScanDepth）..." -ForegroundColor Gray

# 排除的顶级目录（性能）
$ExcludeTop = @(
  '.skillhome', '.cache', '.npm', '.cargo', '.rustup', '.conda', '.anaconda',
  '.m2', '.gradle', '.docker', '.ollama', '.ssh', '.gnupg', '.kube',
  '.android', '.dotnet', '.openjfx', '.platformio', '.ipython', '.jupyter',
  '.keras', '.matplotlib', '.vscode-R', '.xlwings', '.dbus-keyrings',
  '.ms-ad', '.aws', '.azure', '.oracle_jre_usage', '.windows-build-tools',
  '.npm-cache', '.mcp-auth', '.smithery', '.chub', '.cpz',
  'node_modules', 'AppData', 'OneDrive', '.git', 'Desktop', 'Documents',
  'Downloads', 'Music', 'Videos', 'Pictures', 'Contacts', 'Favorites',
  'Searches', 'Saved Games', 'Links', 'Templates', 'Recent', 'Cookies',
  'NetHood', 'PrintHood', 'SendTo', 'Start Menu', 'Local Settings',
  'Application Data', 'My Documents', 'Library', 'source', 'src',
  'public', 'rules', 'ai_completion', 'audiodump',
  # 编辑器/IDE 扩展目录 — 内含的 skills 是扩展自带的，不纳入
  '.vscode', '.windsurf', '.antigravity'
)

# 深度扫描时要排除的路径片段（出现在路径中就跳过）
$ExcludePathPatterns = @(
  'extensions', 'builtin', '\.tmp', '\.github', 'node_modules', '\.git\\',
  'computer-use'  # qoderworkcn 的嵌套 computer-use skills 是内部依赖
)

# 阶段 1 已找到的目录及其子目录都要排除（避免在已知 skill 目录里再找 skill 目录）
$foundPaths = @()
foreach ($k in $found.Keys) { $foundPaths += $found[$k] }

$topDirs = Get-ChildItem -Path $UserProfile -Directory -Force -ErrorAction SilentlyContinue |
  Where-Object { $ExcludeTop -notcontains $_.Name -and ($_.Name.StartsWith('.') -or $_.Name -match 'skill') }

$newCount = 0
foreach ($top in $topDirs) {
  try {
    $candidates = Get-ChildItem -Path $top.FullName -Directory -Recurse -Depth $ScanDepth -Force -ErrorAction SilentlyContinue |
      Where-Object {
        $_.Name -match 'skill' -and
        $_.FullName -notlike "$HomeRoot*" -and
        $_.FullName -notmatch 'node_modules|\.git\\'
      }
    # 更严格的过滤
    $filtered = @()
    foreach ($c in $candidates) {
      $skip = $false
      # 排除路径片段
      foreach ($pat in $ExcludePathPatterns) {
        if ($c.FullName -match $pat) { $skip = $true; break }
      }
      if ($skip) { continue }
      # 排除已找到目录的子目录
      foreach ($fp in $foundPaths) {
        if ($c.FullName -like "$fp\*") { $skip = $true; break }
      }
      if ($skip) { continue }
      $filtered += $c
    }
    foreach ($c in $filtered) {
      if (-not (Test-IsSkillDir -Path $c.FullName)) { continue }
      # 已在 found 中？
      $alreadyFound = $false
      foreach ($k in $found.Keys) { if ($found[$k] -eq $c.FullName) { $alreadyFound = $true; break } }
      if ($alreadyFound) { continue }

      $agentName = Get-AgentName -Path $c.FullName
      if ($found.Contains($agentName)) {
        # 重名处理：加路径后缀
        $suffix = ($c.FullName.Substring($UserProfile.Length) -split '[\\/]')[-2]
        $agentName = "${agentName}-${suffix}"
      }
      if ($found.Contains($agentName)) { continue }  # 还是重名，跳过

      $info = Get-SkillDirInfo -Path $c.FullName
      $found[$agentName] = $c.FullName
      $newCount++
      $status = if ($info.Real -gt 0) { "real=$($info.Real)" } elseif ($info.Junction -gt 0) { "junction=$($info.Junction)" } else { "empty" }
      Write-Host "  [NEW] $agentName => $($c.FullName) ($status)" -ForegroundColor Yellow
    }
  } catch {}
}

# ============================================================
# 交互模式：确认纳入
# ============================================================
$finalDirs = [ordered]@{}

if ($Interactive) {
  Write-Host ""
  Write-Host "确认纳入的目录:" -ForegroundColor Cyan
  foreach ($k in $found.Keys) {
    $info = Get-SkillDirInfo -Path $found[$k]
    $prompt = "纳入 $k => $($found[$k])? (real=$($info.Real), junction=$($info.Junction)) [Y/n]"
    $response = Read-Host $prompt
    if ($response -ne 'n' -and $response -ne 'N') {
      $finalDirs[$k] = $found[$k]
    }
  }
} else {
  $finalDirs = $found
}

# ============================================================
# 写入 config.json
# ============================================================
if ($finalDirs.Count -eq 0) {
  Write-Host "`n未发现任何 skill 目录" -ForegroundColor Red
  return
}

# 保留已有配置的 skipNames 和 threshold
$existing = $null
if (-not $Force -and (Test-Path $ConfigPath)) {
  $existing = Get-SkillHomeConfig
}
$skipNames = if ($existing) { $existing.skipNames } else { $script:DefaultSkipNames }
$threshold = if ($existing) { $existing.similarityThreshold } else { $script:DefaultSimilarityThreshold }

Save-SkillHomeConfig -AgentDirs $finalDirs -SkipNames $skipNames -SimilarityThreshold $threshold

Write-Host ""
Write-Host "=== 完成 ===" -ForegroundColor Green
Write-Host "发现 $($finalDirs.Count) 个 skill 目录，已写入 config.json" -ForegroundColor Green
Write-Host "  $ConfigPath" -ForegroundColor Gray
Write-Host ""
Write-Host "下一步: skillhome sync" -ForegroundColor Cyan
