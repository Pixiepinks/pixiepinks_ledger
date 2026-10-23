"""Validation and in-memory conversion for staff-recorded WhatsApp voice notes."""

from io import BytesIO

import av
from av.audio.resampler import AudioResampler


MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
ALLOWED_INPUT_TYPES = {
    "audio/webm", "audio/ogg", "audio/mp4", "audio/mpeg", "audio/wav", "audio/x-wav"
}


class VoiceAudioError(ValueError):
    """Raised when a recording is invalid or cannot be safely converted."""


def normalize_voice_audio(raw: bytes, content_type: str) -> tuple[bytes, float]:
    """Decode browser audio and return a mono 48 kHz OGG/Opus WhatsApp voice note."""
    mime = (content_type or "").split(";", 1)[0].strip().casefold()
    if mime not in ALLOWED_INPUT_TYPES:
        raise VoiceAudioError("Unsupported audio format")
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise VoiceAudioError("Recording must be between 1 byte and 16 MB")

    output_buffer = BytesIO()
    samples = 0
    try:
        with av.open(BytesIO(raw), mode="r") as source:
            if not source.streams.audio:
                raise VoiceAudioError("The uploaded file does not contain audio")
            with av.open(output_buffer, mode="w", format="ogg") as target:
                output_stream = target.add_stream("libopus", rate=48_000)
                output_stream.layout = "mono"
                resampler = AudioResampler(format="fltp", layout="mono", rate=48_000)
                for frame in source.decode(audio=0):
                    for converted in resampler.resample(frame):
                        samples += converted.samples
                        if samples / 48_000 > MAX_AUDIO_SECONDS:
                            raise VoiceAudioError("Voice messages cannot exceed 10 minutes")
                        for packet in output_stream.encode(converted):
                            target.mux(packet)
                for converted in resampler.resample(None):
                    samples += converted.samples
                    for packet in output_stream.encode(converted):
                        target.mux(packet)
                for packet in output_stream.encode(None):
                    target.mux(packet)
    except VoiceAudioError:
        raise
    except Exception as exc:
        raise VoiceAudioError("The recording could not be decoded") from exc

    duration = samples / 48_000
    if duration < 0.25:
        raise VoiceAudioError("Recording is too short")
    converted = output_buffer.getvalue()
    if not converted or len(converted) > MAX_AUDIO_BYTES:
        raise VoiceAudioError("Converted recording exceeds WhatsApp's 16 MB limit")
    return converted, round(duration, 2)
