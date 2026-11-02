// 오아시스 마을 인물 봇 - mineflayer로 인물마다 한 명씩 접속해서 시킨 일(나무 모으기 / 마을 짓기 / 몬스터 순찰)을 한다.
// discord_bot_v2 poring_food/minecraft.py가 이 HTTP API로만 부른다 (127.0.0.1에만 열림).
//   GET  /health                  {ok, bots:[이름], started, heapMB, rcon:{ok, at, error}} - 자가 점검이 본다
//   GET  /state                   {day, time, bots:[{name, health, food, pos, task}]}
//   POST /join  {name}            접속 (이름은 영문 3~16자)
//   POST /leave {name|"*"}        나가기
//   POST /task  {name, kind, args, say}   kind: gather(args.count) / mine(args.count) / farm(args.plots) / ore(args.count) /
//                                         build(args.blueprint, args.slot) / patrol(args.seconds) / rest
//     결과(events done.result): gather {logs} / mine {stone} / farm {food} / ore {coal, iron} / build {blueprint, ...} / patrol {kills}
//     짓기는 세운 뒤 블록을 다시 확인해서 실제로 서 있을 때만 done (아니면 failed - 장부/연대기에 거짓이 안 남게)
//   POST /say   {name, text}          게임 채팅 한마디 (개척지 회의)
//   POST /raid  {count, mob}          몬스터 습격 - 마을 둘레에 몬스터 소환
//   POST /rations {ok}                마을 창고에 식량이 있는지 - 있으면 배고픈 봇이 먹고('ate' 이벤트, 장부에서 식량을 뺀다),
//                                     없으면 굶는다 (쉬움 난이도라 굶어 죽지는 않지만 체력이 안 찬다)
//   GET  /events?after=N          [{id, at, name, kind, ...}] - 끝난 일, 잡은 몬스터, 죽음
// 건물은 마을 중심 둘레의 자리(slot)에 /fill 명령으로 세운다. 마을 중심은 VILLAGE_X/Z가 있으면 그 자리, 없으면(기본)
// 월드 스폰 - 봇은 늘 스폰에서 나타나는데 예전 기본값 0,0은 스폰에서 수백 칸 떨어져 청크가 안 불러와져서
// 짓기/밭 명령이 "That position is not loaded"로 실패하고 봇은 가다가 포기했다.
// 명령(/fill /give /clear /effect /summon ...)은 RCON(MC_RCON_PASSWORD)으로 서버에 직접 보낸다 - 봇이 OP가 아니어도 되고
// (OPS 목록에 없는 인물은 예전엔 명령이 전부 실패해 도구도 못 받고 짓기/밭이 안 됐다), 게임 채팅도 어지럽히지 않는다.
// RCON이 없으면 예전처럼 봇 채팅으로 (그 봇이 OP여야 함).
// 메모리: 봇마다 세계 조각(청크)을 따로 들고 있어서 시야(BOT_VIEW_DISTANCE, 기본 4청크)를 줄이고, Node 힙은 compose의
// NODE_OPTIONS로 늘린다 (기본값이면 3명이 넓게 돌아다닐 때 "JavaScript heap out of memory"로 통째로 죽는다).
// 재료(통나무)는 봇 쪽(poring_food)이 마을 창고 장부로 세고, 모자라면 짓기를 시키지 않는다.
'use strict';
const http = require('http');
const mineflayer = require('mineflayer');
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder');
const pvp = require('mineflayer-pvp').plugin;
const collectBlock = require('mineflayer-collectblock').plugin;
const { Vec3 } = require('vec3');

const MC_HOST = process.env.MC_HOST || 'mc-village';
const MC_PORT = parseInt(process.env.MC_PORT || '25565', 10);
const MC_VERSION = process.env.MC_VERSION || false;
const API_PORT = parseInt(process.env.API_PORT || '3010', 10);
const FIXED_CENTER = /^-?\d+$/.test(process.env.VILLAGE_X || '') && /^-?\d+$/.test(process.env.VILLAGE_Z || '');
let VX = FIXED_CENTER ? parseInt(process.env.VILLAGE_X, 10) : null;   // null = 첫 봇이 들어올 때 월드 스폰으로
let VZ = FIXED_CENTER ? parseInt(process.env.VILLAGE_Z, 10) : null;

function setCenter(bot) {
  if (VX !== null) return;
  const p = bot.spawnPoint && (bot.spawnPoint.x || bot.spawnPoint.z) ? bot.spawnPoint : bot.entity.position;
  VX = Math.round(p.x);
  VZ = Math.round(p.z);
  console.log(`마을 중심 = 월드 스폰 (${VX}, ${VZ})`);
}
const NAME_RE = /^[A-Za-z0-9_]{3,16}$/;
const RCON_HOST = process.env.MC_RCON_HOST || MC_HOST;
const RCON_PORT = parseInt(process.env.MC_RCON_PORT || '25575', 10);
const RCON_PASS = process.env.MC_RCON_PASSWORD || '';
const BOT_VIEW = parseInt(process.env.BOT_VIEW_DISTANCE || '4', 10);
const net = require('net');

