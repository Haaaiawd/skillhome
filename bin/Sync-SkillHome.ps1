<#
.SYNOPSIS
  SkillHome 同步脚本 — 统一管理各 agent 的 skill，中央存储 + junction 挂载。
  从 config.json 读取配置，零硬编码，可在任何用户电脑上运行。

.PARAMETER DryRun
  只输出将要执行的操作，不实际移动/删除/创建任何东西。

.PARAMETER Incremental
  增量模式：非破坏性同步。供 watcher 和手动 sync 调用。

.PARAMETER VerboseLog
  输出详细日志。
#>
[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$Incremental,
  [switch]$VerboseLog
)

$ErrorActionPreference = 'Stop'

# dot-source 配置模块
. (Join-Path $PSScriptRoot 'Config.ps1')

# 加载配置
$cfg = Get-SkillHomeConfig
if (-not $cfg) {
  Write-Host "config.json 不存在，请先运行: skillhome init" -ForegroundColor Red
  exit 1
}

$AgentDirs             = $cfg.agentDirs
$SkipNames             = $cfg.skipNames
$SimilarityThreshold   = $cfg.similarityThreshold

# ============================================================
# 工具函数
# ============================================================
function Write-Log {
  param([string]$Msg, [string]$Level = 'INFO')
  if ($Level -eq 'DEBUG' -and -not $VerboseLog) { return }
  $color = switch ($Level) {
    'WARN'  { 'Yellow' }
    'ERROR' { 'Red' }
    'OK'    { 'Green' }
    'DRY'   { 'Cyan' }
    default { 'Gray' }
  }
  $line = "[$(Get-Date -Format 'HH:mm:ss')][$Level] $Msg"
  Write-Host $line -ForegroundColor $color
  try { Add-Content -Path $LogFile -Value $line -Encoding UTF8 -ErrorAction SilentlyContinue } catch {}
}

function Test-IsEmptyDir {
  param([string]$Path)
  $items = Get-ChildItem -Path $Path -Recurse -Force -ErrorAction SilentlyContinue
  return ($items.Count -eq 0)
}

function Get-DirFileHashes {
  param([string]$Path)
  $hashes = @{}
  $files = Get-ChildItem -Path $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -notmatch '\\\.skillhome\.json$' -and $_.FullName -notmatch '\\\.skill-metadata\.yaml$' }
  foreach ($f in $files) {
    $rel = $f.FullName.Substring($Path.Length).TrimStart('\','/')
    $h = (Get-FileHash -Path $f.FullName -Algorithm SHA256).Hash
    $hashes[$rel.ToLower()] = $h
  }
  return $hashes
}

function Get-Similarity {
  param([hashtable]$HashesA, [hashtable]$HashesB)
  $allKeys = @($HashesA.Keys) + @($HashesB.Keys) | Sort-Object -Unique
  if ($allKeys.Count -eq 0) { return 1.0 }
  $same = 0
  foreach ($k in $allKeys) {
    if ($HashesA.ContainsKey($k) -and $HashesB.ContainsKey($k) -and $HashesA[$k] -eq $HashesB[$k]) { $same++ }
  }
  return [math]::Round($same / $allKeys.Count, 4)
}

function Get-SkillMtime {
  param([string]$Path)
  $skillFile = Join-Path $Path 'SKILL.md'
  if (Test-Path $skillFile) { return (Get-Item $skillFile).LastWriteTime }
  $latest = Get-ChildItem -Path $Path -Recurse -File -Force -ErrorAction SilentlyContinue |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
  if ($latest) { return $latest.LastWriteTime } else { return [datetime]::MinValue }
}

function Remove-JunctionSafe {
  param([string]$Path)
  $item = Get-Item -Path $Path -Force -ErrorAction SilentlyContinue
  if (-not $item) { return }
  if ($item.LinkType -in @('Junction','SymbolicLink')) {
    cmd /c rmdir "$Path" 2>&1 | Out-Null
  } else {
    Remove-Item -Path $Path -Recurse -Force
  }
}

function New-JunctionSafe {
  param([string]$Link, [string]$Target)
  if (-not (Test-Path $Target)) { Write-Log "junction 目标不存在: $Target" 'ERROR'; return $false }
  if (Test-Path $Link) { Remove-JunctionSafe -Path $Link }
  cmd /c mklink /J "$Link" "$Target" 2>&1 | Out-Null
  if (Test-Path $Link) { return $true } else { Write-Log "创建 junction 失败: $Link" 'ERROR'; return $false }
}

function Read-Meta {
  param([string]$SkillPath)
  $metaPath = Join-Path $SkillPath '.skillhome.json'
  if (Test-Path $metaPath) {
    try { return Get-Content $metaPath -Raw -Encoding UTF8 | ConvertFrom-Json } catch {}
  }
  return $null
}

