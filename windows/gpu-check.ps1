<#
gpu-check.ps1: is the RTX 5080 free for the model, or is Windows quietly taking it?

Reports:
  * whether the display is driven by the NVIDIA GPU (it should be the motherboard/iGPU output)
  * NVIDIA memory used and free
  * the WSL VM's GPU memory: dedicated (good) vs SHARED (= spilled to system RAM over PCIe = ~6x slower decode)
  * other Windows processes holding memory on the NVIDIA GPU

Why it exists: with the monitor on the 5080, the desktop and browser took ~0.5 GB of VRAM. llama.cpp then could
not fit, about 300 MB of its working memory landed in shared system memory, and decode went from ~20 to ~122
ms/token. Every health check stayed green and nothing logged an error. This script is the only place that shows it.

Usage (PowerShell):   .\gpu-check.ps1            human-readable
                      .\gpu-check.ps1 -Machine   key=value lines (used by bin/verify.sh)
#>
param([switch]$Machine)
$ErrorActionPreference = 'SilentlyContinue'

$smi = 'nvidia-smi'
$q = & $smi --query-gpu=display_active,memory.used,memory.free,memory.total --format=csv,noheader,nounits
if (-not $q) { if ($Machine) { 'error=nvidia-smi-unavailable' } else { 'nvidia-smi not available' }; exit 2 }
$parts = $q.Split(',') | ForEach-Object { $_.Trim() }
$displayOnNvidia = if ($parts[0] -match 'Enabled') { 1 } else { 0 }
$used = [int]$parts[1]; $free = [int]$parts[2]; $total = [int]$parts[3]

# Per-process GPU memory via Windows performance counters. Instances look like pid_1234_luid_0x0_0x1808278_phys_0.
$ded = (Get-Counter '\GPU Process Memory(*)\Dedicated Usage').CounterSamples
$shr = (Get-Counter '\GPU Process Memory(*)\Shared Usage').CounterSamples
function Parse($s) { $p = $s.InstanceName -split '_'; [pscustomobject]@{ Pid = [int]$p[1]; Luid = ($p[3..4] -join '_'); MiB = [math]::Round($s.CookedValue / 1MB) } }
$dedRows = $ded | ForEach-Object { Parse $_ }
$shrRows = $shr | ForEach-Object { Parse $_ }

# The WSL2 VM shows up as vmwp (or vmmem). Its biggest dedicated allocation identifies the NVIDIA adapter's LUID.
$vmPids = (Get-Process vmwp, vmmem, vmmemWSL | Select-Object -ExpandProperty Id)
$vmDed = $dedRows | Where-Object { $vmPids -contains $_.Pid } | Sort-Object MiB -Descending | Select-Object -First 1
$nvLuid = if ($vmDed -and $vmDed.MiB -gt 500) { $vmDed.Luid } else { $null }
$vmDedMiB = if ($vmDed) { $vmDed.MiB } else { 0 }
$vmShrMiB = 0
if ($nvLuid) { $vmShrMiB = ($shrRows | Where-Object { ($vmPids -contains $_.Pid) -and $_.Luid -eq $nvLuid } | Measure-Object MiB -Sum).Sum }

$others = @()
if ($nvLuid) {
  # dwm reports a large cross-adapter figure (seen: 13 GB, more than the card has in use); it is not real usage.
  # Drop it when the display is on the iGPU, or when its figure is impossible.
  $others = $dedRows | Where-Object { $_.Luid -eq $nvLuid -and ($vmPids -notcontains $_.Pid) -and $_.MiB -ge 20 } | ForEach-Object {
    $n = (Get-Process -Id $_.Pid).ProcessName; if (-not $n) { $n = "pid$($_.Pid)" }
    if (-not ($n -eq 'dwm' -and ($displayOnNvidia -eq 0 -or $_.MiB -gt ($used - $vmDedMiB)))) { "${n}:$($_.MiB)" } }
}

if ($Machine) {
  "display_on_nvidia=$displayOnNvidia"
  "nvidia_used_mib=$used"
  "nvidia_free_mib=$free"
  "wsl_vm_dedicated_mib=$vmDedMiB"
  "wsl_vm_shared_mib=$([int]$vmShrMiB)"
  "other_nvidia_procs=$($others -join ',')"
  exit 0
}

"NVIDIA GPU: $used / $total MiB used, $free MiB free"
if ($displayOnNvidia -and $free -ge 1024) { "  NOTE: the display is on the NVIDIA GPU, with $free MiB still free. Fine for the 14B/8B; the 27B needs the desktop only on the motherboard's screen (Win+P)." }
elseif ($displayOnNvidia) { "  PROBLEM: the display is on the NVIDIA GPU and VRAM is nearly full. Win+P: show the desktop only on the motherboard's screen, or use a smaller model." }
else { "  ok: display is NOT on the NVIDIA GPU" }
if ($nvLuid) {
  "WSL VM on NVIDIA: $vmDedMiB MiB dedicated, $([int]$vmShrMiB) MiB shared (system RAM)"
  # same rule as verify.sh: WSL keeps ~100-170 MiB shared even when healthy; with >1 GiB VRAM free it isn't a spill
  if ($vmShrMiB -gt 150 -and $free -ge 1024) { "  ok: that shared memory is WSL's own ($free MiB VRAM still free), not a spill" }
  elseif ($vmShrMiB -gt 150) { "  PROBLEM: the model has spilled to system RAM. Free VRAM, then restart it (bin/llama-up.sh). See docs/STABILITY.md" }
} else { "WSL VM: no large GPU allocation (model not running?)" }
if ($others.Count) { "Other processes on the NVIDIA GPU (MiB): $($others -join ', ')"; "  Close them, or pin them to the iGPU with .\set-igpu-preference.ps1" }
