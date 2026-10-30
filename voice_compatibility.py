"""Offline Opus/OGG stream evidence, not a WhatsApp playback certification."""
import struct
import hashlib
from io import BytesIO
import av
from audio_service import validate_voice_audio


def _tags(packet):
    result={'present':packet.startswith(b'OpusTags'),'structure_valid':False}
    if not result['present'] or len(packet)<16:return result
    size=struct.unpack_from('<I',packet,8)[0];position=12+size
    if position+4>len(packet):return result
    vendor=packet[12:position]
    count=struct.unpack_from('<I',packet,position)[0];position+=4
    result.update(vendor_size_bytes=size,vendor_sha256=hashlib.sha256(vendor).hexdigest(),comment_count=count)
    if count>len(packet)//4:return result
    for _ in range(count):
        if position+4>len(packet):return result
        length=struct.unpack_from('<I',packet,position)[0];position+=4+length
        if position>len(packet):return result
    result['structure_valid']=True
    return result


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
    coded_durations=[];toc_configs=[]
    for packet in packets[2:]:
        if not packet:continue
        config=packet[0]>>3; code=packet[0]&3
        toc_configs.append(config)
        frame_samples=(120 << (config&3)) if config>=16 else (480 << (config&1)) if config>=12 else 2880 if config&3==3 else 480 << (config&3)
        count=1 if code==0 else 2 if code in (1,2) else (packet[1]&63) if len(packet)>1 else 0
        coded_durations.append(frame_samples*count/48)
    granules=[p['granule'] for p in pages if p['granule']!=0xffffffffffffffff]
    warnings=[]
    if partial:warnings.append('Unterminated OGG packet')
    if any(value<=0 or value>120 for value in coded_durations):warnings.append('Invalid encoded Opus packet duration')
    if not _tags(tags).get('structure_valid'):warnings.append('Invalid OpusTags structure')
    if not tags.startswith(b'OpusTags'):warnings.append('Missing OpusTags comment packet')
    if any(b<a for a,b in zip(granules,granules[1:])):warnings.append('Nonmonotonic granule positions')
    if granules and granules[-1]<preskip:warnings.append('Final granule precedes pre-skip')
    return {'validation':validated,'opus_head_version':head[8],'channels':head[9],
        'pre_skip_samples':preskip,'input_sample_rate_hint':struct.unpack_from('<I',head,12)[0],
        'output_gain_q8':struct.unpack_from('<h',head,16)[0],'channel_mapping_family':head[18],
        'opus_tags_present':tags.startswith(b'OpusTags'),'opus_tags':_tags(tags),'page_count':len(pages),
        'packet_count':len(packets),'final_granule':granules[-1] if granules else None,
        'final_page_eos':bool(pages[-1]['flags']&4),'packet_duration_ms_unique':sorted(set(durations)),
        'encoded_packet_duration_ms_unique':sorted(set(coded_durations)),
        'encoded_toc_config_counts':{str(config):toc_configs.count(config) for config in sorted(set(toc_configs))},
        'encoded_modes':sorted({'CELT' if config>=16 else 'hybrid' if config>=12 else 'SILK' for config in toc_configs}),
        'demux_duration_note':'Demux durations can be trimmed by pre-skip/EOS; they are not the encoded Opus frame durations.',
        'granule_duration_seconds':(granules[-1]-preskip)/48000 if granules else None,
        'pages':pages,
        'warnings':warnings,'interpretation':'Offline stream evidence only; no documented additional WhatsApp constraint is asserted.'}
