"""Non-secret artifact provenance; unknown remains unknown, never a guessed SHA."""
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parent
FILES=('audio_service.py','whatsapp_service.py','saved_voice_service.py','saved_voice_routes.py','main.py','voice_trace.py','voice_production_diagnostics.py','build_info.py','voice_playback_test.py','voice_playback_routes.py','voice_compatibility.py','voice_regression_diagnostics.py')


def fingerprints():
    values={}
    for name in FILES:
        try:values[name]=hashlib.sha256((ROOT/name).read_bytes()).hexdigest()
        except OSError:values[name]=None
    return values


def commit(value):
    return value.lower() if isinstance(value,str) and re.fullmatch(r'[a-fA-F0-9]{40}',value) else None


def build_record():
    value=commit(os.environ.get('RAILWAY_GIT_COMMIT_SHA'));source='railway_build_env'
    if not value:
        try:value=commit(subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,stderr=subprocess.DEVNULL,text=True,timeout=2).strip())
        except (OSError,subprocess.SubprocessError):value=None
        source='build_git_checkout' if value else 'unknown'
    return {'commit':value,'source':source,'file_sha256':fingerprints()}


def runtime():
    try:
        marker=json.loads((ROOT/'build_revision.json').read_text())
        if not isinstance(marker,dict):marker={}
    except (OSError,ValueError):marker={}
    artifact=commit(marker.get('commit'));platform=commit(os.environ.get('RAILWAY_GIT_COMMIT_SHA'))
    current=fingerprints()
    return {'artifact_commit':artifact,'platform_reported_commit':platform,
        'commit_sources_agree':artifact==platform if artifact and platform else None,
        'artifact_files_match':marker.get('file_sha256')==current if marker.get('file_sha256') else None,
        'running_file_sha256':current,
        'deployment_id':_identifier('RAILWAY_DEPLOYMENT_ID'),'replica_id':_identifier('RAILWAY_REPLICA_ID'),
        'verification':'Self-reported runtime/artifact evidence; compare with Railway deployment details.'}


def _identifier(name):
    value=os.environ.get(name,'')
    return value if re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value) else None


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--write',action='store_true',required=True);parser.parse_args()
    record=build_record();(ROOT/'build_revision.json').write_text(json.dumps(record,sort_keys=True))
    print(json.dumps({'build_commit':record['commit'],'source':record['source']}))
