// Frida agent: drives one Outlaws Remastered instance by injecting SDL3 input.
//
// The game takes keyboard, mouse buttons and mouse look from SDL_PollEvent (SDL3.dll export).
// Mouse look uses the xrel/yrel of SDL_EVENT_MOUSE_MOTION. SDL_GetRelativeMouseState is also
// called (only while focused), but deltas added there have no effect.
//
// We replace SDL_PollEvent. The game calls it in a loop until it returns 0, once per frame.
// Each call hands out a queued synthetic event first. Pending look is sent as at most one
// motion event of up to lookStep per frame, so turns are smooth instead of a single snap.
//
// Event layouts and type ids are from the SDL3 headers (SDL_events.h), confirmed against a real
// key event from the game:
//   SDL_KeyboardEvent:    +0 type, +8 timestamp, +16 windowID, +20 which, +24 scancode, +28 key,
//                         +32 mod (u16), +34 raw (u16), +36 down (bool), +37 repeat (bool)
//   SDL_MouseButtonEvent: +0 type, +8 timestamp, +16 windowID, +20 which, +24 button (u8),
//                         +25 down (bool), +26 clicks (u8), +28 x (f32), +32 y (f32)
//   SDL_MouseMotionEvent: +0 type, +8 timestamp, +16 windowID, +20 which, +24 state (u32),
//                         +28 x, +32 y, +36 xrel, +40 yrel (f32)
'use strict';

const EV = {
  WINDOW_FOCUS_GAINED: 0x20e, WINDOW_FOCUS_LOST: 0x20f,
  KEY_DOWN: 0x300, KEY_UP: 0x301, TEXT_INPUT: 0x303,
  MOUSE_MOTION: 0x400, MOUSE_BUTTON_DOWN: 0x401, MOUSE_BUTTON_UP: 0x402,
};
const EVENT_SIZE = 128;           // sizeof(SDL_Event)

const sdl = Process.getModuleByName('SDL3.dll');
const pollAddr = sdl.getExportByName('SDL_PollEvent');
const ticksNS = new NativeFunction(sdl.getExportByName('SDL_GetTicksNS'), 'uint64', []);
const origPoll = new NativeFunction(pollAddr, 'bool', ['pointer']);

const queue = [];                 // pending synthetic events: {kind, ...}
const look = { dx: 0, dy: 0 };    // pending look, sent one capped motion event per frame
let lookStep = 15;                // max look delta per frame
let lookSentThisFrame = false;
let buttonMask = 0;               // mouse buttons we are holding (SDL_BUTTON_MASK bits)
let windowID = 0;                 // learned from real window events
let fakeFocus = false;            // swallow FOCUS_LOST so the game keeps treating input as focused
let tauntRequests = [];           // g_taunt writes waiting for the main thread (see serviceTaunts)
const tauntNames = {};
let lobbyRequest = 0;             // lobby pick to make on the main thread (see serviceLobby), or 0
let maxPlayersRequest = null;     // { n, tries, next } until applied or given up
let sessionRequest = null;        // { kind: 'host' } or { kind: 'join', addr } for the main thread (see serviceSession)
let modState = 0;                 // SDL_Keymod bits of the modifier keys we hold (written into key events)
const textKeep = [];              // text event strings, kept alive a while after they are handed to the game
const stats = { polls: 0, frames: 0, injected: 0, swallowedFocusLost: 0, types: {}, firstKeyHex: null };

function writeEvent(ev, e) {
  ev.writeByteArray(new Array(EVENT_SIZE).fill(0));
  ev.add(8).writeU64(ticksNS());
  ev.add(16).writeU32(windowID);
  if (e.kind === 'key') {
    ev.writeU32(e.down ? EV.KEY_DOWN : EV.KEY_UP);
    ev.add(20).writeU32(0);                       // SDL_KeyboardID: 0 = global/unknown keyboard
    ev.add(24).writeU32(e.scancode);
    ev.add(28).writeU32(e.keycode);
    const modBit = { 224: 0x40, 225: 0x01, 226: 0x100 }[e.scancode];    // left ctrl, left shift, left alt
    if (modBit) modState = e.down ? (modState | modBit) : (modState & ~modBit);
    ev.add(32).writeU16(modState);
    ev.add(36).writeU8(e.down ? 1 : 0);
  } else if (e.kind === 'text') {                  // SDL_TextInputEvent: +16 windowID, +24 text (char *)
    const mem = Memory.allocUtf8String(e.text);
    textKeep.push(mem);
    if (textKeep.length > 32) textKeep.shift();
    ev.writeU32(EV.TEXT_INPUT);
    ev.add(24).writePointer(mem);
  } else if (e.kind === 'button') {
    ev.writeU32(e.down ? EV.MOUSE_BUTTON_DOWN : EV.MOUSE_BUTTON_UP);
    ev.add(24).writeU8(e.button);
    ev.add(25).writeU8(e.down ? 1 : 0);
    ev.add(26).writeU8(1);
  } else if (e.kind === 'window') {                // SDL_WindowEvent: +16 windowID, +20 data1, +24 data2
    ev.writeU32(e.type);
  } else if (e.kind === 'motion') {
    ev.writeU32(EV.MOUSE_MOTION);
    ev.add(24).writeU32(buttonMask);
    ev.add(36).writeFloat(e.dx);
    ev.add(40).writeFloat(e.dy);
  }
}

function hex(p, n) {
  return Array.from(new Uint8Array(p.readByteArray(n))).map(b => b.toString(16).padStart(2, '0')).join('');
}

function clamp(v, m) { return Math.max(-m, Math.min(m, v)); }

// The next look step for this frame, or null if there's nothing to send.
function takeLookStep() {
  if (lookSentThisFrame || (look.dx === 0 && look.dy === 0)) return null;
  const dx = clamp(look.dx, lookStep), dy = clamp(look.dy, lookStep);
  look.dx -= dx; look.dy -= dy;
  if (Math.abs(look.dx) < 1e-3) look.dx = 0;
  if (Math.abs(look.dy) < 1e-3) look.dy = 0;
  lookSentThisFrame = true;
  return { kind: 'motion', dx, dy };
}

// Report a hook failure once, then keep passing calls straight to SDL.
const failed = {};
function fail(name, e) {
  if (failed[name]) return;
  failed[name] = true;
  send({ kind: 'hook_error', hook: name, error: e.stack || String(e) });
}

// Frida marshals C `bool` as an integer: callbacks must return 1/0, not true/false.
function poll(ev) {
  if (ev.isNull()) return (queue.length > 0 || origPoll(ev)) ? 1 : 0;
  stats.polls++;
  if (lobbyRequest) {
    try { serviceLobby(); } catch (e) { fail('lobby', e); }
  }
  if (tauntRequests.length) {
    try { serviceTaunts(); } catch (e) { tauntRequests = []; fail('taunt', e); }
  }
  if (cvarRequests.length) {
    try { serviceCvars(); } catch (e) { cvarRequests = []; fail('cvar', e); }
  }
  if (maxPlayersRequest !== null) {
    try { serviceMaxPlayers(); } catch (e) { maxPlayersRequest = null; fail('max_players', e); }
  }
  if (sessionRequest !== null) {          // after the cvars: a host request reads the mp_* cvars set just before it
    try { serviceSession(); } catch (e) { sessionRequest = null; send({ kind: 'warning', message: `session: ${e.stack || e}` }); }
  }
  const synthetic = queue.length > 0 ? queue.shift() : takeLookStep();
  if (synthetic) {
    writeEvent(ev, synthetic);
    stats.injected++;
    return 1;
  }
  for (;;) {
    if (!origPoll(ev)) {          // queue drained: this frame's event loop ends here
      stats.frames++;
      lookSentThisFrame = false;
      return 0;
    }
    const type = ev.readU32();
    stats.types[type.toString(16)] = (stats.types[type.toString(16)] || 0) + 1;
    if (type >= 0x200 && type < 0x300) windowID = ev.add(16).readU32();
    if (type === EV.KEY_DOWN && stats.firstKeyHex === null) stats.firstKeyHex = hex(ev, 40);
    if (fakeFocus && type === EV.WINDOW_FOCUS_LOST) { stats.swallowedFocusLost++; continue; }
    return 1;
  }
}

