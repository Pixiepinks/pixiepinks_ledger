"""Offline Opus/OGG stream evidence, not a WhatsApp playback certification."""
import struct
from io import BytesIO
import av
from audio_service import validate_voice_audio


def inspect(raw):
    validated = validate_voice_audio(raw)  # Includes CRC, sequence and full decode.
    pages=[];packets=[];partial=bytearray();position=0
    while position<len(raw):
        segments=raw[position+26]
        laces=raw[position+27:position+27+segments]
        offset=position+27+segments
        for size in laces:
            partial.extend(raw[offset:offset+size]);offset+=size
            if size<255:
                packets.append(bytes(partial));partial.clear()
        granule=struct.unpack_from('<Q',raw,position+6)[0]
        pages.append({'granule':granule,'flags':raw[position+5]})
        position=offset
    head=packets[0];tags=packets[1] if len(packets)>1 else b''
    preskip=struct.unpack_from('<H',head,10)[0]
    durations=[]
    with av.open(BytesIO(raw)) as source:
        stream=source.streams.audio[0]
        for packet in source.demux(stream):
            if packet.size and packet.duration is not None:
                durations.append(round(float(packet.duration*stream.time_base)*1000,3))
    granules=[p['granule'] for p in pages if p['granule']!=0xffffffffffffffff]
    warnings=[]
    if partial:warnings.append('Unterminated OGG packet')
    if not tags.startswith(b'OpusTags'):warnings.append('Missing OpusTags comment packet')
    if any(b<a for a,b in zip(granules,granules[1:])):warnings.append('Nonmonotonic granule positions')
    if granules and granules[-1]<preskip:warnings.append('Final granule precedes pre-skip')
    return {'validation':validated,'opus_head_version':head[8],'channels':head[9],
        'pre_skip_samples':preskip,'input_sample_rate_hint':struct.unpack_from('<I',head,12)[0],
        'output_gain_q8':struct.unpack_from('<h',head,16)[0],'channel_mapping_family':head[18],
        'opus_tags_present':tags.startswith(b'OpusTags'),'page_count':len(pages),
        'packet_count':len(packets),'final_granule':granules[-1] if granules else None,
        'final_page_eos':bool(pages[-1]['flags']&4),'packet_duration_ms_unique':sorted(set(durations)),
        'warnings':warnings,'interpretation':'Offline stream evidence only; no documented additional WhatsApp constraint is asserted.'}
