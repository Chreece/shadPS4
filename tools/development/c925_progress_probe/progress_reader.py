"""Read bounded, sequence-checked scalar telemetry. Never read guest memory."""
import struct,time
MAGIC=0x4350524f47563131; SIZE=65536
PHASES=['Draw','DrawIndirect','Dispatch','DispatchIndirect','ShaderLookup','BufferDownload','ImageDownload','Submit','TimelineWait','FlipRequest','FlipPrepare','FlipPresent','Presenter','RenderFrame','ComputePipeline','GraphicsPipeline']
HEADER=16;COUNTER_WORDS=4;SLOTS=32;LEVELS=12;FIELDS=7;SLOT_BASE=80;SLOT_WORDS=88

def word(m,n):return struct.unpack_from('<Q',m,8*n)[0]
def snapshot(m):
 if len(m)!=SIZE:raise ValueError('TRACE_SIZE_MISMATCH')
 if word(m,0)!=MAGIC:return {'ready':False}
 if word(m,1)!=1 or word(m,2)!=SIZE:raise ValueError('TRACE_LAYOUT_MISMATCH')
 now=time.monotonic_ns();counters={};active=[];unstable=[]
 for i,name in enumerate(PHASES):
  e,x,total,peak=struct.unpack_from('<4Q',m,8*(HEADER+i*COUNTER_WORDS))
  counters[name]={'entered':e,'exited':x,'total_ns':total,'longest_ns':peak}
 for i in range(min(word(m,5),SLOTS)):
  b=SLOT_BASE+i*SLOT_WORDS;row=None
  for _ in range(3):
   before=word(m,b)
   if before&1:continue
   raw=bytes(m[b*8:(b+SLOT_WORDS)*8]);after=word(m,b)
   if before==after and not after&1:row=struct.unpack('<'+str(SLOT_WORDS)+'Q',raw);break
  if row is None:unstable.append(i);continue
  depth=row[2]
  if depth>LEVELS:raise ValueError('TRACE_DEPTH_INVALID')
  frames=[]
  for k in range(depth):
   phase,start,a,bv,c,d,updated=row[4+k*FIELDS:4+(k+1)*FIELDS]
   if phase>=len(PHASES):raise ValueError('TRACE_PHASE_INVALID')
   frame={'phase':PHASES[phase],'age_ms':round((now-start)/1e6,3),'a':a,'b':bv,'c':c,'d':d,'observation_age_ms':round((now-updated)/1e6,3)}
   if phase==8:frame.update(semaphore_identity=hex(a),requested_tick=bv,last_queried_gpu_tick=c,last_wait_result=d if d<2**63 else d-2**64)
   if phase==4:frame['shader_hash']=hex(a)
   frames.append(frame)
  if frames:active.append({'slot':i,'native_tid':row[1],'frames':frames})
 return {'ready':True,'sample_monotonic_ns':now,'trace_seconds':round((now-word(m,4))/1e9,6),'pid':word(m,3),'counters':counters,'active_threads':active,'unstable_slots':unstable,'dropped_scopes':word(m,6)}