Interceptor.replace(pollAddr, new NativeCallback(function (ev) {
  if (!failed.poll) {
    try { return poll(ev); } catch (e) { fail('poll', e); }
  }
  return origPoll(ev) ? 1 : 0;
}, 'bool', ['pointer']));

// Fake focus. The game does not poll focus each frame (no SDL_GetWindowFlags calls while idle): it
// re-checks when window events arrive. 0x1F4F30 tests SDL_WINDOW_INPUT_FOCUS (bit 9), and the virtual
// 0x1F7BE0 sets its "input active" byte (+0xC1) only when that bit is set and the window isn't
// minimized or hidden (0x48). So turning fake focus on injects a WINDOW_FOCUS_GAINED event, this hook
// then reports input focus, and FOCUS_LOST events are swallowed. The mouse-capture calls become no-ops
// so a "focused" bot copy never grabs, hides or warps the real cursor.
const WINDOW_INPUT_FOCUS = 0x200;
const replacedAddrs = [];         // SDL functions we replaced besides SDL_PollEvent, for shutdown()
const flagsAddr = sdl.getExportByName('SDL_GetWindowFlags');
replacedAddrs.push(flagsAddr);
const origFlags = new NativeFunction(flagsAddr, 'uint64', ['pointer']);
Interceptor.replace(flagsAddr, new NativeCallback(function (win) {
  const flags = origFlags(win);
  return fakeFocus ? flags.or(WINDOW_INPUT_FOCUS) : flags;
}, 'uint64', ['pointer']));

function noOpWhileFaking(name, ret, args, okValue) {
  const addr = sdl.getExportByName(name);
  replacedAddrs.push(addr);
  const orig = new NativeFunction(addr, ret, args);
  Interceptor.replace(addr, new NativeCallback(function (...a) {
    if (fakeFocus) { stats.blockedMouseCalls++; return okValue; }
    return orig(...a);
  }, ret, args));
}
stats.blockedMouseCalls = 0;
noOpWhileFaking('SDL_SetWindowRelativeMouseMode', 'bool', ['pointer', 'bool'], 1);
noOpWhileFaking('SDL_SetWindowMouseGrab', 'bool', ['pointer', 'bool'], 1);
noOpWhileFaking('SDL_WarpMouseInWindow', 'void', ['pointer', 'float', 'float'], undefined);

// ---- Game build ---------------------------------------------------------------------------
// The GOG and Steam exes are the same release, but code and data sit at different RVAs (the Steam one is
// shifted by 0x140-0x6E20 in code and ~0x6580 in data, not uniformly). Each build gets its own table,
// picked by the exe's PE timestamp. The Steam addresses were found by matching the code at, or referencing,
// each GOG address. Addresses in comments below are the GOG build's.
const BUILDS = {
  0x6a39efd0: {
    name: 'GOG', playerPhysics: 0xb66e0, sectorRay: 0x2c5e70, lobbyJoin: 0x237930, lobbyRed: 0x2378d0,
    lobbyBlue: 0x237870, setMaxPlayers: 0x136d20, gameMode: 0xf071a4, gameFlags: 0xf07fac, ctfRed: 0xf32a00,
    ctfBlue: 0xf32a18, gameState: 0xf07194, lobbyPick: 0x1351310, mpManager: 0x134ee00, playerData: 0xf08030,
    weaponAction: 0xf76128, weaponPending: 0xf76328, litStick: 0x116cce0,
    session: { hostLobby: 0x23b820, joinLobby: 0x217e30, resetGame: 0x93010, uiManager: 0x2ae1d0, uiLoad: 0x2b1920,
               gameFlags2: 0xf07ef8, lanName: 0x76cf24 },
  },
  0x6a39ed68: {
    name: 'Steam', playerPhysics: 0xb68f0, sectorRay: 0x2ccc90, lobbyJoin: 0x2381f0, lobbyRed: 0x238190,
    lobbyBlue: 0x238130, setMaxPlayers: 0x136e60, gameMode: 0xf0d724, gameFlags: 0xf0e52c, ctfRed: 0xf38f80,
    ctfBlue: 0xf38f98, gameState: 0xf0d714, lobbyPick: 0x1357934, mpManager: 0x1355420, playerData: 0xf0e5b0,
    weaponAction: 0xf7c698, weaponPending: 0xf7c898, litStick: 0x1173300,
  },
};
const exe = Process.mainModule;
const exeStamp = exe.base.add(exe.base.add(0x3c).readU32() + 8).readU32();
const build = BUILDS[exeStamp] || BUILDS[0x6a39efd0];
if (!BUILDS[exeStamp]) {
  send({ kind: 'warning', message: `unknown game build (exe timestamp 0x${exeStamp.toString(16)}); ` +
                                   'trying the GOG addresses, expect hook errors' });
}
const rvaHex = n => '0x' + n.toString(16).toUpperCase();

// ---- World state --------------------------------------------------------------------------
// Player physics (exe+0xB66E0, Player_Physics(Actor*)) runs once per frame for the local player only,
// on the game thread. Its first argument gives us our own actor. Every actor sits in one pool with a
// 0x150 stride and holds the same level pointer at +0x148. Player actors (local and remote) have flags
// 0x90 at +0xB0, a height at +0xAC and a 1.5 radius at +0xCC. Only the local player has PlayerData at
// +0xD8: remote players' health lives on their own machine.
//   Actor: +0x10 flags (0x4000 = dead, set ~0.2 s after the death arrives over the network); +0x38 x,
//   +0x3C y (feet height), +0x40 z (f32); +0x44 pitch (s16, positive = up); +0x46 heading (u16, low
//   14 bits, 16384 = 360 deg, 0 faces +z, increasing toward +x); +0x50 sector*; +0xAC height;
//   +0xCC radius; +0xD8 PlayerData* (local only); +0xB4/+0xB8/+0xBC velocity x/y/z (units/s, y up; the TNT
//   throw adds the thrower's to the stick's, 0xcfe70)
//   PlayerData: +0x2C health (max 10); +0xD8 current weapon slot (1-9, = the weapon key);
//   +0xF0 + 8*(slot-1) weapon item* per slot (null = not owned); +0x140.. ammo/fuel item* list
//   Weapon item: +0x04 name ("IPISTOL"); +0x1E8 RANGE_1, +0x1EC EFFRANGE_1 (f32); +0x245 AUTO_RELOAD;
//   +0x248 rounds loaded (s32); +0x24C SHOT_CAPACITY (s32); +0x274 ammo item name ("IBULLIT")
//   Ammo item: +0x04 name; +0x8C count in reserve (s32)
//   Ground items are pool actors with their item file at +0x114 ("GBADGE.ITM") and logic at +0xE0
//   ("Inv_GSheriffStar"). The badge picked up is the IBADGE item (Inv_HealthObject) in PlayerData.
const ACTOR_STRIDE = 0x150;
const POOL_SEARCH = 4096;          // slots searched either side of our actor when (re)finding the pool
const DEAD_FLAG = 0x4000;
const PLAYER_PHYSICS_RVA = build.playerPhysics;
const PLAYER_PHYSICS_PROLOGUE = [0x48, 0x8b, 0xc4, 0x55, 0x56, 0x57, 0x41, 0x54, 0x41, 0x55, 0x41, 0x57];
// Ray test: int SectorRay(Sector* from, Sector* to, x1, y1, z1, x2, y2, z2). Walks the ray from `from`
// through the level and returns 1 if it ends in `to` without hitting a wall, floor or ceiling. It writes
// global scratch state, so it must only run on the game thread: we call it from the physics hook.
const SECTOR_RAY_RVA = build.sectorRay;
const SECTOR_RAY_PROLOGUE = [0x48, 0x8b, 0xc4, 0x48, 0x89, 0x58, 0x08, 0x57, 0x48, 0x81, 0xec, 0x90, 0x00, 0x00, 0x00];
const EYE = 0.85;                  // eye height as a fraction of player height (estimate)
const LOS_POINTS = [0.6, 0.9];     // target points tested, fractions of height: body and head
const LOS_EVERY = 2;               // physics frames between visibility updates

