"""
Cadence: Synced Lyrics Visualizer
Backend: FastAPI + yt-dlp + LRCLIB + Pillow

Architecture:
  - Resolves YouTube URLs into direct streamable audio containers
  - Extracts clean artist and track titles from video metadata
  - Queries LRCLIB for synced LRC lyrics and alternative candidates
  - Fallback to YouTube captions or structured instrumental markers
  - Exposes endpoints for alternative lyrics lookup, search, and raw LRC parsing
  - Quantizes video artwork into restrained studio palettes
  - Relays audio streams with Range and open CORS headers for Web Audio FFT
"""

from __future__ import annotations

import asyncio
import html
import io
import os
import re
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

import requests
import yt_dlp
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from PIL import Image
from pydantic import BaseModel

APP_DIR = os.path.dirname(os.path.abspath(__file__))
INDEX_FILE = os.path.join(APP_DIR, "index.html")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
LRCLIB_USER_AGENT = "CadenceLyricsVisualizer/2.0 (https://github.com/shrijan/cadence)"

app = FastAPI(
    title="Cadence API",
    description="Synced lyrics, LRCLIB integration, artwork palettes and audio relay.",
    version="2.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Length", "Content-Range", "Accept-Ranges"],
)


# --------------------------------------------------------------------------- #
# Request & Response Models
# --------------------------------------------------------------------------- #

class ParseRequest(BaseModel):
    url: str


class LrcParseRequest(BaseModel):
    lrc: str
    duration: Optional[float] = 0.0


# --------------------------------------------------------------------------- #
# Palette Extraction
# --------------------------------------------------------------------------- #

NEUTRAL_PALETTE: Dict[str, Any] = {
    "primary": "#E4B363",
    "secondary": "#6FA8A0",
    "accent": "#C9CED9",
    "dark": "#0D0E15",
    "light": "#F7F6F3",
    "swatches": ["#E4B363", "#6FA8A0", "#C9CED9", "#8C93A8", "#3A3F52"],
    "mesh": {"c1": "#E4B363", "c2": "#6FA8A0", "c3": "#5A6178"},
}


def _to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _hex_to_rgb(hex_color: str) -> Tuple[int, int, int]:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        return (140, 147, 168)
    return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))


def _luminance(r: int, g: int, b: int) -> float:
    return (0.299 * r + 0.587 * g + 0.114 * b) / 255.0


def _saturation(r: int, g: int, b: int) -> float:
    hi, lo = max(r, g, b), min(r, g, b)
    return 0.0 if hi == 0 else (hi - lo) / hi


def _tone(hex_color: str, saturation_scale: float = 1.0, lightness_scale: float = 1.0) -> str:
    r, g, b = _hex_to_rgb(hex_color)
    grey = 0.299 * r + 0.587 * g + 0.114 * b
    r = grey + (r - grey) * saturation_scale
    g = grey + (g - grey) * saturation_scale
    b = grey + (b - grey) * saturation_scale
    r, g, b = (max(0.0, min(255.0, c * lightness_scale)) for c in (r, g, b))
    return _to_hex(int(r), int(g), int(b))


def extract_palette(image_url: Optional[str], clusters: int = 14) -> Dict[str, Any]:
    if not image_url:
        return dict(NEUTRAL_PALETTE)

    try:
        req = urllib.request.Request(image_url, headers={"User-Agent": USER_AGENT})
        with urllib.request.urlopen(req, timeout=6) as resp:
            raw = resp.read()

        img = Image.open(io.BytesIO(raw)).convert("RGB").resize((140, 140))
        quant = img.quantize(colors=clusters, method=Image.Quantize.FASTOCTREE)
        flat = quant.getpalette()[: clusters * 3]

        candidates: List[Dict[str, Any]] = []
        for i in range(0, len(flat), 3):
            r, g, b = flat[i], flat[i + 1], flat[i + 2]
            lum = _luminance(r, g, b)
            sat = _saturation(r, g, b)
            if 0.04 <= lum <= 0.96:
                candidates.append({
                    "hex": _to_hex(r, g, b),
                    "lum": lum,
                    "sat": sat,
                    "score": sat * 1.6 + (1.0 - abs(lum - 0.52)),
                })

        if not candidates:
            return dict(NEUTRAL_PALETTE)

        candidates.sort(key=lambda c: c["score"], reverse=True)

        picked: List[Dict[str, Any]] = []
        for candidate in candidates:
            if len(picked) >= 5:
                break
            r1, g1, b1 = _hex_to_rgb(candidate["hex"])
            if all(
                ((r1 - pr) ** 2 + (g1 - pg) ** 2 + (b1 - pb) ** 2) ** 0.5 >= 42
                for pr, pg, pb in (_hex_to_rgb(p["hex"]) for p in picked)
            ):
                picked.append(candidate)

        if not picked:
            picked = candidates[:5]

        swatches = [_tone(c["hex"], 0.86, 1.02) for c in picked]
        while len(swatches) < 5:
            swatches.append(NEUTRAL_PALETTE["swatches"][len(swatches) % 5])

        mesh_colors = [_tone(c["hex"], 0.92, 1.05) for c in picked[:3]]
        while len(mesh_colors) < 3:
            mesh_colors.append(NEUTRAL_PALETTE["swatches"][len(mesh_colors)])

        return {
            "primary": swatches[0],
            "secondary": swatches[1],
            "accent": swatches[2],
            "dark": NEUTRAL_PALETTE["dark"],
            "light": NEUTRAL_PALETTE["light"],
            "swatches": swatches,
            "mesh": {"c1": mesh_colors[0], "c2": mesh_colors[1], "c3": mesh_colors[2]},
        }
    except Exception as exc:
        print(f"[palette] fallback to neutral palette: {exc}")
        return dict(NEUTRAL_PALETTE)


