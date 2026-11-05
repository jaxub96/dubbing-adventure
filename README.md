# gdub – voice-over recording toolkit for the Gothic dub

Line-ID-driven recording: every line exists in a CSV (built from the game's Daedalus
`.d` scripts) before anyone records. The recorder walks that list and names every file
from the ID, so nobody types filenames or cuts up long recordings.

```
.d scripts ──extract──▶ lines.csv ──record──▶ raw/<NPC>/<ID>__takeNN.wav + session.json
                                                   │
                         game speech folder ◀──export (trim · HPF · normalise · ffmpeg)
                                                   │
                                                  qc  (missing / orphans / clipped / progress)
```

## Easiest: no terminal after setup

| | First time | Every time |
|---|---|---|
| macOS | double-click `setup.command` | double-click `start.command` |
| Windows | – | double-click `start.bat` (sets itself up on first run) |

(or `gdub` / `python -m gdub` with no arguments). This opens the **launcher window** with
four tabs that replace every terminal command:

1. **Project** – choose/create the project folder, extract the line list from your `.d`
   scripts, reset takes (all / one character / one line; files go to `_trash/`, not deleted).
2. **Record** – pick microphone and playback device, start the recorder (optionally on one
   voice or character), save actor/mic notes.
3. **Export** – pick the game's speech folder and format, run the export with a progress bar,
   save the settings as project defaults.
4. **Quality check** – run the check, read the report, save it.

The recorder itself opens in its own window. On a Mac, the first double-click of a `.command`
file may need right-click → Open once. The command-line equivalents below still work.

## Setup (Windows, macOS, Linux)

* Python 3.9+ (the python.org installer includes tkinter).
* [ffmpeg](https://ffmpeg.org) on `PATH` (Windows: `winget install ffmpeg`) – export only.

```bash
pip install -r requirements.txt     # numpy, scipy, soundfile, sounddevice
pip install -e .                    # optional: gives you the `gdub` command
```

### macOS notes

```bash
brew install ffmpeg
brew install python-tk@3.13     # only if you use Homebrew Python: it ships without tkinter
python3 -m pip install -r requirements.txt     # PortAudio is bundled in the sounddevice wheel
```

* Use Python from python.org or Homebrew, **not** Apple's system Python 3.9 (its Tk 8.5 is
  buggy). Check with `python3 -c "import tkinter; print(tkinter.TkVersion)"` – needs 8.6.
* **Microphone permission**: the first `gdub record` makes macOS ask whether Terminal (or
  iTerm/VS Code) may use the microphone – allow it. If you denied it earlier, enable it under
  System Settings → Privacy & Security → Microphone, otherwise you record silence.
* The key that reads "delete" on a Mac keyboard rejects the selected take (same as Del).
* The window is raised to the front on start. Tests run on macOS in CI (`.github/workflows`).

Without the editable install use `python -m gdub …` instead of `gdub …`.

## Workflow

One folder per dub project (`-p`, default: current folder) holds everything:
`lines.csv`, `raw/`, `session.json`, optional `project.json`.

### 1. Extract the line list

```bash
gdub extract path/to/_work/Data/Scripts/Content/Story -p myproject
```

Takes `.d` files and/or folders (searched recursively), writes `myproject/lines.csv`.
Encoding is auto-detected (UTF-8, otherwise Windows-1250 as in the Polish scripts; force
with `--encoding cp1252` for German ones). Commented-out lines (`//…`, `/* … */`) are
skipped; malformed or duplicate IDs are reported as warnings.

Columns: `id, npc, scene, speaker, order, text` plus `who, file, line` (where it came from).
The NPC comes from the `npc = …;` of the enclosing script (filename as fallback). Unscripted
dialogue can be written by hand in the same format – only `id`, `npc`, `text` are required
(speaker/order are derived from the ID).

### 2. Record

```bash
gdub devices                                  # find your interface
gdub record -p myproject --device "Focusrite" --voice 15      # hero actor session
gdub record -p myproject --voice 09 --npc Dexter
gdub record -p myproject --simulate           # try the UI without a microphone
```

| Key | Action |
| --- | --- |
| Space | Start / stop a new take |
| → / ← | Next / previous line |
| N | Next unrecorded line |
| P | Play the selected take |
| 1–9 | Pick that take number as the best one (also un-rejects it) |
| Del / ⌫ | Reject the selected take (file stays on disk) |
| F | Cycle the speaker filter (all → each voice → all); also in the drop-downs |
| Esc | Stop playback |

A USB foot pedal or macro pad that sends Space/arrows works as-is (key auto-repeat is ignored).

* Shows the line in large type, ID and next take filename, the previous line from the other
  speaker (greyed, within the same conversation), takes with the chosen one marked ★, and
  progress ("Dexter: 23/41 lines done").
* **Pre-roll**: audio is captured continuously and 300 ms before Space are kept (`--preroll`).
* **Auto-advance** to the next unrecorded line after each take (checkbox).
* Masters are 48 kHz / 24-bit mono WAV in `raw/<NPC>/<ID>__takeNN.wav`, **never overwritten
  or deleted**; reject only flags. `session.json` stores chosen takes, device per take and UI
  state, so closing and reopening resumes at the same line. If `session.json` is lost, the
  takes found in `raw/` are adopted again automatically.
* `--channel N` picks input channel N of a multi-channel interface.

Note the mic setup per actor (kept in `session.json`, shown in the recorder):

```bash
gdub actor -p myproject 15 --name "Hero" --mic "SM7B" --distance 20cm --gain "45 dB"
```

### 3. Export

```bash
gdub export -p myproject --out path/to/_work/Data/Sound/Speech
gdub export -p myproject --out … --voice 15 --denoise --sample-rate 22050 --codec adpcm_ms
```

For every line with a chosen take: remove DC → 80 Hz high-pass → trim silence (50 ms lead /
150 ms tail, threshold adapts to the noise floor) → optional denoise (ffmpeg `afftdn`) →
normalise speech level (default RMS −20 dBFS over speech only, peak never above −1 dBFS) →
convert with ffmpeg (mono, no extra metadata chunks, TPDF dither for 16-bit) →
`<ID>.wav` in the output folder. `export_log.csv` lists gain per line; lines that couldn't
reach the target level without exceeding the peak ceiling are counted in the summary.

**The target format is an assumption** – defaults are 44.1 kHz, mono, 16-bit PCM. Once you
have confirmed what your game needs, set it once in `myproject/project.json`:

```json
{ "export": { "sample_rate": 22050, "codec": "adpcm_ms", "target_db": -20 } }
```

(keys = the `export` options with `-` → `_`; command-line flags override).

### 4. Quality check

```bash
gdub qc -p myproject --export path/to/Speech --report qc.md
```

Lists: script lines with no recording · recordings matching no script line (raw takes and
exported files) · clipped, silent, suspiciously short/long takes (speech duration vs. text
length, `--cps` tunes the expected speaking rate) · recorded-but-not-exported lines ·
progress per character and per voice. `--strict` exits 1 if anything is flagged (for scripts).

## Running a session

* Record per actor, not per scene: the hero actor does all `--voice 15` lines for every NPC in
  one sitting; each NPC actor does all their scenes together.
* Keep mic, distance and gain constant per actor; note them with `gdub actor`.
* Record 2–3 takes of important lines, choose afterwards (open the recorder again, ←/→, 1–9).

## Tests

```bash
python -m pytest
```