let localActor = null;
let pool = null;                   // { level, first, count }
let frame = 0;
const losTargets = new Set();      // remote player actors to test, refreshed by world()
const losCache = {};               // actor address -> { visible, frame }

function prologueMatches(addr, bytes) { return bytes.every((b, i) => addr.add(i).readU8() === b); }

const sectorRayAddr = exe.base.add(SECTOR_RAY_RVA);
const sectorRay = prologueMatches(sectorRayAddr, SECTOR_RAY_PROLOGUE)
  ? new NativeFunction(sectorRayAddr, 'int',
      ['pointer', 'pointer', 'float', 'float', 'float', 'float', 'float', 'float'], 'win64')
  : null;
if (sectorRay === null) send({ kind: 'hook_error', hook: 'sector_ray', error: `prologue mismatch at exe+${rvaHex(SECTOR_RAY_RVA)}` });

function updateVisibility(me) {
  const mySector = me.add(0x50).readPointer();
  if (mySector.isNull()) return;
  const ex = me.add(0x38).readFloat(), ez = me.add(0x40).readFloat();
  const ey = me.add(0x3c).readFloat() + EYE * me.add(0xac).readFloat();
  for (const addr of losTargets) {
    const t = ptr(addr);
    let visible = false;
    try {
      const sector = t.add(0x50).readPointer();
      if (!sector.isNull()) {
        const tx = t.add(0x38).readFloat(), tz = t.add(0x40).readFloat();
        const ty = t.add(0x3c).readFloat(), th = t.add(0xac).readFloat();
        for (const f of LOS_POINTS) {
          if (sectorRay(mySector, sector, ex, ey, ez, tx, ty + f * th, tz)) { visible = true; break; }
        }
      }
    } catch (e) { continue; }
    losCache[addr] = { visible, frame };
  }
}

let speedMul = 1.0, boosted = 0;      // setSpeed(): walking speed multiplier for this game's own player
const physics = exe.base.add(PLAYER_PHYSICS_RVA);
// A leading JMP (0xE9) is another Frida hook on the same function, e.g. left behind by a session that
// didn't detach cleanly. Frida chains listeners, so we can still attach; we just can't check the bytes.
const physicsAlreadyHooked = physics.readU8() === 0xe9;
const physicsHooked = physicsAlreadyHooked || prologueMatches(physics, PLAYER_PHYSICS_PROLOGUE);
if (physicsAlreadyHooked) {
  send({ kind: 'warning', message: `exe+${rvaHex(PLAYER_PHYSICS_RVA)} already carries a hook (another or stale Frida session); attaching anyway` });
}
if (physicsHooked) {
  Interceptor.attach(physics, {
    onEnter(args) {
      localActor = args[0];
      frame++;
      if (sectorRay !== null && frame % LOS_EVERY === 0 && !failed.los) {
        try { updateVisibility(localActor); } catch (e) { fail('los', e); }
      }
      if (speedMul !== 1.0) {                      // remember where we stand before the physics step moves us
        try { this.sx = localActor.add(0x38).readFloat(); this.sz = localActor.add(0x40).readFloat(); this.sa = localActor; } catch (e) { this.sa = null; }
      }
    },
    onLeave() {
      // Speed boost (Hell bots): the physics step has moved us by (dx, dz); move a further (speedMul - 1) of that, flat.
      // The step itself is untouched (no velocity built up), so it can't run away; very large steps (respawn, teleport) are left alone.
      if (speedMul === 1.0 || !this.sa) return;
      try {
        const a = this.sa, nx = a.add(0x38).readFloat(), nz = a.add(0x40).readFloat();
        const dx = nx - this.sx, dz = nz - this.sz;
        if (dx * dx + dz * dz > 0.0000001 && dx * dx + dz * dz < 9.0) {
          a.add(0x38).writeFloat(nx + dx * (speedMul - 1.0));
          a.add(0x40).writeFloat(nz + dz * (speedMul - 1.0));
          boosted++;
        }
      } catch (e) {}
    },
  });
} else {
  send({ kind: 'hook_error', hook: 'player_physics', error: `prologue mismatch at exe+${rvaHex(PLAYER_PHYSICS_RVA)} (game updated?). Line of sight is off.` });
}

function tryRead(fn) { try { return fn(); } catch (e) { return null; } }

function findPool(actor) {
  const level = actor.add(0x148).readPointer();
  let lo = 0, hi = 0;
  for (let i = -1; i >= -POOL_SEARCH; i--) {
    if (tryRead(() => actor.add(i * ACTOR_STRIDE + 0x148).readPointer().equals(level))) lo = i;
  }
  for (let i = 1; i <= POOL_SEARCH; i++) {
    if (tryRead(() => actor.add(i * ACTOR_STRIDE + 0x148).readPointer().equals(level))) hi = i;
  }
  return { level, first: actor.add(lo * ACTOR_STRIDE), count: hi - lo + 1 };
}

function readActor(p) {
  return {
    addr: p.toString(),
    x: p.add(0x38).readFloat(), y: p.add(0x3c).readFloat(), z: p.add(0x40).readFloat(),
    yaw: p.add(0x46).readU16() & 0x3fff,
    pitch: p.add(0x44).readS16(),
    height: p.add(0xac).readFloat(),
    vx: p.add(0xb4).readFloat(), vy: p.add(0xb8).readFloat(), vz: p.add(0xbc).readFloat(),
    dead: (p.add(0x10).readU32() & DEAD_FLAG) !== 0,
    // +0xB0 low nibble = team (1 red, 2 blue, 3 green, 4 black, 0 none); 0x10000 / 0x20000 = carrying the
    // red / blue flag (names from the actor-flag dump at 0x2C2EB4).
    team: p.add(0xb0).readU32() & 0xf,
    carries: (p.add(0xb0).readU32() >>> 16) & 3,     // bit 0 red flag, bit 1 blue flag
  };
}

// Game mode and CTF state. 0xF071A4 = mode within multiplayer: 1 = capture the flag (the flag and base items
// only register then); in 1, 4 and 5 players keep the team they picked; in 2 and 3 everyone is team 2 except
// the one "holder" (team 1). 0xF07FAC bit 31 turns off the game's same-team damage block (0xCF227).
// Per CTF team the game keeps { u32 carrier node, Actor* flag, Actor* base }: red at 0xF32A00, blue at
// 0xF32A18 (-1 or null until spawned). A capture needs your own flag at your base (0x7B146).
const GAME_MODE = build.gameMode;
const GAME_FLAGS = build.gameFlags;
const CTF_RED = build.ctfRed, CTF_BLUE = build.ctfBlue;

