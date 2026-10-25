"""Bounded, disposable content-validation worker. Never renders documents or writes files."""
import io
import json
import resource
import sys
import struct
import zlib


def validate(raw, extension):
    if extension in {'.jpg', '.jpeg', '.png'}:
        import av
        expected = 'png' if extension == '.png' else 'mjpeg'
        signature = raw.startswith(b'\x89PNG\r\n\x1a\n') if expected == 'png' else raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9')
        if not signature:
            raise ValueError()
        if extension == '.png':
            offset, kinds = 8, []
            while offset < len(raw):
                if offset + 12 > len(raw):
                    raise ValueError()
                length = struct.unpack('>I', raw[offset:offset+4])[0]
                kind = raw[offset+4:offset+8]
                end = offset + 12 + length
                if end > len(raw) or zlib.crc32(raw[offset+4:end-4]) != struct.unpack('>I', raw[end-4:end])[0]:
                    raise ValueError()
                kinds.append(kind)
                if kind == b'acTL':
                    raise ValueError()
                offset = end
            if kinds.count(b'IHDR') != 1 or kinds[0] != b'IHDR' or kinds[-1] != b'IEND' or b'IDAT' not in kinds:
                raise ValueError()
        with av.open(io.BytesIO(raw), options={'protocol_whitelist': 'pipe'}) as source:
            stream = source.streams.video[0]
            if stream.codec_context.name != expected or not 0 < stream.width * stream.height <= 20_000_000:
                raise ValueError()
            stream.thread_count = 1
            stream.codec_context.options = {"err_detect": "explode"}
            frame = next(source.decode(stream))
            if frame.width != stream.width or frame.height != stream.height:
                raise ValueError()
        return {}
    if extension == '.pdf':
        from pypdf import PdfReader
        if not raw.startswith(b'%PDF-') or b'%%EOF' not in raw[-1024:]:
            raise ValueError()
        reader = PdfReader(io.BytesIO(raw), strict=True)
        if reader.is_encrypted or not 0 < len(reader.pages) <= 1000:
            raise ValueError()
        # Walk the page tree; no rendering, JavaScript, embedded-file execution or extraction.
        for page in reader.pages:
            if not page.mediabox:
                raise ValueError()
        return {}
    raise ValueError()


if __name__ == '__main__':
    try:
        resource.setrlimit(resource.RLIMIT_CPU, (5, 5))
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))
        extension = sys.stdin.buffer.readline(16).decode('ascii').strip()
        raw = sys.stdin.buffer.read(16_000_001)
        if len(raw) > 16_000_000:
            raise ValueError()
        result = validate(raw, extension)
        sys.stdout.write(json.dumps(result))
    except BaseException:
        sys.exit(1)
