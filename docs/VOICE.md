# Voice: give the agent a voice (optional)

An add-on: local text-to-speech with **voice cloning**, using [Chatterbox](https://github.com/resemble-ai/chatterbox) by
Resemble AI (MIT licensed code and weights). It runs on the **CPU** cores the model doesn't use, so the GPU stays with
the language model and nothing leaves your PC. No account or key is needed.

```bash
bin/voice-install.sh        # or: console → Voice → Install voice   (~4 GB, ~5 minutes)
```

## What you get
- **Voice tab in the console**: add voices from a recording, preview them, pick the agent's voice, and *Talk to your
  agent*: type a message, read the reply, and hear it in the agent's voice.
- **A built-in voice** ("default") that works before you add any of your own.
- **Spoken replies in messaging apps**: if you connect OpenClaw to a messaging channel (see *Hearing it on your phone*),
  replies come back as voice notes in the agent's voice. `VOICE_REPLIES` in `config/stack.env` sets when:
  `inbound` (default: when you sent a voice note), `always`, or `off`.

The browser dashboard (`:18789`) shows text only. It has no audio player; use the console's Voice tab to listen.

## Speed (measured on a Ryzen 9 9900X, 8 cores)
| Engine (`VOICE_ENGINE`) | Speed | A 10-second reply takes | Notes |
|---|---|---|---|
| `nano` (default) | ~1.4× real time | ~7 s | loads in ~20 s |
| `turbo` | ~0.65× real time | ~16 s | a little richer; English |

Speech starts after the text reply is finished. Long replies are spoken in sentence-sized pieces, and OpenClaw
summarizes replies over 1,500 characters before speaking them in messaging apps.

## Adding a voice
Console → Voice → **Add a voice**: a name, a recording, and the consent box. Or from a terminal:
```bash
bin/voice.sh add my-voice ~/recording.wav --consent
bin/voice.sh use my-voice            # make it the agent's voice
bin/voice.sh say "Testing, one two three."
```
The recording is cleaned automatically: converted to mono 24 kHz, low rumble removed, silence trimmed at both ends,
loudness evened out, and cut to 30 seconds. It's stored in `~/.local/share/local-agent/voices/<name>/` (never in the
repo). **Hear the clip** in the console plays the cleaned version, which is exactly what the model copies.

### Consent and the watermark
- Only clone **your own voice**, or a voice whose owner has clearly agreed. The console asks you to confirm this, and
  records that you did in the voice's `meta.json`.
- Every clip Chatterbox makes carries Resemble AI's **Perth watermark**. You can't hear it, it survives MP3 compression,
  and it lets detection tools identify the audio as synthetic. This setup leaves it on.
- Don't use a cloned voice to deceive people: no impersonation, no fake calls or messages "from" someone.
- Laws on cloned voices vary by place, and some apply to commercial use. Check yours if you share what it makes.

