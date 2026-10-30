# build.ps1 - builds dist\BoomerGT-setup.exe from this folder. Needs only what ships with Windows (the .NET Framework C# compiler)
# plus Python with Pillow for the pixel art. The agent's files are taken from this repo with `git archive`: exactly what is
# committed, never local edits, and without this installer folder.
# -Rev: what to package (default HEAD); a release is built from its tag. Windows' built-in C# compiler stamps each build
# with the time, so two builds of the same commit differ byte for byte (and so do their SHA-256s).
param([string]$Rev = 'HEAD')
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Repo = Split-Path -Parent $here
Set-Location $here
$csc = "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path $csc)) { throw 'The .NET Framework C# compiler was not found.' }

python make_assets.py | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'make_assets.py failed (needs Python with Pillow: pip install pillow)' }
git -C $Repo show "${Rev}:windows/Autostart.ps1" | Set-Content -Path payload\Autostart.ps1 -Encoding UTF8
git -C $Repo archive --format=tar.gz -o "$here\payload\agent.tar.gz" $Rev -- . ':(exclude)installer'
if ($LASTEXITCODE -ne 0) { throw "git archive failed for $Rev" }
$sha = (git -C $Repo rev-parse --short "${Rev}^{}")   # ^{}: a tag names its commit, not the tag object
Set-Content -Path payload\VERSION.txt -Value $sha -Encoding ASCII

New-Item -ItemType Directory -Force -Path dist | Out-Null
$res = @(
  'payload\engine.ps1,engine.ps1', 'payload\agent.tar.gz,agent.tar.gz', 'payload\Autostart.ps1,Autostart.ps1', 'payload\BoomerGT.ico,BoomerGT.ico',
  'payload\banner.bmp,banner.bmp', 'payload\info.png,info.png', 'payload\warn.png,warn.png', 'payload\ask.png,ask.png', 'payload\stop.png,stop.png', 'payload\app.png,app.png')
$args = @('/nologo', '/target:winexe', '/out:dist\BoomerGT-setup.exe', '/win32icon:payload\BoomerGT.ico', '/optimize+',
  '/r:System.Windows.Forms.dll', '/r:System.Drawing.dll', '/r:System.Web.Extensions.dll', '/r:System.dll', '/r:System.Core.dll')
foreach ($r in $res) { $args += "/resource:$r" }
$args += (Get-ChildItem src\*.cs | ForEach-Object { $_.FullName })
& $csc @args
if ($LASTEXITCODE -ne 0) { throw "compile failed ($LASTEXITCODE)" }
$f = Get-Item dist\BoomerGT-setup.exe
"built $($f.FullName) ($([math]::Round($f.Length/1MB,1)) MB) from agent files at commit $sha"
"SHA-256: " + (Get-FileHash $f.FullName -Algorithm SHA256).Hash