# --------------------------------------------------------------------------- #
# Metadata Cleaning & Heuristics
# --------------------------------------------------------------------------- #

NOISE_PATTERNS = [
    re.compile(r"\(official\s+(?:music\s+)?video\)", re.I),
    re.compile(r"\[official\s+(?:music\s+)?video\]", re.I),
    re.compile(r"\(official\s+audio\)", re.I),
    re.compile(r"\[official\s+audio\]", re.I),
    re.compile(r"\(official\s+lyric\s+video\)", re.I),
    re.compile(r"\[official\s+lyric\s+video\]", re.I),
    re.compile(r"\(lyrics?\)", re.I),
    re.compile(r"\[lyrics?\]", re.I),
    re.compile(r"\(audio\)", re.I),
    re.compile(r"\[audio\]", re.I),
    re.compile(r"\(visualizer\)", re.I),
    re.compile(r"\[visualizer\]", re.I),
    re.compile(r"\(music\s+video\)", re.I),
    re.compile(r"\[music\s+video\]", re.I),
    re.compile(r"\(4k(?:\s+60fps)?\)", re.I),
    re.compile(r"\[4k(?:\s+60fps)?\]", re.I),
    re.compile(r"\[hd\]", re.I),
    re.compile(r"\(hd\)", re.I),
    re.compile(r"\(hq\)", re.I),
    re.compile(r"\(remastered(?:\s+\d{4})?\)", re.I),
    re.compile(r"\[remastered(?:\s+\d{4})?\]", re.I),
    re.compile(r"\(live(?:\s+at\s+[^)]+)?\)", re.I),
    re.compile(r"\[live(?:\s+at\s+[^\]]+)?\]", re.I),
]


def clean_metadata(info: Dict[str, Any]) -> Tuple[str, str]:
    raw_title = (info.get("track") or info.get("title") or "").strip()
    raw_artist = (info.get("artist") or info.get("uploader") or info.get("channel") or "").strip()

    title = raw_title
    for pattern in NOISE_PATTERNS:
        title = pattern.sub("", title)
    title = re.sub(r"\s+", " ", title).strip()

    artist = raw_artist
    if artist.endswith(" - Topic"):
        artist = artist[:-8].strip()
    if artist.endswith("VEVO"):
        artist = artist[:-4].strip()

    if " - " in title:
        parts = title.split(" - ", 1)
        potential_artist = parts[0].strip()
        potential_title = parts[1].strip()
        if potential_artist and potential_title:
            artist = potential_artist
            title = potential_title
    elif " – " in title:
        parts = title.split(" – ", 1)
        potential_artist = parts[0].strip()
        potential_title = parts[1].strip()
        if potential_artist and potential_title:
            artist = potential_artist
            title = potential_title
    elif ":" in title and not artist:
        parts = title.split(":", 1)
        if len(parts[0]) < 30 and parts[1].strip():
            artist = parts[0].strip()
            title = parts[1].strip()

    feat_pattern = re.compile(r"\s*[({\[](?:ft\.?|feat\.?)\s+[^)}\]]+[)}\]]", re.I)
    title = feat_pattern.sub("", title)
    title = re.sub(r"\s+(?:ft\.?|feat\.?)\s+.*$", "", title, flags=re.I)

    title = title.strip("\"' ")
    artist = artist.strip("\"' ")

    return (title or raw_title or "Untitled", artist or raw_artist or "Unknown Artist")


# --------------------------------------------------------------------------- #
# LRC Parsing Logic
# --------------------------------------------------------------------------- #

LRC_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")
LRC_OFFSET_RE = re.compile(r"\[offset:\s*([+-]?\d+)\s*\]", re.I)


def parse_lrc(lrc_content: str, track_duration: float = 0.0) -> List[Dict[str, Any]]:
    if not lrc_content or not isinstance(lrc_content, str):
        return []

    lines = lrc_content.splitlines()
    offset_ms = 0.0
    for line in lines:
        match = LRC_OFFSET_RE.search(line)
        if match:
            try:
                offset_ms = float(match.group(1))
            except ValueError:
                pass
            break

    offset_sec = offset_ms / 1000.0

    raw_cues: List[Tuple[float, str]] = []
    for line in lines:
        line_str = line.strip()
        if not line_str:
            continue

        timestamps = list(LRC_TIMESTAMP_RE.finditer(line_str))
        if not timestamps:
            continue

        text = LRC_TIMESTAMP_RE.sub("", line_str).strip()
        text = html.unescape(text)
        text = re.sub(r"^<[^>]+>", "", text).strip()

        for ts in timestamps:
            minutes = int(ts.group(1))
            seconds = int(ts.group(2))
            frac_str = ts.group(3) or "0"
            if len(frac_str) == 1:
                fraction = int(frac_str) / 10.0
            elif len(frac_str) == 2:
                fraction = int(frac_str) / 100.0
            else:
                fraction = int(frac_str) / 1000.0

            total_seconds = max(0.0, minutes * 60 + seconds + fraction + offset_sec)
            raw_cues.append((total_seconds, text))

    if not raw_cues:
        return []

    raw_cues.sort(key=lambda x: x[0])

    cues: List[Dict[str, Any]] = []
    for i, (start_time, text) in enumerate(raw_cues):
        if not text:
            continue

        if i < len(raw_cues) - 1:
            next_time = raw_cues[i + 1][0]
            end_time = max(start_time + 0.5, next_time)
            if end_time - start_time > 8.0:
                end_time = start_time + 6.0
        else:
            if track_duration and track_duration > start_time:
                end_time = min(start_time + 6.0, track_duration)
            else:
                end_time = start_time + 5.0

        duration = max(0.1, end_time - start_time)

        cues.append({
            "time": round(start_time, 2),
            "end_time": round(end_time, 2),
            "duration": round(duration, 2),
            "text": text,
        })

    return cues