## Recording a good clip with Audacity
[Audacity](https://www.audacityteam.org/) is free. The clone copies **everything** in the recording: the voice, but also
the room's echo, background hum and microphone character. A clean recording matters more than a long one.

**Before you record**
- A quiet room with soft things in it (curtains, a sofa, a bed). Bare walls add echo. A closet full of clothes is a
  good recording booth.
- A headset or USB microphone beats a laptop's built-in mic. Sit 15-20 cm from it, slightly off to the side, to avoid
  breath pops.
- Turn off fans, notifications and music.

**1. Set up Audacity**
- *Audio Setup → Recording Device*: choose your microphone.
- *Audio Setup → Recording Channels*: **1 (Mono)**.
- *Audio Setup → Audio Settings → Quality → Project Sample Rate*: 44100 or 48000 Hz (either is fine).

**2. Record (about 40 seconds in total)**
- Press **Record** (R). Stay **silent for 3 seconds** first: this "room tone" is used to remove noise.
- Then read for **20-30 seconds** in the voice you want the agent to have: relaxed, natural, at a normal pace. Use
  varied sentences with some questions, not a list of words. Read something like:
  > *"I checked the weather this morning, and it looks like rain by the afternoon. Do you want me to move the walk to
  > tomorrow? Honestly, I think that's the better plan. Let me know, and I'll update the calendar and send a quick
  > note to everyone."*
- Watch the level meter: peaks around **−12 to −6 dB**. Never let it touch 0 (red), which distorts the sound.
- Press **Stop** (Space). Listen back with headphones.

**3. Clean it up**
- **Remove background noise:** drag over the 3 silent seconds → *Effect → Noise Removal and Repair → Noise Reduction →*
  **Get Noise Profile**. Then select everything (Ctrl+A) → *Effect → Noise Removal and Repair → Noise Reduction* → OK.
  The default (12 dB) is right; much more makes the voice sound watery and robotic.
- **Remove low rumble (optional):** *Effect → EQ and Filters → High-Pass Filter*, 80 Hz.
- **Cut:** select the silent start, coughs, stumbles and long pauses, and press **Delete**.
- **Even out the level:** *Effect → Volume and Compression → Normalize*, "Normalize peak amplitude to" **−1 dB**.

**4. Export**
- *File → Export Audio* → format **WAV (Microsoft)**, encoding **Signed 16-bit PCM**, channels **Mono**. Save it.
- In the console: Voice → Add a voice → choose the file.

**Using a recording you already have:** open it in Audacity and do steps 3-4. If it's stereo, first use
*Tracks → Mix → Mix Stereo Down to Mono*. Pick 10-30 seconds where only that person speaks, with no music, laughter or
crosstalk. Phone-call and video-call recordings clone poorly because they're compressed.

**If the result sounds off**
| Sounds… | Usually because | Try |
|---|---|---|
| Echoey or "in a bathroom" | room reflections in the clip | record somewhere softer; closer to the mic |
| Hissy, buzzy | background noise | Noise Reduction (step 3), or turn off the noise source |
| Robotic or watery | too much noise reduction | redo it at 6-12 dB |
| Flat or monotone | the clip was read flatly | re-record more naturally, with some questions |
| Wrong accent | the clip was in another language | record in the language it will speak (English for nano/turbo) |
| Clipped words, crackle | the level hit 0 dB | record quieter, peaks around −6 dB |

## Hearing it on your phone (optional; one more account)
OpenClaw can talk to you over messaging apps such as Telegram, and send replies as voice notes in the agent's voice.
That needs a Telegram account and a free **bot token** from @BotFather, and it puts your agent on the internet behind
Telegram's login. That's a bigger security decision than anything else here. See OpenClaw's own channel docs, and keep
`dmPolicy` on pairing so only you can talk to it. The voice side needs nothing extra: `gateway-up.sh` already points
OpenClaw's spoken replies at the local voice server.

## Under the hood
| Piece | What it does |
|---|---|
| `bin/voice-install.sh` | apt `ffmpeg`, a separate venv (`$VOICE_VENV`; Chatterbox pins its own torch/transformers), CPU PyTorch, Chatterbox (released package for dependencies, plus a pinned source commit that adds `nano`), weights download |
| `services/voice_server.py` | OpenAI-compatible `POST /v1/audio/speech` on `127.0.0.1:$VOICE_PORT` (8880). CPU only, pinned to `VOICE_CPUSET`, one synthesis at a time, voice conditioning cached per voice |
| `bin/voice.sh` | `add / remove / list / use / say / talk / status / wait`. The console calls these |
| `bin/gateway-up.sh` | sets OpenClaw's `messages.tts` to the local server (never the online default), timeout 120 s (OpenClaw's maximum; `nano` speaks a full 1,500-character reply well inside it, `turbo` may not) |
| `bin/services-up.sh`, watchdog, `verify.sh` | start, restart and check it, only when it's installed |

## Troubleshooting
| Symptom | Check | Fix |
|---|---|---|
| Voice tab says "loading" for minutes | `tail ~/.local/state/local-agent/voice-server.log` | the first start downloads weights; if it says `load failed`, re-run `bin/voice-install.sh` |
| "voice server not reachable" | `bin/voice.sh status` | `bin/services-up.sh` (the watchdog also restarts it within a minute) |
| Replies are slow while it speaks | `VOICE_CPUSET` overlaps `LLAMA_CPUSET`? | keep them on different cores (defaults: llama 0-7, voice 8-15) |
| "only N s of speech after trimming" | the clip is mostly silence | record 10-30 s of continuous speech |
| Voice notes don't arrive in a messaging app | `openclaw config get messages.tts` | `bin/gateway-up.sh`; `VOICE_REPLIES` must not be `off` |

**Uninstall:** `rm -rf ~/.local/share/local-agent/voice-venv ~/.cache/huggingface/hub/models--ResembleAI--chatterbox-*`,
then `bin/gateway-up.sh` (spoken replies switch off). Your recordings stay in `~/.local/share/local-agent/voices/` until
you delete them.