function Write-Meta {
  param([string]$SkillPath, [hashtable]$Meta)
  $metaPath = Join-Path ([string]$SkillPath) '.skillhome.json'
  $json = $Meta | ConvertTo-Json -Depth 5 -Compress
  [System.IO.File]::WriteAllText($metaPath, $json, (New-Object System.Text.UTF8Encoding $false))
}

# ============================================================
# 阶段 1：扫描
# ============================================================
Write-Log "=== SkillHome 同步开始 (mode: $(if($Incremental){'incremental'}else{'full'})) ==="

$skillRegistry = @{}
$centralDistribution = @{}

if (Test-Path $CentralSkills) {
  $centralDirs = Get-ChildItem -Path $CentralSkills -Directory -ErrorAction SilentlyContinue
  foreach ($cs in $centralDirs) {
    $meta = Read-Meta -SkillPath $cs.FullName
    if ($meta -and $meta.sources) {
      $centralDistribution[$cs.Name] = @($meta.sources)
    }
  }
}

foreach ($agentName in $AgentDirs.Keys) {
  $dir = $AgentDirs[$agentName]
  if (-not (Test-Path $dir)) { continue }
  $children = Get-ChildItem -Path $dir -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $SkipNames -notcontains $_.Name }
  foreach ($child in $children) {
    $sname = $child.Name
    $isJunction = ($child.LinkType -in @('Junction','SymbolicLink'))

    if (-not $skillRegistry.ContainsKey($sname)) {
      $skillRegistry[$sname] = @{ realLocations=@(); junctionLocations=@(); realPaths=@{} }
    }

    if ($isJunction) {
      $skillRegistry[$sname].junctionLocations += $agentName
    } else {
      if (Test-IsEmptyDir -Path $child.FullName) {
        Write-Log "[$agentName] $sname 空目录，清理" 'WARN'
        if (-not $DryRun) { Remove-Item -Path $child.FullName -Force }
        continue
      }
      $skillRegistry[$sname].realLocations += $agentName
      $skillRegistry[$sname].realPaths[$agentName] = $child.FullName
    }
  }
}

foreach ($sname in $centralDistribution.Keys) {
  if (-not $skillRegistry.ContainsKey($sname)) {
    $skillRegistry[$sname] = @{ realLocations=@(); junctionLocations=@(); realPaths=@{} }
  }
}

# ============================================================
# 阶段 2：迁移真实存储到中央仓库
# ============================================================
Write-Log '=== 阶段 2: 迁移真实存储 ==='

$skillDistribution = @{}
$centralPathOf = @{}

