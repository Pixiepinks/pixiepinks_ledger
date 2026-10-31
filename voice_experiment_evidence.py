"""Read existing experiment evidence only. No upload, send or record mutation."""
import argparse
import json
from models import VoicePlaybackTest,VoicePlaybackVariant,BotAuditEvent
import voice_packet_analysis as bits
import voice_diagnostic_tools as tools
from voice_production_diagnostics import retrieve_meta,same_fingerprints


def report(db,run_id,*,retrieve=False,remux=False):
    run=db.get(VoicePlaybackTest,run_id)
    if not run or run.evidence.get('kind')!='same_source_encoding':raise ValueError('Encoding experiment not found')
    evidence=run.evidence;profiles={};rows=[]
    for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id).order_by(VoicePlaybackVariant.variant):
        value=evidence['variants'][v.variant];stages=(v.audio_trace or {}).get('stages',{})
        expected=value.get('bytes');wire=stages.get('multipart_file',{})
        row={'variant':v.variant,'status':v.status,'media_id':v.media_id,'whatsapp_message_id':v.whatsapp_message_id,
            'delivery_status':v.delivery_status,'delivery_error_code':v.delivery_error_code,'expected_file':expected,
            'source':value.get('source'),'parameters':value.get('parameters'),'audio':value.get('audio'),
            'immutable_matches_upload':same_fingerprints(expected,wire),'trace':v.audio_trace,
            'payload_uses_recorded_media_id':stages.get('send_payload',{}).get('audio',{}).get('id')==v.media_id if v.media_id and stages.get('send_payload',{}).get('audio',{}).get('id') else None}
        if retrieve and v.media_id:
            def inspect(raw,label=v.variant):
                try:p=bits.profile(raw)
                except ValueError:return {'bitstream_error':'Bounded OGG packet inspection failed'}
                profiles[label]=p
                result={'bitstream':p}
                if remux:
                    try:response=tools._run('remux',raw)
                    except ValueError:
                        result['remux_error']='Pinned offline remux failed or timed out';return result
                    if response.returncode:result['remux_error']='Pinned offline remux failed'
                    else:
                        remuxed=bits.profile(response.stdout)
                        result['remux_comparison']=bits.compare(p,remuxed)
                        result['remux_file']=remuxed['file']
                return result
            row['retrieval']=retrieve_meta(v.media_id,expected,offline_inspector=inspect)
        rows.append(row)
    logs=db.query(BotAuditEvent).filter(BotAuditEvent.action.like('VOICE_ENCODING_%'),BotAuditEvent.summary.contains(run.id)).order_by(BotAuditEvent.id).all()
    log_rows=[]
    for entry in logs:
        try:summary=json.loads(entry.summary)
        except ValueError:continue
        if summary.get('run_id')==run.id:log_rows.append({'action':entry.action,'actor':entry.actor,'at':entry.created_at,'summary':summary})
    return {'run_id':run.id,'administrator_id':run.staff_id,'created_at':run.created_at,'confirmed_at':run.confirmed_at,
        'runtime':evidence.get('runtime'),'source':evidence.get('source'),'variants':rows,
        'playback':run.playback,'results_by':run.results_by,'results_at':run.results_at,
        'audit':log_rows,
        'embedded_actions':evidence.get('actions',[]),
        'b_vs_c':bits.compare(profiles['B'],profiles['C']) if 'B' in profiles and 'C' in profiles else None,
        'missing':['No stored phone results'] if run.playback is None else [],'live_sends':0}


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--run-id',required=True);p.add_argument('--owner-authorized',action='store_true',required=True)
    p.add_argument('--retrieve-meta',action='store_true');p.add_argument('--offline-remux',action='store_true')
    a=p.parse_args()
    if a.offline_remux and not a.retrieve_meta:p.error('remux requires explicitly requested existing Meta retrieval')
    from database import SessionLocal
    try:
        with SessionLocal() as db:print(json.dumps(report(db,a.run_id,retrieve=a.retrieve_meta,remux=a.offline_remux),indent=2,default=str))
    except (ValueError,OSError):raise SystemExit('Read-only evidence inspection failed; no messages sent') from None
