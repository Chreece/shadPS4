"""Offline-only type repair of COPY modules for the signed-image regression.
Never writes the shader cache or patch directory; not an emulator implementation.
The production fix is in emit_spirv_atomic.cpp. This isolates the exact validator
complaint on captured modules before allowing the new native build to run.
"""
from __future__ import annotations
import struct


def repair(data: bytes) -> tuple[bytes, dict]:
    if len(data)<20 or len(data)%4:
        raise ValueError('Malformed SPIR-V size')
    words=list(struct.unpack('<'+'I'*(len(data)//4),data))
    if words[0]!=0x07230203:
        raise ValueError('Wrong SPIR-V endian/magic')
    instructions=[];i=5
    while i<len(words):
        n=words[i]>>16;op=words[i]&65535
        if n==0 or i+n>len(words):raise ValueError('Malformed instruction')
        instructions.append([op,words[i+1:i+n]])
        i+=n
    ints={a[0]:(a[1],a[2]) for op,a in instructions if op==21}
    pointers={a[0]:(a[1],a[2]) for op,a in instructions if op==32}
    images={a[0]:a[1:] for op,a in instructions if op==25}
    type_ids={a[0] for op,a in instructions if 19<=op<=39}
    result_types={a[1]:a[0] for op,a in instructions if len(a)>=2 and a[0] in type_ids and not 19<=op<=39}
    fix_pointers={}
    for op,a in instructions:
        if op!=60:continue
        result_type,result,image,coord,sample=a
        image_pointer=pointers.get(result_types.get(image))
        if not image_pointer or image_pointer[1] not in images:
            raise ValueError('Unknown image pointer shape')
        sampled=images[image_pointer[1]][0]
        actual=pointers[result_type]
        if actual==(11,sampled):continue
        if ints.get(sampled)!=(32,1) or ints.get(actual[1])!=(32,0) or actual[0]!=11:
            raise ValueError('Mismatch outside signed-integer regression')
        fix_pointers[result]=(sampled,actual[1])
    if not fix_pointers:
        return data,{'image_pointers':0,'atomic_operations':0,'input_unchanged':True}
    next_id=words[3]
    def alloc():
        nonlocal next_id
        value=next_id;next_id+=1;return value
    def emit(op,*args):return [op,list(args)]
    added_types=[];signed_pointer_types={}
    for sampled,_ in set(fix_pointers.values()):
        matches=[t for t,p in pointers.items() if p==(11,sampled)]
        if len(matches)>1:raise ValueError('Duplicate pointer types')
        pointer=matches[0] if matches else alloc()
        if not matches:added_types.append(emit(32,pointer,11,sampled))
        signed_pointer_types[sampled]=pointer
    # Only the operations implemented by the two production integer helpers.
    normal={229,234,236,237,238,239,240,241,242}
    supported=normal|{230}
    out=[];changed=0;uses={p:0 for p in fix_pointers}
    for op,a in instructions:
        if op==60 and a[1] in fix_pointers:
            a=a.copy();a[0]=signed_pointer_types[fix_pointers[a[1]][0]]
        elif op in supported and len(a)>2 and a[2] in fix_pointers:
            a=a.copy();sampled,original_type=fix_pointers[a[2]]
            if a[0]!=original_type:raise ValueError('Atomic original result not pointer scalar')
            operand_indices=[5] if op in normal else [6,7]
            if len(a)!=(6 if op in normal else 8):raise ValueError('Unexpected atomic operand count')
            for index in operand_indices:
                if result_types.get(a[index])!=original_type:raise ValueError('Atomic original operand type mismatch')
                cast=alloc();out.append(emit(124,sampled,cast,a[index]));a[index]=cast
            result=a[1];a[0]=sampled;a[1]=alloc()
            out.append([op,a]);out.append(emit(124,original_type,result,a[1]))
            uses[a[2]]+=1;changed+=1;continue
        # Reject an unhandled atomic consumer; never silently partially repair.
        elif 227<=op<=242 and len(a)>2 and a[2] in fix_pointers:
            raise ValueError('Unsupported atomic consumer')
        out.append([op,a])
    if not all(uses.values()):raise ValueError('Unaccounted-for texel pointer')
    first_function=next(i for i,(op,_) in enumerate(out) if op==54)
    out[first_function:first_function]=added_types
    result=words[:5];result[3]=next_id
    for op,a in out:result.extend([((len(a)+1)<<16)|op,*a])
    encoded=struct.pack('<'+'I'*len(result),*result)
    return encoded,{'image_pointers':len(fix_pointers),'atomic_operations':changed,
                    'added_type_declarations':len(added_types),'input_unchanged':False}
