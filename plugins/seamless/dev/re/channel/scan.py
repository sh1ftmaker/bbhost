# For every call/jmp to the packet send/pop entry points, find the immediate loaded into the type register before it.
import subprocess,re,sys
sys.argv=['x']
exec(open('d.py').read().split("c=sys.argv[1]")[0])
import xref
T={0x1b894e0:('SendPacket','edx'),0x1b89980:('SendToAll','esi'),0x1b89a40:('SendToHost','esi'),0x1b89200:('PopPeer','edx'),0x1b893e0:('PopHost','esi'),0x1b910f0:('PeerPopQueued','esi')}
from collections import defaultdict
types=defaultdict(set); unk=[]
for t,(nm,reg) in T.items():
    for v,k in xref.calls_to(t-B):
        a=v+B
        txt=d(a-0x60,a+5).splitlines()
        imm=None
        for l in txt:
            m=re.search(r'mov\s+'+reg+r', (0x[0-9a-f]+)$',l.strip())
            if m: imm=int(m.group(1),16)
            if re.search(r'mov\s+'+reg+r', [a-z]',l) or re.search(r'lea\s+'+reg+',',l) or re.search(r'xor\s+'+reg+', '+reg,l): imm=('0' if 'xor' in l else None)
        if imm is None: unk.append((nm,hex(a)))
        else: types[nm].add(imm if isinstance(imm,int) else 0)
for nm in types: print(nm, sorted(hex(x) for x in types[nm]))
print('unresolved:',unk)
