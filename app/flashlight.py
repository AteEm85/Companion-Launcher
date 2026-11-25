"""Flashlight look for Dark Mode (Survival Horde): edits the game's Vulkan (SPIR-V) pixel shaders so the world is almost black
except inside a cone of light centred on the view, like a flashlight held at the crosshair.

For every colour write of a shader, the colour is multiplied by  mix(ambient, 1, 1 - smoothstep(r0, r1, r))  where r is the
pixel's distance from the middle of the screen in half-screen-heights (gl_FragCoord and the RenderView block's
uViewWidth / uViewHeight, found by their names inside the shader). It is a screen-space light: it lights what the cone
points at, near or far; it casts no shadows. darkmode.py installs it (and puts the originals back) the same way as the dark look.

patch(bytes, ambient, r0, r1) -> new shader bytes, or None when the shader isn't built the way we expect (it is then left alone)."""
import struct


def _f(x):
    return struct.unpack("<I", struct.pack("<f", float(x)))[0]


def _words(b):
    if len(b) % 4 or len(b) < 24 or struct.unpack_from("<I", b)[0] != 0x07230203:
        return None
    return list(struct.unpack("<%dI" % (len(b) // 4), b))


def _walk(w):
    ins, i = [], 5
    while i < len(w):
        wc = w[i] >> 16
        if wc == 0 or i + wc > len(w):
            return None
        ins.append((i, w[i] & 0xFFFF, wc))
        i += wc
    return ins


def _str(ws):
    return b"".join(struct.pack("<I", x) for x in ws).split(b"\0")[0].decode("latin-1")


def patch(b, ambient=(0.16, 0.17, 0.22), light=(1.2, 1.15, 1.0), r0=0.08, r1=0.72):
    w = _words(b)
    ins = _walk(w) if w else None
    if not ins:
        return None
    bound = w[3]
    # ---- find what we need -------------------------------------------------------------------------------------------
    names = {w[i + 1]: _str(w[i + 2:i + wc]) for i, op, wc in ins if op == 5}
    member = {(w[i + 1], w[i + 2]): _str(w[i + 3:i + wc]) for i, op, wc in ins if op == 6}
    rv = next((k for k, v in names.items() if v == "RenderView"), None)
    if rv is None:
        return None
    idx = {}
    for (s, m), n in member.items():
        if s == rv and n in ("uViewWidth", "uViewHeight"):
            idx[n] = m
    if len(idx) != 2:
        return None
    flt = next((w[i + 1] for i, op, wc in ins if op == 22 and w[i + 2] == 32), None)
    if flt is None:
        return None
    struct_ins = next(((i, wc) for i, op, wc in ins if op == 30 and w[i + 1] == rv), None)
    if struct_ins is None:
        return None
    i0, wc0 = struct_ins
    mtypes = w[i0 + 2:i0 + wc0]
    if any(mtypes[idx[n]] != flt for n in idx):
        return None
    ptr_rv = {w[i + 1]: w[i + 2] for i, op, wc in ins if op == 32 and w[i + 3] == rv}      # pointer type id -> storage class
    rvvar = next(((w[i + 2], ptr_rv[w[i + 1]]) for i, op, wc in ins if op == 59 and w[i + 1] in ptr_rv), None)
    if rvvar is None:
        return None
    rv_id, rv_class = rvvar
    glsl = next((w[i + 1] for i, op, wc in ins if op == 11 and _str(w[i + 2:i + wc]) == "GLSL.std.450"), None)
    int_t = next((w[i + 1] for i, op, wc in ins if op == 21 and w[i + 2] == 32 and w[i + 3] == 1), None)
    if glsl is None or int_t is None:
        return None
    # the colour output (as darkmode.py does it)
    loc0 = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 30 and w[i + 3] == 0}
    outs = [(w[i + 2], w[i + 1]) for i, op, wc in ins if op == 59 and w[i + 3] == 3 and w[i + 2] in loc0]
    if len(outs) != 1:
        return None
    var, ptype = outs[0]
    pointee = next((w[i + 3] for i, op, wc in ins if op == 32 and w[i + 1] == ptype), None)
    vec = next(((w[i + 2], w[i + 3]) for i, op, wc in ins if op == 23 and w[i + 1] == pointee), None)
    if not vec or vec[1] != 4 or vec[0] != flt:
        return None
    partial = any(op in (65, 66) and w[i + 3] == var for i, op, wc in ins)        # written a component at a time
    first_fn = next((i for i, op, wc in ins if op == 54), None)
    if partial:
        # multiply once, just before main returns (the entry point's function)
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
    # ---- new global declarations (types, constants, variables), placed just before the first function ---------------------
    g, next_id = [], [bound]

    def new_id():
        next_id[0] += 1
        return next_id[0] - 1

    def find_const(tp, bits):
        return next((w[i + 2] for i, op, wc in ins if op == 43 and w[i + 1] == tp and wc == 4 and w[i + 3] == bits), None)

    def const(tp, bits):
        c = find_const(tp, bits)
        if c is None:
            c = next((x[2] for x in newc if x[0] == tp and x[1] == bits), None)
            if c is None:
                c = new_id()
                newc.append((tp, bits, c))
                g.extend([(4 << 16) | 43, tp, c, bits])
        return c

    newc = []
    c_idx_w, c_idx_h = const(int_t, idx["uViewWidth"]), const(int_t, idx["uViewHeight"])
    c_half, c_one = const(flt, _f(0.5)), const(flt, _f(1.0))
    c_r0, c_r1 = const(flt, _f(r0)), const(flt, _f(r1))
    c_min, c_max = const(flt, _f(100.0)), const(flt, _f(8192.0))        # a sane screen height, in pixels
    if isinstance(ambient, (int, float)):
        ambient = (ambient,) * 3
    amb_c = [const(flt, _f(x)) for x in ambient]
    dif_c = [const(flt, _f(l - a)) for l, a in zip(light, ambient)]
    c_amb4, c_dif4 = new_id(), new_id()                                 # vec4 (r, g, b, 1): the colour outside the cone, and what the cone adds
    g.extend([(7 << 16) | 44, pointee, c_amb4] + amb_c + [c_one])
    g.extend([(7 << 16) | 44, pointee, c_dif4] + dif_c + [const(flt, _f(0.0))])
    ptr_f = next((w[i + 1] for i, op, wc in ins if op == 32 and w[i + 2] == rv_class and w[i + 3] == flt), None)
    if ptr_f is None:
        ptr_f = new_id()
        g.extend([(4 << 16) | 32, ptr_f, rv_class, flt])
    # gl_FragCoord: reuse the shader's own, else declare one
    dec_builtin = {w[i + 1] for i, op, wc in ins if op == 71 and wc >= 4 and w[i + 2] == 11 and w[i + 3] == 15}
    frag = next((w[i + 2] for i, op, wc in ins if op == 59 and w[i + 3] == 1 and w[i + 2] in dec_builtin), None)
    new_decor, add_iface = [], None
    if frag is None:
        vec4_in = next((w[i + 1] for i, op, wc in ins if op == 32 and w[i + 2] == 1 and w[i + 3] == pointee), None)
        if vec4_in is None:
            vec4_in = new_id()
            g.extend([(4 << 16) | 32, vec4_in, 1, pointee])
        frag = new_id()
        g.extend([(4 << 16) | 59, vec4_in, frag, 1])
        new_decor = [(4 << 16) | 71, frag, 11, 15]
        add_iface = frag
    # ---- the per-store sequence ---------------------------------------------------------------------------------------------
    def seq(value):
        s = []
        def emit(op, *args):
            s.extend([((len(args) + 1) << 16) | op] + list(args))
        def r(tp, op, *args):
            rid = new_id()
            emit(op, tp, rid, *args)
            return rid
        if value is None:
            value = r(pointee, 61, var)
        fc = r(pointee, 61, frag)
        fx = r(flt, 81, fc, 0)
        fy = r(flt, 81, fc, 1)
        pw = r(ptr_f, 65, rv_id, c_idx_w)
        w_ = r(flt, 61, pw)
        ph = r(ptr_f, 65, rv_id, c_idx_h)
        h_ = r(flt, 61, ph)
        hh = r(flt, 133, h_, c_half)
        hw = r(flt, 133, w_, c_half)
        dx = r(flt, 131, fx, hw)
        dy = r(flt, 131, fy, hh)
        nx = r(flt, 136, dx, hh)
        ny = r(flt, 136, dy, hh)
        xx = r(flt, 133, nx, nx)
        yy = r(flt, 133, ny, ny)
        r2 = r(flt, 129, xx, yy)
        rr = r(flt, 12, glsl, 31, r2)                    # Sqrt
        ss = r(flt, 12, glsl, 49, c_r0, c_r1, rr)        # SmoothStep(r0, r1, r)
        beam = r(flt, 131, c_one, ss)
        ok1 = r(flt, 12, glsl, 48, c_min, h_)            # Step(100, height): the screen height must be sane, else no cone at all
        ok2 = r(flt, 12, glsl, 48, h_, c_max)            # Step(height, 8192)
        beam = r(flt, 133, beam, ok1)
        beam = r(flt, 133, beam, ok2)
        add = r(pointee, 142, c_dif4, beam)              # VectorTimesScalar: what the cone adds
        mv = r(pointee, 129, c_amb4, add)                # outside colour + cone
        res = r(pointee, 133, value, mv)
        return s, res
    # ---- rebuild ------------------------------------------------------------------------------------------------------------
    last_annot = max((i + wc for i, op, wc in ins if op in (71, 72, 73, 74, 75)), default=None)
    seqs = {}
    for i in stores:
        if partial:
            s, res = seq(None)
        else:
            s, res = seq(w[i + 2])
        seqs[i] = (s, res)
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
                out += [(3 << 16) | 62, var, res] + w[i:i + wc]            # store the dimmed colour, then the original return
            else:
                out += [w[i], w[i + 1], res] + w[i + 3:i + wc]
        else:
            out += w[i:i + wc]
        if last_annot is not None and i + wc == last_annot and new_decor:
            out += new_decor
    out[3] = next_id[0]
    return struct.pack("<%dI" % len(out), *out)


def check(orig, new):
    """Cheap self-check: parses, the id bound grew, every id defined once, every id used is defined or below the old bound."""
    w0, w1 = _words(orig), _words(new)
    if not w0 or not w1:
        return False
    i0, i1 = _walk(w0), _walk(w1)
    if not i0 or not i1 or w1[3] <= w0[3] or len(i1) <= len(i0):
        return False
    seen = set()
    for i, op, wc in i1:
        if op in (5, 6, 7, 8, 9, 10, 11, 12, 14, 15, 16, 17, 71, 72, 73, 74, 75, 62, 54, 56, 248, 249, 250, 251, 252, 253, 255):
            continue
    # new result ids (>= old bound) must be unique and < new bound
    new_ids = []
    for i, op, wc in i1:
        if op in (22, 21, 23, 28, 29, 30, 32, 43, 59, 61, 65, 80, 81, 12, 129, 131, 133, 136):
            rid = w1[i + 2] if op in (43, 59, 61, 65, 80, 81, 12, 129, 131, 133, 136) else w1[i + 1]
            if rid >= w0[3]:
                new_ids.append(rid)
    return len(new_ids) == len(set(new_ids)) and all(x < w1[3] for x in new_ids)
