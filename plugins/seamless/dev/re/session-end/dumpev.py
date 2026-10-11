import sys,json,struct
from emevd import *
d=json.load(open('bb.emedf.clean.json'))
NM={(c['index'],i['index']):i['name'] for c in d['main_classes'] for i in c['instrs']}
def dump(fname,eid):
    import os
    f=os.path.join(EV,fname)
    b=load(f)
    for e,rb,ins,pc,po in parse(b):
        if e!=eid: continue
        print('event',e,'restart',rb)
        ps=params(b,po,pc)
        for k,(bank,iid,args) in enumerate(ins):
            pp=[p for p in ps if p[0]==k]
            ints=[struct.unpack_from('<i',args,j)[0] for j in range(0,len(args)-3,4)]
            print('  %2d %d[%02d] %-45s %s %s'%(k,bank,iid,NM.get((bank,iid),'?')[:45],args.hex(),('P'+str([(p[1],p[2]) for p in pp])) if pp else ''))
if __name__=='__main__':
    for e in sys.argv[2:]: dump(sys.argv[1],int(e))
