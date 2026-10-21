from io import BytesIO
import math
import struct
import wave

import pytest

from audio_service import VoiceAudioError, normalize_voice_audio


def wav_recording(seconds=1):
    output = BytesIO()
    with wave.open(output, "wb") as recording:
        recording.setnchannels(1)
        recording.setsampwidth(2)
        recording.setframerate(16_000)
        recording.writeframes(b"".join(
            struct.pack("<h", int(5_000 * math.sin(2 * math.pi * 440 * sample / 16_000)))
            for sample in range(int(16_000 * seconds))
        ))
    return output.getvalue()


def test_browser_audio_is_converted_to_ogg_opus_and_measured():
    converted, duration = normalize_voice_audio(wav_recording(), "audio/wav")
    assert converted.startswith(b"OggS")
    assert duration == pytest.approx(1.0, abs=0.02)


def test_audio_validation_rejects_unsupported_and_empty_files():
    with pytest.raises(VoiceAudioError, match="Unsupported"):
        normalize_voice_audio(b"data", "application/octet-stream")
    with pytest.raises(VoiceAudioError, match="between"):
        normalize_voice_audio(b"", "audio/webm")