function actorPos(p) {
  if (p.isNull() || p.equals(ptr('0xffffffffffffffff'))) return null;
  return { x: p.add(0x38).readFloat(), y: p.add(0x3c).readFloat(), z: p.add(0x40).readFloat() };
}

function ctfTeam(at) {
  const s = exe.base.add(at);
  return { flag: tryRead(() => actorPos(s.add(8).readPointer())),
           base: tryRead(() => actorPos(s.add(0x10).readPointer())) };
}

// Lobby. Game state 0xF07194 == 8 means waiting in the lobby until the player picks Join (or Join Red / Join
// Blue in a team game). The pick lands in 0x1351310: 0 none yet, 1 leave, 2 join, 3 red, 4 blue. The lobby
// buttons' handlers ignore their arguments: each one stores its pick and closes the lobby menu. Join is
// 0x237930, red 0x2378D0, blue 0x237870. They touch the UI, so we call them on the game's main thread, from the
// SDL_PollEvent hook. The lobby's is_team_game is the mode table 0x748D80 bit 0, i.e. modes 1, 4 and 5.
const GAME_STATE = build.gameState, LOBBY_PICK = build.lobbyPick;
const LOBBY_STATE = 8;
const LOBBY_HANDLERS = { 2: build.lobbyJoin, 3: build.lobbyRed, 4: build.lobbyBlue };

function lobby() {
  return { state: exe.base.add(GAME_STATE).readS32(), pick: exe.base.add(LOBBY_PICK).readS32(),
           mode: exe.base.add(GAME_MODE).readS32() };
}

function serviceLobby() {
  const pick = lobbyRequest;
  lobbyRequest = 0;
  const l = lobby();
  if (l.state !== LOBBY_STATE || l.pick !== 0) return;      // not (or no longer) waiting in the lobby
  new NativeFunction(exe.base.add(LOBBY_HANDLERS[pick]), 'void', ['pointer', 'pointer'])(NULL, NULL);
  stats.lobbyPicks = (stats.lobbyPicks || 0) + 1;
}

// Lobby size. The console command `kexmultiplayer maxplayers <n>` (handler in 0x13F030) calls
// kexMultiplayerManager::SetMaximumPlayers (0x136D20) on the manager *(0x134EE00) without a range check;
// only the -maxplayers startup option is clamped to 2-8 (0x23B9C9). SetMaximumPlayers stores n at
// manager+0x40, passes it to the lobby (manager+0x110, vfunc +0x108), then stores the lobby's own answer
// (vfunc +0x110) back at +0x40, so reading +0x40 afterwards tells whether the lobby took it. Without a lobby
// it only logs a warning, so we wait until the host's lobby exists. It runs on the main thread (SDL_PollEvent
// hook), like the console would.
const MP_MANAGER = build.mpManager;
const SET_MAX_PLAYERS_RVA = build.setMaxPlayers;
const SET_MAX_PLAYERS_PROLOGUE = [0x48, 0x89, 0x5c, 0x24, 0x08, 0x57, 0x48, 0x83, 0xec, 0x60];

function mpManager() {
  const m = exe.base.add(MP_MANAGER).readPointer();
  return m.isNull() ? null : m;
}

function maxPlayers() {
  const m = mpManager();
  return m === null ? { lobby: false, max: null }
                    : { lobby: !m.add(0x110).readPointer().isNull(), max: m.add(0x40).readU32() };
}

function serviceMaxPlayers() {
  const r = maxPlayersRequest;
  if (Date.now() < r.next) return;
  r.next = Date.now() + 1000;
  const m = mpManager();
  if (m === null || m.add(0x110).readPointer().isNull()) return;      // no lobby yet
  new NativeFunction(exe.base.add(SET_MAX_PLAYERS_RVA), 'void', ['pointer', 'uint32'])(m, r.n);
  const now = m.add(0x40).readU32();
  if (now === r.n) {
    send({ kind: 'info', message: `lobby size set to ${now}` });
    maxPlayersRequest = null;
  } else if (++r.tries >= 10) {
    send({ kind: 'warning', message: `lobby size: asked for ${r.n}, the lobby keeps ${now}` });
    maxPlayersRequest = null;
  }
}

// Next match without restarting the copy. When a match ends the game shuts the LAN session down: the lobby object
// (manager+0x110) goes away, everyone sits on the score screen, and Continue takes them to the main menu. From
// there the host makes a new lobby the way -host does at startup (0x23BAD0): the UI back to main.rml, then
// 0x23B820(driver name), which reads mp_levelName / mp_gamemode / the limits and creates the lobby. A joiner does
// what -join <address> does: the UI back to main.rml, game mode 0, the reset 0x93010, clear 0xF07EF8 bit 2, then
// 0x217E30("lan", kexArray<kexStr> holding the address). Both run on the main thread (SDL_PollEvent hook): a
// second Frida script hooking anything to run them crashes inside frida-agent.dll.
const SESSION = build.session || null;
const HOST_LOBBY_PROLOGUE = [0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x18, 0x48, 0x89, 0x7c, 0x24, 0x20];
const JOIN_LOBBY_PROLOGUE = [0x48, 0x89, 0x5c, 0x24, 0x08, 0x48, 0x89, 0x74, 0x24, 0x10, 0x48, 0x89, 0x7c, 0x24, 0x18];
const sessionKeep = [];

function sessionReady(kind) {
  if (SESSION === null) return `${kind}: not known for the ${build.name} build yet`;
  const [rva, bytes] = kind === 'host' ? [SESSION.hostLobby, HOST_LOBBY_PROLOGUE] : [SESSION.joinLobby, JOIN_LOBBY_PROLOGUE];
  if (!prologueMatches(exe.base.add(rva), bytes)) return `${kind}: prologue mismatch at exe+${rvaHex(rva)} (game updated?)`;
  return null;
}

function keepStr(s) {
  const m = Memory.allocUtf8String(s);
  sessionKeep.push(m);
  if (sessionKeep.length > 32) sessionKeep.shift();
  return m;
}

function uiToMainMenu() {
  const ui = new NativeFunction(exe.base.add(SESSION.uiManager), 'pointer', [])();
  new NativeFunction(ui.readPointer().add(0x20).readPointer(), 'void', ['pointer'])(ui);
  const view = Memory.alloc(16);              // { const char*, length }
  sessionKeep.push(view);
  view.writePointer(keepStr('main.rml'));
  view.add(8).writeU64(8);
  new NativeFunction(exe.base.add(SESSION.uiLoad), 'void', ['pointer', 'pointer'])(ui, view);
}

function serviceSession() {
  const r = sessionRequest;
  sessionRequest = null;
  const l = lobby();
  const m = mpManager();
  if (l.state !== 0 || (m !== null && !m.add(0x110).readPointer().isNull())) {
    send({ kind: 'warning', message: `${r.kind}: not at the main menu (game state ${l.state}); press Continue first` });
    return;
  }
  uiToMainMenu();
  let ok;
  if (r.kind === 'host') {
    ok = new NativeFunction(exe.base.add(SESSION.hostLobby), 'uint8', ['pointer'])(keepStr('lan'));
  } else {
    const el = Memory.alloc(0x28);            // kexStr: 16-byte inline buffer, +0x10 data*, +0x18 length, +0x20 capacity
    el.writeUtf8String(r.addr);
    el.add(0x10).writePointer(el);
    el.add(0x18).writeU64(r.addr.length);
    el.add(0x20).writeU64(0x10);
    const arr = Memory.alloc(0x18);           // kexArray: data*, capacity, count, grow by
    arr.writePointer(el);
    arr.add(8).writeU32(1);
    arr.add(0xc).writeU32(1);
    arr.add(0x10).writeU32(0x40);
    sessionKeep.push(el, arr);
    exe.base.add(GAME_MODE).writeS32(0);
    new NativeFunction(exe.base.add(SESSION.resetGame), 'void', [])();
    const flags = exe.base.add(SESSION.gameFlags2);
    flags.writeU32(flags.readU32() & ~4);
    ok = new NativeFunction(exe.base.add(SESSION.joinLobby), 'uint8', ['pointer', 'pointer'])(exe.base.add(SESSION.lanName), arr);
  }
  send({ kind: ok ? 'info' : 'warning', message: r.kind === 'host' ? `host lobby: ${ok ? 'created' : 'failed'}`
                                                                    : `join ${r.addr}: ${ok ? 'on its way' : 'failed'}` });
}

