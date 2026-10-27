# Voice: give the agent a voice (optional)

An add-on: local speech in both directions: **you speak to the agent** (speech-to-text) and **it answers in a voice you
choose or clone** (text-to-speech). The speaking half uses [Chatterbox](https://github.com/resemble-ai/chatterbox) by
Resemble AI (MIT licensed code and weights); the listening half uses [faster-whisper](https://github.com/SYSTRAN/faster-whisper)
(MIT; OpenAI's Whisper model, run locally). Both run on the **CPU** cores the language model doesn't use, so the GPU
stays with the model and nothing leaves your PC. No account or key is needed.

```bash
bin/voice-install.sh        # or: console → Voice → Install voice   (~4.5 GB, ~5 minutes)
```

## What you get
- **Voice tab in the console**: a conversation with the agent first: **press the microphone, say your message, press again**; the words are recognised on your PC, the agent
  answers, and you read and hear the reply (or just type the message). A line under the reply shows where the time
  went. The browser asks once for microphone permission; it works on `http://localhost` (how the console opens).
  Talking while it's speaking stops it. Changing or adding a voice and the recording tips live under **Advanced settings**
  (the *Change voice* button opens them).
- **A built-in voice** ("default") that works before you add any of your own.
- **Spoken replies in messaging apps**: if you connect OpenClaw to a messaging channel (see *Hearing it on your phone*),
  replies come back as voice notes in the agent's voice, and **voice notes you send are transcribed locally** before
  the agent reads them. `VOICE_REPLIES` in `config/stack.env` sets when it answers by voice:
  `inbound` (default: when you sent a voice note), `always`, or `off`.

The browser dashboard (`:18789`) shows text only. It has no audio player; use the console's Voice tab to listen.

## Speed (measured on a Ryzen 9 9900X, 8 cores)
| Engine (`VOICE_ENGINE`) | Speed | A 10-second reply takes | Notes |
|---|---|---|---|
| `nano` (default) | ~1.4× real time | ~7 s | loads in ~20 s |
| `turbo` | ~0.65× real time | ~16 s | a little richer; English |

Speech starts after the text reply is finished. Long replies are spoken in sentence-sized pieces, and OpenClaw
summarizes replies over 1,500 characters before speaking them in messaging apps.

### Listening (speech-to-text) speed
`faster-whisper` `small.en` (default; `STT_MODEL` in `config/stack.env`) on the same CPU cores, int8, measured on the
12 questions below spoken by the voice engine and compressed like a phone voice note (Opus 24 kbps): **0.5-0.55 s per
question** (1.6-2.8 s of speech), several times faster than real time. Longer speech takes proportionally longer
(~0.25 s per second of speech). It got **10 of 12 exactly**; of the other two, "Where's the spare house key?" has the
same meaning as the question, and **"carprofen" came out as "carper fin"**, so rare names and drug names can be
misheard. `medium.en` is more accurate on those and about 3x slower (set `STT_MODEL=medium.en`, re-run
`bin/voice-install.sh`). This was clean synthetic speech: a real microphone, an accent or background noise will make
more mistakes, and the console shows what it heard so you can see them. A recording with no speech is reported as
"nothing heard" rather than guessed at.

### Time from question to first spoken answer, with memory (measured)
Suggested on the Hugging Face forum: decode speed alone doesn't say which model *feels* best as a voice assistant, so
this measures the whole path, for a **typed** question and for a **spoken** one.

Setup (2026-10-19, RTX 5080 + 9900X, llama.cpp as each profile pins it): 12 questions about facts stored in the memory
service, among ~250 other memories, including a near-miss distractor for each fact (the sister's birthday vs a coworker's
with the same name; the cabin's wifi password vs the home one). Everything uses the release's own code: the memory
service and memory proxy for retrieval, the voice server (`nano`) for speech, and the speech-to-text server for the
spoken questions. For the spoken runs each question was first spoken by the voice engine and compressed like a phone
voice note, then transcribed, and **the memory lookup was done again on the words that were heard**. Medians over the
12 questions, in seconds from the end of the question to the first sound:

| Model profile | Typed: right answer | Typed: first sound | Spoken: right answer | Listening | Spoken: first sound |
|---|---|---|---|---|---|
| `qwen3.5-27b-iq4xs-64k` (64k context) | 12/12 | **3.6 s** | 12/12 | 0.58 s | **4.1 s** |
| `qwen3.5-27b-mtp` (32k, fastest decode) | 12/12 | **4.0 s** | 12/12 | 0.62 s | **4.4 s** |
| `qwen3-14b-q4km` | 12/12 | **3.3 s** | 12/12 | 0.66 s | **4.3 s** |
| `qwen3-8b-q4km` | 12/12 | **3.4 s** | 11/12 | 0.69 s | **4.2 s** |

"First sound" is what the console's Talk box does: wait for the written reply, then speak it a sentence at a time
(starting the voice on the first sentence before the reply was finished would save at most ~0.15 s here, because these
replies are one short sentence). Of that, the model's own part is small: memory lookup 0.25 s, the written reply
0.2-0.65 s, and **2.7-3.3 s is the voice making the first sentence**, about 70-80% of the wait.

What it shows:
- **The models barely differ for short spoken answers.** They are within about 0.7 s of each other, and that gap is
  mostly the length of the first sentence the voice has to make, not the model's speed. The MTP build is the fastest at
  producing text (0.48 s vs 0.65 s for the full answer), but with answers this short that doesn't reach the ear; its
  advantage shows on long answers.
- **A spoken question costs about 0.4-1.0 s more than a typed one**: listening takes 0.6-0.7 s, and the memory lookup
  runs on the transcript.
- **Misheard words mostly didn't matter.** Two questions were transcribed wrongly on every run ("Wi-Fi" came out as
  "Was the Wi-Fi", and "carprofen" as "car prevent"), yet the memory lookup still found the right fact and the answer was
  right, because it matches on meaning. The one miss (8B, "When is my sister's birthday?") was transcribed perfectly,
  and the model wasn't at fault: that lookup took 2.50 s (every other one 0.20-0.36 s), hit the memory proxy's 2.5 s
  timeout, and the turn went ahead with no memories, so the 8B said it didn't have that information. It was the first
  lookup after a model load, which pushes the memory service's weights out of RAM. Fixed since: the weights are held in
  RAM, a timed-out lookup is retried once, and if memory still can't be checked the agent is told so and says so.
- The questions were easy enough that the models mostly got them all. Harder ones (several memories that must be
  combined, or a memory that should be ignored) are where they will separate, and this test doesn't cover those.

These are synthetic questions spoken by a clean synthetic voice. A real microphone, an accent or a noisy room will
make more listening errors. The agent turn through OpenClaw (the console Talk box and messaging apps) adds the agent's
own instructions to every request and is slower than this bare-model test: with a stand-in microphone through the real
console and `qwen3.5-27b-iq4xs`, the time from the end of the question to the first sound was **~6.5-7.3 s** once warm
(0.6 s listening, 2.6-2.9 s for the agent's written answer, 3-4 s for the first sentence of speech), and 11.3 s on the
first turn after a restart. That run had an empty memory, so add the ~0.25 s memory lookup.

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
OpenClaw's spoken replies at the local voice server, and sets `tools.media.audio` so voice notes **you** send are
transcribed by the local speech-to-text (`bin/stt-cli.sh`) before the agent reads them. Test it without a phone:
`bin/voice.sh listen some-recording.ogg` prints what it would hear.

## Under the hood
| Piece | What it does |
|---|---|
| `bin/voice-install.sh` | apt `ffmpeg`, a separate venv (`$VOICE_VENV`; Chatterbox pins its own torch/transformers), CPU PyTorch, Chatterbox (released package for dependencies, plus a pinned source commit that adds `nano`), weights download |
| `services/voice_server.py` | OpenAI-compatible `POST /v1/audio/speech` on `127.0.0.1:$VOICE_PORT` (8880). CPU only, pinned to `VOICE_CPUSET`, one synthesis at a time, voice conditioning cached per voice |
| `services/stt_server.py` | speech-to-text on `127.0.0.1:$STT_PORT` (8890): `POST /v1/audio/transcriptions` (OpenAI style, or raw audio). faster-whisper, int8, CPU, pinned to `VOICE_CPUSET`, voice-activity filter, one recording at a time, logs timings and never the words. Same venv; its own marker (`.stt-installed`) is written after the model is cached |
| `bin/stt-cli.sh` | the small wrapper OpenClaw runs for each voice note you send (`tools.media.audio`); `bin/voice.sh listen <file>` uses it too |
| `bin/voice.sh` | `add / remove / list / use / say / listen / talk / status / wait`. The console calls these |
| `bin/gateway-up.sh` | sets OpenClaw's `messages.tts` to the local server (never the online default), timeout 120 s (OpenClaw's maximum; `nano` speaks a full 1,500-character reply well inside it, `turbo` may not) |
| `bin/services-up.sh`, watchdog, `verify.sh` | start, restart and check it, only when it's installed |

## Troubleshooting
| Symptom | Check | Fix |
|---|---|---|
| Voice tab says "loading" for minutes | `tail ~/.local/state/local-agent/voice-server.log` | the first start downloads weights; if it says `load failed`, re-run `bin/voice-install.sh` |
| "voice server not reachable" | `bin/voice.sh status` | `bin/services-up.sh` (the watchdog also restarts it within a minute) |
| Replies are slow while it speaks | `VOICE_CPUSET` overlaps `LLAMA_CPUSET`? | keep them on different cores (defaults: llama 0-7, voice 8-15) |
| "only N s of speech after trimming" | the clip is mostly silence | record 10-30 s of continuous speech |
| The microphone button is missing or greyed | Voice tab status; `tail ~/.local/state/local-agent/stt-server.log` | missing = speech-to-text not installed (re-run `bin/voice-install.sh`); greyed = still loading (~10 s) or down (`bin/services-up.sh`) |
| "The microphone is blocked" | the browser's address-bar permission | allow the microphone for the console page; open it as `http://localhost:<port>`, not an IP address (browsers only allow the mic on localhost or https) |
| It hears the wrong words | the "I heard:" line under the box | speak closer to the mic, or set `STT_MODEL=medium.en` and re-run `bin/voice-install.sh` |
| Voice notes don't arrive in a messaging app | `openclaw config get messages.tts` | `bin/gateway-up.sh`; `VOICE_REPLIES` must not be `off` |

**Uninstall:** `rm -rf ~/.local/share/local-agent/voice-venv ~/.cache/huggingface/hub/models--ResembleAI--chatterbox-* ~/.cache/huggingface/hub/models--Systran--faster-whisper-*`,
then `bin/gateway-up.sh` (spoken replies switch off). Your recordings stay in `~/.local/share/local-agent/voices/` until
you delete them.
