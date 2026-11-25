"""Recolor the team tint inside the game's precompiled pixel shaders (HLSL DXBC) for OLHardwareSprite.
The shader does  rgb = lerp(tintColor, rgb, 0.5)  which the compiler turned into  add/mad  with immediates:
   t = rgb + (-tint);  rgb = t * 0.5 + tint
so the tint colour shows up twice (negated, then plain), possibly packed into other lanes."""
import struct
F=lambda f: struct.unpack('<I',struct.pack('<f',f))[0]
NZ=0x80000000
def fv(x): return struct.unpack('<f',struct.pack('<I',x))[0]
def w_of(v,neg=False):
    if v==0: return NZ if neg else 0
    return F(-v if neg else v)
HALF=F(.5)
CMAP={(1.0,0.0,0.0):(0.6,0.0,0.9),      # team 1 red  -> purple (Survivors)
      (0.0,0.0,1.0):(0.0,0.8,0.0)}      # team 2 blue -> green  (Horde)
def shex(d):
    cnt=struct.unpack_from('<I',d,28)[0]
    for i in range(cnt):
        o=struct.unpack_from('<I',d,32+4*i)[0]
        if d[o:o+4]==b'SHEX': return o+8, struct.unpack_from('<I',d,o+4)[0]
def close(a,b): return abs(a-b)<1e-4
def patch(d,want):
    cmap=CMAP if want=='on' else {v:k for k,v in CMAP.items()}
    d=bytearray(d); so,l=shex(d); w=list(struct.unpack_from('<%dI'%(l//4),d,so))
    ins=[]; i=2
    while i<len(w):
        ln=(w[i]>>24)&0x7f
        if ln==0: break
        ins.append((i,w[i]&0x7ff,ln)); i+=ln
    n=0; used=set()
    def imms(i,ln):
        return [j for j in range(i+1,i+ln-4) if w[j]==0x4002]
    for idx,(i,op,ln) in enumerate(ins):
        if op!=0x32 or ln!=15: continue
        js=imms(i,ln)
        if len(js)<2: continue
        jb,jc=js[-2],js[-1]
        B=w[jb+1:jb+5]; lanes=[k for k in range(4) if B[k]==HALF]
        if len(lanes)!=3: continue
        C=tuple(fv(w[jc+1+k]) for k in lanes)
        key=next((k for k in cmap if all(close(a,b) for a,b in zip(k,C))),None)
        if key is None: continue
        new=cmap[key]
        # the negated constant in an earlier add (len 10) / mad (len 12), same lanes
        found=None
        for (pi,pop,pln) in reversed(ins[max(0,idx-12):idx]):
            if pop not in (0x00,0x32) or pln not in (10,12): continue
            pj=imms(pi,pln)
            if not pj: continue
            j=pj[-1]
            v=[fv(w[j+1+k]) for k in range(4)]
            if all(close(v[k],-key[t]) for t,k in enumerate(lanes)) and j not in used:
                found=j; break
        if found is None: continue
        used.add(found)
        for t,k in enumerate(lanes):
            w[jc+1+k]=w_of(new[t])
            w[found+1+k]=w_of(new[t],neg=True)
        n+=2
    struct.pack_into('<%dI'%len(w),d,so,*w)
    return bytes(d),n
