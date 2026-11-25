"""Halloween fog for the Horde: edits the game's Vulkan (SPIR-V) pixel shaders so the world fades into a cold green-grey mist
with distance. Every colour write becomes  mix(colour, fog, amount)  with
    amount = base + (most - base) * smoothstep(near, far, depth),  depth = 1 / gl_FragCoord.w  (the distance from the eye, in map units)
The alpha is kept. Light: `most` stays well under 1, so the far end of a map is hazy, never hidden. darkmode.py applies it
on top of its darker look (the Survival theme), and takes it away again with the rest.

patch(bytes, colour, near, far, most, base) -> new shader bytes, or None when the shader isn't built the way we expect."""
import struct
from flashlight import _f, _words, _walk, _str

COLOUR = (0.36, 0.40, 0.38)          # cold green-grey mist (the world shaders are darkened first: this is about their level)
NEAR, FAR, MOST = 0.0, 300.0, 0.82    # in map units (a player is about 6 tall)
BASE = 0.12                           # the light mist up close (18 Nov; from 3-10 away, so never on your own gun)
# (17 Nov, the user: "there's no fog in the survival horde mode". It was installed, just too light to see: 70 / 520 / 0.55 put only
#  11% of mist on a building 200 away. Then about 16% at 100, 55% at 200, the full 78% from 320 on.
#  18 Nov, the user: "increase the fog a bit, and also make it a bit more foggy up close, nothing too thick for up close". Now 12%
#  right up close, about 30% at 100, 64% at 200, the full 82% from 300 on.)


def patch(b, colour=COLOUR, near=NEAR, far=FAR, most=MOST, base=BASE):
    w = _words(b)
    ins = _walk(w) if w else None
    if not ins:
        return None
    bound = w[3]
    flt = next((w[i + 1] for i, op, wc in ins if op == 22 and w[i + 2] == 32), None)
    glsl = next((w[i + 1] for i, op, wc in ins if op == 11 and _str(w[i + 2:i + wc]) == "GLSL.std.450"), None)
    if flt is None or glsl is None:
        return None
    loc0 = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 30 and w[i + 3] == 0}
    outs = [(w[i + 2], w[i + 1]) for i, op, wc in ins if op == 59 and w[i + 3] == 3 and w[i + 2] in loc0]
    if len(outs) != 1:
        return None
    var, ptype = outs[0]
    pointee = next((w[i + 3] for i, op, wc in ins if op == 32 and w[i + 1] == ptype), None)
    vec = next(((w[i + 2], w[i + 3]) for i, op, wc in ins if op == 23 and w[i + 1] == pointee), None)
    if not vec or vec[1] != 4 or vec[0] != flt:
        return None
    partial = any(op in (65, 66) and w[i + 3] == var for i, op, wc in ins)
    first_fn = next((i for i, op, wc in ins if op == 54), None)
    if partial:
        entry_fn = next((w[i + 2] for i, op, wc in ins if op == 15), None)
        stores, inside = [], False
        for i, op, wc in ins:
            if op == 54:
                inside = w[i + 2] == entry_fn
            elif op == 56:
                inside = False
            elif op == 253 and inside:
                stores.append(i)
    else:
        stores = [i for i, op, wc in ins if op == 62 and w[i + 1] == var]
    if not stores or first_fn is None:
        return None
    g, next_id = [], [bound]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1
    newc = []

    def const(tp, bits):
        c = next((w[i + 2] for i, op, wc in ins if op == 43 and w[i + 1] == tp and wc == 4 and w[i + 3] == bits), None)
        if c is None:
            c = next((x[2] for x in newc if x[0] == tp and x[1] == bits), None)
            if c is None:
                c = new_id(); newc.append((tp, bits, c)); g.extend([(4 << 16) | 43, tp, c, bits])
        return c
    c_one, c_zero = const(flt, _f(1.0)), const(flt, _f(0.0))
    c_near, c_far, c_most = const(flt, _f(near)), const(flt, _f(far)), const(flt, _f(most - base))
    c_base, c_g0, c_g1 = const(flt, _f(base)), const(flt, _f(3.0)), const(flt, _f(10.0))
    c_small = const(flt, _f(1e-6))
    col = [const(flt, _f(x)) for x in colour]
    c_fog4, c_mask = new_id(), new_id()
    g.extend([(7 << 16) | 44, pointee, c_fog4] + col + [c_zero])              # the mist (alpha 0: the alpha is not mixed)
    g.extend([(7 << 16) | 44, pointee, c_mask, c_one, c_one, c_one, c_zero])
    dec_builtin = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 11 and w[i + 3] == 15}
    frag = next((w[i + 2] for i, op, wc in ins if op == 59 and w[i + 3] == 1 and w[i + 2] in dec_builtin), None)
    new_decor, add_iface = [], None
    if frag is None:
        vec4_in = next((w[i + 1] for i, op, wc in ins if op == 32 and w[i + 2] == 1 and w[i + 3] == pointee), None)
        if vec4_in is None:
            vec4_in = new_id(); g.extend([(4 << 16) | 32, vec4_in, 1, pointee])
        frag = new_id(); g.extend([(4 << 16) | 59, vec4_in, frag, 1])
        new_decor = [(4 << 16) | 71, frag, 11, 15]
        add_iface = frag

    def seq(value):
        s = []

        def r(tp, op, *args):
            rid = new_id(); s.extend([((len(args) + 3) << 16) | op, tp, rid] + list(args)); return rid
        if value is None:
            value = r(pointee, 61, var)
        fc = r(pointee, 61, frag)
        fw = r(flt, 81, fc, 3)
        fw = r(flt, 12, glsl, 40, fw, c_small)            # FMax(w, tiny)
        depth = r(flt, 136, c_one, fw)                     # 1 / w
        amt = r(flt, 12, glsl, 49, c_near, c_far, depth)   # SmoothStep(near, far, depth)
        amt = r(flt, 133, amt, c_most)                     # * (most - base)
        nb = r(flt, 12, glsl, 49, c_g0, c_g1, depth)       # the up-close mist fades in from 3 to 10 away: none on your own gun or hands
        nb = r(flt, 133, nb, c_base)
        amt = r(flt, 129, amt, nb)                         # + base
        dif = r(pointee, 131, c_fog4, value)               # fog - colour
        dif = r(pointee, 133, dif, c_mask)                 # (not the alpha)
        add = r(pointee, 142, dif, amt)                    # * amount
        return s, r(pointee, 129, value, add)
    last_annot = max((i + wc for i, op, wc in ins if op in (71, 72, 73, 74, 75)), default=None)
    seqs = {i: seq(None if partial else w[i + 2]) for i in stores}
    out = w[:5]
    for i, op, wc in ins:
        if i == first_fn:
            out += g
        if op == 15 and add_iface is not None:
            out += [(((wc + 1) << 16) | 15)] + w[i + 1:i + wc] + [add_iface]
        elif i in seqs:
            sq, res = seqs[i]
            out += sq
            if partial:
                out += [(3 << 16) | 62, var, res] + w[i:i + wc]
            else:
                out += [w[i], w[i + 1], res] + w[i + 3:i + wc]
        else:
            out += w[i:i + wc]
        if last_annot is not None and i + wc == last_annot and new_decor:
            out += new_decor
    out[3] = next_id[0]
    return struct.pack("<%dI" % len(out), *out)
