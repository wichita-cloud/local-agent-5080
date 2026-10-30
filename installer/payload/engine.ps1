# engine.ps1 - the work behind the BoomerGT Setup wizard. ASCII only (Windows PowerShell 5.1 reads BOM-less files as ANSI).
#
#   -Mode check      look at this PC and print CHECK lines (nothing is changed)
#   -Mode install    do everything, printing PROGRESS / LOG / RESULT lines
#   -Mode uninstall  remove it (keeps the agent's memory and notes when -KeepData is given)
#
# Output protocol, one line each, read by the wizard:
#   CHECK <ok|warn|fail|info>|<key>|<text>      PROGRESS <percent>|<text>      LOG <text>
#   RESULT <key>=<value>                        NEEDRESTART                     ERROR <text>
# Nothing here ever runs `wsl --shutdown` or touches any other Ubuntu: other WSL distributions on this PC are left alone.
param(
  [ValidateSet('check', 'install', 'uninstall')][string]$Mode = 'check',
  [string]$AnswersFile = '',
  [string]$PayloadDir = '',          # folder holding agent.tar.gz, windows scripts and assets
  [string]$InstallDir = '',          # default %LOCALAPPDATA%\BoomerGT
  [string]$RootfsPath = '',          # use this Ubuntu root file system instead of downloading one
  [switch]$KeepData
)
# Native programs (docker, wsl) print ordinary progress on the error stream; 'Stop' would turn that into a failure.
# Real problems are caught by exit codes and by -ErrorAction Stop on the few cmdlets that matter.
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'
$env:WSL_UTF8 = '1'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding $false
$Distro = 'BoomerGT'
$Product = 'BoomerGT'
$Version = '1.0'
if (-not $InstallDir) { $InstallDir = Join-Path $env:LOCALAPPDATA $Product }
if (-not $PayloadDir) { $PayloadDir = Split-Path -Parent $MyInvocation.MyCommand.Path }
$WorkDir = Join-Path $env:TEMP 'BoomerGT-work'
$TaskName = "$Product - start at sign-in"   # its own name: a GitHub install's 'Local Agent - start at sign-in' is never touched
$DeskLinks = @("$Product.lnk", "$Product - Game Mode.lnk", "$Product - Restore after a game.lnk")
New-Item -ItemType Directory -Force -Path $WorkDir | Out-Null
$LogFile = Join-Path $InstallDir 'setup.log'

function Emit($tag, $text) { [Console]::Out.WriteLine("$tag $text"); [Console]::Out.Flush() }
$script:NoFileLog = $false
function Log($text) {
  Emit 'LOG' $text
  if ($script:NoFileLog) { return }
  try { New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null; Add-Content -Path $LogFile -Value ("[{0}] {1}" -f (Get-Date -Format 'HH:mm:ss'), $text) -Encoding ASCII } catch {}
}
function Prog($pct, $text) { Emit 'PROGRESS' ("{0}|{1}" -f [int]$pct, $text); Log $text }
function Chk($level, $key, $text) { Emit 'CHECK' "$level|$key|$text" }
function Fail($text) { Emit 'ERROR' $text; Log "ERROR: $text"; exit 1 }

function ToWslPath($p) { $p = $p -replace '\\', '/'; if ($p -match '^([A-Za-z]):(.*)$') { return '/mnt/' + $Matches[1].ToLower() + $Matches[2] }; return $p }
function BashQuote($s) { return "'" + ($s -replace "'", "'\''") + "'" }   # one safe bash word, even with ' or spaces
function Clean($s) { if ($null -eq $s) { return '' }; return (($s -replace "`0", '') -replace '\x1b\[[0-9;]*m', '').TrimEnd() }

