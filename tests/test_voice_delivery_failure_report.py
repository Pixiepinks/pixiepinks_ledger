from types import SimpleNamespace

from voice_delivery_failure_report import summarize


def variant(audio):
    return SimpleNamespace(variant='P', status='SENT', delivery_status='failed',
                           delivery_error_code=131053, media_id='media.test',
                           audio_trace={'stages': {'send_payload': {'audio': audio},
                                                  'send_http': {'http_status': 200}}})


def test_submission_success_does_not_mean_delivery_success():
    value = summarize(variant({'id': 'media.test', 'voice': True}))
    assert value['send_http']['http_status'] == 200
    assert value['delivery_status'] == 'failed'
    assert value['delivery_error_code'] == 131053
    assert value['native_voice_flag'] is True
    assert value['payload_media_matches_record'] is True
    assert value['failure_details_available'] is False
    assert value['missing']


def test_distinguishes_ordinary_audio_without_inventing_error_details():
    value = summarize(variant({'id': 'media.test'}))
    assert value['voice_flag_present'] is False
    assert value['native_voice_flag'] is None
    assert 'error_title' not in value


def test_missing_trace_and_media_mismatch():
    row = variant({'id': 'different'})
    assert summarize(row)['payload_media_matches_record'] is False
    row.audio_trace = None
    value = summarize(row)
    assert value['payload_media_matches_record'] is None
    assert value['multipart_file'] is None
