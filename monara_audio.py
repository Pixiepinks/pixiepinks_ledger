"""Bounded local audio conversion; only the existing pinned FFmpeg executable is used."""
import base64,io,json,os,subprocess,wave
from pathlib import Path
from settings import settings
from audio_service import validate_voice_audio
import voice_diagnostic_tools as tools

class MonaraAudioError(Exception):pass

def pcm_wav(raw):
    if not isinstance(raw,bytes) or not raw or len(raw)%2 or len(raw)>4320000:raise MonaraAudioError('INVALID_PCM')
    out=io.BytesIO()
    with wave.open(out,'wb') as wav:
        wav.setnchannels(1);wav.setsampwidth(2);wav.setframerate(24000);wav.writeframes(raw)
    return out.getvalue()

def encode(pcm):
    duration=len(pcm)/48000
    if not 0<duration<=min(90,settings.MONARA_TTS_MAX_AUDIO_SECONDS):raise MonaraAudioError('DURATION_LIMIT')
    wav=pcm_wav(pcm);binary=tools.verified_binary();result={}
    # Fixed argv, no shell, no file paths or network protocols supplied by the provider.
    for kind,codec in (('ogg','libopus'),('mp3','libmp3lame')):
        args=[str(binary),'-hide_banner','-loglevel','error','-nostdin','-threads','1','-protocol_whitelist','pipe','-i','pipe:0','-vn','-ac','1','-ar','48000','-c:a',codec,'-b:a','64k','-f',kind,'pipe:1']
        try:p=subprocess.run(args,input=wav,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={"PATH":"/usr/bin:/bin"},timeout=20,check=True)
        except (OSError,subprocess.SubprocessError):raise MonaraAudioError('ENCODER_UNAVAILABLE') from None
        if not p.stdout or len(p.stdout)>min(settings.MONARA_TTS_MAX_AUDIO_BYTES,5000000):raise MonaraAudioError('OUTPUT_LIMIT')
        result[kind]=p.stdout
    info=validate_voice_audio(result['ogg'])
    if abs(info['duration_seconds']-duration)>.1:raise MonaraAudioError('DURATION_MISMATCH')
    # Decode MP3 independently through packaged PyAV; never trust the extension.
    import av
    samples=0
    with av.open(io.BytesIO(result['mp3'])) as container:
        if len(container.streams.audio)!=1 or container.streams.audio[0].codec_context.name not in {'mp3','mp3float'}:raise MonaraAudioError('INVALID_MP3')
        for frame in container.decode(audio=0):samples+=frame.samples/frame.sample_rate
    if abs(samples-duration)>.15:raise MonaraAudioError('MP3_DURATION_MISMATCH')
    return {**result,'duration':duration}

def generate(transcript,model,voice,api_version):
    python=settings.MONARA_TTS_PYTHON or str(Path(__file__).resolve().parent/'.monara-venv/bin/python')
    payload={'transcript':transcript,'model':model,'voice':voice,'api_version':api_version,'timeout':max(5,min(60,settings.MONARA_TTS_TIMEOUT_SECONDS)),'max_pcm_bytes':min(4320000,settings.MONARA_TTS_MAX_AUDIO_SECONDS*48000)}
    env={'PATH':'/usr/bin:/bin','GEMINI_API_KEY':settings.GEMINI_API_KEY}
    # Keep inherited CA/proxy configuration, never arbitrary application secrets.
    for key in ('HTTPS_PROXY','HTTP_PROXY','NO_PROXY','https_proxy','http_proxy','no_proxy','SSL_CERT_FILE','REQUESTS_CA_BUNDLE'):
        if key in os.environ:env[key]=os.environ[key]
    try:
        p=subprocess.run([python,str(Path(__file__).with_name('monara_sdk_worker.py'))],input=json.dumps(payload).encode(),stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,timeout=payload['timeout']+5,check=True)
        if len(p.stdout)>6000000:raise ValueError()
        result=json.loads(p.stdout);raw=base64.b64decode(result['pcm'],validate=True)
        if result['mime'].lower().replace(' ','')!='audio/l16;codec=pcm;rate=24000':raise ValueError()
        return {**encode(raw),'usage':result.get('usage',{})}
    except (OSError,subprocess.SubprocessError,ValueError,KeyError):raise MonaraAudioError('PROVIDER_UNAVAILABLE_OR_INVALID_AUDIO') from None