function game() {
  const mode = exe.base.add(GAME_MODE).readS32();
  return { mode, teamDamage: (exe.base.add(GAME_FLAGS).readU32() >>> 31) === 1,
           ctf: mode === 1 ? { red: ctfTeam(CTF_RED), blue: ctfTeam(CTF_BLUE) } : null };
}

// Item names are letters and digits, but aren't always zero-terminated within the bytes we read (and
// readCString(n) returns all n bytes), so take the leading run of A-Z, a-z and 0-9, upper-cased. The game
// compares item names ignoring case (has-item 0xB5660), and an item's name comes from its resource, which
// can be lower case (olpatch4.lab stores "idocbag.itm").
function readName(p) {
  let name = '';
  for (const c of new Uint8Array(p.readByteArray(16))) {
    if ((c >= 0x41 && c <= 0x5a) || (c >= 0x61 && c <= 0x7a) || (c >= 0x30 && c <= 0x39)) name += String.fromCharCode(c);
    else break;
  }
  return name.toUpperCase();
}

// The printable start of a name as it is in memory (case and dots kept), for diagnostics.
function readRaw(p) {
  let s = '';
  for (const c of new Uint8Array(p.readByteArray(32))) {
    if (c < 0x20 || c > 0x7e) break;
    s += String.fromCharCode(c);
  }
  return s;
}

// An available ground item: flag 0x400 at +0xB0, an item file name starting with G at +0x114 (not
// zero-terminated within 16 bytes, so we take the leading letters and digits), and logic attached at
// +0xD8. Taking an item detaches its logic (+0xD8, +0x88 and the logic name at +0xE0 are cleared) while
// the actor stays where it was, so a null +0xD8 means "taken".
function readGroundItem(p, level) {
  if (!p.add(0x148).readPointer().equals(level)) return null;
  if ((p.add(0xb0).readU32() & 0x400) === 0) return null;
  if (p.add(0xd8).readPointer().isNull()) return null;
  if (isExplosive(p)) return null;               // a lit stick, never something to pick up
  const name = readName(p.add(0x114));
  if (name.length < 3 || name[0] !== 'G') return null;
  return { addr: p.toString(), name, x: p.add(0x38).readFloat(), y: p.add(0x3c).readFloat(), z: p.add(0x40).readFloat() };
}

// TNT. Lighting a stick (the secondary fire's chor event, 0xda389) spawns the projectile actor at once and
// keeps it in exe+0x116CCE0 until the throw (0xdeea0) clears it, so a non-null pointer there means a lit stick in
// our hand. Its fuse runs from lighting: the spawn (0xce490) sets logic+0x10 = game clock + LIFE (pdynam.itm, 4500
// ms, u64 ms). A lit or thrown stick is a pool actor with logic "shot_ExpProjectileDispatch" at +0xE0 (attached by
// 0x2c2540) and its logic at +0xD8. Inert sticks lying on the ground (GDYNAM, a pickup) use
// shot_ProjectileDispatch instead.
const LIT_STICK = build.litStick;
const EXP_LOGIC = uint64('0x7078455f746f6873');   // "shot_Exp" as a little-endian u64

function isExplosive(p) {
  return p.add(0xe0).readU64().equals(EXP_LOGIC);
}

function readExplosive(p) {
  const logic = p.add(0xd8).readPointer();
  return { addr: p.toString(), x: p.add(0x38).readFloat(), y: p.add(0x3c).readFloat(), z: p.add(0x40).readFloat(),
           vx: p.add(0xb4).readFloat(), vy: p.add(0xb8).readFloat(), vz: p.add(0xbc).readFloat(),
           fuseAt: logic.isNull() ? null : logic.add(0x10).readU64().toNumber() };
}

function readInventory(pd) {
  const ammo = {};
  for (let o = 0x140; o < 0x1a0; o += 8) {
    const a = pd.add(o).readPointer();
    if (a.isNull()) continue;
    const name = tryRead(() => a.add(4).readCString());
    if (name) ammo[name] = a.add(0x8c).readS32();
  }
  const weapons = [];
  for (let slot = 1; slot <= 10; slot++) {
    const w = pd.add(0xf0 + 8 * (slot - 1)).readPointer();
    if (w.isNull()) continue;
    const ammoName = w.add(0x274).readCString();
    weapons.push({
      slot, name: w.add(4).readCString(),
      loaded: w.add(0x248).readS32(), capacity: w.add(0x24c).readS32(),
      range: w.add(0x1e8).readFloat(), effRange: w.add(0x1ec).readFloat(),
      effRange2: w.add(0x208).readFloat(),                 // EFFRANGE of the second fire mode block
      spread: w.add(0x1f0).readFloat(), spread2: w.add(0x20c).readFloat(),   // pellet SPREAD, degrees
      ammo: ammoName || null,
      reserve: ammoName ? (ammo[ammoName] !== undefined ? ammo[ammoName] : 0) : null,
    });
  }
  // Everything else we carry: scan PlayerData's item pointers for named items (IBADGE, IBPLATE, ...).
  // Generic items keep their count at +0x8C like ammo. The badge is an Inv_HealthObject.
  const carried = {};
  for (let o = 0x100; o < 0x400; o += 8) {
    const it = tryRead(() => pd.add(o).readPointer());
    if (it === null || it.isNull()) continue;
    const name = tryRead(() => readName(it.add(4)));
    if (!name || name.length < 3 || name[0] !== 'I') continue;
    if (carried[name] === undefined) carried[name] = tryRead(() => it.add(0x8c).readS32());
  }
  // The inventory array itself, raw: PlayerData+0xE8 + 8*i for i = 1..25 (weapons 1-10, other items from
  // 11 on; give-item 0xB4DC0, has-item 0xB5660). For checking what a pickup really added.
  const slots = [];
  for (let i = 1; i <= 25; i++) {
    const it = tryRead(() => pd.add(0xe8 + 8 * i).readPointer());
    if (it === null || it.isNull()) continue;
    slots.push({ i, name: tryRead(() => readRaw(it.add(4))), count: tryRead(() => it.add(0x8c).readS32()) });
  }
  // The local weapon's logic state (0xdd150): current action and the one queued behind it.
  // -1 = none, 0/1/2 = fire mode, 3 = reload. A press sets the queued action; letting go of a button
  // whose action is only queued cancels it.
  return { slot: pd.add(0xd8).readS32(), weapons, ammo, carried, slots,
           action: exe.base.add(WEAPON_ACTION).readS32(), pending: exe.base.add(WEAPON_PENDING).readS32(),
           hasBadge: Object.keys(carried).some(n => n.startsWith('IBADGE')),
           armor: carried.IBPLATE !== undefined ? carried.IBPLATE : 0 };
}

