# Cadence: YouTube Synced Lyrics Visualizer

A distraction-free, audio-reactive synced lyrics visualizer. Built with a standalone single-file editorial frontend powered by a lightweight FastAPI, yt-dlp, LRCLIB API, and Pillow backend.

---

## Architecture & Subsystems

### 1. Audio and LRCLIB Synced Lyrics Engine (FastAPI + yt-dlp + LRCLIB + Pillow)
- **Direct stream resolution**: pulls the best streamable audio container (`bestaudio`) straight from YouTube via `yt-dlp`.
- **LRCLIB Integration & Fallback Subsystem**:
  - Automatically queries the public LRCLIB API (`https://lrclib.net/api/search`) using sanitized metadata with custom `User-Agent` headers (`CadenceLyricsVisualizer/2.0`).
  - Converts standard and multi-timestamp LRC lines (`[mm:ss.xx]`, `[mm:ss.xxx]`, `[offset: +/-ms]`) into structured second offsets:
    ```json
    [
      { "time": 10.4, "end_time": 15.2, "duration": 4.8, "text": "Every signal tower blinking as the hours pass" }
    ]
    ```
  - Returns the automatic best-match synced lyrics alongside an alternative candidates array.
  - Falls back to YouTube auto-captions (WebVTT/json3) and structured instrumental placeholders when no LRCLIB match is found.
- **Range-aware audio relay**: `/api/audio-proxy` forwards client `Range: bytes=...` headers, returns `206 Partial Content`, and applies open CORS headers (`Cross-Origin-Resource-Policy: cross-origin`) so the Web Audio API `AnalyserNode` can sample low frequencies without cross-origin taint.
- **Artwork studio palette extraction**: quantises the YouTube thumbnail into a restrained studio palette using Pillow `FASTOCTREE` and injects tones into CSS custom properties (`--c1`, `--c2`, `--c3`, `--accent`).
- **Instant demo tracks**: built-in Synthwave, Lo-Fi, and Nepali (Devanagari) tracks with pre-timed cues and alternative lyrics candidates for immediate offline testing.
- **Complex Script & Devanagari Grapheme Segmentation**:
  - Native `Intl.Segmenter` grapheme clustering (`ne`, `hi`, `en`) preserving base consonants attached to vowel matras (ो, ै, ि), halants/viramas (्), and conjuncts without orphan dotted circles (`◌`).
  - OpenType ligature rendering (`font-feature-settings: "liga" 1, "dlig" 1, "mset" 1, "calt" 1; text-rendering: optimizeLegibility;`).
  - Font support including `Noto Sans Devanagari` and `Rozha One` alongside editorial serif and modern monospace stacks.

---

### 2. Handcrafted Dark Design & Animations
- **Anti-AI / Strict Editorial Rules**:
  - Zero emojis and decorators: crisp, functional typography, compact scales, and high-contrast midnight tones (`#0D0E15`, `#141620`, `#1B1E2B`).
  - No generic fonts (Inter, Roboto, Open Sans): targets `Geist`, `Newsreader` (Editorial Serif), `Instrument Serif`, `Geist Mono`, and `JetBrains Mono`.
  - Muted spot pastels for badges (`Pale Blue`, `Pale Green`, `Pale Yellow`, `Pale Red`).
  - `<kbd>` keyboard tags for keystroke navigation.
- **Interactive Lyrics Selector Modal**:
  - Live alternative lyrics browser with single-click Apply.
  - On-demand LRCLIB search form (`/api/lyrics/search`).
  - Raw LRC parser & paste tool (`/api/lyrics/parse-lrc`).
  - Sync timing offset adjuster (`-0.5s`, `-0.1s`, `+0.1s`, `+0.5s`, `Reset`).
