"""Video/audio/image content parts on both inbound protocols.

llama.cpp takes `input_video` / `input_audio` and throws
`unsupported content[].type` on anything else, so every client spelling has to
be renamed before it goes upstream. These tests pin the renames and the shape
that reaches the server.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from proxy.translate import (  # noqa: E402
    AudioInputError,
    VideoInputError,
    _anthropic_content_to_openai,
    _normalize_media_parts,
)

B64 = "QUJDREVG"          # "ABCDEF"
MP3_URI = f"data:audio/mpeg;base64,{B64}"
MP4_URI = f"data:video/mp4;base64,{B64}"


def _one(part):
    msgs = [{"role": "user", "content": [part]}]
    _normalize_media_parts(msgs)
    return msgs[0]["content"][0]


# ── OpenAI inbound ─────────────────────────────────────────────────────────

def test_openai_input_audio_raw_base64_is_the_canonical_shape():
    """OpenAI's own spelling: raw base64 in .data, plus a format we ignore."""
    out = _one({"type": "input_audio",
                "input_audio": {"data": B64, "format": "mp3"}})
    assert out == {"type": "input_audio", "input_audio": {"data": B64}}
    assert "format" not in out["input_audio"], "llama.cpp ignores it; don't forward"


def test_openai_input_audio_data_uri_is_unwrapped():
    out = _one({"type": "input_audio", "input_audio": {"data": MP3_URI}})
    assert out == {"type": "input_audio", "input_audio": {"data": B64}}


def test_openai_input_audio_url_field_is_accepted():
    """llama.cpp allows .url; server.py has already inlined a remote one."""
    out = _one({"type": "input_audio", "input_audio": {"url": MP3_URI}})
    assert out == {"type": "input_audio", "input_audio": {"data": B64}}


def test_audio_url_spelling_is_renamed():
    """`audio_url` mirrors `video_url`/`image_url`; forwarding it is a 400."""
    out = _one({"type": "audio_url", "audio_url": {"url": MP3_URI}})
    assert out == {"type": "input_audio", "input_audio": {"data": B64}}


def test_video_url_spelling_is_still_renamed():
    out = _one({"type": "video_url", "video_url": {"url": MP4_URI}})
    assert out == {"type": "input_video", "input_video": {"data": B64}}


def test_unresolved_remote_url_is_refused_per_kind():
    with pytest.raises(AudioInputError):
        _one({"type": "input_audio", "input_audio": {"url": "https://x/a.mp3"}})
    with pytest.raises(VideoInputError):
        _one({"type": "video_url", "video_url": {"url": "https://x/v.mp4"}})


def test_text_and_image_parts_are_untouched():
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "hi"},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,X"}},
    ]}]
    before = [dict(p) for p in msgs[0]["content"]]
    _normalize_media_parts(msgs)
    assert msgs[0]["content"] == before


# ── Anthropic inbound ──────────────────────────────────────────────────────

def test_anthropic_audio_block_base64():
    """Not in Anthropic's API — a telecode extension mirroring `image`."""
    parts = _anthropic_content_to_openai([
        {"type": "audio",
         "source": {"type": "base64", "media_type": "audio/mpeg", "data": B64}},
    ])
    assert parts == [{"type": "input_audio", "input_audio": {"data": B64}}]


def test_anthropic_audio_block_data_uri_source():
    parts = _anthropic_content_to_openai([
        {"type": "audio", "source": {"type": "url", "url": MP3_URI}},
    ])
    assert parts == [{"type": "input_audio", "input_audio": {"data": B64}}]


def test_anthropic_video_block_still_works():
    parts = _anthropic_content_to_openai([
        {"type": "video", "source": {"type": "base64", "data": B64}},
    ])
    assert parts == [{"type": "input_video", "input_video": {"data": B64}}]


def test_anthropic_text_plus_audio_keeps_both_and_order():
    parts = _anthropic_content_to_openai([
        {"type": "text", "text": "what is this sound?"},
        {"type": "audio", "source": {"type": "base64", "data": B64}},
    ])
    assert [p["type"] for p in parts] == ["text", "input_audio"]


def test_anthropic_unresolved_audio_url_is_refused():
    with pytest.raises(AudioInputError):
        _anthropic_content_to_openai([
            {"type": "audio", "source": {"type": "url",
                                         "url": "https://x/a.mp3"}},
        ])
