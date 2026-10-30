"""Optional pinned independent encoder/decoder; never downloads tools at runtime."""
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import re
import stat
import subprocess
import sys

BINARY_SHA256 = 'e7e7fb30477f717e6f55f9180a70386c62677ef8a4d4d1a5d948f4098aa3eb99'
PACKAGE_VERSION = '0.6.0'
BINARY_SIZE = 79826272
ROOT = Path(__file__).resolve().parent
MAX_BYTES = 16 * 1024 * 1024
MAX_PCM = 31 * 48000 * 2


def verified_binary():
    if sys.platform != 'linux' or platform.machine() not in {'x86_64', 'AMD64'}:
        raise ValueError('Pinned diagnostic tool supports Linux x86_64 only')
    distribution = metadata.distribution('imageio-ffmpeg')
    if distribution.version != PACKAGE_VERSION:
        raise ValueError('Diagnostic package version mismatch')
    path = Path(distribution.locate_file('imageio_ffmpeg/binaries/ffmpeg-linux-x86_64-v7.0.2'))
    descriptor=os.open(path,os.O_RDONLY|os.O_NOFOLLOW)
    with os.fdopen(descriptor,'rb') as executable:
        info=os.fstat(executable.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size!=BINARY_SIZE:
            raise ValueError('Diagnostic executable size/type mismatch')
        digest=hashlib.sha256()
        while chunk:=executable.read(1024*1024):digest.update(chunk)
        if digest.hexdigest()!=BINARY_SHA256:
            raise ValueError('Diagnostic executable integrity mismatch')
    return str(path)


def _run(operation, raw=b''):
    if len(raw) > MAX_BYTES:
        raise ValueError('Diagnostic input exceeds size limit')
    # A fresh child applies limits before exec. No preexec_fn in threaded web
    # workers, no shell, no inherited app secrets and pipe-only FFmpeg inputs.
    environment={'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'}
    if operation.startswith('pyav_') or operation.startswith('inspect_'):
        # Nixpacks may provide the C++ loader path needed by the existing PyAV
        # wheel. This is a platform linkage setting, not application credentials.
        if os.environ.get('LD_LIBRARY_PATH'):
            environment['LD_LIBRARY_PATH']=os.environ['LD_LIBRARY_PATH']
    try:
        result = subprocess.run([sys.executable, '-m', 'voice_diagnostic_tools', '--worker', operation],
            input=raw, capture_output=True, timeout=25, cwd=ROOT,
            env=environment)
    except (OSError,subprocess.SubprocessError):
        raise ValueError('Independent tool failed or timed out; private output omitted') from None
    limit = MAX_PCM if operation == 'decode' else MAX_BYTES
    if len(result.stdout) > limit:
        raise ValueError('Diagnostic output exceeds size limit')
    return result


def runtime():
    try:
        verified_binary()
        result = _run('version')
        match = re.match(rb'ffmpeg version (7\.0\.2-static) ', result.stdout)
        if result.returncode or not match:
            raise ValueError('Diagnostic version check failed')
        return {'available':True, 'verified':True, 'package':'imageio-ffmpeg', 'package_version':PACKAGE_VERSION,
            'ffmpeg_version':match.group(1).decode(), 'binary_sha256':BINARY_SHA256,
            'ffprobe_available':False, 'note':'Separate pinned FFmpeg executable; ffprobe is not included. PyAV is unchanged.'}
    except (ValueError, OSError, metadata.PackageNotFoundError, subprocess.SubprocessError):
        return {'available':False, 'verified':False,
            'reason':'Install the optional hash-pinned diagnostic wheel on Linux x86_64; no request-time downloads.'}


def encode(raw):
    result = _run('encode', raw)
    if result.returncode or result.stderr.strip() or not result.stdout:
        raise ValueError('Independent encoding failed; private decoder text omitted')
    return result.stdout


def encode_pyav(raw, profile):
    if profile not in {'current','historical'}:
        raise ValueError('Unknown experimental profile')
    result=_run('pyav_'+profile,raw)
    if result.returncode or not result.stdout:
        raise ValueError('Bounded PyAV encoding failed; private output omitted')
    return result.stdout


def _inspect(raw, operation):
    result=_run(operation,raw)
    if result.returncode:
        raise ValueError('Bounded audio inspection failed; private output omitted')
    try:return json.loads(result.stdout)
    except ValueError:raise ValueError('Invalid audio inspection result') from None


def inspect_source(raw):
    return _inspect(raw,'inspect_source')


def inspect_variant(raw):
    return _inspect(raw,'inspect_variant')


def decode(raw):
    try:result = _run('decode', raw)
    except ValueError:return {'passed':False,'reason':'Independent decode failed or timed out'}
    duration = len(result.stdout) / (48000 * 2)
    return {'passed':result.returncode == 0 and not result.stderr.strip() and .25 <= duration <= 30.05,
        'returncode':result.returncode, 'decoded_duration_seconds':duration,
        'stderr_present':bool(result.stderr.strip()), 'stderr_sha256':hashlib.sha256(result.stderr).hexdigest(),
        'pcm_sha256':hashlib.sha256(result.stdout).hexdigest(), 'pcm_size_bytes':len(result.stdout),
        'executable_sha256':BINARY_SHA256, 'ffmpeg_version':'7.0.2-static'}


def worker(operation):
    import resource
    resource.setrlimit(resource.RLIMIT_CPU, (15,15))
    resource.setrlimit(resource.RLIMIT_AS, (512*1024*1024,512*1024*1024))
    resource.setrlimit(resource.RLIMIT_FSIZE, (32*1024*1024,32*1024*1024))
    resource.setrlimit(resource.RLIMIT_CORE, (0,0))
    if operation in {'pyav_current','pyav_historical','inspect_source','inspect_variant'}:
        from voice_regression_diagnostics import _short_source, historical_profile
        from audio_service import normalize_voice_audio
        from voice_compatibility import inspect
        raw=sys.stdin.buffer.read(MAX_BYTES+1)
        if len(raw)>MAX_BYTES:raise ValueError('Input exceeds limit')
        if operation=='inspect_variant':
            result=inspect(raw)
            if result['validation']['duration_seconds']>30.05:raise ValueError('Variant too long')
            sys.stdout.write(json.dumps(result));return
        info=_short_source(raw)
        if operation=='inspect_source':
            sys.stdout.write(json.dumps(info));return
        output=historical_profile(raw) if operation=='pyav_historical' else normalize_voice_audio(raw)[0]
        if len(output)>MAX_BYTES:raise ValueError('Output exceeds limit')
        sys.stdout.buffer.write(output);return
    binary = verified_binary()
    if operation == 'version':
        args = [binary,'-version']
    elif operation in {'encode','decode'}:
        args = [binary,'-v','error','-nostdin','-threads','1','-protocol_whitelist','pipe','-i','pipe:0',
            '-map','0:a:0','-vn','-sn','-dn','-t','31','-threads','1','-ac','1','-ar','48000']
        if operation == 'encode':
            args += ['-c:a','libopus','-b:a','32k','-application','voip','-frame_duration','20','-vbr','on','-f','ogg','pipe:1']
        else:
            args += ['-c:a','pcm_s16le','-f','s16le','pipe:1']
    else:
        raise ValueError('Unknown diagnostic operation')
    os.execve(binary,args,{'PATH':'/usr/bin:/bin','LANG':'C','LC_ALL':'C'})


if __name__ == '__main__':
    if len(sys.argv)==3 and sys.argv[1]=='--worker':
        try:worker(sys.argv[2])
        except (ValueError,OSError,metadata.PackageNotFoundError):
            raise SystemExit(2) from None
    else:
        print(json.dumps(runtime(),indent=2))
