"""Disposable local media conversion worker. No customer/API credentials or network protocols."""
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import tempfile


def probe(raw, extension):
    import av
    image = extension in {'.jpg','.jpeg','.png','.webp'}
    if extension in {'.jpg','.jpeg'} and not (raw.startswith(b'\xff\xd8\xff') and raw.endswith(b'\xff\xd9')):
        raise ValueError('Invalid JPEG')
    if extension == '.png' and (not raw.startswith(b'\x89PNG\r\n\x1a\n') or b'acTL' in raw[:65536]):
        raise ValueError('Invalid or animated PNG')
    if extension == '.webp' and (not (raw.startswith(b'RIFF') and raw[8:12] == b'WEBP') or b'ANIM' in raw):
        raise ValueError('Invalid WebP')
    if not image and (len(raw) < 12 or raw[4:8] != b'ftyp'):
        raise ValueError('Use MP4/MOV video')
    with av.open(io.BytesIO(raw), options={'protocol_whitelist':'pipe','enable_drefs':'0'}) as source:
        if len(source.streams.video) != 1 or len(source.streams.audio) > 1:
            raise ValueError('Unsupported streams')
        video = source.streams.video[0]
        if not 0 < video.width * video.height <= (40_000_000 if image else 3840 * 2160):
            raise ValueError('Image/video resolution exceeds processing limit')
        seconds = source.duration / av.time_base if source.duration else float(video.duration * video.time_base) if video.duration and video.time_base else 0
        if not image and not 0 < seconds <= 120:
            raise ValueError('Video must be at most 120 seconds')
        return image, seconds


def main():
    resource.setrlimit(resource.RLIMIT_CPU, (90,90))
    resource.setrlimit(resource.RLIMIT_AS, (1024*1024*1024,1024*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (64*1024*1024,64*1024*1024))
    extension, maximum, binary = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    raw = sys.stdin.buffer.read(64*1024*1024+1)
    if not raw or len(raw) > 64*1024*1024: raise ValueError()
    image, seconds = probe(raw, extension)
    with tempfile.TemporaryDirectory(prefix='staff-media-') as folder:
        source = Path(folder)/('source'+extension); source.write_bytes(raw)
        output = Path(folder)/('optimized.jpg' if image else 'optimized.mp4')
        args = [binary,'-hide_banner','-loglevel','error','-nostdin','-xerror','-threads','1',
            '-filter_threads','1','-protocol_whitelist','file','-err_detect','explode','-i',str(source),
            '-map','0:v:0','-map_metadata','-1','-map_chapters','-1']
        if image:
            args += ['-frames:v','1','-vf',"scale='min(1600,iw)':'min(1600,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2",'-c:v','mjpeg','-q:v','3','-threads','1']
        else:
            budget = min(2_500_000, int(maximum*.86*8/seconds)-96_000)
            if budget < 100_000: raise ValueError('Configured video size is too small')
            args += ['-map','0:a:0?','-t','120','-vf',"scale='min(1280,iw)':'min(720,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2,fps=30",
                '-c:v','libx264','-preset','veryfast','-pix_fmt','yuv420p','-threads','1',
                '-b:v',str(budget),'-maxrate',str(budget),'-bufsize',str(budget*2),
                '-c:a','aac','-b:a','96k','-ac','2','-ar','48000','-movflags','+faststart']
        args += ['-fs',str(maximum+1),'-y',str(output)]
        subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            check=True, timeout=100, env={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})
        if not 0 < output.stat().st_size <= maximum: raise ValueError('Unable to fit configured media limit')
        converted = output.read_bytes()
        if not image:
            _, output_seconds = probe(converted, '.mp4')
            if abs(output_seconds - seconds) > .25: raise ValueError('Video duration changed; refusing a truncated conversion')
        sys.stdout.buffer.write(converted)


if __name__ == '__main__':
    try: main()
    except ImportError: sys.exit(70)
    except BaseException: sys.exit(1)
