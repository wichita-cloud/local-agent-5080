# BoomerGT: the Windows installer

`BoomerGT-setup.exe` sets up the whole stack from Windows, with no typing: a 1997-style setup wizard that asks a few
questions and does the README's steps for you. It is an alternative to the manual steps in the main README; the agent
it installs is the same.

## What it does
1. Checks the PC: NVIDIA card and driver, free disk space, memory, whether the monitor is on the graphics card.
2. Turns on Windows Subsystem for Linux if it is off (Windows asks for administrator permission, and may need a restart;
   Setup carries on by itself afterwards).
3. Installs Docker Desktop if it is missing (only after you accept Docker's license; the download's signature is checked).
4. Creates a WSL settings file (`.wslconfig`) only if you have none. It never replaces yours.
5. Creates its own Ubuntu, named `BoomerGT`, in `%LOCALAPPDATA%\BoomerGT`. Any Ubuntu you already have is not touched.
6. Connects Docker Desktop to that Ubuntu (this restarts Docker Desktop once).
7. Copies in the agent, downloads the model chosen for your PC (the Bolt 74B with 32 GB of RAM, otherwise the 27B),
   runs the agent's own installer, starts it, and makes desktop shortcuts: open the agent, Game Mode, and Restore.

Uninstall is in Windows' **Settings → Apps**. It asks whether to keep the agent's memory and notes.

## It is not signed: what Windows will say
Signing a program needs a certificate tied to a verified real name, and this project is anonymous. So when you run the
file, Windows SmartScreen shows **"Windows protected your PC"**. Click **More info → Run anyway**.

Instead of a signature, every release lists the file's **SHA-256**: a fingerprint of the file. Changing even one byte
of the file (a broken download, or someone swapping in a tampered copy) gives a completely different fingerprint. To
check yours, open PowerShell in your Downloads folder and run:

```powershell
Get-FileHash .\BoomerGT-setup.exe
```

The `Hash` it prints must be exactly the SHA-256 shown on the release page. If it differs, delete the file and don't
run it. A matching fingerprint proves you have the same file the release published; it does not tell you who made it.
For that, the source is all here: you can read it, and build the file yourself.

## Building it yourself
Needs Windows (the C# compiler that ships with Windows' .NET Framework), Git, and Python with Pillow (`pip install pillow`).

```powershell
cd installer
.\build.ps1              # packages the agent files of the current commit
.\build.ps1 -Rev v1.0    # or of a tag
```

The result is `installer\dist\BoomerGT-setup.exe`, and the script prints its SHA-256. Your build's SHA-256 won't match the
release's: Windows' built-in compiler stamps each build with the time it was made. What it packages is the same.

| File | What it is |
|---|---|
| `src/Program.cs` | Entry point: install, `/uninstall`, `/resume` after a restart, `/screens <dir>` (pictures of every page, changes nothing) |
| `src/Wizard.cs` | The wizard pages and their questions |
| `src/Engine.cs` | Runs `payload/engine.ps1` and reports its progress to the wizard |
| `src/Retro.cs` | The 1997 look: classic colours, fonts and controls |
| `payload/engine.ps1` | Everything Setup actually does, step by step (check, install, uninstall) |
| `make_assets.py`, `dog-source.png` | Draws the pixel-art banner and icons |
