"""Byte-based audio detection and validated FFmpeg/libopus voice-note encoding."""
from io import BytesIO
import logging
import struct

import av
from av.audio.resampler import AudioResampler

logger = logging.getLogger(__name__)
MAX_AUDIO_BYTES = 16 * 1024 * 1024
MAX_AUDIO_SECONDS = 10 * 60
NORMALIZATION_VERSION = 1
ALLOWED_INPUT_TYPES = {"audio/webm", "audio/ogg", "audio/mp4", "audio/x-m4a", "audio/m4a",
    "audio/aac", "audio/x-aac", "audio/mpeg", "audio/wav", "audio/x-wav", "application/octet-stream"}


class VoiceAudioError(ValueError):
    """A safe staff-facing message, never a decoder error or private filename."""


def _check_size(raw):
    if not raw or len(raw) > MAX_AUDIO_BYTES:
        raise VoiceAudioError("Recording must be between 1 byte and 16 MB")


def _signature(raw):
    # Never let libav probe playlists, URLs or arbitrary documents. MIME labels
    # and filenames are advisory; supported audio is identified from its bytes.
    return (raw.startswith((b"OggS", b"\x1aE\xdf\xa3", b"ID3"))
        or (raw.startswith(b"RIFF") and raw[8:12] == b"WAVE")
        or raw[4:8] == b"ftyp"
        or (len(raw)>2 and raw[0] == 255 and raw[1] & 0xe0 == 0xe0))


def _open(raw):
    return av.open(BytesIO(raw), mode="r", options={"protocol_whitelist":"pipe", "err_detect":"explode"})


def _stream(source):
    if len(source.streams.audio) != 1 or source.streams.video:
        raise VoiceAudioError("Upload a recording with one audio stream and no video")
    stream = source.streams.audio[0]
    codec = stream.codec_context
    if not codec.sample_rate or not 8000 <= codec.sample_rate <= 192000 or not 1 <= codec.channels <= 8:
        raise VoiceAudioError("Unsupported audio sample rate or channel count")
    return stream


def source_audio_info(raw):
    _check_size(raw)
    if not _signature(raw):
        raise VoiceAudioError("Unsupported or corrupted audio recording")
    try:
        with _open(raw) as source:
            stream = _stream(source)
            container = source.format.name
            if not set(container.split(',')) & {"ogg","wav","mp3","aac","mov","mp4","m4a","matroska","webm"}:
                raise VoiceAudioError("Unsupported audio container")
            codec = stream.codec_context.name
            if codec not in {"aac","opus","vorbis","mp3","mp3float"} and not codec.startswith("pcm_"):
                raise VoiceAudioError("Unsupported audio codec")
            duration=float(stream.duration*stream.time_base) if stream.duration is not None and stream.time_base else None
            return {"container":container,"codec":codec,"duration_seconds":duration,"sample_rate":stream.codec_context.sample_rate,
                "channels":stream.codec_context.channels,"size_bytes":len(raw)}
    except VoiceAudioError:
        raise
    except Exception as exc:
        raise VoiceAudioError("The recording could not be decoded; upload another recording") from exc


def _crc_table():
    table=[]
    for value in range(256):
        value <<= 24
        for _ in range(8):value=((value<<1)^0x04c11db7 if value & 0x80000000 else value<<1) & 0xffffffff
        table.append(value)
    return tuple(table)


_CRC = _crc_table()


def _validate_ogg_pages(raw):
    position=0; sequence=0; serial=None; last_flags=0; first_packet=None
    while position < len(raw):
        header=raw[position:position+27]
        if len(header)!=27 or header[:4]!=b"OggS" or header[4]!=0 or header[5]&~7:
            raise VoiceAudioError("Invalid OGG container")
        segments=header[26]; laces=raw[position+27:position+27+segments]
        end=position+27+segments+sum(laces)
        if len(laces)!=segments or end>len(raw):raise VoiceAudioError("Truncated OGG recording")
        page=bytearray(raw[position:end]); expected=struct.unpack_from('<I',page,22)[0]; page[22:26]=b'\0'*4
        checksum=0
        for byte in page:checksum=((checksum<<8)&0xffffffff)^_CRC[((checksum>>24)^byte)&255]
        page_serial,page_sequence=struct.unpack_from('<II',header,14)
        if checksum!=expected or page_sequence!=sequence or (serial is not None and serial!=page_serial):
            raise VoiceAudioError("Corrupted OGG recording")
        if sequence==0:
            if header[5]!=2:raise VoiceAudioError("Invalid OGG stream header")
            first_packet=raw[position+27+segments:end]
        elif header[5]&2:raise VoiceAudioError("Multiple OGG streams are not supported")
        if header[5]&4 and end!=len(raw):raise VoiceAudioError("Invalid OGG end marker")
        serial=page_serial;last_flags=header[5];position=end;sequence+=1
    if not last_flags&4 or not first_packet or len(first_packet)!=19 or first_packet[:8]!=b'OpusHead':
        raise VoiceAudioError("Recording must contain a complete OGG/Opus stream")
    if first_packet[8]!=1 or first_packet[9]!=1 or first_packet[18]!=0:
        raise VoiceAudioError("Voice notes require mono Opus audio")