// ------------------------------------------------------------------ RCON (명령은 서버에 직접)
let rconSock = null;
let rconReady = null;
let rconId = 0;
let rconBuf = Buffer.alloc(0);
const rconWait = new Map();
const STARTED = new Date().toISOString();   // 이 프로세스가 뜬 시각 - 바뀌면 죽었다 살아난 것
const rconStat = { ok: null, at: null, error: '' };

function rconPacket(id, type, body) {
  const b = Buffer.from(body, 'utf8');
  const p = Buffer.alloc(14 + b.length);
  p.writeInt32LE(10 + b.length, 0);
  p.writeInt32LE(id, 4);
  p.writeInt32LE(type, 8);
  b.copy(p, 12);
  return p;
}

function rconConnect() {
  if (rconReady) return rconReady;
  rconReady = new Promise((resolve, reject) => {
    const sock = net.createConnection(RCON_PORT, RCON_HOST);
    let dead = false;
    const fail = (e) => {
      if (dead) return;
      dead = true;
      for (const w of rconWait.values()) w.reject(e);
      rconWait.clear();
      rconSock = null;
      rconReady = null;
      rconBuf = Buffer.alloc(0);
      try { sock.destroy(); } catch (x) { /* 이미 닫힘 */ }
      reject(e);
    };
    sock.on('connect', () => {
      const id = ++rconId;
      rconWait.set(id, { resolve: () => { rconSock = sock; resolve(sock); }, reject: fail });
      sock.write(rconPacket(id, 3, RCON_PASS));
    });
    sock.on('data', (d) => {
      rconBuf = Buffer.concat([rconBuf, d]);
      while (rconBuf.length >= 4) {
        const len = rconBuf.readInt32LE(0);
        if (rconBuf.length < len + 4) break;
        const id = rconBuf.readInt32LE(4);
        const text = rconBuf.toString('utf8', 12, len + 2);
        rconBuf = rconBuf.subarray(len + 4);
        if (id === -1) return fail(new Error('RCON 비밀번호가 틀림'));
        const w = rconWait.get(id);
        if (w) { rconWait.delete(id); w.resolve(text); }
      }
    });
    sock.on('error', fail);
    sock.on('close', () => fail(new Error('RCON 연결 끊김')));
  });
  return rconReady;
}

async function rcon(cmd) {
  const sock = await rconConnect();
  const id = ++rconId;
  const done = new Promise((resolve, reject) => rconWait.set(id, { resolve, reject }));
  sock.write(rconPacket(id, 2, cmd));
  return Promise.race([done, sleep(10000).then(() => { rconWait.delete(id); throw new Error('RCON 응답 없음'); })]);
}

// 명령 하나 (앞의 / 없이). @s는 그 봇 이름으로 바꾼다. RCON이 안 되면 봇 채팅으로
async function run(bot, cmd) {
  const name = bot && bot.username;
  const c = name ? cmd.replace(/@s\b/g, name) : cmd;
  if (RCON_PASS) {
    // 연결이 끊겼으면(서버가 RCON 연결을 닫음) 새로 붙어서 한 번 더 - 그래도 안 되면 채팅으로
    for (let i = 0; i < 2; i++) {
      try {
        const out = await rcon(c);
        Object.assign(rconStat, { ok: true, at: new Date().toISOString(), error: '' });
        return out;
      } catch (e) {
        Object.assign(rconStat, { ok: false, at: new Date().toISOString(), error: String(e.message || e).slice(0, 120) });
        if (i === 1 || /비밀번호/.test(e.message)) { console.log(`RCON 실패 (${e.message}) - 채팅으로: ${c.slice(0, 60)}`); break; }
        await sleep(300);
      }
    }
  }
  if (bot) bot.chat(`/${cmd}`);
  return '';
}

const bots = new Map();      // 이름 -> {bot, task, kills}
let forest = null;           // 마지막으로 나무를 벤 곳 (모두가 같이 쓴다)
let worldReady = false;      // 게임 규칙을 한 번 맞췄는지
let rations = true;          // 마을 창고에 식량이 있는지 (poring_food가 바퀴마다 알려 준다)
const farmAt = new Map();    // 밭 자리 -> 마지막으로 돌본 시각 (밭 일을 이어서 할 때 자란 만큼 계산)
const events = [];           // 최근 500개
let nextId = 1;

function emit(name, kind, data = {}) {
  events.push({ id: nextId++, at: new Date().toISOString(), name, kind, ...data });
  console.log(`[${new Date().toISOString().slice(11, 19)}] ${name} ${kind} ${Object.keys(data).length ? JSON.stringify(data) : ''}`);   // docker logs mc-agents 로 보이게
  if (events.length > 500) events.splice(0, events.length - 500);
}

// ------------------------------------------------------------------ 건물 (마을 중심 기준 자리마다)
// 자리: 반지름 18 둘레에 8칸 (건물 종류마다 같은 자리 목록을 따로 쓴다 - slot은 poring_food가 센다)
function slotOrigin(blueprint, slot, groundY) {
  const ring = { hut: 18, well: 8, smithy: 22, hall: 0, farm: 26, tower: 34, wall: 40 }[blueprint] || 18;
  if (blueprint === 'hall') return { x: VX - 4, y: groundY, z: VZ + 10 };   // 마을회관은 광장 북쪽 한가운데
  const n = 8;
  const a = (2 * Math.PI * (slot % n)) / n + (blueprint === 'farm' ? 0.4 : blueprint === 'tower' ? 0.2 : 0);
  return { x: Math.round(VX + ring * Math.cos(a)), y: groundY, z: Math.round(VZ + ring * Math.sin(a)) };
}

