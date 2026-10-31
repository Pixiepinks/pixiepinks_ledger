"""Bounded offline comparisons only: this module has no WhatsApp send/upload path."""
from io import BytesIO
from pathlib import Path
import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
from uuid import uuid4

import av
from av.audio.resampler import AudioResampler
import audio_service
import voice_compatibility
from voice_trace import fingerprint

MAX_SECONDS = 30


def read_private_audio(path):
    """No symlink or special-file reads, bounded even if the file changes."""
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(descriptor, 'rb') as file:
        if not stat.S_ISREG(os.fstat(file.fileno()).st_mode):
            raise ValueError('Use a regular owner-authorized audio file')
        raw = file.read(audio_service.MAX_AUDIO_BYTES + 1)
    audio_service.source_audio_info(raw)
    return raw


def private_workspace(parent):
    parent = Path(parent)
    parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = parent.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValueError('Diagnostic output directory must be owned by this process user with permissions 0700')
    return Path(tempfile.mkdtemp(prefix='voice-offline-', dir=parent))


def write_private(directory, name, raw):
    descriptor = os.open(Path(directory) / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'wb') as file:
        file.write(raw)


def _short_source(raw):
    info = audio_service.source_audio_info(raw)
    if info.get('duration_seconds') and info['duration_seconds'] > MAX_SECONDS:
        raise ValueError('Offline comparisons are limited to 30-second recordings')
    # Duration headers can lie or be absent; count decoded samples independently.
    duration = 0.0
    with audio_service._open(raw) as source:
        for frame in source.decode(audio=0):
            duration += frame.samples / frame.sample_rate
            if duration > MAX_SECONDS + 0.001:
                raise ValueError('Offline comparisons are limited to 30-second recordings')
    if duration < .25:
        raise ValueError('Offline source is too short')
    return info


def historical_profile(raw):
    """Pre-22b05f8 encoding settings, NOT a verified known-working profile.

    Keeps current safe input opening/duration limits; no production path calls it.
    Previous defaults: mono 48k libopus, fltp resampler, bitrate/application/frame
    options unspecified. Security validation is never rolled back.
    """
    return _encode(raw, 'ogg', 'libopus', 'fltp', None)


def _encode(raw, container, codec, sample_format, bitrate):
    output = BytesIO()
    with audio_service._open(raw) as source:
        with av.open(output, mode='w', format=container) as target:
            stream = target.add_stream(codec, rate=48000)
            stream.layout = 'mono'
            if bitrate is not None:
                stream.bit_rate = bitrate
            resampler = AudioResampler(format=sample_format, layout='mono', rate=48000)
            for frame in source.decode(audio=0):
                for converted in resampler.resample(frame):
                    for packet in stream.encode(converted):
                        target.mux(packet)
            for converted in resampler.resample(None):
                for packet in stream.encode(converted):
                    target.mux(packet)
            for packet in stream.encode(None):
                target.mux(packet)
    return output.getvalue()


def _stderr_evidence(raw):
    # Never return decoder text: it can contain private tags, paths or URLs.
    return {'stderr_present': bool(raw.strip()), 'stderr_size_bytes': len(raw),
            'stderr_sha256': hashlib.sha256(raw).hexdigest()}


def tool_versions():
    values={}
    for name in ('ffmpeg','ffprobe'):
        if not shutil.which(name):
            values[name]={'available':False}
            continue
        try:
            result=subprocess.run([name,'-version'],capture_output=True,timeout=5,check=False)
            match=re.match(rb'(?:ffmpeg|ffprobe) version ([A-Za-z0-9.+_~-]{1,80})',result.stdout)
            values[name]={'available':True,'version':match.group(1).decode() if match else 'unrecognized'}
        except (OSError,subprocess.SubprocessError):
            values[name]={'available':True,'version':'unavailable'}
    return values


