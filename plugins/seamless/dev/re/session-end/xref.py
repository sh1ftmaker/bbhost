import sys,re,struct
from eb import data
import numpy as np
TEXT_END=0x2c00000
buf=np.frombuffer(data,dtype=np.uint8,count=TEXT_END+0x4000)
def calls_to(target):
    # E8/E9 rel32
    res=[]
    for op in (0xE8,0xE9):
        idx=np.nonzero(buf[0x4000:-5]==op)[0]+0x4000
        # compute rel
        rel=(buf[idx+1].astype(np.int64)|(buf[idx+2].astype(np.int64)<<8)|(buf[idx+3].astype(np.int64)<<16)|(buf[idx+4].astype(np.int64)<<24))
        rel=np.where(rel>=2**31,rel-2**32,rel)
        va=idx-0x4000
        t=va+5+rel
        for v in va[t==target]: res.append((int(v),'call' if op==0xE8 else 'jmp'))
    return res
def rip_refs(target):
    # any instruction with disp32 rip-relative: scan all positions p where p+4+disp == target - k for instr end; approximate: disp at position d such that d+4+extra == target, extra in 0..4 (imm after)
    res=[]
    d=np.arange(0x4000,TEXT_END+0x4000-4)
    # vectorized disp
    b=buf.astype(np.int64)
    disp=b[0x4000:TEXT_END+0x4000-4]|(b[0x4001:TEXT_END+0x4001-4]<<8)|(b[0x4002:TEXT_END+0x4002-4]<<16)|(b[0x4003:TEXT_END+0x4003-4]<<24)
    disp=np.where(disp>=2**31,disp-2**32,disp)
    va=d-0x4000
    for extra in (0,1,4):
        hits=va[(va+4+extra+disp)==target]
        for h in hits:
            m=buf[h+0x4000-1]
            if (m&0xC7)==0x05: res.append((int(h),extra))
    return res
if __name__=='__main__':
    t=int(sys.argv[2],16)
    if sys.argv[1]=='call':
        for v,k in sorted(calls_to(t)): print(hex(v),k)
    else:
        for v,e in sorted(rip_refs(t)): print(hex(v),e)
