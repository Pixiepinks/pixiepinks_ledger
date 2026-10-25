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
    if extension == '.mp4':
        import av
        if raw[4:8] != b'ftyp' or raw[8:12] not in {b'isom', b'iso2', b'mp41', b'mp42', b'avc1', b'M4V '}:
            raise ValueError()
        with av.open(io.BytesIO(raw), options={'protocol_whitelist': 'pipe'}) as source:
            if len(source.streams.video) != 1 or len(source.streams.audio) > 1 or len(source.streams) > 2:
                raise ValueError()
            video = source.streams.video[0]
            seconds = source.duration / av.time_base if source.duration else 0
            if video.codec_context.name != 'h264' or not 0 < seconds <= 120 or not 0 < video.width * video.height <= 1920 * 1080:
                raise ValueError()
            if video.average_rate and video.average_rate > 60:
                raise ValueError()
            for stream in source.streams:
                if stream.type == 'audio' and stream.codec_context.name != 'aac':
                    raise ValueError()
                stream.thread_count = 1
                stream.codec_context.options = {'err_detect': 'explode'}
            frames = 0
            for packet in source.demux():
                if packet.is_corrupt:
                    raise ValueError()
                for frame in packet.decode():
                    if isinstance(frame, av.VideoFrame):
                        frames += 1
                        if frames > 7200 or frame.width * frame.height > 1920 * 1080 or frame.pts is not None and frame.time_base and frame.pts * frame.time_base > 120:
                            raise ValueError()
            if not frames or video.frames and frames != video.frames:
                raise ValueError()
        return {'duration_seconds': round(seconds, 3)}
    if extension == '.txt':
        text = raw.decode('utf-8-sig')
        if not text.strip() or any(ord(c) < 32 and c not in '\r\n\t' or 0x7f <= ord(c) <= 0x9f for c in text):
            raise ValueError()
        return {}
    if extension in {'.docx', '.xlsx'}:
        import zipfile
        from pathlib import PurePosixPath
        from xml.etree import ElementTree as ET
        if not raw.startswith(b'PK\x03\x04'):
            raise ValueError()
        with zipfile.ZipFile(io.BytesIO(raw)) as package:
            entries = package.infolist()
            names = [item.filename for item in entries]
            if len(entries) > 1000 or len(set(names)) != len(names) or sum(item.file_size for item in entries) > 32 * 1024 * 1024:
                raise ValueError()
            documents = {}
            main_name, namespace, root_name, content_type = (
                ('word/document.xml', 'http://schemas.openxmlformats.org/wordprocessingml/2006/main', 'document', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml')
                if extension == '.docx' else
                ('xl/workbook.xml', 'http://schemas.openxmlformats.org/spreadsheetml/2006/main', 'workbook', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml'))
            for item in entries:
                name = item.filename
                if item.flag_bits & 1 or '\\' in name or name.startswith('/') or '..' in PurePosixPath(name).parts or ':' in name or 'vbaproject' in name.lower():
                    raise ValueError()
                content = bytearray() if name in {main_name, '[Content_Types].xml'} else None
                with package.open(item) as entry:
                    while chunk := entry.read(65536):
                        if content is not None:
                            content.extend(chunk)
                            if len(content) > 2 * 1024 * 1024:
                                raise ValueError()
                if content is not None:
                    if b'<!DOCTYPE' in content.upper() or b'<!ENTITY' in content.upper():
                        raise ValueError()
                    documents[name] = ET.fromstring(content)
            types = documents['[Content_Types].xml']
            if types.tag != '{http://schemas.openxmlformats.org/package/2006/content-types}Types':
                raise ValueError()
            overrides = list(types)
            if any('macroenabled' in item.get('ContentType', '').lower() for item in overrides):
                raise ValueError()
            if not any(item.get('PartName') == '/' + main_name and item.get('ContentType') == content_type for item in overrides):
                raise ValueError()
            if documents[main_name].tag != '{' + namespace + '}' + root_name:
                raise ValueError()
        return {}
    if extension in {'.doc', '.xls'}:
        import olefile
        if not raw.startswith(b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1'):
            raise ValueError()
        with olefile.OleFileIO(io.BytesIO(raw), raise_defects=olefile.DEFECT_INCORRECT) as document:
            paths = document.listdir()
            if len(paths) > 1000 or any(any(part.lower() in {'vba', 'macros', 'encryptedpackage', 'encryptioninfo'} for part in path) for path in paths):
                raise ValueError()
            if extension == '.doc':
                if not document.exists('WordDocument') or not (document.exists('0Table') or document.exists('1Table')):
                    raise ValueError()
                header = document.openstream('WordDocument').read(32)
                if len(header) < 32 or header[:2] != b'\xec\xa5' or struct.unpack('<H', header[10:12])[0] & 0x8100:
                    raise ValueError()
            else:
                name = 'Workbook' if document.exists('Workbook') else 'Book'
                workbook = document.openstream(name).read(16_000_001)
                if len(workbook) < 12 or workbook[:2] != b'\x09\x08' or struct.unpack('<H', workbook[2:4])[0] not in {8, 16} or struct.unpack('<HH', workbook[4:8]) not in {(0x0500, 5), (0x0600, 5)}:
                    raise ValueError()
                offset, records = 0, 0
                while offset + 4 <= len(workbook):
                    record, length = struct.unpack('<HH', workbook[offset:offset+4])
                    if record == 0 and length == 0:
                        break
                    if record == 0x002f or offset + 4 + length > len(workbook) or records > 100000:
                        raise ValueError()
                    records += 1
                    offset += 4 + length
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
    except ImportError:
        # A missing native/Python dependency is an operational failure, not bad
        # customer content. Never print import diagnostics or deployment paths.
        sys.exit(70)
    except BaseException:
        sys.exit(1)
