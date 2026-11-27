"""Read-only Monara deployment readiness. No application startup or provider API calls."""
import json,os,subprocess
from pathlib import Path
from sqlalchemy import text,func
from database import SessionLocal
from settings import settings
from monara_models import MonaraJob as J,MonaraSend as S,MonaraPolicy as P
from models import GlobalAIReplyControl
import voice_diagnostic_tools as tools

def sdk():
    python=settings.MONARA_TTS_PYTHON or str(Path(__file__).resolve().parents[1]/'.monara-venv/bin/python')
    try:
        p=subprocess.run([python,str(Path(__file__).resolve().parents[1]/'monara_sdk_worker.py'),'--capabilities'],stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env={'PATH':'/usr/bin:/bin'},timeout=15,check=True)
        if len(p.stdout)>16000:raise ValueError()
        d=json.loads(p.stdout)
        return {'available':True,'version':d['sdk'],'custom_voice_supported':d['custom_voice_supported'],'exact_voice':d['config']['speech_config']['voice_config']=={'voice':'voice_xu8nahptj1p9'},'no_sdk_retry':d['http_options']['retry_options']['attempts']==1,'styles_verified':False}
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):return {'available':False,'reason':'ISOLATED_SDK_UNAVAILABLE_OR_INCOMPATIBLE'}
def main():
    with SessionLocal() as db:
        if db.bind.dialect.name!='postgresql':raise ValueError('PostgreSQL READ ONLY required')
        db.execute(text('SET TRANSACTION READ ONLY'));db.execute(text("SET LOCAL statement_timeout='15s'"))
        row=db.get(P,1);global_ai=db.get(GlobalAIReplyControl,1)
        out={'status':'READ_ONLY_CHECK_COMPLETE','transaction_read_only':db.execute(text('SHOW transaction_read_only')).scalar()=='on','migration':db.execute(text('SELECT version_num FROM alembic_version')).scalar(),'generation_gate':settings.MONARA_TTS_ENABLED,'send_gate':settings.MONARA_TTS_SEND_ENABLED,'api_key_configured':bool(settings.GEMINI_API_KEY),'policy_available':row is not None,'policy_generation':bool(row and row.data.get('generation',False)),'policy_sending':bool(row and row.data.get('sending',False)),'global_ai_enabled':global_ai.enabled if global_ai else None,'jobs_by_state':dict(db.query(J.status,func.count()).group_by(J.status)),'sends_by_state':dict(db.query(S.status,func.count()).group_by(S.status)),'stored_audio_bytes':db.query(func.coalesce(func.sum(func.coalesce(func.length(J.mp3),0)+func.coalesce(func.length(J.ogg),0)),0)).scalar(),'audio_storage_limit':settings.MONARA_TTS_STORAGE_MAX_BYTES,'sdk':sdk(),'ffmpeg':tools.runtime(),'provider_calls':0,'messages_sent':0,'database_mutations':False,'official_meta_docs_verified_in_this_environment':False}
        db.rollback();print(json.dumps(out,default=str,indent=2))
if __name__=='__main__':
    try:main()
    except Exception:print(json.dumps({'status':'READ_ONLY_CHECK_UNAVAILABLE','database_mutations':False,'provider_calls':0}));raise SystemExit(1)
