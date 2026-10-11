# BN-address annotated disassembly: python d.py BNstart [BNend]   (end defaults to eh_frame function end)
# python d.py call BN  -> callers (BN) ; python d.py rip BN -> rip refs (BN); python d.py fn BN -> containing function
import sys,re,subprocess
sys.path.insert(0,r'C:\Users\shift\Desktop\Bloodborne\coop-test\re\session-end')
import os; os.chdir(r'C:\Users\shift\Desktop\Bloodborne\coop-test\re\session-end')
from eb import *
B=0x400000
OD=r"C:\Users\shift\AppData\Local\Microsoft\WinGet\Packages\MartinStorsjo.LLVM-MinGW.UCRT_Microsoft.Winget.Source_8wekyb3d8bbwe\llvm-mingw-20260505-ucrt-x86_64\bin\llvm-objdump.exe"
G={0x593b0c8:'g_LuaEventMan',0x593b130:'g_GameDataMan',0x5940290:'g_SprjSessionManager',0x593d6d0:'g_WSOM',0x593e878:'g_WorldChrMan',0x5940230:'g_CSMultiPlayMan',0x593b100:'g_EventFlagMan',0x593b108:'g_EventMan'}
def d(a,b):
    out=subprocess.run([OD,'-d','-M','intel','--adjust-vma=0x400000','--start-address=%#x'%(a-B),'--stop-address=%#x'%(b-B),ELF],capture_output=True,text=True).stdout
    L=[]
    for l in out.splitlines()[6:]:
        l=re.sub(r'<PT_LOAD#0\+0x[0-9a-f]+>','',l)
        m=re.search(r'# (0x[0-9a-f]+)',l)
        if m:
            t=int(m.group(1),16)+B
            l=l.replace(m.group(0),'# '+hex(t))
            if t in G: l+='  ; '+G[t]
            elif 0x4d00000<t<0x4e00000:
                s=cstr(t-B,60)
                if len(s)>2 and s.isprintable(): l+='  ; "%s"'%s
        L.append(l)
    return '\n'.join(L)
c=sys.argv[1]
if c=='call':
    import xref
    for v,k in sorted(xref.calls_to(int(sys.argv[2],16)-B)): print(hex(v+B),k,'in',hex(func_of(v)+B))
elif c=='rip':
    import xref
    for v,e in sorted(xref.rip_refs(int(sys.argv[2],16)-B)): print(hex(v+B),e,'in',hex(func_of(v)+B))
elif c=='fn':
    a=int(sys.argv[2],16)-B; print(hex(func_of(a)+B),hex(func_end(func_of(a))+B))
elif c=='bytes':
    a=int(sys.argv[2],16)-B; print(rd(a,int(sys.argv[3])).hex(' '))
else:
    a=int(c,16); b=int(sys.argv[2],16) if len(sys.argv)>2 else func_end(a-B)+B
    print(d(a,b))
