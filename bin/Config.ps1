<#
.SYNOPSIS
  SkillHome 共享配置模块 — 被 Sync/skillhome/Discover 脚本 dot-source 引用。
  所有路径动态推导，零硬编码，可在任何用户的电脑上运行。
  跨平台支持：Windows (NTFS junction) / Linux & macOS (symlink)。
#>

# ============================================================
# 平台检测
# ============================================================
# 注意：PowerShell 7 内置 $IsWindows 是只读变量，用 $SH_IsWindows 避免冲突
$SH_IsWindows = $PSVersionTable.Platform -ne 'Unix'
if ($SH_IsWindows) {
  $UserProfile = $env:USERPROFILE
} else {
  $UserProfile = $env:HOME
}

# ============================================================
# 路径推导（零硬编码）
# ============================================================
$HomeRoot        = Join-Path $UserProfile '.skillhome'
$CentralSkills   = Join-Path $HomeRoot 'skills'
$BinDir          = Join-Path $HomeRoot 'bin'
$ConfigPath      = Join-Path $HomeRoot 'config.json'
$LogFile         = Join-Path $HomeRoot 'skillhome.log'
$PwshExe         = if (Get-Command pwsh -ErrorAction SilentlyContinue) { (Get-Command pwsh).Source }
                   elseif ($SH_IsWindows -and (Test-Path 'C:\Program Files\PowerShell\7\pwsh.exe')) { 'C:\Program Files\PowerShell\7\pwsh.exe' }
                   else { 'pwsh' }

# ============================================================
# 默认配置
# ============================================================
# 已知的 agent skill 目录模式（相对于用户目录）
# Windows 用反斜杠，Unix 用正斜杠
if ($SH_IsWindows) {
  $KnownSkillDirPatterns = @(
    '.devin\skills',
    '.agents\skills',
    '.claude\skills',
    '.codeium\windsurf\skills',
    '.codex\skills',
    '.cursor\skills-cursor',
    '.cursor\skills',
    '.qoderworkcn\skills',
    '.qoder\skills',
    '.gemini\antigravity\skills',
    '.gemini\skills',
    '.cc-switch\skills',
    '.config\devin\skills',
    '.roo\skills',
    '.kilocode\skills',
    '.kiro\skills',
    '.trae\skills',
    '.lingma\skills',
    '.qwen\skills',
    '.copilot\skills',
    '.continue\skills',
    '.cline\skills',
    '.antigravity\skills',
    '.marscode\skills',
    '.cagent\skills',
    '.bito\skills',
    '.comate\skills',
    '.codeverse\skills',
    '.continuum\skills',
    '.kimi-code\skills',
    '.trae-aicc\skills'
  )
} else {
  # Unix 路径模式 — 大部分 agent 在 ~ 下用相同的 .dirname 结构
  $KnownSkillDirPatterns = @(
    '.devin/skills',
    '.agents/skills',
    '.claude/skills',
    '.codex/skills',
    '.cursor/skills',
    '.gemini/skills',
    '.config/claude/skills',
    '.config/codex/skills',
    '.config/devin/skills',
    '.continue/skills',
    '.cline/skills',
    '.roo/skills',
    '.kilocode/skills',
    '.kiro/skills',
    '.trae/skills',
    '.lingma/skills',
    '.qwen/skills',
    '.copilot/skills',
    '.local/share/claude/skills',
    '.local/share/codex/skills'
  )
}

$DefaultSkipNames = @('.system', '.git', '.temp', '_shared')
$DefaultSimilarityThreshold = 0.95

# ============================================================
# 配置读写
# ============================================================
function Get-SkillHomeConfig {
  if (-not (Test-Path $ConfigPath)) { return $null }
  try {
    $raw = Get-Content -Path $ConfigPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $cfg = @{
      agentDirs = [ordered]@{}
      skipNames = @($raw.skipNames)
      similarityThreshold = if ($raw.similarityThreshold) { $raw.similarityThreshold } else { $DefaultSimilarityThreshold }
    }
    foreach ($prop in $raw.agentDirs.PSObject.Properties) {
      $cfg.agentDirs[$prop.Name] = $prop.Value
    }
    return $cfg
  } catch {
    Write-Warning "config.json 解析失败: $_"
    return $null
  }
}

function Save-SkillHomeConfig {
  param(
    [Parameter(Mandatory)]
    $AgentDirs,
    [string[]]$SkipNames = $DefaultSkipNames,
    [double]$SimilarityThreshold = $DefaultSimilarityThreshold
  )
  $cfg = [ordered]@{
    version = '1.0'
    createdAt = (Get-Date).ToString('o')
    userProfile = $UserProfile
    centralSkills = $CentralSkills
    agentDirs = $AgentDirs
    skipNames = $SkipNames
    similarityThreshold = $SimilarityThreshold
  }
  $json = $cfg | ConvertTo-Json -Depth 5
  [System.IO.File]::WriteAllText($ConfigPath, $json, (New-Object System.Text.UTF8Encoding $false))
}

function Test-SkillHomeInitialized {
  return (Test-Path $HomeRoot) -and (Test-Path $ConfigPath)
}

function Initialize-SkillHomeDirs {
  if (-not (Test-Path $HomeRoot)) { New-Item -Path $HomeRoot -ItemType Directory -Force | Out-Null }
  if (-not (Test-Path $CentralSkills)) { New-Item -Path $CentralSkills -ItemType Directory -Force | Out-Null }
  if (-not (Test-Path $BinDir)) { New-Item -Path $BinDir -ItemType Directory -Force | Out-Null }
}