const BLUEPRINTS = {
  // 오두막 5x4x5: 바닥, 벽(속 빔), 지붕, 문, 횃불
  hut: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 4} ${o.y} ${o.z + 4} oak_planks`,
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x + 4} ${o.y + 3} ${o.z + 4} oak_planks hollow`,
    `fill ${o.x} ${o.y + 4} ${o.z} ${o.x + 4} ${o.y + 4} ${o.z + 4} spruce_slab`,
    `setblock ${o.x + 2} ${o.y + 1} ${o.z} oak_door[half=lower,facing=north]`,
    `setblock ${o.x + 2} ${o.y + 2} ${o.z} oak_door[half=upper,facing=north]`,
    `setblock ${o.x + 2} ${o.y + 2} ${o.z + 3} wall_torch[facing=north]`,
  ],
  // 밭 7x7: 물 한 칸 + 경작지 + 밀
  farm: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 6} ${o.y} ${o.z + 6} farmland`,
    `setblock ${o.x + 3} ${o.y} ${o.z + 3} water`,
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x + 6} ${o.y + 1} ${o.z + 6} wheat[age=0] replace air`,
    `setblock ${o.x + 3} ${o.y + 1} ${o.z + 3} air`,
    `fill ${o.x - 1} ${o.y + 1} ${o.z - 1} ${o.x + 7} ${o.y + 1} ${o.z + 7} oak_fence outline`,
  ],
  // 감시탑 3x3x8 + 꼭대기 횃불
  tower: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 2} ${o.y + 7} ${o.z + 2} cobblestone hollow`,
    `fill ${o.x - 1} ${o.y + 8} ${o.z - 1} ${o.x + 3} ${o.y + 8} ${o.z + 3} oak_planks`,
    `setblock ${o.x + 1} ${o.y + 1} ${o.z} air`,
    `setblock ${o.x + 1} ${o.y + 2} ${o.z} air`,
    `setblock ${o.x - 1} ${o.y + 9} ${o.z - 1} torch`,
    `setblock ${o.x + 3} ${o.y + 9} ${o.z + 3} torch`,
  ],
  // 우물 3x3
  well: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 2} ${o.y + 1} ${o.z + 2} cobblestone hollow`,
    `setblock ${o.x + 1} ${o.y} ${o.z + 1} water`,
    `fill ${o.x} ${o.y + 2} ${o.z} ${o.x} ${o.y + 3} ${o.z} oak_fence`,
    `fill ${o.x + 2} ${o.y + 2} ${o.z + 2} ${o.x + 2} ${o.y + 3} ${o.z + 2} oak_fence`,
    `fill ${o.x} ${o.y + 4} ${o.z} ${o.x + 2} ${o.y + 4} ${o.z + 2} spruce_slab`,
  ],
  // 대장간 6x4x5: 돌 바닥/벽, 화로·모루·용광로
  smithy: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 5} ${o.y} ${o.z + 4} stone_bricks`,
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x + 5} ${o.y + 3} ${o.z + 4} cobblestone hollow`,
    `fill ${o.x} ${o.y + 4} ${o.z} ${o.x + 5} ${o.y + 4} ${o.z + 4} dark_oak_slab`,
    `fill ${o.x + 2} ${o.y + 1} ${o.z} ${o.x + 3} ${o.y + 2} ${o.z} air`,
    `setblock ${o.x + 1} ${o.y + 1} ${o.z + 3} blast_furnace`,
    `setblock ${o.x + 4} ${o.y + 1} ${o.z + 3} anvil`,
    `setblock ${o.x + 2} ${o.y + 1} ${o.z + 3} smithing_table`,
    `setblock ${o.x + 3} ${o.y + 1} ${o.z + 3} campfire`,
  ],
  // 마을회관 9x6x9: 돌기둥 + 통나무 벽 + 종
  hall: (o) => [
    `fill ${o.x} ${o.y} ${o.z} ${o.x + 8} ${o.y} ${o.z + 8} polished_andesite`,
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x + 8} ${o.y + 5} ${o.z + 8} spruce_planks hollow`,
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x} ${o.y + 5} ${o.z} stone_bricks`,
    `fill ${o.x + 8} ${o.y + 1} ${o.z} ${o.x + 8} ${o.y + 5} ${o.z} stone_bricks`,
    `fill ${o.x} ${o.y + 1} ${o.z + 8} ${o.x} ${o.y + 5} ${o.z + 8} stone_bricks`,
    `fill ${o.x + 8} ${o.y + 1} ${o.z + 8} ${o.x + 8} ${o.y + 5} ${o.z + 8} stone_bricks`,
    `fill ${o.x - 1} ${o.y + 6} ${o.z - 1} ${o.x + 9} ${o.y + 6} ${o.z + 9} dark_oak_slab`,
    `fill ${o.x + 3} ${o.y + 1} ${o.z} ${o.x + 5} ${o.y + 3} ${o.z} air`,
    `setblock ${o.x + 4} ${o.y + 5} ${o.z + 1} bell[attachment=ceiling]`,
    `setblock ${o.x + 4} ${o.y + 1} ${o.z + 7} lectern`,
    `fill ${o.x + 1} ${o.y + 4} ${o.z + 1} ${o.x + 7} ${o.y + 4} ${o.z + 7} lantern replace air`,
  ],
  // 성벽 한 칸 9x3
  wall: (o) => [
    `fill ${o.x} ${o.y + 1} ${o.z} ${o.x + 8} ${o.y + 3} ${o.z} cobblestone_wall`,
    `setblock ${o.x + 4} ${o.y + 4} ${o.z} lantern`,
  ],
};

