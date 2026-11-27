"""Both supported build selectors must install and verify the isolated SDK."""
import json
import os
from pathlib import Path
import subprocess
import tomllib
import pytest
ROOT=Path(__file__).resolve().parents[1]
INSTALL='python -m venv .monara-venv && .monara-venv/bin/pip install -r requirements.monara.txt'
VERIFY='.monara-venv/bin/python monara_sdk_worker.py --capabilities'

@pytest.mark.parametrize('filename',['nixpacks.toml','nixpacks.voice-diagnostics.toml'])
def test_required_sdk_setup_precedes_provenance_in_each_build(filename):
    config=tomllib.loads((ROOT/filename).read_text());commands=config['phases']['build']['cmds']
    assert commands[0]=='...'
    assert commands.count(INSTALL)==commands.count(VERIFY)==1
    assert commands.index(INSTALL)<commands.index(VERIFY)<commands.index('python build_info.py --write')
    assert any('requirements.voice-diagnostics.txt' in c and '--require-hashes' in c for c in commands)
    assert any('voice_diagnostic_tools' in c and 'verified' in c for c in commands)
    assert config['phases']['setup']['nixLibs']==['...','gcc-unwrapped']
    assert config['variables']=={'PIP_ONLY_BINARY':'av'}
    assert '-c requirements.monara.lock' in (ROOT/'requirements.monara.txt').read_text()

def test_build_verification_is_offline_and_credential_free():
    python=os.getenv('MONARA_TTS_PYTHON',str(ROOT/'.monara-venv/bin/python'))
    if not Path(python).is_file():pytest.skip('Install isolated SDK or provide MONARA_TTS_PYTHON')
    result=subprocess.run([python,str(ROOT/'monara_sdk_worker.py'),'--capabilities'],
        stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
        env={'PATH':'/usr/bin:/bin'},timeout=20,check=True)
    value=json.loads(result.stdout)
    assert value['sdk']=='3.6.1' and value['custom_voice_supported']
    assert value['config']['speech_config']['voice_config']=={'voice':'voice_xu8nahptj1p9'}
    assert value['http_options']['retry_options']['attempts']==1
    assert b'api_key' not in result.stdout.lower()