# --------------------------------------------------------------------------- #
# LRCLIB Integration
# --------------------------------------------------------------------------- #

def query_lrclib(
    track_name: str,
    artist_name: str,
    duration: float = 0.0,
) -> Tuple[List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    headers = {
        "User-Agent": LRCLIB_USER_AGENT,
        "X-User-Agent": LRCLIB_USER_AGENT,
        "Accept": "application/json",
    }

    results: List[Dict[str, Any]] = []

    try:
        params = {"track_name": track_name, "artist_name": artist_name}
        resp = requests.get("https://lrclib.net/api/search", params=params, headers=headers, timeout=5)
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, list):
                results.extend(data)
    except Exception as exc:
        print(f"[lrclib search 1] {exc}")

    if not results:
        try:
            q = f"{artist_name} {track_name}".strip()
            params = {"q": q}
            resp = requests.get("https://lrclib.net/api/search", params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data)
        except Exception as exc:
            print(f"[lrclib search 2] {exc}")

    if not results:
        try:
            params = {"q": track_name}
            resp = requests.get("https://lrclib.net/api/search", params=params, headers=headers, timeout=5)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, list):
                    results.extend(data)
        except Exception as exc:
            print(f"[lrclib search 3] {exc}")

    seen_ids = set()
    unique_results: List[Dict[str, Any]] = []
    for item in results:
        iid = item.get("id")
        if iid and iid not in seen_ids:
            seen_ids.add(iid)
            unique_results.append(item)

    best_match: Optional[Dict[str, Any]] = None

    def score_match(candidate: Dict[str, Any]) -> float:
        score = 0.0
        has_synced = bool(candidate.get("syncedLyrics"))
        if has_synced:
            score += 100.0

        c_dur = float(candidate.get("duration") or 0)
        if duration > 0 and c_dur > 0:
            diff = abs(duration - c_dur)
            score += max(0.0, 50.0 - diff * 2.0)

        c_track = (candidate.get("trackName") or candidate.get("name") or "").lower()
        c_artist = (candidate.get("artistName") or "").lower()

        if track_name.lower() in c_track or c_track in track_name.lower():
            score += 30.0
        if artist_name.lower() in c_artist or c_artist in artist_name.lower():
            score += 30.0

        return score

    if unique_results:
        unique_results.sort(key=score_match, reverse=True)
        synced_candidates = [r for r in unique_results if r.get("syncedLyrics")]
        best_match = synced_candidates[0] if synced_candidates else unique_results[0]

    return unique_results, best_match


def get_lrclib_by_id(lyric_id: int) -> Optional[Dict[str, Any]]:
    headers = {
        "User-Agent": LRCLIB_USER_AGENT,
        "X-User-Agent": LRCLIB_USER_AGENT,
        "Accept": "application/json",
    }
    try:
        resp = requests.get(f"https://lrclib.net/api/get/{lyric_id}", headers=headers, timeout=6)
        if resp.status_code == 200:
            return resp.json()
    except Exception as exc:
        print(f"[lrclib get id] {exc}")
    return None


# --------------------------------------------------------------------------- #
# VTT / YouTube Subtitle Fallbacks
# --------------------------------------------------------------------------- #

CUE_BLOCK = re.compile(
    r"(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}\s*-->\s*(?:\d{1,2}:)?\d{2}:\d{2}\.\d{3}[^\n]*\n"
    r"([\s\S]*?)(?=(?:\r?\n){2}|\Z)"
)
CUE_TIMES = re.compile(
    r"(?:(\d{1,2}):)?(\d{2}:\d{2}\.\d{3})\s*-->\s*(?:(\d{1,2}):)?(\d{2}:\d{2}\.\d{3})"
)
TIMING_TAG = re.compile(r"<\d{1,2}:\d{2}:\d{2}[.,]\d{1,3}>")
MARKUP_TAG = re.compile(r"</?[a-zA-Z][^>]*>")
BRACE_TAG = re.compile(r"\{[^}]*\}")


