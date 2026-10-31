"""Bounded, private OGG packet fingerprints; never prints audio/tag contents."""
import hashlib
import struct
from audio_service import MAX_AUDIO_BYTES, _CRC
from voice_compatibility import _mode, _tags


def profile(raw):
    if not raw or len(raw)>MAX_AUDIO_BYTES:raise ValueError('Invalid comparison size')
    pages=[];packets=[];partial=bytearray();position=0;audio_samples=0
    while position<len(raw):
        if len(pages)>=4096:raise ValueError('Too many comparison pages')
        header=raw[position:position+27]
        if len(header)!=27 or header[:4]!=b'OggS':raise ValueError('Invalid OGG')
        segments=header[26];laces=raw[position+27:position+27+segments]
        end=position+27+segments+sum(laces)
        if len(laces)!=segments or end>len(raw):raise ValueError('Truncated OGG')
        offset=position+27+segments;completed=[]
        for size in laces:
            partial.extend(raw[offset:offset+size]);offset+=size
            if size==255:continue
            if len(packets)>=10000:raise ValueError('Too many comparison packets')
            packet=bytes(partial);partial.clear();index=len(packets)
            entry={'index':index,'size_bytes':len(packet),'sha256':hashlib.sha256(packet).hexdigest()}
            if index==0:
                if len(packet)!=19 or packet[:8]!=b'OpusHead':raise ValueError('Invalid OpusHead')
                entry['header']={'version':packet[8],'channels':packet[9],'pre_skip':struct.unpack_from('<H',packet,10)[0],
                    'input_rate':struct.unpack_from('<I',packet,12)[0],'gain':struct.unpack_from('<h',packet,16)[0],'mapping':packet[18]}
            elif index==1:entry['tags']=_tags(packet)
            else:
                if not packet:raise ValueError('Empty Opus packet')
                config=packet[0]>>3;code=packet[0]&3
                frame=(120<<(config&3)) if config>=16 else (480<<(config&1)) if config>=12 else 2880 if config&3==3 else 480<<(config&3)
                count=1 if code==0 else 2 if code in (1,2) else packet[1]&63 if len(packet)>1 else 0
                samples=frame*count
                if not 0<samples<=5760:raise ValueError('Invalid Opus duration')
                audio_samples+=samples
                entry.update(toc_config=config,mode=_mode(config),samples=samples,cumulative_decoded_samples=audio_samples)
            completed.append(index);packets.append(entry)
        granule=struct.unpack_from('<Q',header,6)[0]
        page=bytearray(raw[position:end]);expected=struct.unpack_from('<I',header,22)[0];page[22:26]=b'\0'*4
        checksum=0
        for byte in page:checksum=((checksum<<8)&0xffffffff)^_CRC[((checksum>>24)^byte)&255]
        pages.append({'sequence':struct.unpack_from('<I',header,18)[0],'serial':struct.unpack_from('<I',header,14)[0],
            'flags':header[5],'granule':granule,'crc':expected,'crc_valid':checksum==expected,
            'laces':list(laces),'body_size_bytes':sum(laces),'completed_packets':completed,
            'cumulative_audio_samples':audio_samples,
            'granule_minus_cumulative_samples':granule-audio_samples if granule!=0xffffffffffffffff else None})
        position=end
    if partial:raise ValueError('Incomplete packet')
    return {'file':{'sha256':hashlib.sha256(raw).hexdigest(),'size_bytes':len(raw)},'pages':pages,'packets':packets,
        'interpretation':'CRC/serials can differ when muxing; EOS may trim samples. Fingerprint differences alone do not establish playback causation.'}


def compare(left,right):
    lp=left['packets'][2:];rp=right['packets'][2:]
    hashes=lambda rows:[row['sha256'] for row in rows]
    first=next((i for i,(a,b) in enumerate(zip(lp,rp)) if a['sha256']!=b['sha256']),None)
    if first is None and len(lp)!=len(rp):first=min(len(lp),len(rp))
    page_pairs=list(zip(left['pages'],right['pages']))
    return {'codec_packets_equal':hashes(lp)==hashes(rp),'first_different_audio_packet':first,
        'packet_lengths_equal':[p['size_bytes'] for p in lp]==[p['size_bytes'] for p in rp],
        'coded_durations_equal':[p['samples'] for p in lp]==[p['samples'] for p in rp],
        'opus_head_equal':left['packets'][0]['header']==right['packets'][0]['header'],
        'opus_tags_equal':left['packets'][1]['sha256']==right['packets'][1]['sha256'],
        'page_count_equal':len(left['pages'])==len(right['pages']),
        'lacing_equal':[p['laces'] for p in left['pages']]==[p['laces'] for p in right['pages']],
        'granule_deltas_right_minus_left':[b['granule']-a['granule'] for a,b in page_pairs],
        'final_granule_delta_samples':right['pages'][-1]['granule']-left['pages'][-1]['granule'],
        'final_granule_delta_seconds':(right['pages'][-1]['granule']-left['pages'][-1]['granule'])/48000,
        'conclusion':'Offline differences only; no field is declared the WhatsApp failure cause.'}