foreach ($sname in ($skillRegistry.Keys | Sort-Object)) {
  $info = $skillRegistry[$sname]
  $realAgents = @($info.realLocations)
  $junctionAgents = @($info.junctionLocations)
  $allAgents = @($realAgents + $junctionAgents | Sort-Object -Unique)

  if ($centralDistribution.ContainsKey($sname)) {
    $allAgents = @($allAgents + $centralDistribution[$sname] | Sort-Object -Unique)
  }

  $centralExisting = Join-Path $CentralSkills $sname
  $centralExists = (Test-Path $centralExisting)

  if ($realAgents.Count -eq 0) {
    if ($centralExists) {
      $centralPathOf[$sname] = $centralExisting
      $skillDistribution[$sname] = $allAgents
    }
    continue
  }

  if ($realAgents.Count -eq 1 -and -not $centralExists) {
    $srcAgent = $realAgents[0]
    $srcPath = $info.realPaths[$srcAgent]
    Write-Log "$sname : 迁移 ($srcAgent -> central)"
    if (-not $DryRun) { Move-Item -Path $srcPath -Destination $centralExisting -Force }
    $centralPathOf[$sname] = $centralExisting
    $skillDistribution[$sname] = $allAgents

  } elseif ($realAgents.Count -ge 1 -and $centralExists) {
    $centralHashes = Get-DirFileHashes -Path $centralExisting
    $centralMtime = Get-SkillMtime -Path $centralExisting

    foreach ($srcAgent in $realAgents) {
      $srcPath = $info.realPaths[$srcAgent]
      $srcHashes = Get-DirFileHashes -Path $srcPath
      $srcMtime = Get-SkillMtime -Path $srcPath
      $sim = Get-Similarity -HashesA $centralHashes -HashesB $srcHashes
      Write-Log "$sname : 检测到 $srcAgent 的真实副本，与中央相似度=$sim" 'WARN'

      if ($sim -ge $SimilarityThreshold) {
        if ($srcMtime -gt $centralMtime) {
          Write-Log "$sname : 以 $srcAgent 版本更新中央 (较新)"
          if (-not $DryRun) {
            Remove-Item -Path $centralExisting -Recurse -Force
            Move-Item -Path $srcPath -Destination $centralExisting -Force
          }
          $centralMtime = $srcMtime
        } else {
          Write-Log "$sname : 保留中央版本，删除 $srcAgent 副本 (较旧)"
          if (-not $DryRun) { Remove-Item -Path $srcPath -Recurse -Force }
        }
      } else {
        $suffixed = "${sname}--${srcAgent}"
        $suffixedCentral = Join-Path $CentralSkills $suffixed
        Write-Log "$sname : 相似度不足，保留为 $suffixed"
        if (-not $DryRun) { Move-Item -Path $srcPath -Destination $suffixedCentral -Force }
        $centralPathOf[$suffixed] = $suffixedCentral
        $skillDistribution[$suffixed] = @($srcAgent)
        $allAgents = @($allAgents | Where-Object { $_ -ne $srcAgent })
      }
    }
    $centralPathOf[$sname] = $centralExisting
    $skillDistribution[$sname] = $allAgents

  } elseif ($realAgents.Count -gt 1 -and -not $centralExists) {
    Write-Log "$sname : 多个真实来源 ($($realAgents -join ', '))，计算相似度..." 'WARN'
    $baseAgent = $realAgents[0]
    $basePath = $info.realPaths[$baseAgent]
    $mergedHashes = Get-DirFileHashes -Path $basePath
    $mergedMtime = Get-SkillMtime -Path $basePath
    $mergedAgent = $baseAgent
    $mergeNotes = @()

    for ($i = 1; $i -lt $realAgents.Count; $i++) {
      $other = $realAgents[$i]
      $otherPath = $info.realPaths[$other]
      $otherHashes = Get-DirFileHashes -Path $otherPath
      $otherMtime = Get-SkillMtime -Path $otherPath
      $sim = Get-Similarity -HashesA $mergedHashes -HashesB $otherHashes
      Write-Log "  相似度 $mergedAgent vs $other = $sim" 'DEBUG'

      if ($sim -ge $SimilarityThreshold) {
        if ($otherMtime -gt $mergedMtime) {
          $mergedAgent = $other; $mergedHashes = $otherHashes; $mergedMtime = $otherMtime
          $mergeNotes += "合并 $other (较新, sim=$sim) 覆盖前版"
        } else {
          $mergeNotes += "合并 $other (较旧, sim=$sim) 保留前版"
        }
      } else {
        $suffixed = "${sname}--${other}"
        $suffixedCentral = Join-Path $CentralSkills $suffixed
        Write-Log "  保留为独立条目: $suffixed"
        if (-not $DryRun) { Move-Item -Path $otherPath -Destination $suffixedCentral -Force }
        $centralPathOf[$suffixed] = $suffixedCentral
        $skillDistribution[$suffixed] = @($other)
        $allAgents = @($allAgents | Where-Object { $_ -ne $other })
      }
    }

    $mergedPath = $info.realPaths[$mergedAgent]
    Write-Log "$sname : 合并完成，主来源 $mergedAgent"
    if (-not $DryRun) {
      Move-Item -Path $mergedPath -Destination $centralExisting -Force
      foreach ($a in $realAgents) {
        if ($a -ne $mergedAgent -and $info.realPaths.ContainsKey($a) -and (Test-Path $info.realPaths[$a])) {
          Remove-Item -Path $info.realPaths[$a] -Recurse -Force
        }
      }
    }
    $centralPathOf[$sname] = $centralExisting
    $skillDistribution[$sname] = $allAgents
    if (-not $DryRun -and $mergeNotes.Count -gt 0) {
      Write-Meta -SkillPath $centralExisting -Meta @{
        name = $sname; sources = $allAgents; merged = $true; merge_notes = $mergeNotes; merged_at = (Get-Date).ToString('o')
      }
    }
  }
}

# ============================================================
# 阶段 3：junction 管理
# ============================================================
Write-Log '=== 阶段 3: junction 管理 ==='

