#!/usr/bin/env python3
"""
Gemini text-to-speech for short hook voiceovers.

The API key is NOT in this repo. In the cloud environment it is a network secret:
the agent proxy adds the `x-goog-api-key` header to requests for
generativelanguage.googleapis.com. If GEMINI_API_KEY is set as an environment
variable instead, it is sent too.

Usage:  python scripts/voiceover.py "Text to speak" out.wav [--voice Puck] [--style "..."]
"""
import argparse
import base64
import json
import os
import struct
import sys
import urllib.error
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from common import load_config  # noqa: E402

ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"


def _find_audio(node):
    """Return the last base64 audio payload found anywhere in the response."""
    found = []

    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "audio" and isinstance(n.get("data"), str):
                found.append(n["data"])
            inline = n.get("inlineData") or n.get("inline_data")
            if isinstance(inline, dict) and isinstance(inline.get("data"), str):
                found.append(inline["data"])
            for v in n.values():
                walk(v)
        elif isinstance(n, list):
            for v in n:
                walk(v)

    walk(node)
    return found[-1] if found else None


def _pcm_to_wav(pcm, rate=24000):
    header = b"RIFF" + struct.pack("<I", 36 + len(pcm)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(pcm))
    return header + pcm


def synthesize(text, out_wav, voice=None, style=None, model=None, timeout=90):
    cfg = load_config().get("voiceover", {})
    body = {
        "model": model or cfg.get("model", "gemini-3.8-flash-tts"),
        "input": [{
            "type": "user_input",
            "content": [{
                "type": "text",
                "text": text,
                "annotations": [{"type": "speech_metadata",
                                 "style": style or cfg.get("style", "energetic narrator")}],
            }],
        }],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice or cfg.get("voice", "Puck")}]},
    }
    headers = {"Content-Type": "application/json"}
    if os.environ.get("GEMINI_API_KEY"):
        headers["x-goog-api-key"] = os.environ["GEMINI_API_KEY"]
    req = urllib.request.Request(ENDPOINT, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Gemini TTS HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:400]}")
    b64 = _find_audio(data)
    if not b64:
        raise RuntimeError("Gemini TTS response had no audio: " + json.dumps(data)[:400])
    audio = base64.b64decode(b64)
    if audio[:4] != b"RIFF":  # raw 24 kHz 16-bit mono PCM -> wrap in a WAV header
        audio = _pcm_to_wav(audio)
    with open(out_wav, "wb") as f:
        f.write(audio)
    return out_wav


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("text")
    ap.add_argument("out")
    ap.add_argument("--voice")
    ap.add_argument("--style")
    a = ap.parse_args()
    synthesize(a.text, a.out, a.voice, a.style)
    print(f"wrote {a.out}")