- **10 Presentation & Typography Modes** (selectable in Settings Drawer):
  - **Staggered Character Wave (`wave`)**: splits active lyrics into per-grapheme spans with `--char-index`, driven by a staggered sinusoidal keyframe.
  - **Precise Karaoke Sweep (`karaoke`)**: linear-gradient text mask on the active line animated across the cue's duration via `background-position`.
  - **Breathing Neon Glow (`glow`)**: multi-layered drop-shadow bloom on an infinite 3s loop.
  - **Single Line Focus (`single`)**: hides inactive lyrics completely (`opacity: 0; max-height: 0; filter: blur(6px)`), smoothly transitioning and centering one active line at a time.
  - **Vertical Stage Fall & Float (`fall`)**: upcoming lines enter from above (`translateY(-36px)`), the active line gently floats on a sinusoidal stage float keyframe, and past cues drop away (`translateY(36px)`).
  - **Kinetic Weight Shift (`kinetic`)**: fluid typography mode where inactive cues render at ultralight weight (`300`), dynamically transitioning to bold (`800`) with expanded tracking on cue activation.
  - **3D Perspective Tilt (`tilt`)**: true 3D perspective viewport (`perspective: 1100px`) with upcoming lyrics tilted backward (`rotateX(22deg) translateZ(-35px)`), past cues tilted downward, and the active line projected forward.
  - **Horizontal Carousel Reader (`carousel`)**: upcoming lyrics slide into view from the right (`translateX(45px)`), the active cue locks at center, and past lyrics slide left.
  - **Typewriter Terminal (`typewriter`)**: monospace terminal aesthetic with crisp zero-blur rendering and a blinking block cursor (`▌`) on the active line.
  - **Static Editorial (`none`)**: classic clean typography without motion graphics.
- **Audio-Reactive Bass Pulse & Zero-Thrashing Engine**:
  - Web Audio `AnalyserNode` samples 20Hz–150Hz energy and scales the active lyric container.
  - Property mutations (`--bass-scale`, `--karaoke-pos`) are throttled with change-detection caches to eliminate layout thrashing.
  - Canvas DPR is capped to 2 with GPU-accelerated composite layers (`transform: translateZ(0); will-change: transform, opacity;`).
- **Custom Minimalist Scrollbars**:
  - Dark 6px scrollbars with `scrollbar-gutter: stable` and card overflow encapsulation across all panels and modals.
- **Background Modes**:
  - **Dynamic Mesh**: 3 orbiting radial gradient blobs rendered behind a `backdrop-filter: blur(60px)` plate.
  - **Interactive Dust Canvas**: ambient floating dust that accelerates on every lyric cue transition.
  - **Ken Burns Pan**: the thumbnail rendered fixed with `filter: blur(45px) brightness(0.35)` and a slow pan/zoom.
  - **Solid Dark**: clean `#0D0E15` ground with subtle vignette.
- **Interactions**:
  - **Physics-based scroll centering**: smooth easing curve (`cubic-bezier(0.16, 1, 0.3, 1)`) centers the active line.
  - **Click-to-seek**: click any lyric line to jump audio playback to that timestamp.
  - **Manual scroll override**: manual scrolling temporarily pauses auto-scroll and presents a Resume pill.
  - **Auto-hiding HUD**: top bar retreats after 3.5s of playback and reappears on pointer movement.

---

## Quick Start (Under 2 Minutes)

### 1. Install dependencies
```bash
pip install -r requirements.txt
```

### 2. Start the server
```bash
python3 app.py
```
Or with uvicorn directly:
```bash
uvicorn app:app --host 127.0.0.1 --port 8000 --reload
```

### 3. Open in your browser
Navigate to **http://127.0.0.1:8000** and click one of the demo tracks, or paste any YouTube URL into the top bar.

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| <kbd>Space</kbd> | Play / Pause |
| <kbd>←</kbd> / <kbd>→</kbd> | Seek ±5 seconds |
| <kbd>↑</kbd> / <kbd>↓</kbd> | Volume Up / Down |
| <kbd>M</kbd> | Mute / Unmute |
| <kbd>F</kbd> | Toggle Fullscreen |
| <kbd>S</kbd> | Toggle Settings Drawer |
| <kbd>L</kbd> | Toggle Lyrics Selector Modal |
| <kbd>/</kbd> | Focus Search / URL Input |
| <kbd>Esc</kbd> | Close Modals & Drawers |

---

## API Endpoints

- `GET /api/process?url={url}`: primary resolver returning metadata, palette, stream URL, best-match synced lyrics, and alternative candidates.
- `POST /api/parse`: same as above with JSON body `{"url": "..."}`.
- `GET /api/lyrics/search?q={query}`: search LRCLIB on-demand for tracks/artists.
- `GET /api/lyrics/{id}`: fetch alternative LRCLIB record and return parsed cues.
- `POST /api/lyrics/parse-lrc`: parse raw user-pasted LRC text into structured timestamps.
- `GET /api/audio-proxy?url={url}`: range-aware streaming proxy with open CORS headers.
- `GET /api/demo`: list of built-in demo tracks.
- `GET /api/health`: service health check.