def validate_voice_audio(raw):
    """Validate every OGG page plus full independent audio decode, not MIME tags."""
    _check_size(raw);_validate_ogg_pages(raw)
    try:
        with _open(raw) as source:
            stream=_stream(source)
            if source.format.name!='ogg' or stream.codec_context.name!='opus' or stream.codec_context.channels!=1 or stream.codec_context.sample_rate!=48000:
                raise VoiceAudioError("Voice notes require OGG/Opus, mono, 48000 Hz")
            samples=0
            for frame in source.decode(audio=0):
                samples+=frame.samples
                if samples/48000>MAX_AUDIO_SECONDS:raise VoiceAudioError("Voice messages cannot exceed 10 minutes")
            duration=samples/48000
            if duration<.25:raise VoiceAudioError("Recording is too short or contains no decodable audio")
            return {"container":"ogg","codec":"opus","sample_rate":48000,"channels":1,
                    "duration_seconds":round(duration,3),"size_bytes":len(raw)}
    except VoiceAudioError:raise
    except Exception as exc:raise VoiceAudioError("The converted recording failed playback validation") from exc


def normalize_voice_audio(raw: bytes, content_type: str = "") -> tuple[bytes, float]:
    """Convert actual supported audio bytes to speech-quality mono 48k OGG/Opus."""
    info=source_audio_info(raw)
    output_buffer=BytesIO();samples=0
    try:
        with _open(raw) as source:
            _stream(source)
            with av.open(output_buffer,mode="w",format="ogg") as target:
                stream=target.add_stream("libopus",rate=48000)
                stream.layout="mono";stream.bit_rate=32000
                stream.codec_context.options={"application":"voip","frame_duration":"20","vbr":"on"}
                resampler=AudioResampler(format="flt",layout="mono",rate=48000)
                for frame in source.decode(audio=0):
                    for converted in resampler.resample(frame):
                        samples+=converted.samples
                        if samples/48000>MAX_AUDIO_SECONDS:raise VoiceAudioError("Voice messages cannot exceed 10 minutes")
                        for packet in stream.encode(converted):target.mux(packet)
                for converted in resampler.resample(None):
                    samples+=converted.samples
                    for packet in stream.encode(converted):target.mux(packet)
                for packet in stream.encode(None):target.mux(packet)
    except VoiceAudioError:raise
    except Exception as exc:
        logger.warning("Voice conversion failed source_container=%s source_codec=%s error_type=%s",info['container'],info['codec'],type(exc).__name__)
        raise VoiceAudioError("Audio conversion failed; upload another recording or contact an administrator") from exc
    converted=output_buffer.getvalue();validated=validate_voice_audio(converted)
    if abs(validated['duration_seconds']-samples/48000)>.05:
        raise VoiceAudioError("Converted recording duration does not match its source")
    logger.info("Voice normalized source_container=%s source_codec=%s source_rate=%s source_channels=%s source_bytes=%s output_container=ogg output_codec=opus output_rate=48000 output_channels=1 duration_seconds=%s output_bytes=%s",
        info['container'],info['codec'],info['sample_rate'],info['channels'],info['size_bytes'],validated['duration_seconds'],len(converted))
    return converted,round(validated['duration_seconds'],2)


if __name__ == "__main__":
    import argparse
    import json
    import wave
    parser=argparse.ArgumentParser(description="Offline voice codec deployment check; sends nothing")
    parser.add_argument("--check",action="store_true",required=True)
    parser.parse_args()
    av.codec.Codec("aac","r")
    source=BytesIO()
    with wave.open(source,"wb") as recording:
        recording.setnchannels(1);recording.setsampwidth(2);recording.setframerate(16000)
        recording.writeframes(b"\0\0"*8000)
    normalized,_=normalize_voice_audio(source.getvalue(),"audio/wav")
    print(json.dumps({"encoder":"libopus","aac_decoder":True,**validate_voice_audio(normalized)}))