// exe+0xF08030 holds the local PlayerData*, and PlayerData+0x0 points to its current actor. The actor
// changes on every respawn (the old one becomes a corpse), so always go through PlayerData.
const PLAYER_DATA_GLOBAL = build.playerData;
const WEAPON_ACTION = build.weaponAction;
const WEAPON_PENDING = build.weaponPending;

function world() {
  const pd = exe.base.add(PLAYER_DATA_GLOBAL).readPointer();
  if (pd.isNull()) return { ok: false, reason: 'no local player (not in a match?)', lobby: tryRead(lobby) };
  // Our own state, available even while dead (when there is no live player actor).
  // PlayerData +0x20 is the crouched height (HEIGHT / CROUCH_DIV: Bob, Dr. Death, Sanchez 3.0; James, Mary 4.0;
  // Two Feathers 4.5), which decides which low openings we fit through.
  const self = { health: tryRead(() => pd.add(0x2c).readFloat()), inventory: tryRead(() => readInventory(pd)),
                 crouchHeight: tryRead(() => pd.add(0x20).readFloat()) };
  const me = pd.readPointer();
  if (me.isNull()) return { ok: false, reason: 'no local actor', self, lobby: tryRead(lobby) };
  const level = me.add(0x148).readPointer();
  if (pool === null || !pool.level.equals(level)) pool = findPool(me);
  const players = [];
  const targets = [];
  const badges = [];
  const items = [];
  const explosives = [];             // lit and thrown TNT sticks, ours and others'
  for (let i = 0; i < pool.count; i++) {
    const p = pool.first.add(i * ACTOR_STRIDE);
    const a = tryRead(() => {
      if (!p.add(0x148).readPointer().equals(level)) return null;
      if ((p.add(0xb0).readU32() & 0x90) !== 0x90) return null;
      const r = p.add(0xcc).readFloat(), h = p.add(0xac).readFloat();
      if (!(r > 1.4 && r < 1.6 && h > 1 && h < 10)) return null;
      return readActor(p);
    });
    if (a === null) {
      const stick = tryRead(() => p.add(0x148).readPointer().equals(level) && !p.add(0xd8).readPointer().isNull()
                                  && isExplosive(p) ? readExplosive(p) : null);
      if (stick !== null) { explosives.push(stick); continue; }
      const item = tryRead(() => readGroundItem(p, level));
      if (item === null) continue;
      if (item.name.startsWith('GBADGE')) badges.push(item);
      else items.push(item);
      continue;
    }
    a.local = p.equals(me);
    if (a.local) {
      a.health = self.health;
      a.inventory = self.inventory;
    } else {
      targets.push(a.addr);
      const c = losCache[a.addr];
      // A result stays fresh for a few frames; null means "not known yet".
      a.visible = c && frame - c.frame <= LOS_EVERY * 4 ? c.visible : null;
    }
    players.push(a);
  }
  losTargets.clear();
  for (const t of targets) losTargets.add(t);
  // The stick lit in our hand, if any: { addr, fuseAt, ... } (its addr shows up in `explosives` after the throw).
  const litPtr = tryRead(() => exe.base.add(LIT_STICK).readPointer());
  const lit = litPtr && !litPtr.isNull() ? tryRead(() => readExplosive(litPtr)) || { addr: litPtr.toString() } : null;
  return { ok: true, frame, pool: { first: pool.first.toString(), count: pool.count }, players, badges, items, self,
           explosives, lit, level: level.toString(), game: tryRead(game), lobby: tryRead(lobby) };
}

// The player's name when Steam is running: the game asks Steam for the account's persona name, so every copy
// would carry the same one. Hook ISteamFriends::GetPersonaName (vtable slot 0) and the flat export to answer
// with this bot's name instead.
let personaMem = null;
const personaHooked = new Set();
function setPersona(name) {
  personaMem = Memory.allocUtf8String(name);
  const mod = Process.findModuleByName('steam_api64.dll');
  if (!mod) return 'no steam_api64.dll in this process (not the Steam build, or it is not loaded yet)';
  const reply = new NativeCallback(() => personaMem, 'pointer', ['pointer']);
  const notes = [];
  const replaceOnce = (addr, what) => {
    const key = addr.toString();
    if (personaHooked.has(key)) { notes.push(what + ' already hooked'); return; }
    Interceptor.replace(addr, reply);
    replacedAddrs.push(addr);
    personaHooked.add(key);
    notes.push(what);
  };
  for (const e of mod.enumerateExports()) {
    if (e.name === 'SteamAPI_ISteamFriends_GetPersonaName') {
      replaceOnce(e.address, 'flat GetPersonaName');
    } else if (/^SteamAPI_SteamFriends_v\d+$/.test(e.name)) {
      try {
        const iface = new NativeFunction(e.address, 'pointer', [])();
        if (!iface.isNull()) replaceOnce(iface.readPointer().readPointer(), e.name + ' vtable[0]');
      } catch (err) { notes.push(e.name + ': ' + err); }
    }
  }
  return notes.length ? 'persona name -> ' + name + ' (' + notes.join(', ') + ')' : 'steam_api64.dll has no friends interface to hook';
}

// Debug: where in the game's memory does this text live (UTF-8 and UTF-16)? Returns up to `limit` hits with the
// memory range's protection and file, and the text around the hit.
function findText(text, limit) {
  const enc = [];
  const u8 = Memory.allocUtf8String(text);
  enc.push(['utf8', Array.from(new Uint8Array(u8.readByteArray(text.length)), b => ('0' + b.toString(16)).slice(-2)).join(' ')]);
  let h16 = [];
  for (let i = 0; i < text.length; i++) { const c = text.charCodeAt(i); h16.push(('0' + (c & 255).toString(16)).slice(-2), ('0' + (c >> 8).toString(16)).slice(-2)); }
  enc.push(['utf16', h16.join(' ')]);
  const out = [];
  for (const r of Process.enumerateRanges('rw-')) {
    if (r.size > 0x40000000) continue;
    for (const [name, pat] of enc) {
      let hits = [];
      try { hits = Memory.scanSync(r.base, r.size, pat); } catch (e) { continue; }
      for (const m of hits) {
        if (out.length >= limit) return out;
        let around = '';
        try { around = m.address.sub(16).readByteArray(16 + text.length * (name === 'utf16' ? 2 : 1) + 16); around = Array.from(new Uint8Array(around), b => ('0' + b.toString(16)).slice(-2)).join(''); } catch (e) {}
        out.push({ enc: name, addr: m.address.toString(), range: r.base + '+' + r.size.toString(16), file: r.file ? r.file.path : null, around });
      }
    }
  }
  return out;
}

