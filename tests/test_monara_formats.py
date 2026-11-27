import base64,io,json,struct,wave,subprocess,os
from types import SimpleNamespace as N
from pathlib import Path
import pytest
from monara_formats import profile,normalize,describe,ResponseFormatError
from monara_diagnostics import diagnostic,clean
from monara_sdk_worker import collect
from test_monara import sdk_python

PCM=b'\x01\x02\x03\x04'*240

def wav(raw=PCM,rate=24000,channels=1,width=2):
    f=io.BytesIO()
    with wave.open(f,'wb') as w:w.setnchannels(channels);w.setsampwidth(width);w.setframerate(rate);w.writeframes(raw)
    return f.getvalue()
def chunk(raw,mime):return N(candidates=[N(finish_reason=None,content=N(parts=[N(inline_data=N(data=raw,mime_type=mime))]))],usage_metadata=None)

@pytest.mark.parametrize('mime',['audio/L16;codec=pcm;rate=24000','audio/L16;rate=24000;codec=pcm','Audio/L16; RATE="24000"; CODEC="PCM"','audio/L16;rate=24000','audio/L16; rate=24000; channels=1; bits=16'])
def test_equivalent_pcm_mime_forms_preserve_samples(mime):
    result=collect([chunk(PCM[:100],mime),chunk(PCM[100:],mime)])
    assert base64.b64decode(result['pcm'])==PCM
    assert result['mime']=='audio/L16;codec=pcm;rate=24000'

@pytest.mark.parametrize('mime',['audio/wav','audio/x-wav','audio/wave','audio/wav; rate=24000; channels=1; bits=16'])
def test_header_validated_wav_stream_preserves_exact_pcm(mime):
    raw=wav();out=collect([chunk(raw[:30],mime),chunk(raw[30:],mime)])
    assert base64.b64decode(out['pcm'])==PCM

@pytest.mark.parametrize('params',[{'rate':48000},{'channels':2},{'width':1},{'width':3}])
def test_unsupported_wav_profile_fails_closed(params):
    with pytest.raises(ResponseFormatError) as e:collect([chunk(wav(**params),'audio/wav')])
    assert e.value.reason=='UNSUPPORTED_WAV_PROFILE'

@pytest.mark.parametrize('raw',[b'RIFF',b'not-a-wav',wav()+wav(),wav()[:-2],b'RIFX'+wav()[4:]])
def test_corrupt_truncated_big_endian_or_concatenated_wav_is_not_guessed(raw):
    with pytest.raises(ResponseFormatError):collect([chunk(raw,'audio/wav')])

@pytest.mark.parametrize('mime',['audio/pcm;rate=24000','audio/L16;rate=48000','audio/L16;rate=24000;channels=2','audio/L16;rate=24000;rate=8000','audio/L16;codec=mp3;rate=24000','text/plain','audio/L16;rate=24000;SECRET=PRIVATE'])
def test_unsupported_and_conflicting_declarations_rejected(mime):
    with pytest.raises(ResponseFormatError) as e:collect([chunk(PCM,mime)])
    d=diagnostic('response_validation',e.value,e.value.reason,audio=e.value.audio)
    assert 'PRIVATE' not in json.dumps(d) and 'SECRET' not in json.dumps(d)

def test_diagnostic_metadata_never_includes_arbitrary_mime_or_parameters():
    assert describe('PRIVATE_API_KEY')=={'mime_type':'OTHER'}
    assert describe('audio/wav\r\nAuthorization: PRIVATE')=={'mime_type':'OTHER'}
    d=clean({'stage':'response_validation','reason':'UNSUPPORTED_PCM_FORMAT','audio':{'mime_type':'SECRET','sample_rate':True,'channels':1,'token':'PRIVATE'}})
    assert d['audio']=={'channels':1}

def test_strict_duration_and_byte_bounds_apply_to_wav():
    with pytest.raises(ResponseFormatError):collect([chunk(wav(),'audio/wav')],max_bytes=len(PCM)-1)
    raw=bytearray(wav());raw[40:44]=struct.pack('<I',1000000000)
    with pytest.raises(ResponseFormatError):collect([chunk(bytes(raw),'audio/wav')])

def test_actual_isolated_sdk_worker_helpers_need_no_key_or_api():
    code="""import base64,io,wave,json
from monara_formats import profile,normalize
b=io.BytesIO()
with wave.open(b,'wb') as w:w.setparams((1,2,24000,0,'NONE','not compressed'));w.writeframes(b'\\x01\\x02'*100)
k,info=profile('audio/wav')
print(json.dumps({'bytes':len(normalize(b.getvalue(),k,info,1000))}))
"""
    p=subprocess.run([str(sdk_python()),'-c',code],capture_output=True,check=True,timeout=10,env={'PATH':'/usr/bin:/bin'})
    assert json.loads(p.stdout)=={'bytes':200}

def test_wave_declaration_is_not_lost_on_later_chunks():
    raw=wav()
    with pytest.raises(ResponseFormatError) as e:collect([chunk(raw[:30],'audio/wav;rate=8000'),chunk(raw[30:],'audio/wav')])
    assert e.value.reason=='UNSUPPORTED_WAV_PROFILE'

@pytest.mark.parametrize('tag',[3,6])
def test_float_or_companded_wav_is_never_treated_as_pcm(tag):
    raw=bytearray(wav());raw[20:22]=struct.pack('<H',tag)
    with pytest.raises(ResponseFormatError):collect([chunk(bytes(raw),'audio/wav')])
