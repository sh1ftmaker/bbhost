import zlib,struct,glob,os
EV=r"C:\Users\shift\Desktop\Bloodborne\games\CUSA00900\dvdroot_ps4\event"
def load(p):
    b=open(p,'rb').read()
    i=b.find(b'\x78\xda')
    return zlib.decompress(b[i:])
def parse(b):
    q=lambda o:struct.unpack_from('<q',b,o)[0]
    assert b[:4]==b'EVD\0',b[:8]
    n,evat,_,insat=q(0x10),q(0x18),q(0x20),q(0x28)
    argsat=q(0x78)
    # params
    events=[]
    for e in range(n):
        o=evat+e*0x30
        eid,ic,io,pc,po=q(o),q(o+8),q(o+16),q(o+24),q(o+32)
        rb=struct.unpack_from('<I',b,o+40)[0]
        ins=[]
        for k in range(ic):
            x=insat+io+k*0x20
            bank,iid=struct.unpack_from('<ii',b,x)
            alen=q(x+8); aoff=struct.unpack_from('<i',b,x+0x10)[0]
            args=b[argsat+aoff:argsat+aoff+alen] if aoff!=-1 and alen>0 else b''
            ins.append((bank,iid,args))
        params=[]
        for k in range(pc):
            x=q(0x58)+po+k*0x20 if False else None
        events.append((eid,rb,ins,pc,po))
    return events
def params(b,po,pc):
    q=lambda o:struct.unpack_from('<q',b,o)[0]
    pat=q(0x58)
    out=[]
    for k in range(pc):
        x=pat+po+k*0x20
        out.append((q(x),q(x+8),q(x+16),struct.unpack_from('<i',b,x+24)[0]))
    return out
def files():
    return sorted(glob.glob(os.path.join(EV,'*.emevd.dcx')))
