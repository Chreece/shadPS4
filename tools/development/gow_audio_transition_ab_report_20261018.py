#!/usr/bin/env python3
"""Analyze GoW gameplay by Audio3D starvation onset, never by image/timestamp.

Only reads existing emulator console logs. Sync counters are cumulative
samples rounded to 256 calls. Cross-thread window alignment is approximate.
"""
import argparse
import json
from pathlib import Path
import re
import statistics

AUDIO=re.compile(r'AUDIO_PRODUCER_TIMING\s+port=126\s+period_us=(\d+)\s+ready=(\d+)\s+no_guest_buffer=(\d+)\s+backend_over_period=(\d+)\s+max_backend_us=(\d+)')
FPS=re.compile(r'VIDEOOUT_FLIP_TIMING\s+window_ms=(\d+)\s+game_flips=(\d+)\s+fps_x1000=(\d+)\s+max_flip_gap_us=(\d+)')
SYNC=re.compile(r'GOW_BIG_BUFFER_SYNC\s+calls=(\d+)\s+size=(\d+)\s+written=(\w+)\s+uploaded_bytes=(\d+)\s+dirty_ranges=(\d+)\s+scan_sum_us=(\d+)\s+all_sum_us=(\d+)\s+max_us=(\d+)')
GUEST=re.compile(r'AUDIO_GUEST_SUBMIT\s+port=22\s+calls=(\d+)\s+period_us=(\d+)\s+late_entries=(\d+)\s+max_entry_gap_us=(\d+)\s+long_waits=(\d+)\s+max_wait_us=(\d+)\s+null_pcm=(\d+)')
SDL=re.compile(r'AUDIO_SDL_TIMING\s+port=126\s+pacing=(\w+)\s+buffers=(\d+)\s+period_us=(\d+)\s+late_gaps=(\d+)\s+max_gap_us=(\d+)\s+backend_resyncs=(\d+)')

def scan(path):
    windows=[]
    last_fps=last_sync=last_guest=last_sdl=prev_sync=None
    counts={x:0 for x in ('video','audio','sync','guest','sdl','backend_over_period')}
    for line in path.open(errors='replace'):
        if 'GOW_BIG_BUFFER_SYNC' in line and (m:=SYNC.search(line)):
            calls,_,_,uploaded,ranges,scan_us,total_us,_=m.groups()
            last_sync=dict(calls=int(calls),upload_bytes=int(uploaded),
                           dirty_ranges=int(ranges),scan_us=int(scan_us),total_us=int(total_us))
            counts['sync']+=1
        elif 'VIDEOOUT_FLIP_TIMING' in line and (m:=FPS.search(line)):
            *_,fps,gap=m.groups()
            last_fps=dict(fps=int(fps)/1000,max_gap_ms=int(gap)/1000)
            counts['video']+=1
        elif 'AUDIO_GUEST_SUBMIT port=22' in line and (m:=GUEST.search(line)):
            labels=('calls','period_us','late_entries','max_entry_gap_us','long_waits','max_wait_us','null_pcm')
            last_guest=dict(zip(labels,map(int,m.groups())))
            counts['guest']+=1
        elif 'AUDIO_SDL_TIMING port=126' in line and (m:=SDL.search(line)):
            pacing,*items=m.groups()
            labels=('buffers','period_us','late_gaps','max_gap_us','backend_resyncs')
            last_sdl=dict(pacing=pacing,**dict(zip(labels,map(int,items))))
            counts['sdl']+=1
        elif 'AUDIO_PRODUCER_TIMING port=126' in line and (m:=AUDIO.search(line)):
            period,ready,missing,over,max_backend=map(int,m.groups())
            row=dict(index=len(windows),ready=ready,missing=missing,
                     availability=round(ready/max(1,ready+missing),5),
                     period_us=period,backend_over_period=over,max_backend_us=max_backend,
                     latest_fps=last_fps,guest_submit=last_guest,sdl_backend=last_sdl)
            if last_sync and prev_sync:
                delta={key:last_sync[key]-prev_sync[key] for key in
                       ('calls','upload_bytes','dirty_ranges','scan_us','total_us')}
                if min(delta.values())>=0:
                    row['sync_delta']=delta
                    if delta['calls']>0:
                        row['scan_per_call_us']=delta['scan_us']/delta['calls']
                        row['total_per_call_us']=delta['total_us']/delta['calls']
            if last_sync:prev_sync=last_sync
            windows.append(row)
            counts['audio']+=1
            counts['backend_over_period']+=over
    return windows,counts