function groundY(bot, x, z) {
  // 지면 높이 (나뭇잎/풀은 건너뛴다, 못 찾으면 봇 발밑)
  for (let y = 120; y > -60; y--) {
    const b = bot.blockAt(new Vec3(x, y, z));
    if (b && b.boundingBox === 'block' && !/leaves|grass|fern|flower|snow$/.test(b.name)) return y;
  }
  return Math.floor(bot.entity.position.y) - 1;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

// 건물마다 그 자리 기둥(세로줄)에 있어야 할 블록 - 실제로 서 있는지 확인(둘러보기/밭 고치기)에 쓴다
const SIGNS = {
  hut: /oak_planks|spruce_slab|oak_door/, farm: /farmland|wheat/, well: /cobblestone|water/,
  tower: /cobblestone|oak_planks/, smithy: /stone_bricks|cobblestone|dark_oak_slab/,
  hall: /polished_andesite|spruce_planks|stone_bricks|dark_oak_slab/, wall: /cobblestone_wall|lantern/,
};

function standing(bot, blueprint, slot) {
  // 그 자리(모서리 + 한 칸 안쪽)를 위에서 아래로 훑어 건물 블록이 있으면 true, 없으면 false, 청크가 안 보이면 null
  const o = slotOrigin(blueprint, slot, 0);
  const sign = SIGNS[blueprint];
  let seen = false;
  for (const [dx, dz] of [[0, 0], [1, 1], [2, 2]]) {
    for (let y = 120; y > -40; y--) {
      const b = bot.blockAt(new Vec3(o.x + dx, y, o.z + dz));
      if (!b) break;
      seen = true;
      if (sign.test(b.name)) return true;
      if (b.boundingBox === 'block' && !/leaves|log/.test(b.name) && y < 100) break;   // 땅에 닿음
    }
  }
  return seen ? false : null;
}

// 길찾기/캐기가 끝나지 않고 매달리는 일이 있다 (닿을 수 없는 곳, 물/절벽) - 그러면 봇은 '일하는 중'인 채 서 있고
// 새 일도 못 받는다. 일정 시간이 지나면 멈추고 다음으로 넘어간다
function withTimeout(promise, ms, onTimeout) {
  let t;
  return Promise.race([promise, new Promise((_, reject) => {
    t = setTimeout(() => { try { onTimeout && onTimeout(); } catch (e) { /* 무시 */ } reject(new Error('시간 초과')); }, ms);
  })]).finally(() => clearTimeout(t));
}

async function goNear(bot, x, z, range = 3) {
  try {
    await withTimeout(bot.pathfinder.goto(new goals.GoalNearXZ(x, z, range)), 90000, () => bot.pathfinder.stop());
  } catch (e) { /* 막혀도 근처에서 진행 */ }
}

async function collect(bot, block) {
  return withTimeout(bot.collectBlock.collect(block), 60000, () => { bot.pathfinder.stop(); bot.stopDigging(); });
}

// ------------------------------------------------------------------ 일
async function doGather(entry, count, near) {
  // near: 결계가 높을 때 - 멀리 걸어가지 않고(새 청크를 안 불러오게) 마을 둘레 32칸 안에서만
  const bot = entry.bot;
  const logIds = ['oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'cherry_log', 'mangrove_log']
    .map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 일부만 불러와도 진행 */ }
  if (!near && forest && !bot.findBlock({ matching: logIds, maxDistance: 32 })) {
    await goNear(bot, forest.x, forest.z, 6);           // 지난번에 나무를 벤 숲으로 먼저
    try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
  }
  let got = 0;
  let misses = 0;
  while (got < count && !entry.cancel && misses < (near ? 2 : 6)) {
    const block = bot.findBlock({ matching: logIds, maxDistance: near ? 32 : 64 });
    if (!block && near) { misses++; continue; }
    if (!block) {
      // 근처에 나무가 없으면 한쪽으로 점점 멀리 (50, 100, 150칸...) 걸어가서 다시 찾는다
      misses++;
      if (misses === 1) entry.dir = Math.random() * Math.PI * 2;
      const a = entry.dir + (misses % 2 ? 0 : 0.6);
      const p = bot.entity.position;
      await goNear(bot, Math.round(p.x + 50 * Math.cos(a)), Math.round(p.z + 50 * Math.sin(a)), 4);
      try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
      continue;
    }
    try { await collect(bot, block); got++; } catch (e) { misses++; }
  }
  // 모은 통나무는 마을 창고로 (가방에서 빼서 장부로 - 실제 건물은 /fill)
  if (got) {
    await run(bot, `clear @s #minecraft:logs ${got}`);
    forest = bot.entity.position.floored();          // 다음에 나무 베러 갈 곳
  }
  await goNear(bot, VX, VZ, 6);
  return { logs: got };
}

async function doMine(entry, count, near) {
  // 돌 캐기 - 땅 위나 얕은 곳의 돌을 캐서 마을 창고로 (조약돌로 셈)
  const bot = entry.bot;
  const ids = ['stone', 'cobblestone', 'andesite', 'diorite', 'granite', 'deepslate', 'tuff']
    .map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
  let got = 0;
  let misses = 0;
  while (got < count && !entry.cancel && misses < (near ? 2 : 5)) {
    const block = bot.findBlock({ matching: ids, maxDistance: 32,
      useExtraInfo: (b) => b.position.y >= bot.entity.position.y - 6 });   // 너무 깊이는 안 판다
    if (!block) {
      misses++;
      if (near) continue;            // 결계가 높을 땐 멀리 안 간다
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goNear(bot, Math.round(p.x + 32 * Math.cos(a)), Math.round(p.z + 32 * Math.sin(a)), 4);
      continue;
    }
    try { await collect(bot, block); got++; } catch (e) { misses++; }
  }
  if (got) await run(bot, `clear @s #minecraft:stone_tool_materials ${got}`);
  await goNear(bot, VX, VZ, 6);
  return { stone: got };
}

async function doFarm(entry, plots, grow, growMin) {
  // 밭 수확 - 지은 밭(자리 0..plots-1)을 돌며 다 익은 밀(age=7)을 거두고 그 자리에 다시 심는다. 거둔 밀 한 포기 = 식량 1
  // grow: 지난 수확 뒤 흐른 시간만큼 자란 단계 (poring_food가 계산). 밭 청크는 사람이 근처에 있을 때만 시간이 흘러서
  // (봇은 보통 숲/채석장에 있다) 실제 무작위 성장만 기다리면 거의 안 자란다 - 그만큼 나이를 올려 준 뒤 거둔다
  const bot = entry.bot;
  let food = 0;
  let crops = 0;                  // 밭에서 찾은 밀 포기 수 (0이면 밭이 망가졌거나 자리를 못 찾은 것)
  let rebuilt = 0;                // 다시 일군 밭
  for (let slot = 0; slot < plots && !entry.cancel; slot++) {
    const probe = slotOrigin('farm', slot, 0);
    await goNear(bot, probe.x + 3, probe.z + 3, 4);
    try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
    if (!bot.blockAt(new Vec3(probe.x + 1, 60, probe.z + 1))) continue;   // 청크가 안 보이면 건드리지 않는다
    const y = groundY(bot, probe.x + 1, probe.z + 1);
    const before = crops;
    if (growMin && farmAt.has(slot)) grow = Math.max(0, Math.min(7, Math.floor((Date.now() - farmAt.get(slot)) / (growMin * 60000))));
    farmAt.set(slot, Date.now());
    for (let dx = 0; dx < 7; dx++) {
      for (let dz = 0; dz < 7; dz++) {
        const b = bot.blockAt(new Vec3(probe.x + dx, y + 1, probe.z + dz));
        if (!b || b.name !== 'wheat') continue;
        crops++;
        const age = Math.min(7, (Number(b.getProperties().age) || 0) + grow);
        if (age >= 7) {
          await run(bot, `setblock ${probe.x + dx} ${y + 1} ${probe.z + dz} wheat[age=0]`);
          food++;
        } else if (grow) {
          await run(bot, `setblock ${probe.x + dx} ${y + 1} ${probe.z + dz} wheat[age=${age}]`);
        }
      }
    }
    if (!(crops - before) || standing(bot, 'farm', slot) === false) {
      // 밭이 아예 없다 (예전에 명령 권한이 없어 장부에만 지어진 밭, 또는 망가짐) - 그 자리에 밭을 다시 일군다
      const o = slotOrigin('farm', slot, y);
      for (const cmd of BLUEPRINTS.farm(o)) await run(bot, cmd);
      rebuilt++;
      continue;
    }
    // 비어 버린 칸(밟혀 망가진 곳)은 다시 심는다
    await run(bot, `fill ${probe.x} ${y + 1} ${probe.z} ${probe.x + 6} ${y + 1} ${probe.z + 6} wheat[age=0] replace air`);
    await run(bot, `setblock ${probe.x + 3} ${y + 1} ${probe.z + 3} air`);
    await sleep(400);
  }
  await goNear(bot, VX, VZ, 6);
  return { food, crops, rebuilt };
}

async function doOre(entry, count) {
  // 광석 캐기 - 둘레(아래 포함)의 석탄/철광석을 찾아 캔다 (pathfinder가 땅을 파고 내려간다). 캔 만큼 창고로
  const bot = entry.bot;
  const names = ['coal_ore', 'deepslate_coal_ore', 'iron_ore', 'deepslate_iron_ore'];
  const ids = names.map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
  const got = { coal: 0, iron: 0 };
  let misses = 0;
  while (got.coal + got.iron < count && !entry.cancel && misses < 4) {
    const block = bot.findBlock({ matching: ids, maxDistance: 48 });
    if (!block) {
      misses++;
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goNear(bot, Math.round(p.x + 40 * Math.cos(a)), Math.round(p.z + 40 * Math.sin(a)), 4);
      try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
      continue;
    }
    const kind = block.name.includes('iron') ? 'iron' : 'coal';
    try { await collect(bot, block); got[kind]++; } catch (e) { misses++; }
  }
  if (got.coal) await run(bot, `clear @s coal ${got.coal}`);
  if (got.iron) await run(bot, `clear @s raw_iron ${got.iron}`);
  await goNear(bot, VX, VZ, 6);
  return got;
}

async function doAudit(entry, slots) {
  // 둘러보기 - 장부에 지어졌다고 적힌 건물 자리마다 가서 실제로 서 있는지 본다. 반환 {found: {건물: 서 있는 수}, checked}
  const bot = entry.bot;
  const found = {};
  let checked = 0;
  for (const [bp, n] of Object.entries(slots || {})) {
    if (!SIGNS[bp]) continue;
    found[bp] = 0;
    for (let slot = 0; slot < Math.min(8, n) && !entry.cancel; slot++) {
      const p = slotOrigin(bp, slot, 0);
      await goNear(bot, p.x, p.z, 6);
      try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
      const ok = standing(bot, bp, slot);
      if (ok === null) continue;      // 못 봄 - 세지 않는다 (장부를 함부로 줄이지 않게)
      checked++;
      if (ok) found[bp]++;
    }
  }
  if (entry.cancel) throw new Error('둘러보다 멈춤');
  await goNear(bot, VX, VZ, 6);
  return { found, checked };
}

async function doRaid(count, mob) {
  // 몬스터 습격 - 마을 둘레(반지름 30) 여기저기에 몬스터가 나타난다 (서버 부하 급등 = 결계가 얇아진 틈)
  const any = [...bots.values()][0];
  if (!any) throw new Error('개척지에 아무도 없음');
  for (let i = 0; i < count; i++) {
    const a = (2 * Math.PI * i) / count;
    const x = Math.round(VX + 30 * Math.cos(a));
    const z = Math.round(VZ + 30 * Math.sin(a));
    await run(any.bot, `execute positioned ${x} 0 ${z} positioned over motion_blocking_no_leaves run summon ${mob} ~ ~ ~`);
    await sleep(300);
  }
  return { count, mob };
}

async function doBuild(entry, blueprint, slot) {
  const bot = entry.bot;
  const plan = BLUEPRINTS[blueprint];
  if (!plan) throw new Error(`모르는 건물: ${blueprint}`);
  const probe = slotOrigin(blueprint, slot, 0);
  await goNear(bot, probe.x, probe.z, 4);
  const o = slotOrigin(blueprint, slot, groundY(bot, probe.x, probe.z));
  for (const cmd of plan(o)) {
    if (entry.cancel) throw new Error('짓다가 멈춤');
    await run(bot, cmd);
    await sleep(RCON_PASS ? 100 : 400);
  }
  // 검증: 모든 건물은 (o.x, o.y+1, o.z)에 블록이 있다 - 정말 서 있는지 확인 (청크가 안 보이면 확인 못 함 = 믿는다)
  await sleep(800);
  const check = bot.blockAt(new Vec3(o.x, o.y + 1, o.z));
  if (check && check.name === 'air') throw new Error('명령은 보냈지만 건물이 서 있지 않음 (권한/위치 확인)');
  return { blueprint, slot, x: o.x, y: o.y, z: o.z, verified: !!check };
}

function isHostile(e) {
  return e && (e.type === 'hostile' || e.kind === 'Hostile mobs') && e.name !== 'creeper';  // 크리퍼는 건물이 부서지니 피한다
}

async function doPatrol(entry, seconds) {
  const bot = entry.bot;
  const until = Date.now() + seconds * 1000;
  const before = entry.kills;
  while (Date.now() < until && !entry.cancel) {
    const target = bot.nearestEntity((e) => isHostile(e) && e.position.distanceTo(bot.entity.position) < 24);
    if (target) {
      try { bot.pvp.attack(target); } catch (e) { /* 이미 사라짐 */ }
      await sleep(3000);
    } else {
      bot.pvp.stop();
      // 마을 중심 둘레를 천천히 돈다
      const a = Math.random() * Math.PI * 2;
      await goNear(bot, Math.round(VX + 14 * Math.cos(a)), Math.round(VZ + 14 * Math.sin(a)), 3);
      await sleep(2000);
    }
  }
  bot.pvp.stop();
  return { kills: entry.kills - before };
}

async function runTask(name, kind, args, say) {
  const entry = bots.get(name);
  if (!entry) throw new Error('접속 안 함');
  if (entry.task) entry.cancel = true;
  while (entry.task) await sleep(200);
  entry.cancel = false;
  entry.task = kind;
  await Promise.race([entry.ready, sleep(60000)]);
  if (say) entry.bot.chat(String(say).slice(0, 200));
  // repeat: 모으는 일(gather/mine/ore)은 다음 일을 받을 때까지 이어서 한다 (한 번 끝날 때마다 done) -
  // 일을 10분마다 나눠 주므로 금방 끝나면 남는 시간 동안 놀던 것을 없앤다. 빈손이면 멈춘다 (같은 헛수고 반복 X)
  // 농부(farm repeat)는 밭을 돌고 잠깐 기다렸다가(밀이 자라게) 다시 돈다 - 빈손이어도 계속 (밀은 자라는 중)
  const repeat = args.repeat && ['gather', 'mine', 'ore', 'farm'].includes(kind);
  try {
    for (let round = 0; ; round++) {
      entry.roundAt = Date.now();
      let result = {};
      if (kind === 'gather') result = await doGather(entry, Math.min(32, args.count || 8), !!args.near);
      else if (kind === 'build') result = await doBuild(entry, args.blueprint, args.slot || 0);
      else if (kind === 'mine') result = await doMine(entry, Math.min(32, args.count || 8), !!args.near);
      else if (kind === 'farm') {
        if (round) { for (let w = 0; w < 36 && !entry.cancel; w++) await sleep(5000); }   // 3분 기다렸다 다시
        if (entry.cancel) break;
        result = await doFarm(entry, Math.max(1, Math.min(8, args.plots || 1)), Math.max(0, Math.min(7, args.grow || 0)),
          Math.max(0, args.grow_min || 0));
      }
      else if (kind === 'ore') result = await doOre(entry, Math.min(16, args.count || 4));
      else if (kind === 'patrol') { entry.limitMs = Math.min(900, args.seconds || 300) * 1000; result = await doPatrol(entry, Math.min(900, args.seconds || 300)); }
      else if (kind === 'rest') { await goNear(entry.bot, VX, VZ, 4); result = {}; }
      else if (kind === 'audit') result = await doAudit(entry, args.slots || {});
      emit(name, 'done', { task: kind, result, round });
      const gained = Object.values(result).some((v) => typeof v === 'number' && v > 0);
      if (!repeat || entry.cancel) break;
      if (kind === 'farm' ? round >= 7 : (!gained || round >= 5)) break;
    }
  } catch (e) {
    emit(name, 'failed', { task: kind, error: String(e.message || e).slice(0, 200) });
  } finally {
    entry.task = null;
  }
}

// 지킴이: 한 바퀴(round)가 너무 오래 걸리면(순찰은 정해진 시간 + 2분, 그 밖은 12분) 그 일을 멈춘다 -
// 봇이 일하는 중인 채로 서 있어 새 일을 못 받는 것을 막는다 ('stuck' 이벤트 - poring_food가 교훈으로)
setInterval(() => {
  for (const [name, e] of bots) {
    if (!e.task || !e.roundAt || e.cancel) continue;
    const limit = e.task === 'patrol' ? (e.limitMs || 600000) + 120000 : 720000;
    if (Date.now() - e.roundAt > limit) {
      e.cancel = true;
      try { e.bot.pathfinder.stop(); e.bot.pvp.stop(); } catch (x) { /* 무시 */ }
      emit(name, 'stuck', { task: e.task, minutes: Math.round((Date.now() - e.roundAt) / 60000) });
    }
  }
}, 30000);

// ------------------------------------------------------------------ 접속
// 같은 IP에서 몇 초 안에 여러 명이 들어가면 서버가 "Connection throttled"로 쫓아낸다 - 한 명씩 6초 간격으로
const joinQueue = [];
let joining = false;
async function pumpJoins() {
  if (joining) return;
  joining = true;
  while (joinQueue.length) {
    const name = joinQueue.shift();
    if (!bots.has(name)) join(name);
    await sleep(6000);
  }
  joining = false;
}

function queueJoin(name) {
  if (bots.has(name) || joinQueue.includes(name)) return;
  joinQueue.push(name);
  pumpJoins();
}

function join(name) {
  if (bots.has(name)) return;
  const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username: name, version: MC_VERSION || undefined,
    auth: 'offline', viewDistance: BOT_VIEW });
  const entry = { bot, task: null, kills: 0, cancel: false };
  entry.ready = new Promise((resolve) => bot.once('spawn', resolve));   // 일은 들어간 뒤에 (접속 직후 받은 일은 기다린다)
  bots.set(name, entry);
  bot.loadPlugin(pathfinder);
  bot.loadPlugin(pvp);
  bot.loadPlugin(collectBlock);
  bot.once('spawn', () => {
    setCenter(bot);
    const moves = new Movements(bot);
    moves.canDig = true;
    bot.pathfinder.setMovements(moves);
    // 맨손으로는 돌이 안 나온다 - 개척지 공용 도구 (RCON으로 주니 OP가 아니어도 받는다)
    (async () => {
      if (!worldReady) {
        worldReady = true;
        // 쓰러져도 도구를 잃지 않게, 잠을 안 자는 봇에게 팬텀이 몰려오지 않게
        for (const g of ['keepInventory true', 'doInsomnia false']) await run(bot, `gamerule ${g}`);
      }
      for (const item of ['iron_pickaxe', 'iron_axe', 'iron_sword', 'bread 8']) await run(bot, `give @s ${item}`);
    })();
    emit(name, 'joined');
  });
  // 배고픔 (봇은 스스로 먹지 않는다) - 배가 고프면 포만감을 채워 굶어서 쓰러지거나 체력이 안 차는 일이 없게
  // 배가 고프면 마을 창고 식량(농부가 거둔 밀)을 먹는다 - 창고가 비었으면(rations false) 굶는다. 2분에 한 번까지
  entry.feeder = setInterval(() => {
    if (!bot.entity || typeof bot.food !== 'number' || bot.food >= 14 || !rations) return;
    if (Date.now() - (entry.ateAt || 0) < 120000) return;
    entry.ateAt = Date.now();
    run(bot, 'effect give @s saturation 2 1 true').then(() => emit(name, 'ate')).catch(() => {});
  }, 30000);
  bot.on('entityDead', (e) => {
    if (isHostile(e) && bot.pvp.target === e) { entry.kills++; emit(name, 'kill', { mob: e.name }); }
  });
  bot.on('death', () => {        // 한 번 쓰러질 때 신호가 여러 번 올 수 있다 - 1분에 한 번만 센다
    if (Date.now() - (entry.diedAt || 0) > 60000) { entry.diedAt = Date.now(); emit(name, 'died'); }
  });
  bot.on('kicked', (reason) => emit(name, 'kicked', { reason: String(reason).slice(0, 120) }));
  bot.on('error', (e) => emit(name, 'error', { error: String(e.message || e).slice(0, 120) }));
  bot.on('end', () => { clearInterval(entry.feeder); bots.delete(name); emit(name, 'left'); });
}

