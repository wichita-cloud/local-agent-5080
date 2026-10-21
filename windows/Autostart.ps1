<#
Autostart.ps1: start the agent for you when you sign in to Windows. Optional.

Why: after a Windows restart nothing runs until you open Ubuntu and type bin/up.sh. This adds one Windows scheduled task, for
your user only (no administrator rights), that waits a minute after sign-in (so Docker Desktop can start) and runs bin/up.sh
inside your Ubuntu. That loads the model onto the GPU, so it takes the GPU from games until you use Game Mode (see the README).

  .\Autostart.ps1 -Status               is it installed?
  .\Autostart.ps1 -Install              add the task
  .\Autostart.ps1 -Install -DryRun      show exactly what it would add, change nothing
  .\Autostart.ps1 -Remove               remove the task
  Options: -Distro Ubuntu-22.04 (default: your default Ubuntu)   -RepoPath '~/local-agent-5080'   -DelaySeconds 60

What it runs (a normal user-level command; its output goes to ~/.local/state/local-agent/boot.log inside Ubuntu):
  wsl.exe -d <Distro> --exec /bin/bash -lc "cd <RepoPath> && bin/up.sh >> ~/.local/state/local-agent/boot.log 2>&1"
#>
param([switch]$Install, [switch]$Remove, [switch]$Status, [switch]$DryRun,
      [string]$Distro = '', [string]$RepoPath = '~/local-agent-5080', [int]$DelaySeconds = 60)
$ErrorActionPreference = 'Stop'
$TaskName = 'Local Agent - start at sign-in'

function Get-DefaultDistro {
  $names = (wsl.exe -l -q) -replace "`0", '' | ForEach-Object { $_.Trim() } | Where-Object { $_ -and $_ -notmatch '^docker-desktop' }
  if (-not $names) { throw 'No WSL Ubuntu found. Install one first (README step 0).' }
  return @($names)[0]
}

if ($Status -or (-not $Install -and -not $Remove)) {
  $t = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
  if ($t) { "installed: '$TaskName' ($($t.State)); remove with .\Autostart.ps1 -Remove" } else { "not installed; add with .\Autostart.ps1 -Install" }
  return
}
if ($Remove) {
  if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) { Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false; "removed '$TaskName'" } else { "'$TaskName' was not installed" }
  return
}

if (-not $Distro) { $Distro = Get-DefaultDistro }
if ($Distro -notmatch '^[A-Za-z0-9._-]+$') { throw "Unusual distro name '$Distro'; pass -Distro with letters, digits, . _ - only." }
if ($RepoPath -notmatch '^[A-Za-z0-9._/~-]+$') { throw "Unusual -RepoPath '$RepoPath'." }
$inner = "cd $RepoPath && bin/up.sh >> ~/.local/state/local-agent/boot.log 2>&1"
$actionArgs = "-d $Distro --exec /bin/bash -lc `"$inner`""
if (-not $DryRun) {
  $ok = (wsl.exe -d $Distro --exec /bin/bash -lc "test -x $RepoPath/bin/up.sh && echo yes") -replace "`0", ''
  if ($ok -notmatch 'yes') { throw "Can't find $RepoPath/bin/up.sh in '$Distro'. Is the agent installed there? (use -RepoPath / -Distro)" }
}
"Task:     $TaskName (user: $env:USERNAME, at sign-in, after $DelaySeconds s, no admin rights)"
"Runs:     wsl.exe $actionArgs"
if ($DryRun) { "Dry run: nothing was changed."; return }
$action   = New-ScheduledTaskAction -Execute 'wsl.exe' -Argument $actionArgs
$trigger  = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$trigger.Delay = "PT$($DelaySeconds)S"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
"installed. It will run at your next sign-in. Run it now to test:  Start-ScheduledTask -TaskName '$TaskName'"
"Remove any time:  .\Autostart.ps1 -Remove"