function Get-Distros {
  $o = & wsl.exe -l -q 2>$null
  if (-not $o) { return @() }
  return @($o | ForEach-Object { (Clean $_).Trim() } | Where-Object { $_ })
}
function Test-Wsl { try { $null = & wsl.exe --status 2>&1; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function Find-DockerDesktop {
  foreach ($p in @("$env:ProgramFiles\Docker\Docker\Docker Desktop.exe", "$env:LOCALAPPDATA\Programs\DockerDesktop\Docker Desktop.exe", "$env:LOCALAPPDATA\Programs\Docker\Docker\Docker Desktop.exe")) { if (Test-Path $p) { return $p } }
  return $null
}
function Test-Docker { try { $null = & docker.exe info 2>&1; return ($LASTEXITCODE -eq 0) } catch { return $false } }
function Wait-Docker($seconds) { $t0 = Get-Date; while (((Get-Date) - $t0).TotalSeconds -lt $seconds) { if (Test-Docker) { return $true }; Start-Sleep -Seconds 3 }; return $false }

# run a bash script inside the new Ubuntu as <user>, streaming its output to the log and returning the exit code
function Run-Distro($script, $user = 'agent', $percentFrom = -1, $percentTo = -1, $stepRegex = '') {
  $f = Join-Path $WorkDir ('run-' + [guid]::NewGuid().ToString('N').Substring(0, 8) + '.sh')
  $script = "export LOCAL_AGENT_UNATTENDED=1`n" + $script     # nothing in Ubuntu may stop to ask a question
  [IO.File]::WriteAllText($f, ($script -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
  $code = 0
  & wsl.exe -d $Distro -u $user --exec /bin/bash (ToWslPath $f) 2>&1 | ForEach-Object {
    $line = Clean $_
    if ($line) {
      Log $line
      if ($stepRegex -and $percentFrom -ge 0 -and $line -match $stepRegex) { $n = [int]$Matches[1]; $d = [int]$Matches[2]; Emit 'PROGRESS' ("{0}|{1}" -f [int]($percentFrom + ($percentTo - $percentFrom) * ($n / $d)), $line) }
    }
  }
  $code = $LASTEXITCODE
  Remove-Item $f -ErrorAction SilentlyContinue
  return $code
}

function Get-Gpu {
  $q = $null
  try { $q = & nvidia-smi.exe --query-gpu=name,memory.total,memory.used,driver_version,display_active '--format=csv,noheader,nounits' 2>$null } catch {}
  if (-not $q) { return $null }
  $p = @(($q | Select-Object -First 1).Split(',') | ForEach-Object { $_.Trim() })
  return [pscustomobject]@{ Name = $p[0]; TotalMiB = [int]$p[1]; UsedMiB = [int]$p[2]; Driver = $p[3]; Display = $p[4] }
}

# ---------------------------------------------------------------- check
function Do-Check {
  $gpu = Get-Gpu
  if (-not $gpu) { Chk 'fail' 'gpu' 'No NVIDIA graphics card was found (nvidia-smi is missing). Install the NVIDIA driver first.' }
  else {
    Chk 'ok' 'gpu' ("{0}, {1} GB, driver {2}" -f $gpu.Name, [math]::Round($gpu.TotalMiB / 1024, 0), $gpu.Driver)
    $model = 'qwen3.5-27b-iq4xs'
    # NVIDIA's Bolt 74B keeps ~7 GB of experts in system RAM: the default with a 16 GB card and a 32 GB PC (Windows shows 31.x)
    if ([math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0) -ge 30) { $model = 'nemotron-3.5-bolt-74b-128k' }
    if ($gpu.TotalMiB -lt 15000) { $model = 'qwen3-14b-q4km' }
    if ($gpu.TotalMiB -lt 11000) { $model = 'qwen3-8b-q4km' }
    Chk 'info' 'model' $model
    if ($gpu.TotalMiB -lt 15000) { Chk 'warn' 'vram' ("This card has {0} GB. The big 27B model needs 16 GB, so a smaller model will be suggested." -f [math]::Round($gpu.TotalMiB / 1024, 0)) }
    if ($gpu.Display -match 'Enabled') { Chk 'warn' 'display' 'Your monitor is plugged into the graphics card. Plug it into the motherboard instead, or the agent runs about 6 times slower (see the README).' }
    else { Chk 'ok' 'display' 'The monitor is not using the graphics card. Good.' }
    if ($gpu.UsedMiB -gt 2048) { Chk 'warn' 'gpubusy' ("The graphics card is busy ({0:N1} GB in use by another program, maybe another AI model or a game). The agent needs about 15 GB free to load." -f ($gpu.UsedMiB / 1024)) }
  }
  $drive = (Split-Path -Qualifier $InstallDir).TrimEnd(':')
  $free = [math]::Round((Get-PSDrive $drive).Free / 1GB, 0)
  if ($free -lt 35) { Chk 'fail' 'disk' ("Only {0} GB free on drive {1}:. About 45 GB is needed (the model alone is 14 GB)." -f $free, $drive) }
  elseif ($free -lt 60) { Chk 'warn' 'disk' ("{0} GB free on drive {1}:. About 45 GB is needed, so it will be tight." -f $free, $drive) }
  else { Chk 'ok' 'disk' ("{0} GB free on drive {1}:." -f $free, $drive) }
  Chk 'info' 'freegb' "$free"
  $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
  if ($ram -lt 24) { Chk 'warn' 'ram' ("{0} GB of memory. 32 GB is recommended." -f $ram) } else { Chk 'ok' 'ram' ("{0} GB of memory." -f $ram) }
  Chk 'info' 'ramgb' "$ram"   # the wizard lists the Bolt 74B only with 32 GB (its experts live in system RAM)
  # A laptop case (not a battery: a desktop on a USB battery backup reports one too): the wizard then advises against
  # scheduled research, which keeps the GPU busy for hours. 8-10 portable/laptop/notebook, 14 sub-notebook, 30-32 tablet/convertible/detachable.
  $chassis = @((Get-CimInstance Win32_SystemEnclosure -ErrorAction SilentlyContinue).ChassisTypes)
  Chk 'info' 'laptop' $(if ($chassis | Where-Object { $_ -in 8, 9, 10, 14, 30, 31, 32 }) { 'yes' } else { 'no' })
  if (-not (Test-Path (Join-Path $env:USERPROFILE '.wslconfig'))) {
    $ram2 = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
    Chk 'info' 'wslconfig' ("You have no WSL settings file, so Setup will create one (.wslconfig: WSL may use up to {0} GB of memory). It applies to every Linux on this PC. Uninstall removes it if you have not changed it." -f [math]::Max(8, [math]::Min(20, [math]::Floor($ram2 * 0.6))))
  }
  if (Test-Wsl) { Chk 'ok' 'wsl' 'Windows Subsystem for Linux is installed.' } else { Chk 'warn' 'wsl' 'Windows Subsystem for Linux is not installed. Setup can turn it on (needs administrator permission and a restart).' }
  $dd = Find-DockerDesktop
  if ($dd) {
    if (Test-Docker) {
      Chk 'ok' 'docker' 'Docker Desktop is installed and running.'
      $n = @(& docker.exe ps -q 2>$null).Count
      if ($n -gt 0) { Chk 'warn' 'containers' ("Docker is running {0} container(s) right now. Setup restarts Docker Desktop once, which stops and restarts them." -f $n) }
    } else { Chk 'warn' 'docker' 'Docker Desktop is installed but not running. Setup will start it.' }
  } else { Chk 'warn' 'dockermissing' 'Docker Desktop is not installed. Setup can install it (you must accept its license).' }
  $d = Get-Distros
  if ($d -contains $Distro) { Chk 'info' 'existing' "$Distro is already installed on this PC. Setup will repair it and keep its memory and notes." }
  $others = @($d | Where-Object { $_ -notmatch '^docker-desktop' -and $_ -ne $Distro })
  if ($others.Count -gt 0) { Chk 'info' 'others' ("Your other Ubuntu(s) ({0}) are left untouched." -f ($others -join ', ')) }
  Emit 'LOG' 'check finished'
}

# ---------------------------------------------------------------- install
function Download($url, $dest, $label, $p0, $p1) {
  Log "downloading $label"
  $job = Start-BitsTransfer -Source $url -Destination $dest -Asynchronous -DisplayName 'BoomerGT'
  try {
    while ($job.JobState -in 'Queued', 'Connecting', 'Transferring', 'TransientError') {
      # BITS reports an unknown size (server sent no length, e.g. a small text file) as UInt64.MaxValue: treat it as
      # unknown. Converting that to [int] crashed every install on its first download.
      $total = [double]$job.BytesTotal; $done = [double]$job.BytesTransferred
      if ($total -gt 0 -and $total -lt [double][uint64]::MaxValue) { Emit 'PROGRESS' ("{0}|{1} ({2:N0} of {3:N0} MB)" -f [int]($p0 + ($p1 - $p0) * ($done / $total)), $label, ($done / 1MB), ($total / 1MB)) }
      elseif ($done -gt 0) { Emit 'PROGRESS' ("{0}|{1} ({2:N0} MB)" -f $p0, $label, ($done / 1MB)) }
      Start-Sleep -Seconds 2
    }
    if ($job.JobState -ne 'Transferred') { Fail "Download of $label failed ($($job.JobState)). Check your internet connection and run Setup again." }
    Complete-BitsTransfer -BitsJob $job
  } finally { if ($job -and $job.JobState -ne 'Transferred') { try { Remove-BitsTransfer -BitsJob $job } catch {} } }
}

function Do-Install {
  if (-not $AnswersFile -or -not (Test-Path $AnswersFile)) { Fail 'No answers file was given.' }
  $a = Get-Content -Raw -Path $AnswersFile | ConvertFrom-Json
  $model = if ($a.model) { [string]$a.model } else { 'qwen3.5-27b-iq4xs' }
  $security = if ($a.security -eq 'standard') { 'standard' } else { 'protected' }
  New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
  Log "BoomerGT $Version setup, log file $LogFile"

  # 1. checks that must pass
  Prog 1 'Checking your computer'
  if (-not (Get-Gpu)) { Fail 'No NVIDIA graphics card was found. Install the NVIDIA driver and run Setup again.' }

  # 2. WSL
  Prog 3 'Checking Windows Subsystem for Linux'
  if (-not (Test-Wsl)) {
    Prog 4 'Turning on Windows Subsystem for Linux (Windows will ask for administrator permission)'
    $p = Start-Process -FilePath 'powershell.exe' -ArgumentList '-NoProfile', '-Command', 'wsl.exe --install --no-distribution' -Verb RunAs -Wait -PassThru
    Log "wsl --install exit code $($p.ExitCode)"
    Emit 'NEEDRESTART' 'Windows needs to restart to finish turning on WSL. Setup will continue after you sign back in.'
    exit 3010
  }

  # 3. Docker Desktop
  Prog 6 'Checking Docker Desktop'
  $dd = Find-DockerDesktop
  if (-not $dd) {
    if (-not $a.acceptDocker) { Fail 'Docker Desktop is required and its license was not accepted. Setup cannot continue.' }
    $inst = Join-Path $WorkDir 'DockerDesktopInstaller.exe'
    Download 'https://desktop.docker.com/win/main/amd64/Docker%20Desktop%20Installer.exe' $inst 'Docker Desktop' 6 14
    $sig = Get-AuthenticodeSignature -FilePath $inst
    Log "Docker Desktop installer signature: $($sig.Status), $($sig.SignerCertificate.Subject)"
    if ($sig.Status -ne 'Valid' -or $sig.SignerCertificate.Subject -notmatch 'O="?Docker,? Inc') { Remove-Item $inst -Force -ErrorAction SilentlyContinue; Fail 'The Docker Desktop download is not signed by Docker, so Setup did not run it. Install Docker Desktop yourself from docker.com, then run Setup again.' }
    Prog 14 'Installing Docker Desktop (this takes a few minutes)'
    $p = Start-Process -FilePath $inst -ArgumentList 'install', '--user', '--quiet', '--accept-license', '--backend=wsl-2' -Wait -PassThru
    Log "Docker Desktop installer exit code $($p.ExitCode)"
    $dd = Find-DockerDesktop
    if (-not $dd) { Fail 'Docker Desktop did not install. Install it yourself from docker.com, then run Setup again.' }
  }
  if (-not (Test-Docker)) {
    Prog 15 'Starting Docker Desktop'
    Start-Process -FilePath $dd | Out-Null
    if (-not (Wait-Docker 240)) { Fail 'Docker Desktop did not start within 4 minutes. Open it yourself, wait until it says it is running, then run Setup again.' }
  }

  # 4. WSL limits file (only if you have none; never replaces yours)
  $wc = Join-Path $env:USERPROFILE '.wslconfig'
  if (-not (Test-Path $wc)) {
    $ram = [math]::Round((Get-CimInstance Win32_ComputerSystem).TotalPhysicalMemory / 1GB, 0)
    $mem = [math]::Max(8, [math]::Min(20, [math]::Floor($ram * 0.6)))
    $cpu = (Get-CimInstance Win32_ComputerSystem).NumberOfLogicalProcessors
    $txt = "[wsl2]`r`nmemory=${mem}GB`r`nswap=8GB`r`nprocessors=$cpu`r`nlocalhostForwarding=true`r`n`r`n[experimental]`r`nautoMemoryReclaim=gradual`r`n"
    [IO.File]::WriteAllText($wc, $txt, (New-Object Text.UTF8Encoding $false))
    # remember what we wrote: Uninstall deletes the file only if it is still exactly this (never a file you changed)
    Set-Content -Path (Join-Path $InstallDir 'wslconfig.created') -Value (Get-FileHash -Algorithm SHA256 $wc).Hash -Encoding ASCII
    Log "wrote $wc (memory ${mem}GB, $cpu processors); it applies to every WSL Ubuntu from the next WSL restart"
  } else { Log "kept your existing $wc" }

  # 5. the dedicated Ubuntu
  $existing = Get-Distros
  if ($existing -contains $Distro) { Prog 20 "$Distro already exists: reusing it (your memory and notes stay)" }
  else {
    $rootfs = $RootfsPath
    if (-not $rootfs -or -not (Test-Path $rootfs)) {
      $base = 'https://cloud-images.ubuntu.com/wsl/jammy/current/'
      $sums = Join-Path $WorkDir 'SHA256SUMS'
      Download ($base + 'SHA256SUMS') $sums 'Ubuntu checksum list' 16 16
      # Find the amd64 root file system by pattern: Ubuntu renamed it in 2026 (...-amd64-wsl.rootfs -> ...-amd64-ubuntu22.04lts.rootfs),
      # and a hard-coded name broke every install. A line looks like: <sha256> *<file name>
      $line = Get-Content $sums | Where-Object { $_ -match '^([0-9a-f]{64}) \*?(ubuntu-jammy-wsl-amd64-[\w.-]*rootfs\.tar\.gz)$' } | Select-Object -First 1
      if (-not $line -or $line -notmatch '^([0-9a-f]{64}) \*?(\S+)$') { Fail 'Could not find Ubuntu 22.04 in the Ubuntu checksum list. Ubuntu may have moved it; please report this.' }
      $want = $Matches[1]; $name = $Matches[2]
      Log "Ubuntu image: $name"
      $rootfs = Join-Path $WorkDir $name
      Download ($base + $name) $rootfs 'Ubuntu 22.04' 16 28
      Prog 28 'Checking the Ubuntu download'
      if ((Get-FileHash -Algorithm SHA256 $rootfs).Hash.ToLower() -ne $want.ToLower()) { Remove-Item $rootfs -Force; Fail 'The Ubuntu download failed its checksum test. It was deleted; run Setup again.' }
    }
    Prog 30 'Creating your private Ubuntu'
    $wslDir = Join-Path $InstallDir 'wsl'
    New-Item -ItemType Directory -Force -Path $wslDir -ErrorAction Stop | Out-Null
    & wsl.exe --import $Distro $wslDir $rootfs --version 2 2>&1 | ForEach-Object { Log (Clean $_) }
    if ($LASTEXITCODE -ne 0) { Fail 'Windows could not create the Ubuntu (wsl --import failed). See the log for details.' }
  }
  $mk = @'
set -e
id agent >/dev/null 2>&1 || useradd -m -s /bin/bash agent
echo 'agent ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/agent
chmod 440 /etc/sudoers.d/agent
printf '[boot]\nsystemd=false\n[user]\ndefault=agent\n' > /etc/wsl.conf
echo user ready
'@
  if ((Run-Distro $mk 'root') -ne 0) { Fail 'Could not create the agent user inside the new Ubuntu.' }
  & wsl.exe --terminate $Distro 2>&1 | Out-Null

  # 6. tell Docker Desktop about it. Docker only attaches to an Ubuntu that is RUNNING when it (re)starts, so keep this one awake.
  #    This restarts Docker Desktop once; other Ubuntus are not touched.
  Prog 34 'Connecting Docker Desktop to the new Ubuntu'
  $keep = Start-Process -FilePath 'wsl.exe' -ArgumentList '-d', $Distro, '--exec', 'sleep', '1500' -WindowStyle Hidden -PassThru
  Start-Sleep -Seconds 3
  $sf = Join-Path $env:APPDATA 'Docker\settings-store.json'
  if (-not (Test-Path $sf)) { Fail "Docker Desktop's settings file was not found. Start Docker Desktop once, then run Setup again." }
  $json = Get-Content -Raw -Path $sf -ErrorAction Stop | ConvertFrom-Json
  $list = @(); if ($json.PSObject.Properties.Name -contains 'IntegratedWslDistros') { $list = @($json.IntegratedWslDistros) }
  if ($list -notcontains $Distro) {
    Copy-Item $sf ($sf + '.bak-BoomerGT-' + (Get-Date -Format 'yyyyMMdd-HHmmss')) -ErrorAction Stop
    $list += $Distro
    if ($json.PSObject.Properties.Name -contains 'IntegratedWslDistros') { $json.IntegratedWslDistros = $list } else { $json | Add-Member -NotePropertyName IntegratedWslDistros -NotePropertyValue $list }
    [IO.File]::WriteAllText($sf, ($json | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding $false))
    Log 'added the new Ubuntu to Docker Desktop WSL integration'
  } else { Log 'Docker Desktop already lists this Ubuntu' }
  function Test-Attached { & wsl.exe -d $Distro -u agent --exec docker info 2>&1 | Out-Null; return ($LASTEXITCODE -eq 0) }
  # Docker attaches to a freshly started Ubuntu after a short delay: give it up to a minute before restarting anything
  $att = $false
  for ($i = 0; $i -lt 15 -and -not $att; $i++) { if (Test-Attached) { $att = $true } else { Start-Sleep -Seconds 4 } }
  if (-not $att) {
    Prog 36 'Restarting Docker Desktop once'
    & docker.exe desktop restart 2>&1 | ForEach-Object { Log (Clean $_) }
    if ($LASTEXITCODE -ne 0) {
      Log 'docker desktop restart is not available here; restarting Docker Desktop by hand'
      Get-Process -Name 'Docker Desktop' -ErrorAction SilentlyContinue | Stop-Process -Force
      Start-Sleep -Seconds 5
      Start-Process -FilePath $dd | Out-Null
    }
    Start-Sleep -Seconds 8
    if (-not (Wait-Docker 300)) { Fail 'Docker Desktop did not come back within 5 minutes. Open it, wait until it says it is running, then run Setup again.' }
    $okd = $false
    for ($i = 0; $i -lt 40; $i++) { if (Test-Attached) { $okd = $true; break }; Start-Sleep -Seconds 3 }
    if (-not $okd) { Fail 'Docker is not reachable from the new Ubuntu. In Docker Desktop open Settings, Resources, WSL integration, switch BoomerGT on, then run Setup again.' }
  }
  try { & taskkill.exe /T /F /PID $keep.Id 2>&1 | Out-Null } catch {}
  Prog 42 'Docker works inside the new Ubuntu'

  # 7. the program files
  Prog 44 'Copying the agent into Ubuntu'
  $tgz = Join-Path $PayloadDir 'agent.tar.gz'
  if (-not (Test-Path $tgz)) { Fail 'The agent files are missing from this installer (agent.tar.gz).' }
  $cp = @"
set -e
mkdir -p ~/local-agent-5080
tar -xzf $(BashQuote (ToWslPath $tgz)) -C ~/local-agent-5080
cd ~/local-agent-5080
[ -f config/stack.env ] || cp config/stack.env.example config/stack.env
[ -f config/secrets.env ] || { cp config/secrets.env.example config/secrets.env; chmod 600 config/secrets.env; }
chmod +x bin/*.sh guards/*.sh patches/*/apply.sh 2>/dev/null || true
# keep it updatable with git (best effort, needs the internet)
if [ ! -d .git ]; then git init -q . && git remote add origin https://github.com/wichita-cloud/local-agent-5080.git && git fetch -q --depth 1 origin main && git reset -q origin/main || true; fi
echo files ready
"@
  if ((Run-Distro $cp) -ne 0) { Fail 'Could not copy the agent files into Ubuntu.' }

  # 8. your answers that must be in place before the installer runs
  Prog 46 'Applying your choices'
  $pre = "set -e`ncd ~/local-agent-5080`n"
  if ($a.networkInternet) { $pre += "grep -q '^SANDBOX_NETWORK=' config/stack.env || echo 'SANDBOX_NETWORK=`"on`"' >> config/stack.env`n" }
  # always written, with MODEL_PICKED, so the install's own default-model pick (bin/model.sh recommend) keeps this choice
  $pre += "sed -i '/^ACTIVE_MODEL=/d; /^MODEL_PICKED=/d' config/stack.env; echo 'ACTIVE_MODEL=$model' >> config/stack.env; echo 'MODEL_PICKED=installer' >> config/stack.env`n"
  if ((Run-Distro $pre) -ne 0) { Fail 'Could not apply your choices.' }
  # A folder security.sh refuses (a whole drive or profile, AppData, credentials) is named on the finish page, not just logged.
  if ($a.shareFolder) {
    $mode = if ($a.shareWrite) { 'rw' } else { 'ro' }
    if ((Run-Distro ("cd ~/local-agent-5080`nbin/security.sh share add " + (BashQuote $a.shareFolder) + " $mode --no-apply`n")) -ne 0) {
      Emit 'RESULT' ("warn=The folder " + $a.shareFolder + " was not shared. Click View log to see why, then share a folder from the console's Security page.")
    }
  }

  # 9. the model
  $sizeMB = switch -Wildcard ($model) { 'nemotron-3.5-bolt-74b*' { 21000 } 'qwen3-14b-q4km' { 8600 } 'qwen3-8b-q4km' { 4800 } default { 14000 } }
  Prog 48 ("Downloading the model ({0}, about {1} GB; this is the long part)" -f $model, [math]::Round($sizeMB / 1000))
  $dlf = Join-Path $WorkDir 'dl.sh'
  [IO.File]::WriteAllText($dlf, ("set -e`ncd ~/local-agent-5080`nbin/model.sh download $model`n" -replace "`r`n", "`n"), (New-Object Text.UTF8Encoding $false))
  $modelDir = "\\wsl.localhost\$Distro\home\agent\models"
  $dlJob = Start-Job -ScriptBlock { param($d, $f) & wsl.exe -d $d -u agent --exec /bin/bash $f 2>&1 } -ArgumentList $Distro, (ToWslPath $dlf)
  while ($dlJob.State -eq 'Running') {
    $big = $null
    try { $big = Get-ChildItem -Path $modelDir -Filter '*.gguf' -ErrorAction SilentlyContinue | Sort-Object Length -Descending | Select-Object -First 1 } catch {}
    if ($big) { $mb = [int]($big.Length / 1MB); $pct = 48 + [math]::Min(24, [int](24 * $mb / $sizeMB)); Emit 'PROGRESS' ("{0}|Downloading the model ({1} MB so far)" -f $pct, $mb) }
    Start-Sleep -Seconds 3
  }
  $out = Receive-Job $dlJob; foreach ($l in $out) { Log (Clean $l) }
  if ($dlJob.State -ne 'Completed' -or ($out -join "`n") -match 'FAIL|failed|too small') { if (($out -join "`n") -notmatch 'downloaded|ready') { Fail 'The model download failed. Run Setup again to resume it.' } }
  Remove-Job $dlJob -Force

  # 10. the installer inside Ubuntu (apt, Node, OpenClaw, Python, patches, cron, sandbox)
  Prog 72 'Installing the agent software (several minutes)'
  $ins = "cd ~/local-agent-5080`nexport LOCAL_AGENT_SECURITY=$security`nbin/install.sh < /dev/null`n"
  $rc = Run-Distro $ins 'agent' 72 88 '\[[0-9:]+\]\s+([0-9])/(9)'
  if ($rc -ne 0) { Fail 'The agent installer reported a problem. See the log for the last lines.' }
  # No `wsl --terminate` here: every wsl.exe call is a new session and already has the docker group. Restarting this
  # Ubuntu used to cut it off from Docker Desktop (no docker socket) and up.sh then failed with "Docker did not come up".

  # 11. start it. Docker must reach this Ubuntu first; if it can't, restart Docker Desktop once (it attaches to running Ubuntus).
  Prog 88 'Starting the agent (loads the model onto the graphics card)'
  $keep2 = Start-Process -FilePath 'wsl.exe' -ArgumentList '-d', $Distro, '--exec', 'sleep', '900' -WindowStyle Hidden -PassThru
  $att = $false
  for ($i = 0; $i -lt 10 -and -not $att; $i++) { if (Test-Attached) { $att = $true } else { Start-Sleep -Seconds 3 } }
  if (-not $att) {
    Log 'Docker is not reachable from the new Ubuntu; restarting Docker Desktop once'
    & docker.exe desktop restart 2>&1 | ForEach-Object { Log (Clean $_) }
    if (-not (Wait-Docker 300)) { Fail 'Docker Desktop did not come back within 5 minutes. Open it, wait until it says it is running, then run Setup again.' }
    for ($i = 0; $i -lt 40 -and -not $att; $i++) { if (Test-Attached) { $att = $true } else { Start-Sleep -Seconds 3 } }
    if (-not $att) { Fail 'Docker is not reachable from the new Ubuntu. In Docker Desktop open Settings, Resources, WSL integration, switch BoomerGT on, then run Setup again.' }
  }
  $rc = Run-Distro "cd ~/local-agent-5080`nbin/up.sh < /dev/null`n" 'agent' 88 95 '\[[0-9:]+\]\s+([0-9])/(6)'
  try { & taskkill.exe /T /F /PID $keep2.Id 2>&1 | Out-Null } catch {}
  # A Setup that ends with "complete" must mean the agent answers. up.sh exits non-zero if any layer failed.
  # (reported after the shortcuts and uninstaller exist, so a user is never left without a way to retry or remove it)
  $startFailed = ($rc -ne 0)

  # 12. optional extras
  Prog 95 'Setting up the extras you chose'
  # Each extra the user chose either works or is named on the finish page. They used to fail silently (`|| true`):
  # web search failed while Docker was unreachable and the agent then said it had no web search.
  $extras = @()
  if ($a.overnight) { $extras += ,@('Overnight research', 'bin/overnight.sh set OVERNIGHT on') }
  if ($a.webSearch) { $extras += ,@('Web search', 'bin/searxng.sh up') }
  if ($a.voice) { $extras += ,@('Voice', 'bin/voice-install.sh < /dev/null') }
  if ($a.jina) { $extras += ,@('Jina reader', 'bin/security.sh jina on') }
  foreach ($x in $extras) {
    if ((Run-Distro ("cd ~/local-agent-5080`n" + $x[1] + "`n")) -ne 0) {
      Log "$($x[0]) could not be set up"
      Emit 'RESULT' ("warn=" + $x[0] + " could not be turned on. Open the console and use its Health page to see why and try again.")
    }
  }
  # 13. addresses, shortcuts, uninstall entry
  Prog 97 'Making shortcuts'
  $dash = ''; $cons = ''
  $raw = & wsl.exe -d $Distro -u agent --exec /bin/bash -lc "cd ~/local-agent-5080 && . bin/lib.sh >/dev/null 2>&1; echo D=http://localhost:`${GATEWAY_PORT:-18789}/#token=`${GATEWAY_TOKEN}; echo C=http://localhost:`${CONSOLE_PORT:-18800}/" 2>$null
  foreach ($l in $raw) { $l = Clean $l; if ($l -like 'D=*') { $dash = $l.Substring(2) }; if ($l -like 'C=*') { $cons = $l.Substring(2) } }
  if ($dash) { Emit 'RESULT' "dashboard=$dash" }
  if ($cons) { Emit 'RESULT' "console=$cons" }
  Make-Shortcuts $dash $cons
  Register-Uninstall
  if ($a.autostart) {
    $as = Join-Path $PayloadDir 'Autostart.ps1'
    if (Test-Path $as) { try { & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $as -Install -Distro $Distro -TaskName $TaskName 2>&1 | ForEach-Object { Log (Clean $_) } } catch { Log "autostart not set up: $_" } }
  }
  if ($startFailed) { Fail 'Everything was installed, but the agent did not start. Click View log to see the step that failed. To try again, open the Start menu: BoomerGT > Start the agent.' }
  Prog 100 'Done'
  Emit 'RESULT' 'finished=yes'
}

function Make-Shortcuts($dash, $cons) {
  $sm = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$Product"
  New-Item -ItemType Directory -Force -Path $sm | Out-Null
  $desk = [Environment]::GetFolderPath('Desktop')   # finds the real Desktop, including a moved one
  $icon = Join-Path $InstallDir 'BoomerGT.ico'
  $src = Join-Path $PayloadDir 'BoomerGT.ico'; if (Test-Path $src) { Copy-Item $src $icon -Force }
  $ws = New-Object -ComObject WScript.Shell
  function Lnk($path, $target, $argline, $desc) { $l = $ws.CreateShortcut($path); $l.TargetPath = $target; $l.Arguments = $argline; $l.Description = $desc; $l.WorkingDirectory = $InstallDir; if (Test-Path $icon) { $l.IconLocation = $icon }; $l.Save() }
  # A command window that shows what happened and waits for a key (a bare wsl.exe shortcut closed before you could read it).
  # No parentheses in the messages: inside the if-block they would end it.
  function Cmd($file, $title, $intro, $script, $failmsg) {
    $t = "@echo off`r`nchcp 65001 >nul`r`ntitle $title`r`necho $intro`r`necho.`r`nwsl.exe -d $Distro --exec /bin/bash -lc `"export LOCAL_AGENT_ICONS=1; cd ~/local-agent-5080 && bin/$script`"`r`nif errorlevel 1 (`r`n  echo.`r`n  echo   $failmsg`r`n)`r`necho.`r`npause`r`n"
    $f = Join-Path $InstallDir $file; [IO.File]::WriteAllText($f, $t, (New-Object Text.ASCIIEncoding)); return $f
  }
  $start = Cmd 'Start the agent.cmd' "$Product - start" 'Starting the agent. Loading the model takes a minute or two...' 'up.sh' 'Something did not start - read the messages above.'
  $game = Cmd 'Game Mode.cmd' "$Product - Game Mode" 'Freeing the graphics card for a game. The rest of the agent stays up...' 'gpu-release.sh' 'The graphics card was NOT fully freed - read the message above. If it stays stuck, restart Windows: after a restart the agent stays off the graphics card until you send it a message.'
  $rest = Cmd 'Restore after a game.cmd' "$Product - Restore" 'Loading the model again and checking it runs at full speed...' 'gpu-restore.sh' 'Restore did NOT finish - read the message above. Close any game first, then try again.'
  if ($dash) {
    $url = Join-Path $InstallDir 'dashboard.url'
    Set-Content -Path $url -Value "[InternetShortcut]`r`nURL=$dash`r`n" -Encoding ASCII
    # Starts whatever is not running (after a Windows restart nothing is) EXCEPT the model, then opens the chat. The model
    # loads with the first message, so this icon never takes the graphics card by itself.
    $t = "@echo off`r`nchcp 65001 >nul`r`ntitle $Product`r`necho Opening your agent...`r`n" +
         "wsl.exe -d $Distro --exec /bin/bash -lc `"export LOCAL_AGENT_ICONS=1; cd ~/local-agent-5080 && . bin/lib.sh && { curl -sf -m 3 -o /dev/null http://127.0.0.1:`$GATEWAY_PORT/ || bin/up.sh --no-model; }`"`r`n" +
         "if errorlevel 1 (`r`n  echo.`r`n  echo   Your agent did not start - read the messages above.`r`n  pause`r`n  exit /b 1`r`n)`r`n" +
         "start `"`" `"$url`"`r`n"
    $open = Join-Path $InstallDir 'Open my agent.cmd'; [IO.File]::WriteAllText($open, $t, (New-Object Text.ASCIIEncoding))
    Lnk (Join-Path $sm 'Open my agent.lnk') $open '' 'Chat with your agent'
    Lnk (Join-Path $desk "$Product.lnk") $open '' 'Chat with your agent'
  }
  if ($cons) { Set-Content -Path (Join-Path $InstallDir 'console.url') -Value "[InternetShortcut]`r`nURL=$cons`r`n" -Encoding ASCII; Lnk (Join-Path $sm 'Agent console (settings).lnk') 'explorer.exe' (Join-Path $InstallDir 'console.url') 'Health, models, security, overnight' }
  Lnk (Join-Path $sm 'Start the agent.lnk') $start '' 'Start everything (after a restart)'
  # The agent's own files (its notes, projects, USER.md), opened in place inside its Ubuntu. Not a copy: a mirror that
  # syncs both ways can spread a wiped file to the other side.
  Lnk (Join-Path $sm "My agent's files.lnk") 'explorer.exe' "\\wsl.localhost\$Distro\home\agent\.openclaw\workspace" 'Notes and files your agent has written'
  # The teach drop folder (services/teach_inbox.py checks it every 2 minutes). Made now so the shortcut opens; it holds
  # your own notes, so Uninstall leaves it in place.
  $teach = Join-Path ([Environment]::GetFolderPath('MyDocuments')) 'Teach my agent'
  New-Item -ItemType Directory -Force -Path $teach | Out-Null
  Lnk (Join-Path $sm 'Teach my agent (drop notes here).lnk') 'explorer.exe' $teach 'Notes dropped here become your agent''s memories'
  Lnk (Join-Path $sm 'Game Mode - free the graphics card.lnk') $game '' 'Stops the model so you can play a game'
  Lnk (Join-Path $sm 'Restore the agent after a game.lnk') $rest '' 'Loads the model again'
  Lnk (Join-Path $desk "$Product - Game Mode.lnk") $game '' 'Stops the model so you can play a game'
  Lnk (Join-Path $desk "$Product - Restore after a game.lnk") $rest '' 'Loads the model again'
  Lnk (Join-Path $sm "Uninstall $Product.lnk") (Join-Path $InstallDir 'Uninstall.exe') '' "Remove $Product"
}

function Register-Uninstall {
  $k = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$Product"
  New-Item -Path $k -Force | Out-Null
  $icon = Join-Path $InstallDir 'BoomerGT.ico'
  Set-ItemProperty $k DisplayName $Product
  Set-ItemProperty $k DisplayVersion $Version
  Set-ItemProperty $k Publisher $Product
  Set-ItemProperty $k InstallLocation $InstallDir
  Set-ItemProperty $k UninstallString ('"' + (Join-Path $InstallDir 'Uninstall.exe') + '" /uninstall')
  if (Test-Path $icon) { Set-ItemProperty $k DisplayIcon $icon }
  Set-ItemProperty $k NoModify 1 -Type DWord
  Set-ItemProperty $k NoRepair 1 -Type DWord
}

# ---------------------------------------------------------------- uninstall
function Do-Uninstall {
  Prog 5 'Stopping the agent'
  if ((Get-Distros) -contains $Distro) {
    $stop = @'
cd ~/local-agent-5080 2>/dev/null || exit 0
crontab -r 2>/dev/null || true
docker rm -f local-llama local-agent-searxng >/dev/null 2>&1 || true
docker ps -aq --filter name=openclaw-sbx | xargs -r docker rm -f >/dev/null 2>&1 || true
docker rmi local-agent-sandbox:1 >/dev/null 2>&1 || true
pkill -u agent -f 'openclaw|memory_service|rag_proxy|ready_proxy|search_shim|console/server.py' 2>/dev/null || true
echo stopped
'@
    Run-Distro $stop | Out-Null
  }
  Prog 25 'Removing the sign-in task and shortcuts'
  try { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false -ErrorAction SilentlyContinue } catch {}
  Remove-Item -Recurse -Force (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\$Product") -ErrorAction SilentlyContinue
  foreach ($n in $DeskLinks) { Remove-Item -Force (Join-Path ([Environment]::GetFolderPath('Desktop')) $n) -ErrorAction SilentlyContinue }
  Remove-Item -Recurse -Force "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\$Product" -ErrorAction SilentlyContinue
  Prog 45 'Updating Docker Desktop'
  $sf = Join-Path $env:APPDATA 'Docker\settings-store.json'
  if (Test-Path $sf) {
    try {
      $json = Get-Content -Raw -Path $sf | ConvertFrom-Json
      if ($json.PSObject.Properties.Name -contains 'IntegratedWslDistros' -and @($json.IntegratedWslDistros) -contains $Distro) {
        $json.IntegratedWslDistros = @($json.IntegratedWslDistros | Where-Object { $_ -ne $Distro })
        [IO.File]::WriteAllText($sf, ($json | ConvertTo-Json -Depth 30), (New-Object Text.UTF8Encoding $false))
        Log 'removed it from Docker Desktop WSL integration (takes effect when Docker Desktop next starts)'
      }
    } catch { Log "could not edit Docker's settings: $_" }
  }
  # the WSL settings file, only if Setup created it and nobody changed it since
  $wc = Join-Path $env:USERPROFILE '.wslconfig'; $mark = Join-Path $InstallDir 'wslconfig.created'
  if ((Test-Path $mark) -and (Test-Path $wc)) {
    if ((Get-FileHash -Algorithm SHA256 $wc).Hash -eq (Get-Content $mark -Raw).Trim()) { Remove-Item $wc -Force -ErrorAction SilentlyContinue; Log "removed $wc (Setup had created it)" }
    else { Log "kept $wc because it was changed after Setup created it" }
  }
  # Only the files Setup itself made are deleted, never a whole folder: the install folder may be one the user typed.
  $ours = 'Uninstall.exe', 'dashboard.url', 'console.url', 'BoomerGT.ico', 'answers.json', 'wslconfig.created', 'Start the agent.cmd', 'Open my agent.cmd', 'Game Mode.cmd', 'Restore after a game.cmd'
  if ($KeepData) {
    Prog 70 'Keeping your agent''s memory and notes'
    Log "kept the Ubuntu '$Distro' and $InstallDir\wsl. Remove later with: wsl --unregister $Distro"
    foreach ($f in $ours) { Remove-Item (Join-Path $InstallDir $f) -Force -ErrorAction SilentlyContinue }
  } else {
    Prog 70 'Removing the agent''s Ubuntu and everything in it'
    if ((Get-Distros) -contains $Distro) { & wsl.exe --unregister $Distro 2>&1 | ForEach-Object { Log (Clean $_) } }
    $script:NoFileLog = $true
    foreach ($f in $ours + 'setup.log') { Remove-Item (Join-Path $InstallDir $f) -Force -ErrorAction SilentlyContinue }
    $wd = Join-Path $InstallDir 'wsl'   # wsl --unregister deletes its disk; remove the folder only if nothing else is in it
    # (and only once Windows no longer lists the Ubuntu: if --unregister failed, its disk must not be deleted under it)
    if ((Test-Path $wd) -and ((Get-Distros) -notcontains $Distro) -and -not (Get-ChildItem -Force $wd | Where-Object { $_.Name -ne 'ext4.vhdx' })) { Remove-Item -Recurse -Force $wd -ErrorAction SilentlyContinue }
    if ((Test-Path $InstallDir) -and -not (Get-ChildItem -Force $InstallDir)) { Remove-Item -Force $InstallDir -ErrorAction SilentlyContinue }
  }
  Remove-Item -Recurse -Force $WorkDir -ErrorAction SilentlyContinue
  Prog 100 'Done'
  Emit 'RESULT' 'finished=yes'
}

try {
  switch ($Mode) { 'check' { Do-Check } 'install' { Do-Install } 'uninstall' { Do-Uninstall } }
} catch {
  Emit 'ERROR' ("Unexpected problem: " + $_.Exception.Message)
  Log ("ERROR: " + $_.Exception.ToString())
  exit 1
}
