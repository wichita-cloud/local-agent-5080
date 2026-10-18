<#
set-igpu-preference.ps1: tell Windows to run apps on the integrated (power-saving) GPU, keeping them off the 5080.
Same as Settings > System > Display > Graphics > (app) > "Power saving". It takes effect the next time each app starts.

  .\set-igpu-preference.ps1                      # Chrome, Edge and Edge WebView2 (the usual VRAM takers)
  .\set-igpu-preference.ps1 "C:\path\app.exe"    # any specific app
  .\set-igpu-preference.ps1 -Remove "C:\...exe"  # undo for one app
#>
param([string[]]$Paths, [switch]$Remove)
$key = 'HKCU:\Software\Microsoft\DirectX\UserGpuPreferences'
if (-not (Test-Path $key)) { New-Item $key -Force | Out-Null }
if (-not $Paths) {
  $Paths = @("$env:ProgramFiles\Google\Chrome\Application\chrome.exe",
             "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe")
  $Paths += Get-ChildItem "${env:ProgramFiles(x86)}\Microsoft\EdgeWebView\Application\*\msedgewebview2.exe" -ErrorAction SilentlyContinue | ForEach-Object FullName
}
foreach ($p in $Paths) {
  if (-not (Test-Path $p)) { "skip (not found): $p"; continue }
  if ($Remove) { Remove-ItemProperty -Path $key -Name $p -ErrorAction SilentlyContinue; "removed: $p" }
  else { Set-ItemProperty -Path $key -Name $p -Value 'GpuPreference=1;'; "power-saving GPU: $p" }
}
"Restart those apps for it to take effect."
