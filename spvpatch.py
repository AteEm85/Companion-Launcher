"""Vulkan side of the team tint: the compiled SPIR-V pixel shaders of OLHardwareSprite mix the actor's colour with a
constant vec3 (1,0,0) for team 1 (red) and (0,0,1) for team 2 (blue). Give those two composites new values."""
import struct
F=lambda f: struct.unpack('<I',struct.pack('<f',f))[0]
fv=lambda x: struct.unpack('<f',struct.pack('<I',x))[0]
PURPLE=(0.6,0.0,0.9)
GREEN=(0.40,0.62,0.40)        # the Horde: a pale, sickly grey-green, half mixed into the skin and clothes = a dead look
def patch(b, neutral=False):
    """-> new bytes, or None when the shader has no team tint constants.
    neutral=True: keep the actors' own colours (no team tint at all): the tint mix weight is switched to all-ones.
    neutral='horde': the Horde keeps its own colours (zombie skins) but the Survivors keep their purple tint."""
    w=list(struct.unpack('<%dI'%(len(b)//4),b)); ins=[]; i=5
    while i<len(w):
        wc=w[i]>>16; ins.append((i,w[i]&0xffff,wc)); i+=wc
    ftypes={w[i+1] for i,op,wc in ins if op==22 and w[i+2]==32}
    consts={}                                       # id -> (type, value)
    for i,op,wc in ins:
        if op==43 and w[i+1] in ftypes: consts[w[i+2]]=(w[i+1],fv(w[i+3]))
    def val(x): return consts.get(x,(None,None))[1]
    mixed=set()                                     # FMix (GLSL.std.450 #46) first vectors
    for i,op,wc in ins:
        if op==12 and wc==8 and w[i+4]==46: mixed.add(w[i+5])
    targets=[]
    for i,op,wc in ins:
        if op==44 and wc==6 and w[i+2] in mixed:
            v=tuple(val(x) for x in w[i+3:i+6])
            if v==(1.0,0.0,0.0): targets.append((i,PURPLE))
            elif v==(0.0,0.0,1.0): targets.append((i,GREEN))
    if len(targets)!=2: return None
    if neutral:
        tid={w[i+2] for i,col in targets if neutral!='horde' or col==GREEN}      # 'horde': only the Horde (blue) tint goes; the Survivors keep purple
        vtype=next(w[i+1] for i,_ in targets)
        ones=None
        for i,op,wc in ins:
            if op==44 and wc==6 and w[i+1]==vtype and all(val(x)==1.0 for x in w[i+3:i+6]): ones=w[i+2]; break
        add=[]
        if ones is None:
            one=next((k for k,(t,v) in consts.items() if v==1.0),None)
            if one is None: return None
            ones=w[3]; add=[(6<<16)|44,vtype,ones,one,one,one]; w[3]+=1
        n=0
        for i,op,wc in ins:
            if op==12 and wc==8 and w[i+4]==46 and w[i+5] in tid and w[i+1]==vtype:
                w[i+7]=ones; n+=1
        if n!=len(tid): return None
        first=min(i for i,_ in targets)
        w[first:first]=add
        return struct.pack('<%dI'%len(w),*w)
    ftype=next(iter(ftypes)); bound=w[3]; new={}
    adds=[]
    zero=next((k for k,(t,v) in consts.items() if t==ftype and v==0.0 and w[0]==w[0]),None)
    for v in sorted({c for col in (PURPLE,GREEN) for c in col if c!=0.0}):
        new[v]=bound; adds+=[(4<<16)|43,ftype,bound,F(v)]; bound+=1
    if zero is None:
        zero=bound; adds+=[(4<<16)|43,ftype,bound,0]; bound+=1
    ids={0.0:zero,**new}
    first=min(i for i,_ in targets)
    for i,col in targets:
        for k in range(3): w[i+3+k]=ids[col[k]]
    w[3]=bound
    w[first:first]=adds
    return struct.pack('<%dI'%len(w),*w)
