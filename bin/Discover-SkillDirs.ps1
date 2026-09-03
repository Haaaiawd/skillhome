<#
.SYNOPSIS
  SkillHome 自检索 — 扫描用户目录，自动发现所有 skill 存放地，写入 config.json。

.DESCRIPTION
  特征识别（不依赖目录名，看目录结构）：
  一个目录是 skill 仓库，当且仅当：
  1. 它有 ≥2 个子目录，每个子目录含 SKILL.md 或 .skill-metadata.yaml
     （≥2 避免单个随机 SKILL.md 造成的误报）
  2. 或它有 ≥1 个这样的子目录，且目录名含 "skill"（宽松匹配，辅助确认）

  扫描策略：
  - 第一阶段：快速探测已知路径模式（~30 个），命中且通过特征验证即纳入
  - 第二阶段：深度扫描用户目录下所有 . 开头的目录（深度 3），
    不看目录名，纯靠结构特征识别 skill 仓库

  这样能发现 Open Cloud、Hermes 等未知 agent 的 skill 目录，
  即使它们不叫 "skills" 也能被识别。

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
# 特征识别函数
# ============================================================

# 计算一个目录下有多少个 skill 子目录（含 SKILL.md 或 .skill-metadata.yaml）
function Get-SkillChildCount {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return 0 }
  $count = 0
  $children = Get-ChildItem -Path $Path -Directory -Force -ErrorAction SilentlyContinue
  foreach ($child in $children) {
    if ($SkipNames -contains $child.Name) { continue }
    if (Test-Path (Join-Path $child.FullName 'SKILL.md')) { $count++; continue }
    if (Test-Path (Join-Path $child.FullName '.skill-metadata.yaml')) { $count++; continue }
  }
  return $count
}

# 特征识别：一个目录是否是 skill 仓库
# 规则：≥2 个 skill 子目录 → 确认；1 个 + 目录名含 skill → 也确认
function Test-IsSkillRepo {
  param([string]$Path)
  if (-not (Test-Path $Path)) { return $false }
  $skillCount = Get-SkillChildCount -Path $Path
  if ($skillCount -ge 2) { return $true }
  if ($skillCount -ge 1 -and (Split-Path $Path -Leaf) -match 'skill') { return $true }
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
  # .hermes\capabilities => hermes
  # .opencloud\agent-skills => opencloud
  $rel = $Path.Substring($UserProfile.Length).TrimStart('\','/')
  $parts = $rel -split '[\\/]'
  # 取非 skill 类的词作为 agent 名
  $nameParts = @()
  foreach ($p in $parts) {
    if ($p -match '^skill') { continue }     # skills, skills-cursor
    if ($p -match 'skill$') { continue }      # agent-skills
    if ($p -eq 'capabilities') { continue }   # hermes 用这个名字
    if ($p -eq 'agents') { continue }         # .agents/skills
    $nameParts += $p
  }
  if ($nameParts.Count -eq 0) { return 'unknown' }
  $name = ($nameParts -join '-')
  $name = $name -replace '^\.', ''
  return $name
}

# ============================================================
# 阶段 1：快速探测已知模式
# ============================================================
Write-Host "=== SkillHome 自检索 ===" -ForegroundColor Cyan
Write-Host ""
Write-Host "[1/2] 探测已知路径模式..." -ForegroundColor Gray

$found = [ordered]@{}

foreach ($pattern in $KnownSkillDirPatterns) {
  $path = Join-Path $UserProfile $pattern
  if (Test-IsSkillRepo -Path $path) {
    $agentName = Get-AgentName -Path $path
    if ($found.Contains($agentName)) {
      $rel = $path.Substring($UserProfile.Length).TrimStart('\','/').Replace('\','-').Replace('/','-')
      $agentName = $rel -replace '^\.', '' -replace '-skills$','' -replace 'skills-',''
    }
    $info = Get-SkillDirInfo -Path $path
    $found[$agentName] = $path
    $skillCount = Get-SkillChildCount -Path $path
    $status = if ($info.Real -gt 0) { "real=$($info.Real)" } elseif ($info.Junction -gt 0) { "junction=$($info.Junction)" } else { "empty" }
    Write-Host "  [OK] $agentName => $path ($status, $skillCount skills)" -ForegroundColor Green
  }
}

# ============================================================
# 阶段 2：深度扫描——纯特征识别，不依赖目录名
# ============================================================
Write-Host ""
Write-Host "[2/2] 深度扫描（特征识别，不依赖目录名）..." -ForegroundColor Gray

# 排除的顶级目录（性能 + 安全）
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

# 深度扫描时要排除的路径片段
$ExcludePathPatterns = @(
  'extensions', 'builtin', '\.tmp', '\.github', 'node_modules', '\.git[\\/]',
  'computer-use', 'vendor_imports', 'curated'
)

# 阶段 1 已找到的目录及其子目录都要排除
$foundPaths = @()
foreach ($k in $found.Keys) { $foundPaths += $found[$k] }

# 扫描所有 . 开头的目录（不再要求名字含 skill）
$topDirs = Get-ChildItem -Path $UserProfile -Directory -Force -ErrorAction SilentlyContinue |
  Where-Object { $ExcludeTop -notcontains $_.Name -and $_.Name.StartsWith('.') }

$newCount = 0
foreach ($top in $topDirs) {
  try {
    # 递归找所有子目录，不限名字
    $candidates = Get-ChildItem -Path $top.FullName -Directory -Recurse -Depth $ScanDepth -Force -ErrorAction SilentlyContinue |
      Where-Object {
        $_.FullName -notlike "$HomeRoot*" -and
        $_.FullName -notmatch 'node_modules|\.git[\\/]'
      }
    foreach ($c in $candidates) {
      # 排除路径片段
      $skip = $false
      foreach ($pat in $ExcludePathPatterns) {
        if ($c.FullName -match $pat) { $skip = $true; break }
      }
      if ($skip) { continue }
      # 排除已找到目录的子目录
      foreach ($fp in $foundPaths) {
        if ($c.FullName -like "$fp*" -or $fp -like "$($c.FullName)*") { $skip = $true; break }
      }
      if ($skip) { continue }

      # 纯特征识别：不关心目录叫什么，只看结构
      if (-not (Test-IsSkillRepo -Path $c.FullName)) { continue }

      # 已在 found 中？
      $alreadyFound = $false
      foreach ($k in $found.Keys) { if ($found[$k] -eq $c.FullName) { $alreadyFound = $true; break } }
      if ($alreadyFound) { continue }

      $agentName = Get-AgentName -Path $c.FullName
      if ($found.Contains($agentName)) {
        $suffix = ($c.FullName.Substring($UserProfile.Length) -split '[\\/]')[-2]
        $agentName = "${agentName}-${suffix}"
      }
      if ($found.Contains($agentName)) { continue }

      $info = Get-SkillDirInfo -Path $c.FullName
      $skillCount = Get-SkillChildCount -Path $c.FullName
      $found[$agentName] = $c.FullName
      $newCount++
      $status = if ($info.Real -gt 0) { "real=$($info.Real)" } elseif ($info.Junction -gt 0) { "junction=$($info.Junction)" } else { "empty" }
      Write-Host "  [NEW] $agentName => $($c.FullName) ($status, $skillCount skills)" -ForegroundColor Yellow
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
    $skillCount = Get-SkillChildCount -Path $found[$k]
    $prompt = "纳入 $k => $($found[$k])? ($skillCount skills, real=$($info.Real), junction=$($info.Junction)) [Y/n]"
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