// Quick taunt slot n plays the sound named by the cvar g_taunt<n>. The cvars are globals (exe+0xf7bba0, 0x80 apart);
// the value the game reads is the C string that the first entry of the array at cvar+0x60 points to. Point it at a
// new name to choose what the next press of that slot plays.
const TAUNT_CVAR_RVA = 0xf7bba0;
// The game's own cvar setter (GOG exe+0x1C2C80: cvar, string, flags, source), the one the console ends up in, so
// the string copy, the parsed values and the change hooks all happen; -host calls it for a random mp_levelName
// (0x23B923). Found by its first 24 bytes (unique, nothing address-relative in them), so both builds work. Until
// 2026-10-26 this called exe+0x1C2E90, the middle of an instruction inside it: numbers sometimes got through, a
// map name crashed in frida-agent.dll. It is called from the SDL_PollEvent hook (the main thread), like a typed
// `g_taunt1 max.wav` would be.
const CVAR_SET_PROLOGUE = '48 89 5c 24 18 48 89 74 24 20 55 41 56 41 57 48 8b ec 48 81 ec 80 00 00 00';
let cvarSetFn = null;
function cvarSetter() {
  if (cvarSetFn) return cvarSetFn;
  for (const r of exe.enumerateRanges('r-x')) {
    let hits = [];
    try { hits = Memory.scanSync(r.base, r.size, CVAR_SET_PROLOGUE); } catch (e) { continue; }
    if (hits.length === 1) {
      cvarSetFn = new NativeFunction(hits[0].address, 'void', ['pointer', 'pointer', 'uint32', 'uint32']);
      return cvarSetFn;
    }
  }
  throw new Error('the cvar setter was not found (game updated?)');
}
function setTaunt(slot, name) {
  if (!(slot >= 1 && slot <= 10) || !/^[A-Za-z0-9_.-]{1,40}$/.test(name)) return false;
  const cvar = exe.base.add(TAUNT_CVAR_RVA + (slot - 1) * 0x80);
  if (cvar.readPointer().readCString() !== 'g_taunt' + slot) return false;      // not this build
  tauntRequests.push({ cvar, slot, name, mem: Memory.allocUtf8String(name) });
  if (tauntRequests.length > 8) tauntRequests.shift();
  return true;
}
function serviceTaunts() {
  const reqs = tauntRequests;
  tauntRequests = [];
  for (const r of reqs) {
    (tauntNames[r.slot] = tauntNames[r.slot] || []).push(r.mem);        // keep the text alive
    cvarSetter()(r.cvar, r.mem, 0, 0);
    const now = r.cvar.add(0x60).readPointer().readPointer().readCString();
    send({ kind: now === r.name ? 'info' : 'warning', message: `g_taunt${r.slot} is now ${now}` });
  }
}


// ---- Any cvar by name (host: end the match) ------------------------------------------------------
// A cvar object starts with a pointer to its name string, so find the name in the exe's data, then the pointer to
// that string; the object is there. The game's own setter (cvarSetter) does the rest, on the main thread.
let cvarRequests = [];
const cvarCache = {};
function findCvar(name) {
  if (cvarCache[name]) return cvarCache[name];
  const pat = Array.from(name, c => c.charCodeAt(0).toString(16).padStart(2, '0')).join(' ') + ' 00';
  const strs = [];
  for (const r of exe.enumerateRanges('r--')) {
    try { for (const m of Memory.scanSync(r.base, r.size, pat)) strs.push(m.address); } catch (e) {}
  }
  for (const sAddr of strs) {
    const b = sAddr.toString().replace('0x', '').padStart(16, '0');
    const le = b.match(/../g).reverse().join(' ');
    for (const r of exe.enumerateRanges('rw-')) {
      let hits = [];
      try { hits = Memory.scanSync(r.base, r.size, le); } catch (e) {}
      for (const h of hits) {
        try { if (h.address.readPointer().readCString() === name) return (cvarCache[name] = h.address); } catch (e) {}
      }
    }
  }
  return null;
}
function setCvar(name, value) {
  if (!/^[A-Za-z0-9_]{1,40}$/.test(name) || !/^[A-Za-z0-9_.-]{1,40}$/.test(String(value))) return false;
  const cvar = findCvar(name);
  if (!cvar) return false;
  cvarRequests.push({ cvar, name, value: String(value), mem: Memory.allocUtf8String(String(value)) });
  if (cvarRequests.length > 8) cvarRequests.shift();
  return true;
}
function serviceCvars() {
  const reqs = cvarRequests;
  cvarRequests = [];
  for (const r of reqs) {
    keepStrings.push(r.mem);
    cvarSetter()(r.cvar, r.mem, 0, 0);
    send({ kind: 'info', message: `${r.name} set to ${r.value}` });
  }
}

// ---- Give items (gun game) -------------------------------------------------------------------
// Player_PickupItem(PlayerData*, const char* itemName, int slot) adds a named item (IRIFLE, IDBSGUN, ...) to a
// player the way the spawn code does. Found by its prologue, so it works on both builds.
const PICKUP_PROLOGUE = '48 89 5c 24 18 48 89 54 24 10 55 56 57 41 54 41 55 41 56 41 57 48 8d ac 24 30 fe ff ff 48 81 ec d0 02 00 00 41 8b f0 4c 8b e2 4c 8b e9';
let pickupFn = null;
const keepStrings = [];
function findPickup() {
  if (pickupFn) return pickupFn;
  const text = exe.enumerateRanges('r-x');
  for (const r of text) {
    let hits = [];
    try { hits = Memory.scanSync(r.base, r.size, PICKUP_PROLOGUE); } catch (e) { continue; }
    if (hits.length) {
      pickupFn = new NativeFunction(hits[0].address, 'int', ['pointer', 'pointer', 'int']);
      return pickupFn;
    }
  }
  return null;
}
function giveItems(names) {
  const fn = findPickup();
  if (!fn) return { ok: false, reason: 'pickup function not found' };
  const pd = exe.base.add(PLAYER_DATA_GLOBAL).readPointer();
  if (pd.isNull()) return { ok: false, reason: 'no local player' };
  const out = [];
  for (const n of names) {
    const parts = String(n).split(':');            // "IBPLATE:50" = that item with an amount of 50
    const str = Memory.allocUtf8String(parts[0]);
    keepStrings.push(str);
    if (keepStrings.length > 64) keepStrings.shift();
    out.push([n, fn(pd, str, parts.length > 1 ? parseInt(parts[1], 10) | 0 : 0)]);
  }
  return { ok: true, results: out };
}

// Set the count of an item the player already carries (IBPLATE after a pickup created it with 0): armor is the count.
function setItemCount(name, value) {
  const pd = exe.base.add(PLAYER_DATA_GLOBAL).readPointer();
  if (pd.isNull()) return { ok: false, reason: 'no local player' };
  for (let i = 1; i <= 25; i++) {
    const it = tryRead(() => pd.add(0xe8 + 8 * i).readPointer());
    if (it === null || it.isNull()) continue;
    if (tryRead(() => readName(it.add(4))) === name) {
      it.add(0x8c).writeS32(value | 0);
      return { ok: true, slot: i, count: it.add(0x8c).readS32() };
    }
  }
  return { ok: false, reason: 'not carried' };
}

// Probe: the raw actor bytes of every ground item (to learn which fields make an item a badge or an elixir).
function dumpItems() {
  if (pool === null || localActor === null && !pool) return { ok: false, reason: 'no pool yet' };
  const pd = exe.base.add(PLAYER_DATA_GLOBAL).readPointer();
  const me = pd.readPointer();
  const level = me.add(0x148).readPointer();
  const out = [];
  for (let i = 0; i < pool.count; i++) {
    const p = pool.first.add(i * ACTOR_STRIDE);
    const it = tryRead(() => readGroundItem(p, level));
    if (it === null) continue;
    const bytes = p.readByteArray(ACTOR_STRIDE);
    out.push({ name: it.name, addr: it.addr, x: it.x, y: it.y, z: it.z,
               logic: tryRead(() => p.add(0xe0).readCString(8)), hex: Array.from(new Uint8Array(bytes)).map(b => ('0' + b.toString(16)).slice(-2)).join('') });
  }
  return { ok: true, items: out };
}

