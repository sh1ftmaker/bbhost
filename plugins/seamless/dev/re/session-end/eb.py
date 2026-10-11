import struct,sys,subprocess
ELF=r"C:\Users\shift\Desktop\Bloodborne\games\eboot-109-decrypted.elf"
OBJ=r"C:\Users\shift\AppData\Local\Microsoft\WinGet\Packages\MartinStorsjo.LLVM-MinGW.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\llvm-mingw-20260505-ucrt-x86_64\bin\llvm-objdump.exe"
data=open(ELF,'rb').read()
def off(va):
    if va<0x50d96dc: return va+0x4000
    return va-0x50dc000+0x50e0000
def rd(va,n): return data[off(va):off(va)+n]
def i32(va): return struct.unpack('<i',rd(va,4))[0]
def u64(va): return struct.unpack('<Q',rd(va,8))[0]
def jt(base,n,start=0):
    return [(i+start, base+i32(base+4*i)) for i in range(n)]
def dis(a,b):
    out=subprocess.run([OBJ,'-d','--no-show-raw-insn','--start-address=%#x'%a,'--stop-address=%#x'%b,ELF],capture_output=True,text=True).stdout
    return '\n'.join(l for l in out.splitlines()[6:])
def u16s(va,maxn=200):
    s=[];
    while len(s)<maxn:
        c=struct.unpack('<H',rd(va+2*len(s),2))[0]
        if c==0:break
        s.append(chr(c))
    return ''.join(s)
def cstr(va,maxn=200):
    b=rd(va,maxn); return b.split(b'\0')[0].decode('latin1')
if __name__=='__main__':
    cmd=sys.argv[1]
    if cmd=='dis': print(dis(int(sys.argv[2],16),int(sys.argv[3],16)))
    elif cmd=='jt':
        for i,t in jt(int(sys.argv[2],16),int(sys.argv[3]),int(sys.argv[4]) if len(sys.argv)>4 else 0): print(i,hex(t))

import bisect as _bis
_H=0x4f41510
def _funcs():
    n=struct.unpack('<I',rd(_H+8,4))[0]
    raw=rd(_H+12,8*n)
    st=[]
    for i in range(n):
        a,f=struct.unpack_from('<ii',raw,8*i)
        st.append(_H+a)
    return st
FUNCS=_funcs()
def func_of(va):
    i=_bis.bisect_right(FUNCS,va)-1
    return FUNCS[i]
def func_end(start):
    i=_bis.bisect_right(FUNCS,start)
    return FUNCS[i] if i<len(FUNCS) else start+0x1000
