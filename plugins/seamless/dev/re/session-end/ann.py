import re,sys
from eb import *
N={}
for f,pre in (('lua_bind.txt','L_'),('ge_map.txt','GE_')):
    for l in open(f):
        p=l.split()
        if p and p[0].startswith('0x'):
            N[int(p[0],16)]=pre+p[-1]
N.update({0x1ed07a0:'SessMgr_BeginLeaveSession',0x1ed0910:'SessMgr_ResetToIdle',0x17894e0:'WSOM_SendPacket',0x15bed10:'WorldTransition_ResetTracked',
0x132eeb0:'LuaCtx_Raise54_3',0x132ed40:'LuaCtx_Raise54_2',0x139a9d0:'MsgMap_Dispatch',0x132b1c0:'LuaCtx_ScheduleTimed',0x20b55b0:'DL_PANIC',0x2bbe4f8:'stack_chk_fail',
0x13cfcc0:'SetEventFlag',0x13cfc00:'IsEventFlag',0x13cfd80:'GetEventFlagValue',0x1e54fd0:'MultiPlay_RequestAllTaskReturns',0x2974370:'string_ctor',0x139b2a0:'MsgMap_PushRegistration'})
G={0x553b0c8:'g_LuaEventMan',0x553b130:'g_GameDataMan',0x5540290:'g_SprjSessionManager',0x553d6d0:'g_WorldSessionObjectMan',0x5556678:'g_GameStateMan?',0x553e878:'g_WorldChrMan',0x5540230:'g_CSMultiPlayMan',0x553b100:'g_EventFlagMan',0x553b108:'g_EventMan'}
def ann(a,b=None):
    if b is None:b=func_end(a)
    out=[]
    for l in dis(a,b).splitlines():
        m=re.search(r'(call|jmp)q?\s+0x([0-9a-f]+)',l)
        if m:
            t=int(m.group(2),16)
            if t in N: l+='   ; '+N[t]
        m=re.search(r'# (0x[0-9a-f]+)',l)
        if m:
            t=int(m.group(1),16)
            if t in G: l+='   ; '+G[t]
            elif 0x4900000<t<0x4a00000:
                s=cstr(t,60)
                if len(s)>2 and s.isprintable(): l+='   ; "%s"'%s
        out.append(l.replace('<PT_LOAD#0+','<'))
    return '\n'.join(out)
if __name__=='__main__':
    a=int(sys.argv[1],16); b=int(sys.argv[2],16) if len(sys.argv)>2 else None
    print(ann(a,b))