// ---- Format trace: what the game formats through wsprintfA / wvsprintfA (the kill feed, if it goes this way) ----
const fmtLog = [];
let fmtOn = false;
function traceFormat() {
  if (fmtOn) return { ok: true, already: true };
  fmtOn = true;
  const hooked = [];
  for (const name of ['wsprintfA', 'wvsprintfA', 'wsprintfW', 'wvsprintfW']) {
    let addr = null;
    try { const m = Process.findModuleByName('user32.dll'); addr = m ? m.findExportByName(name) : null; } catch (e) { addr = null; }
    if (!addr) continue;
    const wide = name.endsWith('W');
    Interceptor.attach(addr, {
      onEnter(args) { this.out = args[0]; try { this.fmt = wide ? args[1].readUtf16String() : args[1].readCString(); } catch (e) { this.fmt = '?'; } },
      onLeave(ret) {
        try {
          const text = wide ? this.out.readUtf16String() : this.out.readCString();
          if (fmtLog.length < 400) fmtLog.push(this.fmt + ' => ' + text);
        } catch (e) {}
      },
    });
    hooked.push(name);
  }
  // the game's own statically linked sprintf (Steam build): sprintf(buf, fmt, ...)
  try {
    const a = Process.mainModule.base.add(0x329480);
    const sig = a.readByteArray(10);
    const want = [0x48,0x89,0x54,0x24,0x10,0x4c,0x89,0x44,0x24,0x18];
    const got = Array.from(new Uint8Array(sig));
    if (want.every((b, i) => b === got[i])) {
      const lastSeen = new Map();
      const feed = /(killed|suicide|fall|died|drowned|crushed|lobby|shot to death|telefrag|blew|self-destruct|joined the game)/i;
      Interceptor.attach(a, {
        onEnter(args) {
          try {
            const t = args[1].readCString();
            if (t && t.length < 120 && feed.test(t)) {
              // the game redraws a feed line every frame while it is shown: one event = one line after a quiet gap
              const now = Date.now(), last = lastSeen.get(t) || 0;
              lastSeen.set(t, now);
              if (now - last > 2500 && fmtLog.length < 400) fmtLog.push('G ' + t);
            }
          } catch (e) {}
        },
      });
      hooked.push('game_sprintf');
    } else hooked.push('game_sprintf_sig_mismatch');
  } catch (e) { hooked.push('game_sprintf_err ' + e); }
  return { ok: true, hooked };
}

rpc.exports = {
  traceFormat() {
    try { return traceFormat(); } catch (e) { return { ok: false, reason: String(e) }; }
  },
  drainFormats() {
    return fmtLog.splice(0, fmtLog.length);
  },
  dumpItems() {
    try { return dumpItems(); } catch (e) { return { ok: false, reason: String(e) }; }
  },
  give(names) {
    try { return giveItems(names); } catch (e) { return { ok: false, reason: String(e) }; }
  },
  setCount(name, value) {
    try { return setItemCount(String(name), Number(value)); } catch (e) { return { ok: false, reason: String(e) }; }
  },
  setCvar(name, value) {
    try { return setCvar(name, value); } catch (e) { return false; }
  },
  setSpeed(mult) {
    const m = Number(mult);
    if (!(m >= 1.0 && m <= 2.0)) return false;
    speedMul = m;
    return true;
  },
  speedStats() {
    return { mul: speedMul, boosted };
  },
  setTaunt(slot, name) {
    try { return setTaunt(slot, name); } catch (e) { return false; }
  },
  findText(text, limit) {
    try { return findText(text, limit || 30); } catch (e) { return [String(e)]; }
  },
  setPersona(name) {
    try { return setPersona(name); } catch (e) { return 'persona hook failed: ' + e; }
  },
  world() {
    try { return world(); } catch (e) { pool = null; return { ok: false, reason: String(e) }; }
  },
  // Undo every hook before the session detaches. Unloading the script while a game thread is inside one
  // of our replacements (SDL_PollEvent calls back into the original from inside it) crashed the game
  // twice, in frida-agent.dll. So the controller calls shutdown(), waits a moment for threads to leave,
  // and only then detaches.
  shutdown() {
    fakeFocus = false;
    queue.length = 0;
    Interceptor.detachAll();
    Interceptor.revert(pollAddr);
    for (const addr of replacedAddrs) Interceptor.revert(addr);
    Interceptor.flush();
    return true;
  },
  lobby() {
    try { return lobby(); } catch (e) { return null; }
  },
  // Press Join (2), Join Red (3) or Join Blue (4) in the lobby, on the main thread at the next poll.
  lobbyJoin(pick) {
    if (!(pick in LOBBY_HANDLERS)) return false;
    lobbyRequest = pick;
    return true;
  },
  // Set the hosted lobby's size (like `kexmultiplayer maxplayers n`) once the lobby exists; the result
  // arrives as an info or warning message.
  setMaxPlayers(n) {
    if (!prologueMatches(exe.base.add(SET_MAX_PLAYERS_RVA), SET_MAX_PLAYERS_PROLOGUE)) {
      send({ kind: 'warning', message: `lobby size: prologue mismatch at exe+${rvaHex(SET_MAX_PLAYERS_RVA)} (game updated?)` });
      return false;
    }
    maxPlayersRequest = { n, tries: 0, next: 0 };
    return true;
  },
  // At the main menu after a match: host a new LAN lobby with the mp_* cvars as they are now (set them first with
  // setCvar), or join the LAN game at addr. Done on the main thread at the next poll; the outcome arrives as a message.
  hostLobby() {
    const why = sessionReady('host');
    if (why) { send({ kind: 'warning', message: why }); return false; }
    sessionRequest = { kind: 'host' };
    return true;
  },
  joinLobby(addr) {
    if (!/^[A-Za-z0-9.:-]{1,15}$/.test(String(addr))) return false;      // fits the kexStr's inline buffer
    const why = sessionReady('join');
    if (why) { send({ kind: 'warning', message: why }); return false; }
    sessionRequest = { kind: 'join', addr: String(addr) };
    return true;
  },
  maxPlayers() {
    try { return maxPlayers(); } catch (e) { return null; }
  },
  hooks() {                       // which hooks are live (all false/absent on a clean install = fine)
    return { failed, physicsHooked, sectorRay: sectorRay !== null };
  },
  key(scancode, keycode, down) { queue.push({ kind: 'key', scancode, keycode, down }); },
  typeText(text) { queue.push({ kind: 'text', text: String(text) }); },   // as if typed (SDL text input), e.g. into the console
  button(button, down) {
    const bit = 1 << (button - 1);
    buttonMask = down ? (buttonMask | bit) : (buttonMask & ~bit);
    queue.push({ kind: 'button', button, down });
  },
  look(dx, dy) { look.dx += dx; look.dy += dy; },
  setLook(dx, dy) { look.dx = dx; look.dy = dy; },     // replace pending look (for aim loops)
  motion(dx, dy) { queue.push({ kind: 'motion', dx, dy }); },
  setLookStep(step) { lookStep = Math.max(0.1, step); return lookStep; },
  setFakeFocus(on) {
    on = !!on;
    if (on !== fakeFocus) {
      fakeFocus = on;
      // FOCUS_LOST on turning it off returns the game to its real, unfocused state; a real
      // FOCUS_GAINED arrives anyway if the user then clicks into the window.
      queue.push({ kind: 'window', type: on ? EV.WINDOW_FOCUS_GAINED : EV.WINDOW_FOCUS_LOST });
    }
    return fakeFocus;
  },
  stats() {
    return { ...stats, build: build.name, queued: queue.length, pendingLook: look, lookStep, windowID, fakeFocus, buttonMask, failed };
  },
  release() {                     // drop everything we hold so a detach never leaves keys stuck
    queue.length = 0; look.dx = 0; look.dy = 0;
    for (const b of [1, 2, 3]) if (buttonMask & (1 << (b - 1))) queue.push({ kind: 'button', button: b, down: false });
    buttonMask = 0;
  },
};
