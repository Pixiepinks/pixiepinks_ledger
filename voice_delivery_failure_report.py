"""Read stored owner-candidate evidence. No Meta requests, sends or writes."""
import argparse
import json


def summarize(variant):
    trace = variant.audio_trace or {}
    stages = trace.get('stages', {})
    audio = stages.get('send_payload', {}).get('audio', {})
    return {
        'variant': variant.variant,
        'submission_status': variant.status,
        'delivery_status': variant.delivery_status,
        'delivery_error_code': variant.delivery_error_code,
        'native_voice_flag': audio.get('voice'),
        'voice_flag_present': 'voice' in audio,
        'payload_media_matches_record': audio.get('id') == variant.media_id if audio.get('id') else None,
        'multipart_file': stages.get('multipart_file'),
        'upload_http': stages.get('upload_http'),
        'send_http': stages.get('send_http'),
        'failure_details_available': False,
        'missing': ['Status webhook retains only status and first numeric error code; '
                    'Meta title, message, error_data.details, href and additional errors are not retained.'],
    }


def report(db, run_id):
    from models import VoicePlaybackTest, VoicePlaybackVariant, BotAuditEvent
    run = db.get(VoicePlaybackTest, run_id)
    if not run or (run.evidence or {}).get('kind') != 'production_ffmpeg_candidate':
        raise ValueError('Owner candidate run not found')
    audit = []
    for row in db.query(BotAuditEvent).filter(BotAuditEvent.summary.contains(run.id)).order_by(BotAuditEvent.id):
        try:
            summary = json.loads(row.summary)
        except (ValueError, TypeError):
            continue
        if summary.get('run_id') == run.id:
            # Avoid arbitrary audit text or recipient-bearing opaque message IDs.
            audit.append({'id': row.id, 'action': row.action, 'at': row.created_at})
    return {
        'run_id': run.id,
        'kind': run.evidence['kind'],
        'created_at': run.created_at,
        'delivery_mode': run.evidence.get('delivery_mode'),
        'playback': run.playback,
        'variants': [summarize(v) for v in db.query(VoicePlaybackVariant).filter_by(run_id=run.id)],
        'audit_events': audit,
        'meta_requests': 0, 'messages_sent': 0,
        'warning': 'Database evidence only. No historical webhook details can be reconstructed. '
                   '131053 alone does not establish that the voice flag caused the failure.',
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-id', required=True)
    parser.add_argument('--owner-authorized', action='store_true', required=True)
    args = parser.parse_args()
    from database import SessionLocal
    with SessionLocal() as db:
        # PostgreSQL enforces read-only access as an additional guard.
        if db.get_bind().dialect.name == 'postgresql':
            from sqlalchemy import text
            db.execute(text('SET TRANSACTION READ ONLY'))
        try:
            print(json.dumps(report(db, args.run_id), indent=2, default=str))
        except ValueError:
            raise SystemExit('Owner candidate run not found; no changes or sends') from None
        finally:
            db.rollback()


if __name__ == '__main__':
    main()