if ($Incremental) {
  foreach ($sname in ($skillDistribution.Keys | Sort-Object)) {
    $cpath = $centralPathOf[$sname]
    if (-not $cpath -or -not (Test-Path $cpath)) { continue }

    # 检查是否标记为 global
    $meta = Read-Meta -SkillPath $cpath
    $isGlobal = $meta -and $meta.global

    # 确定要建 junction 的 agent 列表
    $targetAgents = @($skillDistribution[$sname])
    if ($isGlobal) {
      $targetAgents = @($AgentDirs.Keys | Sort-Object -Unique)
      Write-Log "$sname : global 标记，扩散到所有 agent" 'DEBUG'
    }

    foreach ($agentName in $targetAgents) {
      $dir = $AgentDirs[$agentName]
      if (-not $dir -or -not (Test-Path $dir)) { continue }
      $linkPath = Join-Path $dir $sname
      if (-not (Test-Path $linkPath)) {
        Write-Log "[$agentName] 创建缺失 junction: $sname"
        if (-not $DryRun) { New-JunctionSafe -Link $linkPath -Target $cpath | Out-Null }
      }
    }
  }
} else {
  foreach ($agentName in $AgentDirs.Keys) {
    $dir = $AgentDirs[$agentName]
    if (-not (Test-Path $dir)) { continue }
    $children = Get-ChildItem -Path $dir -Directory -Force -ErrorAction SilentlyContinue |
      Where-Object { $SkipNames -notcontains $_.Name }
    foreach ($child in $children) {
      if ($child.LinkType -in @('Junction','SymbolicLink')) {
        Write-Log "[$agentName] 删除旧 junction: $($child.Name)"
        if (-not $DryRun) { Remove-JunctionSafe -Path $child.FullName }
      }
    }
  }
  foreach ($sname in ($skillDistribution.Keys | Sort-Object)) {
    $cpath = $centralPathOf[$sname]
    if (-not $cpath -or -not (Test-Path $cpath)) { continue }

    # 检查是否标记为 global
    $meta = Read-Meta -SkillPath $cpath
    $isGlobal = $meta -and $meta.global

    $targetAgents = @($skillDistribution[$sname])
    if ($isGlobal) {
      $targetAgents = @($AgentDirs.Keys | Sort-Object -Unique)
      Write-Log "$sname : global 标记，扩散到所有 agent" 'DEBUG'
    }

    foreach ($agentName in $targetAgents) {
      $dir = $AgentDirs[$agentName]
      if (-not $dir -or -not (Test-Path $dir)) { continue }
      $linkPath = Join-Path $dir $sname
      Write-Log "[$agentName] 创建 junction: $sname"
      if (-not $DryRun) { New-JunctionSafe -Link $linkPath -Target $cpath | Out-Null }
    }
  }
}

# ============================================================
# 阶段 4：更新元数据
# ============================================================
Write-Log '=== 阶段 4: 更新元数据 ==='
foreach ($sname in ($skillDistribution.Keys | Sort-Object)) {
  $cpath = $centralPathOf[$sname]
  if (-not $cpath -or -not (Test-Path $cpath)) { continue }
  $existing = Read-Meta -SkillPath $cpath
  $desiredSources = $skillDistribution[$sname]

  # 默认 global 规则：非冲突变体（名字不含 --）默认 global: true
  # 冲突变体（名字含 --，如 docx--gemini）不 global
  $defaultGlobal = -not ($sname -match '--')

  if (-not $existing) {
    if (-not $DryRun) {
      Write-Meta -SkillPath $cpath -Meta @{ name=$sname; sources=$desiredSources; merged=$false; global=$defaultGlobal; created_at=(Get-Date).ToString('o') }
      if ($defaultGlobal) { Write-Log "$sname : 新 skill，默认标记为 global" 'DEBUG' }
    }
  } else {
    # 已有元数据：更新 sources，如果还没有 global 字段则按默认规则补上
    $needsUpdate = $false
    if (-not $existing.sources -or ($existing.sources -join ',') -ne ($desiredSources -join ',')) {
      $existing.sources = $desiredSources
      $needsUpdate = $true
    }
    if ($null -eq $existing.global) {
      $existing | Add-Member -NotePropertyName global -NotePropertyValue $defaultGlobal -Force
      $needsUpdate = $true
      if ($defaultGlobal) { Write-Log "$sname : 补充 global 标记（默认规则）" 'DEBUG' }
    }
    if ($needsUpdate -and -not $DryRun) {
      $json = $existing | ConvertTo-Json -Depth 5 -Compress
      [System.IO.File]::WriteAllText((Join-Path ([string]$cpath) '.skillhome.json'), $json, (New-Object System.Text.UTF8Encoding $false))
    }
  }
}

# ============================================================
# 汇总
# ============================================================
$realCount = 0
foreach ($agentName in $AgentDirs.Keys) {
  $dir = $AgentDirs[$agentName]
  if (-not (Test-Path $dir)) { continue }
  $reals = Get-ChildItem -Path $dir -Directory -Force -ErrorAction SilentlyContinue |
    Where-Object { $SkipNames -notcontains $_.Name -and -not $_.LinkType }
  $realCount += $reals.Count
}
Write-Log "=== 完成 | 中央 skill: $($skillDistribution.Count) | 残留真实目录: $realCount ===" 'OK'
if ($DryRun) { Write-Log '*** DryRun — 未执行任何操作 ***' 'DRY' }