def _seconds(stamp: str) -> float:
    parts = stamp.strip().replace(",", ".").split(":")
    try:
        if len(parts) == 3:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        if len(parts) == 2:
            return int(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (ValueError, IndexError):
        return 0.0


def _clean_vtt_text(text: str) -> str:
    text = TIMING_TAG.sub("", text)
    text = MARKUP_TAG.sub("", text)
    text = BRACE_TAG.sub("", text)
    text = html.unescape(text)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    return " ".join(lines).strip()


def parse_vtt(vtt: str) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for block in CUE_BLOCK.finditer(vtt):
        times = CUE_TIMES.search(block.group(0))
        if not times:
            continue
        start = _seconds((times.group(1) or "") + times.group(2))
        end = _seconds((times.group(3) or "") + times.group(4))
        text = _clean_vtt_text(block.group(1))
        if not text or text.startswith("WEBVTT"):
            continue
        cues.append({
            "time": round(start, 2),
            "end_time": round(end, 2),
            "duration": round(max(0.1, end - start), 2),
            "text": text,
        })
    return dedupe_cues(cues)


def parse_json3(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    cues: List[Dict[str, Any]] = []
    for event in payload.get("events", []):
        if "segs" not in event:
            continue
        start = event.get("tStartMs", 0) / 1000.0
        duration = event.get("dDurationMs", 0) / 1000.0
        text = _clean_vtt_text("".join(seg.get("utf8", "") for seg in event["segs"]))
        if not text:
            continue
        cues.append({
            "time": round(start, 2),
            "end_time": round(start + duration, 2),
            "duration": round(max(0.1, duration), 2),
            "text": text,
        })
    return dedupe_cues(cues)


def dedupe_cues(cues: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for cue in cues:
        text = cue["text"].strip()
        if not text:
            continue
        if not out:
            out.append(cue)
            continue

        prev = out[-1]
        if text == prev["text"]:
            prev["end_time"] = max(prev["end_time"], cue["end_time"])
            prev["duration"] = round(prev["end_time"] - prev["time"], 2)
            continue
        if text.startswith(prev["text"]) and cue["time"] - prev["time"] < 3.0:
            prev["text"] = text
            prev["end_time"] = max(prev["end_time"], cue["end_time"])
            prev["duration"] = round(prev["end_time"] - prev["time"], 2)
            continue

        out.append(cue)
    return out


def fetch_youtube_captions(info: Dict[str, Any]) -> List[Dict[str, Any]]:
    manual = info.get("subtitles") or {}
    auto = info.get("automatic_captions") or {}
    preferred = ("en", "en-US", "en-GB", "en-orig", "en-auto")

    tracks: List[Dict[str, Any]] = []
    for source in (manual, auto):
        for lang in preferred:
            if lang in source:
                tracks = source[lang]
                break
        if tracks:
            break
        if source:
            tracks = source[next(iter(source))]
            break

    if not tracks:
        return []

    chosen = next((t for t in tracks if t.get("ext") == "json3"), None) \
        or next((t for t in tracks if t.get("ext") == "vtt"), None) \
        or tracks[0]

    url = chosen.get("url")
    if not url:
        return []

    try:
        resp = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if resp.status_code != 200:
            return []
        if chosen.get("ext") == "json3":
            try:
                return parse_json3(resp.json())
            except ValueError:
                pass
        return parse_vtt(resp.text)
    except Exception as exc:
        print(f"[youtube captions] {exc}")
        return []


def instrumental_filler(duration: float) -> List[Dict[str, Any]]:
    if duration <= 0:
        return []
    step = max(6.0, duration / 14.0)
    cues: List[Dict[str, Any]] = []
    cursor = 0.0
    while cursor < duration:
        end = min(duration, cursor + step)
        cues.append({
            "time": round(cursor, 2),
            "end_time": round(end, 2),
            "duration": round(end - cursor, 2),
            "text": f"Instrumental: {int(cursor // 60):02d}:{int(cursor % 60):02d}",
        })
        cursor += step
    return cues


# --------------------------------------------------------------------------- #
# Demo Tracks with Synced Lyrics and Match Candidate Fixtures
# --------------------------------------------------------------------------- #

DEMOS: List[Dict[str, Any]] = [
    {
        "id": "demo-synthwave",
        "title": "Midnight City Lights",
        "artist": "Antigravity Soundworks",
        "album": "Neon Cartography",
        "duration": 180,
        "duration_formatted": "03:00",
        "thumbnail": "https://images.unsplash.com/photo-1518709268805-4e9042af9f23?q=80&w=1200&auto=format&fit=crop",
        "audio_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-1.mp3",
        "has_lyrics": True,
        "lyrics_source": "lrclib",
        "palette": {
            "primary": "#E4B363",
            "secondary": "#6FA8A0",
            "accent": "#C9CED9",
            "dark": "#0D0E15",
            "light": "#F7F6F3",
            "swatches": ["#E4B363", "#6FA8A0", "#C9CED9", "#5A6178", "#2A2D3D"],
            "mesh": {"c1": "#E4B363", "c2": "#6FA8A0", "c3": "#3E4A63"},
        },
        "lyrics": [
            {"time": 0.0, "end_time": 5.0, "duration": 5.0, "text": "Intro: analog pads and a slow arpeggio"},
            {"time": 5.4, "end_time": 10.0, "duration": 4.6, "text": "Neon reflections running down the window glass"},
            {"time": 10.4, "end_time": 15.2, "duration": 4.8, "text": "Every signal tower blinking as the hours pass"},
            {"time": 15.6, "end_time": 20.4, "duration": 4.8, "text": "The bassline moves like traffic through an empty street"},
            {"time": 20.8, "end_time": 25.6, "duration": 4.8, "text": "Circuits in the dashboard keeping time with the beat"},
            {"time": 26.0, "end_time": 31.0, "duration": 5.0, "text": "And I keep driving till the skyline goes quiet"},
            {"time": 31.4, "end_time": 36.4, "duration": 5.0, "text": "Past the last exit, past the last riot"},
            {"time": 36.8, "end_time": 41.6, "duration": 4.8, "text": "Colours fold together where the headlights meet"},
            {"time": 42.0, "end_time": 48.5, "duration": 6.5, "text": "Midnight city lights beneath my feet"},
            {"time": 49.0, "end_time": 60.0, "duration": 11.0, "text": "Breakdown: filtered synth solo"},
            {"time": 60.4, "end_time": 65.4, "duration": 5.0, "text": "Higher now, the antennae start to sing"},
            {"time": 65.8, "end_time": 71.0, "duration": 5.2, "text": "Static on the radio, a wire with no string"},
            {"time": 71.4, "end_time": 78.0, "duration": 6.6, "text": "Midnight city lights, still burning through the rain"},
            {"time": 78.4, "end_time": 86.0, "duration": 7.6, "text": "Outro: delay trails into the dark"},
        ],
        "alternative_lyrics": [
            {
                "id": 9001,
                "trackName": "Midnight City Lights",
                "artistName": "Antigravity Soundworks",
                "albumName": "Neon Cartography (Standard Edition)",
                "duration": 180,
                "instrumental": False,
                "hasSynced": True,
                "syncedLyrics": "[00:00.00] Intro: analog pads and a slow arpeggio\n[00:05.40] Neon reflections running down the window glass\n[00:10.40] Every signal tower blinking as the hours pass\n[00:15.60] The bassline moves like traffic through an empty street\n[00:20.80] Circuits in the dashboard keeping time with the beat\n[00:26.00] And I keep driving till the skyline goes quiet\n[00:31.40] Past the last exit, past the last riot\n[00:36.80] Colours fold together where the headlights meet\n[00:42.00] Midnight city lights beneath my feet\n[00:49.00] Breakdown: filtered synth solo\n[00:60.40] Higher now, the antennae start to sing\n[00:65.80] Static on the radio, a wire with no string\n[00:71.40] Midnight city lights, still burning through the rain",
            },
            {
                "id": 9002,
                "trackName": "Midnight City Lights (Acoustic Redux)",
                "artistName": "Antigravity Soundworks",
                "albumName": "Unplugged in Kyoto",
                "duration": 188,
                "instrumental": False,
                "hasSynced": True,
                "syncedLyrics": "[00:00.00] Fingerpicked guitar chords\n[00:06.00] Neon reflections running down the window glass\n[00:11.20] Every signal tower blinking as the hours pass\n[00:16.80] The rhythm moves like shadows through an empty street\n[00:22.00] Quiet footsteps keeping time with every beat",
            },
        ],
    },
    {
        "id": "demo-lofi",
        "title": "Chasing Stardust",
        "artist": "Cosmic Drift Lab",
        "album": "Slow Orbit",
        "duration": 150,
        "duration_formatted": "02:30",
        "thumbnail": "https://images.unsplash.com/photo-1534447677768-be436bb09401?q=80&w=1200&auto=format&fit=crop",
        "audio_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-2.mp3",
        "has_lyrics": True,
        "lyrics_source": "lrclib",
        "palette": {
            "primary": "#7FB2C6",
            "secondary": "#A89C7C",
            "accent": "#D5D9E0",
            "dark": "#0D0E15",
            "light": "#F7F6F3",
            "swatches": ["#7FB2C6", "#A89C7C", "#D5D9E0", "#4A5568", "#2A2D3D"],
            "mesh": {"c1": "#7FB2C6", "c2": "#A89C7C", "c3": "#3A4256"},
        },
        "lyrics": [
            {"time": 0.0, "end_time": 5.5, "duration": 5.5, "text": "Vinyl crackle, rhodes piano, brushed drums"},
            {"time": 5.9, "end_time": 11.0, "duration": 5.1, "text": "Floating on a slow and quiet afternoon"},
            {"time": 11.4, "end_time": 17.0, "duration": 5.6, "text": "Listening to the kettle hum a familiar tune"},
            {"time": 17.4, "end_time": 22.8, "duration": 5.4, "text": "Dust is turning circles in the window light"},
            {"time": 23.2, "end_time": 29.0, "duration": 5.8, "text": "Nothing here is urgent, nothing here is right"},
            {"time": 29.4, "end_time": 38.0, "duration": 8.6, "text": "Groove: tape delay and a soft bass walk"},
            {"time": 38.4, "end_time": 44.0, "duration": 5.6, "text": "Take the long way home and let the evening drift"},
            {"time": 44.4, "end_time": 51.0, "duration": 6.6, "text": "Everything you needed was already in the room"},
            {"time": 51.4, "end_time": 60.0, "duration": 8.6, "text": "Outro: piano fading under tape hiss"},
        ],
        "alternative_lyrics": [
            {
                "id": 9003,
                "trackName": "Chasing Stardust",
                "artistName": "Cosmic Drift Lab",
                "albumName": "Slow Orbit (Original Release)",
                "duration": 150,
                "instrumental": False,
                "hasSynced": True,
                "syncedLyrics": "[00:00.00] Vinyl crackle, rhodes piano, brushed drums\n[00:05.90] Floating on a slow and quiet afternoon\n[00:11.40] Listening to the kettle hum a familiar tune\n[00:17.40] Dust is turning circles in the window light\n[00:23.20] Nothing here is urgent, nothing here is right",
            }
        ],
    },
    {
        "id": "demo-nepali",
        "title": "आजभोलि (Aajabholi)",
        "artist": "1974 AD",
        "album": "Samjhi Baschu",
        "duration": 160,
        "duration_formatted": "02:40",
        "thumbnail": "https://images.unsplash.com/photo-1544735716-392fe2489ffa?q=80&w=1200&auto=format&fit=crop",
        "audio_url": "https://www.soundhelix.com/examples/mp3/SoundHelix-Song-3.mp3",
        "has_lyrics": True,
        "lyrics_source": "lrclib",
        "palette": {
            "primary": "#E06D53",
            "secondary": "#E5A93C",
            "accent": "#F2D49B",
            "dark": "#0D0E15",
            "light": "#F7F6F3",
            "swatches": ["#E06D53", "#E5A93C", "#F2D49B", "#4E5D6C", "#2A2D3D"],
            "mesh": {"c1": "#E06D53", "c2": "#E5A93C", "c3": "#3A455B"},
        },
        "lyrics": [
            {"time": 0.0, "end_time": 5.2, "duration": 5.2, "text": "सुमधुर बाँसुरी र गितारको धुन"},
            {"time": 5.6, "end_time": 11.0, "duration": 5.4, "text": "आजभोलि नै मरोस् मेरो यो माया"},
            {"time": 11.4, "end_time": 17.0, "duration": 5.6, "text": "सपना सारा अधुरै रहे पनि तिम्रै सम्झना"},
            {"time": 17.4, "end_time": 23.0, "duration": 5.6, "text": "तिमी हाँसेको हेर्ने ठूलो रहर छ मनमा"},
            {"time": 23.4, "end_time": 29.2, "duration": 5.8, "text": "अँध्यारो रातमा जूनझैँ चम्किने मुहार तिम्रो"},
            {"time": 29.6, "end_time": 36.0, "duration": 6.4, "text": "सधैँभरि तिम्रै साथ पाए हुन्थ्यो जीवनमा"},
            {"time": 36.4, "end_time": 44.0, "duration": 7.6, "text": "यो मनले खोज्ने तिमीलाई मात्रै हो सधैँ"},
            {"time": 44.4, "end_time": 55.0, "duration": 10.6, "text": "संगीतको धुनमा हराउँदै जाने रात..."},
        ],
        "alternative_lyrics": [
            {
                "id": 9004,
                "trackName": "आजभोलि (Aajabholi)",
                "artistName": "1974 AD",
                "albumName": "Samjhi Baschu (Acoustic Studio)",
                "duration": 160,
                "instrumental": False,
                "hasSynced": True,
                "syncedLyrics": "[00:00.00] सुमधुर बाँसुरी र गितारको धुन\n[00:05.60] आजभोलि नै मरोस् मेरो यो माया\n[00:11.40] सपना सारा अधुरै रहे पनि तिम्रै सम्झना\n[00:17.40] तिमी हाँसेको हेर्ने ठूलो रहर छ मनमा\n[00:23.40] अँध्यारो रातमा जूनझैँ चम्किने मुहार तिम्रो",
            }
        ],
    },
]


def _proxy_url(source: str, request: Request) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/api/audio-proxy?url={urllib.parse.quote(source, safe='')}"


def demo_payload(track: Dict[str, Any], request: Request) -> Dict[str, Any]:
    payload = {k: v for k, v in track.items() if k != "audio_url"}
    payload["source"] = "demo"
    payload["audio_url"] = _proxy_url(track["audio_url"], request)
    payload["proxied_audio_url"] = payload["audio_url"]
    return payload


# --------------------------------------------------------------------------- #
# Resolver & Extractor
# --------------------------------------------------------------------------- #

CACHE: Dict[str, Dict[str, Any]] = {}


def extract_video_id(url: str) -> Optional[str]:
    patterns = [
        r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:[&?]|$)",
        r"youtu\.be\/([0-9A-Za-z_-]{11})",
        r"embed\/([0-9A-Za-z_-]{11})",
        r"^([0-9A-Za-z_-]{11})$",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def get_cookie_file() -> Optional[str]:
    """Retrieve or dynamically construct a Netscape cookies file for yt-dlp authentication."""
    # 1. Direct path via COOKIES_FILE env var
    cf = os.environ.get("COOKIES_FILE")
    if cf and os.path.isfile(cf):
        return cf

    # 2. Raw cookie text via YOUTUBE_COOKIES env var (common for Render/Heroku)
    yt_cookies = os.environ.get("YOUTUBE_COOKIES")
    if yt_cookies and yt_cookies.strip():
        tmp_path = "/tmp/youtube_cookies.txt"
        try:
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(yt_cookies.strip())
            return tmp_path
        except Exception as exc:
            print(f"[cookies] Failed to write temp cookie file: {exc}")

    # 3. Base64 encoded cookie text via YOUTUBE_COOKIES_BASE64 env var
    b64_cookies = os.environ.get("YOUTUBE_COOKIES_BASE64")
    if b64_cookies and b64_cookies.strip():
        tmp_path = "/tmp/youtube_cookies_b64.txt"
        try:
            import base64
            decoded = base64.b64decode(b64_cookies.strip()).decode("utf-8")
            with open(tmp_path, "w", encoding="utf-8") as fh:
                fh.write(decoded)
            return tmp_path
        except Exception as exc:
            print(f"[cookies] Failed to decode base64 cookie file: {exc}")

    # 4. Local cookies.txt or youtube_cookies.txt in project directories
    for name in ("cookies.txt", "youtube_cookies.txt"):
        local = os.path.join(APP_DIR, name)
        if os.path.isfile(local):
            return local
        if os.path.isfile(name):
            return os.path.abspath(name)

    return None


def extract_audio_data(youtube_url: str) -> Dict[str, Any]:
    cookie_file = get_cookie_file()
    search_target = youtube_url if youtube_url.startswith("http") else f"ytsearch:{youtube_url}"

    # Multiple client profiles designed for datacenter and cloud execution
    client_strategies: List[Optional[List[str]]] = [
        ["web", "ios"],
        ["android_music", "android", "android_creator"],
        ["ios", "web"],
        ["android", "mweb"],
        None,  # Standard yt-dlp fallback
    ]

    last_error: Optional[Exception] = None
    for clients in client_strategies:
        ydl_opts: Dict[str, Any] = {
            "format": "bestaudio/best",
            "skip_download": True,
            "writesubtitles": True,
            "writeautomaticsub": True,
            "subtitleslangs": ["en.*", "en"],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": True,
            "nocheckcertificate": True,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }

        if cookie_file:
            ydl_opts["cookiefile"] = cookie_file

        if clients:
            ydl_opts["extractor_args"] = {
                "youtube": {
                    "player_client": clients,
                }
            }

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(search_target, download=False)
                if info:
                    if "entries" in info and info["entries"]:
                        info = info["entries"][0]
                    return info
        except Exception as e:
            last_error = e
            continue

    raise Exception(f"Failed to process video: {str(last_error)}")


def _best_audio(info: Dict[str, Any]) -> Optional[str]:
    streams = [
        f for f in info.get("formats", [])
        if f.get("url")
        and f.get("acodec") not in (None, "none")
        and f.get("vcodec") in (None, "none")
    ]
    if not streams:
        streams = [f for f in info.get("formats", []) if f.get("url") and f.get("acodec") != "none"]
    if not streams:
        return info.get("url")

    streams.sort(key=lambda f: float(f.get("abr") or f.get("tbr") or 0), reverse=True)
    return streams[0].get("url")


def _best_thumbnail(info: Dict[str, Any]) -> Optional[str]:
    thumbs = info.get("thumbnails") or []
    if thumbs:
        wide = [t for t in thumbs if (t.get("width") or 0) >= 640]
        return (wide[-1] if wide else thumbs[-1]).get("url")
    return info.get("thumbnail")


async def resolve(url: str, request: Request) -> Dict[str, Any]:
    url = (url or "").strip()
    if not url:
        raise HTTPException(status_code=400, detail="A YouTube URL is required.")

    lowered = url.lower()
    if lowered in {"demo", "demo-1", "demo:synthwave", "demo-synthwave"}:
        return demo_payload(DEMOS[0], request)
    if lowered in {"demo-2", "demo:lofi", "demo-lofi"}:
        return demo_payload(DEMOS[1], request)

    video_id = extract_video_id(url)
    cached = CACHE.get(video_id) if video_id else None
    if cached is None:
        cached = CACHE.get(url)

    if cached is None:
        loop = asyncio.get_running_loop()
        try:
            info = await loop.run_in_executor(None, extract_audio_data, url)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Could not read that video: {exc}") from exc

        audio = _best_audio(info)
        if not audio:
            raise HTTPException(status_code=400, detail="No streamable audio found for that video.")

        duration = float(info.get("duration") or 0)
        title, artist = clean_metadata(info)

        # 1. Attempt LRCLIB Search
        lrclib_results, best_lrclib = await loop.run_in_executor(
            None, query_lrclib, title, artist, duration
        )

        lyrics: List[Dict[str, Any]] = []
        lyrics_source = "none"

        if best_lrclib and best_lrclib.get("syncedLyrics"):
            lyrics = parse_lrc(best_lrclib["syncedLyrics"], duration)
            if lyrics:
                lyrics_source = "lrclib"

        # 2. Fallback to YouTube captions if LRCLIB returned no synced lyrics
        if not lyrics:
            yt_lyrics = await loop.run_in_executor(None, fetch_youtube_captions, info)
            if yt_lyrics:
                lyrics = yt_lyrics
                lyrics_source = "youtube_captions"

        # 3. Fallback to instrumental cues
        has_lyrics = bool(lyrics)
        if not has_lyrics:
            lyrics = instrumental_filler(duration)
            lyrics_source = "instrumental"

        # Prepare alternative lyrics options for frontend selector
        formatted_alternatives: List[Dict[str, Any]] = []
        for item in lrclib_results[:12]:
            formatted_alternatives.append({
                "id": item.get("id"),
                "trackName": item.get("trackName") or item.get("name") or "Untitled",
                "artistName": item.get("artistName") or "Unknown",
                "albumName": item.get("albumName") or "",
                "duration": item.get("duration") or 0,
                "hasSynced": bool(item.get("syncedLyrics")),
                "syncedLyrics": item.get("syncedLyrics") or "",
                "plainLyrics": item.get("plainLyrics") or "",
            })

        thumbnail = _best_thumbnail(info)
        palette = await loop.run_in_executor(None, extract_palette, thumbnail)

        cached = {
            "id": info.get("id", ""),
            "title": title,
            "artist": artist,
            "album": info.get("album") or (best_lrclib.get("albumName") if best_lrclib else "") or "",
            "duration": duration,
            "duration_formatted": f"{int(duration // 60):02d}:{int(duration % 60):02d}",
            "thumbnail": thumbnail or "",
            "audio_source": audio,
            "has_lyrics": has_lyrics,
            "lyrics_source": lyrics_source,
            "palette": palette,
            "lyrics": lyrics,
            "alternative_lyrics": formatted_alternatives,
            "source": "youtube",
        }
        if len(CACHE) > 128:
            CACHE.clear()
        if video_id:
            CACHE[video_id] = cached
        if info.get("id"):
            CACHE[info["id"]] = cached
        CACHE[url] = cached

    payload = dict(cached)
    payload["audio_url"] = _proxy_url(cached["audio_source"], request)
    payload["proxied_audio_url"] = payload["audio_url"]
    payload.pop("audio_source", None)
    return payload


# --------------------------------------------------------------------------- #
# API Endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/process")
async def process(request: Request, url: str = Query(..., description="YouTube URL or demo id")):
    return await resolve(url, request)


@app.get("/api/lyrics")
async def lyrics_endpoint(request: Request, q: str = Query(..., description="Query string or YouTube URL")):
    return await resolve(q, request)


@app.post("/api/parse")
async def parse(payload: ParseRequest, request: Request):
    return await resolve(payload.url, request)


@app.get("/api/lyrics/search")
async def lyrics_search(
    q: Optional[str] = Query(None),
    track: Optional[str] = Query(None),
    artist: Optional[str] = Query(None),
    duration: Optional[float] = Query(0.0),
):
    """Search LRCLIB on-demand with custom query strings from the frontend lyrics modal."""
    loop = asyncio.get_running_loop()
    t_name = track or ""
    a_name = artist or ""
    if q and not t_name:
        t_name = q

    results, _ = await loop.run_in_executor(None, query_lrclib, t_name, a_name, duration or 0.0)
    formatted = []
    for item in results[:16]:
        formatted.append({
            "id": item.get("id"),
            "trackName": item.get("trackName") or item.get("name") or "Untitled",
            "artistName": item.get("artistName") or "Unknown",
            "albumName": item.get("albumName") or "",
            "duration": item.get("duration") or 0,
            "hasSynced": bool(item.get("syncedLyrics")),
            "syncedLyrics": item.get("syncedLyrics") or "",
            "plainLyrics": item.get("plainLyrics") or "",
        })
    return formatted


@app.get("/api/lyrics/{lyric_id}")
async def fetch_lyric_by_id(lyric_id: int, duration: Optional[float] = Query(0.0)):
    """Fetch an alternative LRCLIB record and return parsed timestamped cues."""
    loop = asyncio.get_running_loop()
    item = await loop.run_in_executor(None, get_lrclib_by_id, lyric_id)
    if not item:
        raise HTTPException(status_code=404, detail="Lyric record not found in LRCLIB.")

    synced = item.get("syncedLyrics")
    cues = parse_lrc(synced, duration or 0.0) if synced else []
    return {
        "id": item.get("id"),
        "trackName": item.get("trackName") or item.get("name"),
        "artistName": item.get("artistName"),
        "albumName": item.get("albumName"),
        "duration": item.get("duration"),
        "hasSynced": bool(synced),
        "lyrics": cues,
        "raw_lrc": synced or "",
    }


@app.post("/api/lyrics/parse-lrc")
async def parse_raw_lrc(payload: LrcParseRequest):
    """Parse a user-pasted raw LRC string into structured cues."""
    cues = parse_lrc(payload.lrc, payload.duration or 0.0)
    return {
        "has_lyrics": bool(cues),
        "count": len(cues),
        "lyrics": cues,
    }


# --------------------------------------------------------------------------- #
# Range-Aware Audio Relay
# --------------------------------------------------------------------------- #

@app.get("/api/audio-proxy")
@app.head("/api/audio-proxy")
async def audio_proxy(request: Request, url: str = Query(...)):
    """Relay upstream audio, forwarding Range headers so seeking stays instantaneous."""
    target = urllib.parse.unquote(url)
    if not target.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Invalid audio source.")

    headers = {"User-Agent": USER_AGENT, "Accept": "*/*"}
    incoming_range = request.headers.get("range")
    if incoming_range:
        headers["Range"] = incoming_range

    try:
        upstream = requests.get(target, headers=headers, stream=True, timeout=15)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Upstream audio unavailable: {exc}") from exc

    relay_headers = {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "GET, HEAD, OPTIONS",
        "Access-Control-Allow-Headers": "*",
        "Access-Control-Expose-Headers": "Content-Length, Content-Range, Accept-Ranges",
        "Cross-Origin-Resource-Policy": "cross-origin",
        "Accept-Ranges": "bytes",
        "Cache-Control": "no-store",
    }
    for key in ("Content-Range", "Content-Length", "Content-Type"):
        if key in upstream.headers:
            relay_headers[key] = upstream.headers[key]

    def pump():
        try:
            for chunk in upstream.iter_content(chunk_size=64 * 1024):
                if chunk:
                    yield chunk
        finally:
            upstream.close()

    status = upstream.status_code if upstream.status_code in (200, 206) else 200
    return StreamingResponse(
        pump(),
        status_code=status,
        headers=relay_headers,
        media_type=upstream.headers.get("Content-Type", "audio/mpeg"),
    )


# --------------------------------------------------------------------------- #
# General Endpoints
# --------------------------------------------------------------------------- #

@app.get("/api/demo")
async def demo_list(request: Request):
    return [demo_payload(t, request) for t in DEMOS]


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "cadence", "version": app.version}


@app.get("/", response_class=HTMLResponse)
async def index():
    if os.path.exists(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as fh:
            return HTMLResponse(fh.read())
    return HTMLResponse("<h1>Cadence</h1><p>index.html is missing.</p>", status_code=404)


if __name__ == "__main__":
    import uvicorn

    print("Cadence running at http://127.0.0.1:8000")
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