function leave(name) {
  const names = name === '*' ? [...bots.keys()] : [name];
  for (const n of names) {
    const entry = bots.get(n);
    if (entry) { entry.cancel = true; try { entry.bot.quit(); } catch (e) { /* 이미 끊김 */ } }
  }
}

function state() {
  const any = [...bots.values()][0];
  const t = any && any.bot.time ? any.bot.time.timeOfDay : null;
  return {
    day: t === null ? null : t < 12500 || t > 23500,
    time: t,
    bots: [...bots.entries()].map(([name, e]) => ({
      name, task: e.task, health: e.bot.health, food: e.bot.food,
      pos: e.bot.entity ? e.bot.entity.position.floored() : null,
    })),
  };
}

// ------------------------------------------------------------------ HTTP
function body(req) {
  return new Promise((resolve) => {
    let s = '';
    req.on('data', (c) => { s += c; if (s.length > 1e5) req.destroy(); });
    req.on('end', () => { try { resolve(JSON.parse(s || '{}')); } catch (e) { resolve({}); } });
  });
}

function send(res, code, obj) {
  res.writeHead(code, { 'Content-Type': 'application/json' });
  res.end(JSON.stringify(obj));
}

http.createServer(async (req, res) => {
  const url = new URL(req.url, 'http://x');
  try {
    if (req.method === 'GET' && url.pathname === '/health') {
      return send(res, 200, { ok: true, bots: [...bots.keys()], started: STARTED, rcon: rconStat,
        center: VX === null ? null : { x: VX, z: VZ },
        heapMB: Math.round(process.memoryUsage().heapUsed / 1048576) });
    }
    if (req.method === 'GET' && url.pathname === '/state') return send(res, 200, state());
    if (req.method === 'GET' && url.pathname === '/events') {
      const after = parseInt(url.searchParams.get('after') || '0', 10);
      return send(res, 200, { events: events.filter((e) => e.id > after), last: nextId - 1 });
    }
    if (req.method === 'POST') {
      const b = await body(req);
      const name = String(b.name || '');
      if (url.pathname === '/leave') { leave(name === '*' ? '*' : name); return send(res, 200, { ok: true }); }
      if (url.pathname === '/rations') { rations = !!b.ok; return send(res, 200, { ok: true, rations }); }
      if (url.pathname === '/raid') {
        const mob = /^[a-z_]{3,24}$/.test(String(b.mob || '')) ? b.mob : 'zombie';
        const result = await doRaid(Math.max(1, Math.min(12, b.count || 4)), mob);
        emit('*', 'raid', result);
        return send(res, 200, { ok: true, ...result });
      }
      if (url.pathname === '/say') {
        const e = bots.get(name);
        if (e && b.text) e.bot.chat(String(b.text).replace(/^\//, '').slice(0, 200));   // 명령(/)으로 시작하지 않게
        return send(res, 200, { ok: !!e });
      }
      if (!NAME_RE.test(name)) return send(res, 400, { error: '이름은 영문/숫자/_ 3~16자' });
      if (url.pathname === '/join') { queueJoin(name); return send(res, 200, { ok: true }); }
      if (url.pathname === '/task') {
        if (!['gather', 'mine', 'farm', 'ore', 'build', 'patrol', 'rest', 'audit'].includes(b.kind)) return send(res, 400, { error: '모르는 일' });
        if (!bots.has(name) && joinQueue.includes(name)) {
          // 아직 접속 차례를 기다리는 중 - 들어가면 바로 하게 잠깐 기다렸다가 맡긴다
          const until = Date.now() + 60000;
          while (!bots.has(name) && Date.now() < until) await sleep(500);
        }
        if (!bots.has(name)) return send(res, 409, { error: '접속 안 함' });
        console.log(`[${new Date().toISOString().slice(11, 19)}] ${name} 일 받음: ${b.kind} ${JSON.stringify(b.args || {})}`);
        runTask(name, b.kind, b.args || {}, b.say);
        return send(res, 202, { ok: true });
      }
    }
    send(res, 404, { error: 'not found' });
  } catch (e) {
    send(res, 500, { error: String(e.message || e) });
  }
}).listen(API_PORT, '0.0.0.0', () => console.log(`mc-agents API :${API_PORT} -> ${MC_HOST}:${MC_PORT}`));