def transition(windows):
    good=low=0
    for i,r in enumerate(windows):
        if r['availability']>=.92:
            good+=1;low=0
        elif good>=3 and r['availability']<=.65:
            low+=1
            if low>=2:return i-1
        else:low=0
    return None

def median(values):
    values=[x for x in values if x is not None]
    return round(statistics.median(values),4) if values else None

def describe(rows):
    return dict(windows=len(rows),
        ready_pct=median([r['availability']*100 for r in rows]),
        fps=median([r['latest_fps']['fps'] if r['latest_fps'] else None for r in rows]),
        scan_us_per_sync=median([r.get('scan_per_call_us') for r in rows]),
        total_us_per_sync=median([r.get('total_per_call_us') for r in rows]),
        sync_calls_per_5s=median([r.get('sync_delta',{}).get('calls') for r in rows]),
        upload_mib_per_5s=median([
            r['sync_delta']['upload_bytes']/1048576 if r.get('sync_delta') else None for r in rows]),
        max_guest_submit_gap_ms=max([
            r['guest_submit']['max_entry_gap_us']/1000 for r in rows
            if r['guest_submit']],default=None),
        max_sdl_backend_call_us=max([r['max_backend_us'] for r in rows],default=None),
        backend_over_period=sum(r['backend_over_period'] for r in rows))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('base',type=Path)
    p.add_argument('--output',type=Path)
    a=p.parse_args()
    out=dict(image_comparison='disabled',scene_equality_proven=False,
             basis='Two consecutive Audio3D port126 windows <=65% following 3 healthy >=92% windows',
             caution='Cross-thread 5s windows approximate; audio onset not proof of same scene',
             modes={})
    for mode in ('A_DENSE_CPU_UPLOAD','B_SPARSE_CPU_UPLOAD'):
        path=a.base/mode/'full-console.log'
        if not path.is_file():
            print('NO_LOG_FOR='+mode)
            continue
        windows,counts=scan(path)
        index=transition(windows)
        entry=dict(windows=len(windows),counts=counts,
                   onset_audio_window=index,
                   healthy_before=None if index is None else describe(windows[max(0,index-6):index]),
                   starved_after=None if index is None else describe(windows[index:index+8]))
        if index is not None:
            entry['onset_availability_pct']=round(100*windows[index]['availability'],2)
            entry['per_window_after_onset']=[
                dict(index=r['index']-index,ready_pct=round(100*r['availability'],2),
                     fps=r['latest_fps']['fps'] if r['latest_fps'] else None,
                     scan_us_per_sync=round(r['scan_per_call_us'],4)
                         if 'scan_per_call_us' in r else None,
                     total_us_per_sync=round(r['total_per_call_us'],4)
                         if 'total_per_call_us' in r else None)
                for r in windows[index:index+8]]
        out['modes'][mode]=entry
        print('MODE='+mode+' WINDOWS='+str(len(windows))+
              ' STARVATION_WINDOW='+str(index))
        if index is not None:
            print('BEFORE='+json.dumps(entry['healthy_before'],sort_keys=True))
            print('AFTER='+json.dumps(entry['starved_after'],sort_keys=True))
    if len(out['modes'])==2:
        a0,b0=out['modes'].values()
        print('AUDIO_ANCHOR_IN_BOTH='+str(a0['onset_audio_window'] is not None
                                          and b0['onset_audio_window'] is not None))
        if a0['starved_after'] and b0['starved_after']:
            for metric in ('scan_us_per_sync','total_us_per_sync','ready_pct','fps'):
                x=a0['starved_after'][metric];y=b0['starved_after'][metric]
                if x is not None and y is not None and x:
                    print('AFTER_ONSET_'+metric.upper()+
                          '_DENSE='+str(x)+' OPT='+str(y)+
                          ' DELTA_PCT='+str(round(100*(y-x)/x,2)))
    if a.output:
        a.output.parent.mkdir(parents=True,exist_ok=True)
        a.output.write_text(json.dumps(out,indent=2)+'\n')
        print('ANCHOR_REPORT='+str(a.output))
    print('READ_ONLY=YES NO_IMAGES=YES NO_SCENE_EQUIVALENCE_CLAIM=YES')

if __name__=='__main__':
    main()