def independent_inspection(raw, directory):
    path = Path(directory) / (uuid4().hex + '.audio')
    write_private(directory, path.name, raw)
    report = {}
    for name in ('ffmpeg', 'ffprobe'):
        if not shutil.which(name):
            report[name] = {'available': False}
            continue
        try:
            if name == 'ffmpeg':
                args = [name, '-v', 'warning', '-nostdin', '-protocol_whitelist', 'file,pipe', '-i', str(path), '-f', 'null', '-']
            else:
                args = [name, '-v', 'warning', '-protocol_whitelist', 'file,pipe', '-show_entries',
                    'stream=codec_name,codec_type,sample_rate,channels,duration,bit_rate:format=format_name,duration,size,bit_rate', '-of', 'json', str(path)]
            result = subprocess.run(args, capture_output=True, timeout=20, check=False)
            value = {'available': True, 'returncode': result.returncode, **_stderr_evidence(result.stderr)}
            if name == 'ffprobe' and result.returncode == 0:
                parsed = json.loads(result.stdout)
                # Copy only numeric/enum fields; never tags or filename text.
                value['streams'] = [{k:v for k,v in stream.items() if k in {'codec_name','codec_type','sample_rate','channels','duration','bit_rate'}
                    and (type(v) is int or isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9.,_-]{1,80}',v))} for stream in parsed.get('streams',[])[:2]]
                value['format'] = {k:v for k,v in parsed.get('format',{}).items() if k in {'format_name','duration','size','bit_rate'}
                    and isinstance(v,str) and re.fullmatch(r'[A-Za-z0-9.,_-]{1,80}',v)}
            report[name] = value
        except (subprocess.SubprocessError, OSError, ValueError):
            report[name] = {'available': True, 'error': 'Independent inspection failed or timed out; private output omitted'}
    return report


def analyze(raw, directory):
    info = audio_service.source_audio_info(raw)
    value = {'bytes': fingerprint(raw), 'format': info, 'independent': independent_inspection(raw, directory)}
    if raw.startswith(b'OggS'):
        try:
            value['ogg_opus'] = voice_compatibility.inspect(raw)
        except audio_service.VoiceAudioError:
            value['ogg_opus'] = {'validation_passed': False}
    return value


def compare(raw, directory, *, known_working=None, actual_failed=None):
    info = _short_source(raw)
    report = {'source': fingerprint(raw), 'source_format': info, 'pyav_version':av.__version__,
        'pyav_libraries':av.library_versions, 'independent_tools':tool_versions(), 'variants':{}, 'live_sends':0,
        'interpretation':'Offline decode success is not WhatsApp playback proof. Historical profile is not a verified working deployment.'}
    candidates = [
        ('current', lambda:audio_service._normalize_pyav(raw)[0], {'encoder':'PyAV/libopus','rate':48000,'channels':1,'bitrate':32000,'application':'voip','frame_duration_ms':20,'vbr':'on','resampler':'flt'}, '.ogg','audio/ogg'),
        ('historical_pre_22b05f8', lambda:historical_profile(raw), {'encoder':'PyAV/libopus','rate':48000,'channels':1,'bitrate':'unspecified','application':'unspecified','frame_duration_ms':'unspecified','vbr':'unspecified','resampler':'fltp'}, '.ogg','audio/ogg'),
        ('aac_m4a_offline_only', lambda:_encode(raw,'mp4','aac','fltp',64000), {'encoder':'PyAV/aac','container':'mp4','rate':48000,'channels':1,'bitrate':64000,'voice': 'omitted if a future documented audio experiment is authorized'}, '.m4a','audio/mp4'),
    ]
    for name, encoder, parameters, suffix, mime in candidates:
        try:
            output = encoder()
            value = analyze(output, directory)
            value.update(parameters=parameters, candidate_mime=mime, candidate_extension=suffix,
                sent=False, mime_support='Not independently verified against current Meta documentation')
            write_private(directory, name + suffix, output)
            report['variants'][name] = value
        except (audio_service.VoiceAudioError, av.error.FFmpegError, ValueError):
            report['variants'][name] = {'error':'Offline encoding or validation failed; no production change made'}
    if shutil.which('ffmpeg'):
        try:
            # Decode from a bounded pipe, disallow network/local external media references.
            command=['ffmpeg','-v','error','-nostdin','-protocol_whitelist','pipe','-i','pipe:0',
                '-map','0:a:0','-vn','-c:a','libopus','-b:a','32k','-application','voip','-frame_duration','20',
                '-vbr','on','-ac','1','-ar','48000','-f','ogg','pipe:1']
            result=subprocess.run(command,input=raw,capture_output=True,timeout=30,check=False)
            if result.returncode or len(result.stdout)>audio_service.MAX_AUDIO_BYTES:
                report['variants']['ffmpeg_cli']={'error':'Independent encoding failed', **_stderr_evidence(result.stderr)}
            else:
                output=result.stdout
                report['variants']['ffmpeg_cli']={**analyze(output,directory), 'parameters':command,
                    'candidate_mime':'audio/ogg','candidate_extension':'.ogg','sent':False}
                write_private(directory,'ffmpeg_cli.ogg',output)
        except (subprocess.SubprocessError,OSError,audio_service.VoiceAudioError):
            report['variants']['ffmpeg_cli']={'error':'Independent encoding failed or timed out'}
    else:
        report['variants']['ffmpeg_cli']={'available':False,'note':'Optional CLI absent; PyAV bundled FFmpeg is not an independent tool'}
    for label, supplied in [('owner_reported_working',known_working),('actual_failed',actual_failed)]:
        if supplied is not None:
            _short_source(supplied)
            report[label]=analyze(supplied,directory)
            report[label]['provenance']='Owner supplied/authorized; deployment and phone playback cannot be inferred from file bytes'
            write_private(directory,label+'.audio',supplied)
    write_private(directory,'report.json',json.dumps(report,indent=2,default=str).encode())
    return report


def analyze_meta_attempt(db, route, attempt_id, directory):
    from voice_production_diagnostics import attempt_report, expected_upload, retrieve_meta
    report=attempt_report(db,route,attempt_id)
    if report.get('error'):
        raise ValueError('Owner test attempt not found')
    def inspector(raw):
        write_private(directory,'meta-hosted.audio',raw)
        return analyze(raw,directory)
    result=retrieve_meta(report.get('meta_media_id'),expected_upload(report),offline_inspector=inspector)
    evidence={'route':route,'attempt_id':attempt_id,'recording_id':report.get('recording_id'),
        'recording_revision_at_send':report.get('recording_revision_at_send'),
        'status':report.get('status'),'delivery_status':report.get('delivery_status'),
        'historical_trace_available':bool(report.get('trace')),'expected_upload_fingerprint':expected_upload(report),
        'meta_media_id':report.get('meta_media_id'),'retrieval':result}
    write_private(directory,'meta-report.json',json.dumps(evidence,indent=2,default=str).encode())
    return evidence


def compare_meta_attempts(db, working_route, working_id, failed_route, failed_id, directory):
    """Read two explicitly owner-authorized references, never re-upload them."""
    evidence={}
    for label,route,attempt_id in [('working_reference',working_route,working_id),('failed_reference',failed_route,failed_id)]:
        folder=Path(directory)/label
        folder.mkdir(mode=0o700)
        evidence[label]=analyze_meta_attempt(db,route,attempt_id,folder)
    keys=('opus_head_version','channels','input_sample_rate_hint','pre_skip_samples','output_gain_q8',
          'channel_mapping_family','encoded_packet_duration_ms_unique','final_page_eos')
    left=evidence['working_reference']['retrieval'].get('offline_inspection',{}).get('ogg_opus',{})
    right=evidence['failed_reference']['retrieval'].get('offline_inspection',{}).get('ogg_opus',{})
    evidence['stream_field_comparison']={key:{'working_reference':left.get(key),'failed_reference':right.get(key),
        'equal':left[key]==right[key] if key in left and key in right else None} for key in keys}
    evidence['interpretation']='References are owner-provided labels. CRM Inbox playback is not Android playback proof. Different source content can explain hash/bitrate/duration differences; matching headers do not prove WhatsApp compatibility.'
    evidence['live_sends']=0
    write_private(directory,'pair-report.json',json.dumps(evidence,indent=2,default=str).encode())
    return evidence


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['compare','meta','pair'])
    parser.add_argument('--owner-authorized',action='store_true',required=True)
    parser.add_argument('--output-dir',required=True,help='Private diagnostic parent, owned by app user, permissions 0700')
    parser.add_argument('--source');parser.add_argument('--known-working');parser.add_argument('--actual-failed')
    parser.add_argument('--route',choices=['saved','browser'],default='saved');parser.add_argument('--attempt-id',type=int)
    parser.add_argument('--working-attempt-id',type=int)
    parser.add_argument('--working-route',choices=['saved','browser'],default='saved')
    args=parser.parse_args()
    if args.command=='compare' and not args.source:parser.error('compare requires --source')
    if args.command in {'meta','pair'} and (not args.attempt_id or args.attempt_id<1):parser.error('meta/pair requires a positive --attempt-id')
    if args.command=='pair' and (not args.working_attempt_id or args.working_attempt_id<1):parser.error('pair requires a positive --working-attempt-id')
    try:
        directory=private_workspace(args.output_dir)
        if args.command=='compare':
            report=compare(read_private_audio(args.source),directory,
                known_working=read_private_audio(args.known_working) if args.known_working else None,
                actual_failed=read_private_audio(args.actual_failed) if args.actual_failed else None)
        else:
            from database import SessionLocal
            with SessionLocal() as db:
                report=(compare_meta_attempts(db,args.working_route,args.working_attempt_id,args.route,args.attempt_id,directory)
                    if args.command=='pair' else analyze_meta_attempt(db,args.route,args.attempt_id,directory))
        print(json.dumps({'workspace_id':directory.name,'report':report},indent=2,default=str))
    except (ValueError,OSError,av.error.FFmpegError):
        raise SystemExit('Offline diagnostic could not complete. Check the private directory, input and optional tools; no messages sent.') from None
