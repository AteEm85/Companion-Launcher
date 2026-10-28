"""Vulkan side of the team tint: the compiled SPIR-V pixel shaders of OLHardwareSprite mix the actor's colour with a
constant vec3 (1,0,0) for team 1 (red) and (0,0,1) for team 2 (blue). Give those two composites new values."""
import struct
F=lambda f: struct.unpack('<I',struct.pack('<f',f))[0]
fv=lambda x: struct.unpack('<f',struct.pack('<I',x))[0]
PURPLE=(0.6,0.0,0.9)
GREEN=(0.40,0.62,0.40)        # the Horde: a pale, sickly grey-green, half mixed into the skin and clothes = a dead look
def _parse(b):
    w=list(struct.unpack('<%dI'%(len(b)//4),b)); ins=[]; i=5
    while i<len(w):
        wc=w[i]>>16
        if wc==0: return None
        ins.append((i,w[i]&0xffff,wc)); i+=wc
    return w,ins

def _close(v,c): return all(a is not None and abs(a-x)<1e-4 for a,x in zip(v,c))

def _targets(w,ins):
    """[(index of the team colour composite, 'S' Survivors (red or purple) / 'H' Horde (blue or green))], the float
    constants and the FMix instructions."""
    ftypes={w[i+1] for i,op,wc in ins if op==22 and w[i+2]==32}
    consts={w[i+2]:(w[i+1],fv(w[i+3])) for i,op,wc in ins if op==43 and w[i+1] in ftypes}
    val=lambda x: consts.get(x,(None,None))[1]
    mixed={w[i+5] for i,op,wc in ins if op==12 and wc==8 and w[i+4]==46}
    out=[]
    for i,op,wc in ins:
        if op==44 and wc==6 and w[i+2] in mixed:
            v=tuple(val(x) for x in w[i+3:i+6])
            if _close(v,(1.0,0.0,0.0)) or _close(v,PURPLE): out.append((i,'S'))
            elif _close(v,(0.0,0.0,1.0)) or _close(v,GREEN): out.append((i,'H'))
    return out,ftypes,consts,val

def _recolour(b):
    """Team 1 red -> PURPLE (the Survivors), team 2 blue -> GREEN (the Horde)."""
    r=_parse(b)
    if not r: return None
    w,ins=r
    targets,ftypes,consts,_val=_targets(w,ins)
    if len(targets)!=2: return None
    ftype=next(iter(ftypes)); bound=w[3]; new={}; adds=[]
    zero=next((k for k,(t,v) in consts.items() if t==ftype and v==0.0),None)
    for v in sorted({c for col in (PURPLE,GREEN) for c in col if c!=0.0}):
        new[v]=bound; adds+=[(4<<16)|43,ftype,bound,F(v)]; bound+=1
    if zero is None:
        zero=bound; adds+=[(4<<16)|43,ftype,bound,0]; bound+=1
    ids={0.0:zero,**new}
    first=min(i for i,_ in targets)
    for i,team in targets:
        col=PURPLE if team=='S' else GREEN
        for k in range(3): w[i+3+k]=ids[col[k]]
    w[3]=bound
    w[first:first]=adds
    return struct.pack('<%dI'%len(w),*w)

def _untint(b,teams):
    """No tint for these teams ('S', 'H'): their FMix weight becomes all-ones, so the sprite's own colours show."""
    r=_parse(b)
    if not r: return None
    w,ins=r
    targets,_ft,consts,val=_targets(w,ins)
    if len(targets)!=2: return None
    tid={w[i+2] for i,team in targets if team in teams}
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
    if n==0: return None
    first=min(i for i,_ in targets)
    w[first:first]=add
    return struct.pack('<%dI'%len(w),*w)

def patch(b, neutral=False):
    """-> new bytes, or None when the shader has no team tint constants.
    neutral=False: Survivors purple, Horde green.
    neutral=True: keep the actors' own colours (no team tint at all).
    neutral='horde': the Survivors purple, the Horde keeps its own colours (the zombie skins)."""
    if neutral == 'horde':
        coloured = _recolour(b)
        if coloured is None: return None
        return _untint(coloured, ('H',)) or coloured
    if neutral:
        return _untint(b, ('S', 'H'))
    return _recolour(b)
