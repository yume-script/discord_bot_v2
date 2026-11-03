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
//   GET  /events?after=N          [{id, at, name, kind, ...}] - 끝난 일, 잡은 몬스터, 죽음, 게임 채팅(chat {from, text})
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
let VY = null;   // 마을 바닥 높이 (스폰 발밑) - 창고처럼 한 높이로 맞춰야 하는 것에 쓴다

function setCenter(bot) {
  if (VX !== null) return;
  const p = bot.spawnPoint && (bot.spawnPoint.x || bot.spawnPoint.z) ? bot.spawnPoint : bot.entity.position;
  VX = Math.round(p.x);
  VZ = Math.round(p.z);
  VY = Math.round(p.y) - 1;
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
// 이벤트 번호는 시각(ms)에서 시작 - 봇 서비스를 다시 띄워도 번호가 1로 돌아가지 않게.
// (예전엔 다시 띄울 때마다 1부터 다시 세서, poring_food가 "이미 본 번호"로 여기고 결과를 전부 버렸다 -
//  나무/돌을 캐도 장부가 늘지 않아 같은 일만 되풀이한 원인)
let nextId = Date.now();

function emit(name, kind, data = {}) {
  events.push({ id: nextId++, at: new Date().toISOString(), name, kind, ...data });
  console.log(`[${new Date().toISOString().slice(11, 19)}] ${name} ${kind} ${Object.keys(data).length ? JSON.stringify(data) : ''}`);   // docker logs mc-agents 로 보이게
  if (events.length > 500) events.splice(0, events.length - 500);
}

// ------------------------------------------------------------------ 공동 창고 (Inventory / Logistics)
// 마을 중심 옆 상자 STORAGE_CHESTS개가 공동 창고다. 모은 자원은 봇이 창고까지 걸어와 넣고(봇 가방에서 빼서 상자로),
// 짓기/먹기는 창고에서 꺼내 쓴다. 장부는 상자 안 실제 아이템을 읽어서 만든다 (GET /storage) - "실제 게임 상태로 검증".
// 상자 읽기/넣기/빼기는 RCON(data get block / item replace)으로 한다 (봇이 상자 UI를 여는 것보다 확실하다).
const STORAGE_CHESTS = 4;
let storage = null;           // [{x,y,z}] 상자 위치
const CATEGORY = {            // 장부 자원 -> 아이템
  logs: /_log$|_wood$|_stem$/,
  stone: /^(cobblestone|cobbled_deepslate|stone|andesite|diorite|granite|tuff|deepslate|blackstone)$/,
  food: /^(wheat|bread)$/,
  coal: /^(coal|charcoal)$/,
  iron: /^(raw_iron|iron_ingot)$/,
};
function categoryOf(id) { return Object.keys(CATEGORY).find((k) => CATEGORY[k].test(id)) || null; }

let storageBusy = null;       // 여러 봇이 동시에 창고를 만들지 않게 (한 번에 하나)
async function ensureStorage(bot) {
  if (storage) return storage;
  if (storageBusy) return storageBusy;
  storageBusy = makeStorage(bot).finally(() => { storageBusy = null; });
  return storageBusy;
}

async function makeStorage(bot) {
  // 상자 자리: 마을 중심 북쪽 (VX+2.., VZ-4) 한 줄. 높이는 마을 중심 땅 높이 하나로 맞추고(나무 위에 뜨지 않게),
  // 그 자리에 돌바닥을 깔고 위를 비운 뒤 상자를 놓는다. 이미 상자가 있으면 그대로 쓴다.
  // 청크가 아직 안 보이면 아무것도 하지 않는다 (예전엔 안 보이는 걸 "상자 없음"으로 보고 다른 높이에 또 놓았다)
  if (storage) return storage;
  const base = { x: VX + 2, z: VZ - 4 };
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
  for (let i = 0; i < STORAGE_CHESTS; i++) {
    if (!bot.blockAt(new Vec3(base.x + i * 2, 64, base.z))) throw new Error('창고 자리 청크가 아직 안 보임');
  }
  const found = [];
  for (let i = 0; i < STORAGE_CHESTS; i++) {
    const x = base.x + i * 2;
    for (let yy = 120; yy > -40; yy--) {
      const b = bot.blockAt(new Vec3(x, yy, base.z));
      if (b && b.name === 'chest') { found.push({ x, y: yy, z: base.z }); break; }
    }
  }
  if (found.length === STORAGE_CHESTS && new Set(found.map((c) => c.y)).size === 1) {
    storage = found;
  } else {
    // 한 높이로 새로 만든다 (흩어진 예전 상자가 있으면 그 안 물건은 새 상자로 옮긴다)
    const y = VY !== null ? VY : groundY(bot, VX, VZ);
    await run(bot, `fill ${base.x - 1} ${y} ${base.z - 1} ${base.x + STORAGE_CHESTS * 2 - 1} ${y} ${base.z + 1} stone_bricks`);
    await run(bot, `fill ${base.x - 1} ${y + 1} ${base.z - 1} ${base.x + STORAGE_CHESTS * 2 - 1} ${y + 4} ${base.z + 1} air`
      + ' replace #minecraft:replaceable');
    const old = found.filter((c) => c.y !== y + 1);
    const moved = [];
    for (const c of old) moved.push(...await readChest(c));
    for (const c of old) await run(bot, `setblock ${c.x} ${c.y} ${c.z} air`);   // 상자를 치우면 안 물건이 바닥에 떨어지니 먼저 읽어 둔다
    for (const c of old) await run(bot, `kill @e[type=item,x=${c.x},y=${c.y},z=${c.z},distance=..3]`);
    const chests = [];
    for (let i = 0; i < STORAGE_CHESTS; i++) {
      const x = base.x + i * 2;
      for (let yy = y + 1; yy <= y + 4; yy++) await run(bot, `setblock ${x} ${yy} ${base.z} air`);
      await run(bot, `setblock ${x} ${y + 1} ${base.z} chest[facing=south]`);
      chests.push({ x, y: y + 1, z: base.z });
    }
    storage = chests;
    for (const it of moved) await putIntoStorage(it.id, it.count);
  }
  console.log(`공동 창고 = ${storage.map((c) => `(${c.x},${c.y},${c.z})`).join(' ')}`);
  return storage;
}

function parseItems(text) {
  // "... has the following block data: [{count: 12, Slot: 0b, id: "minecraft:oak_log"}, ...]"
  const items = [];
  for (const m of String(text || '').matchAll(/\{([^{}]*)\}/g)) {
    const id = /id: "minecraft:([a-z0-9_]+)"/.exec(m[1]);
    const slot = /Slot: (\d+)b/.exec(m[1]);
    const count = /count: (\d+)/.exec(m[1]);
    if (id && slot) items.push({ id: id[1], slot: parseInt(slot[1], 10), count: count ? parseInt(count[1], 10) : 1 });
  }
  return items;
}

async function readChest(c) {
  const t = await rcon(`data get block ${c.x} ${c.y} ${c.z} Items`);
  // 창고 청크가 안 불러와졌으면 "비었다"가 아니라 "모른다" (예전엔 0개로 읽혀 재료가 있는데도 부족으로 실패)
  if (/not loaded/i.test(t)) throw new Error('창고 청크가 안 불러와짐');
  if (/No block entity|not a block entity|Found no elements/i.test(t)) return [];
  return parseItems(t);
}

async function storageCounts() {
  const counts = { logs: 0, stone: 0, food: 0, coal: 0, iron: 0 };
  if (!storage || !RCON_PASS) return null;
  for (const c of storage) {
    let items;
    try { items = await readChest(c); } catch (e) { return null; }      // 못 읽으면 모름 (장부를 그대로 둔다)
    for (const it of items) {
      const k = categoryOf(it.id);
      if (k) counts[k] += it.id === 'bread' ? it.count * 3 : it.count;   // 빵 1 = 밀 3
    }
  }
  return counts;
}

async function putIntoStorage(id, count) {
  // 상자 빈 칸(또는 같은 아이템이 덜 찬 칸)에 넣는다. 다 못 넣으면 넣은 만큼 반환
  let left = count;
  for (const c of storage || []) {
    if (left <= 0) break;
    const items = await readChest(c);
    for (const it of items.filter((x) => x.id === id && x.count < 64)) {
      const add = Math.min(left, 64 - it.count);
      await rcon(`item replace block ${c.x} ${c.y} ${c.z} container.${it.slot} with minecraft:${id} ${it.count + add}`);
      left -= add;
      if (left <= 0) break;
    }
    const used = new Set(items.map((x) => x.slot));
    for (let slot = 0; slot < 27 && left > 0; slot++) {
      if (used.has(slot)) continue;
      const n = Math.min(64, left);
      await rcon(`item replace block ${c.x} ${c.y} ${c.z} container.${slot} with minecraft:${id} ${n}`);
      left -= n;
    }
  }
  return count - left;
}

async function takeFromStorage(category, count) {
  // 그 종류를 count만큼 꺼낸다 (모자라면 아무것도 안 꺼내고 false). 식량은 밀부터
  const all = [];
  for (const c of storage || []) for (const it of await readChest(c)) if (categoryOf(it.id) === category) all.push({ c, ...it });
  const unit = (it) => (it.id === 'bread' ? 3 : 1);
  if (all.reduce((a, it) => a + it.count * unit(it), 0) < count) return false;
  let left = count;
  for (const it of all.sort((a, b) => unit(a) - unit(b))) {
    if (left <= 0) break;
    const use = Math.min(it.count, Math.ceil(left / unit(it)));
    const rest = it.count - use;
    await rcon(`item replace block ${it.c.x} ${it.c.y} ${it.c.z} container.${it.slot} with `
      + (rest > 0 ? `minecraft:${it.id} ${rest}` : 'air'));
    left -= use * unit(it);
  }
  return true;
}

async function depositAll(bot, category) {
  // 봇이 창고로 걸어가 가방의 그 종류 아이템을 상자에 넣는다 (물류). 넣은 개수 반환
  if (!storage) await ensureStorage(bot);
  const c = storage[0];
  await goNear(bot, c.x, c.z, 3);
  let moved = 0;
  const byId = {};
  for (const it of bot.inventory.items()) if (categoryOf(it.name) === category) byId[it.name] = (byId[it.name] || 0) + it.count;
  for (const [id, n] of Object.entries(byId)) {
    await run(bot, `clear @s ${id} ${n}`);
    moved += await putIntoStorage(id, n);
  }
  return moved;
}

// ------------------------------------------------------------------ 장비 (Equipment)
async function ensureTools(bot) {
  // 일 시작 전에 곡괭이/도끼/검이 있는지 본다 - 없거나 거의 닳았으면 새로 (공용 도구)
  const have = {};
  for (const it of bot.inventory.items()) {
    const max = it.maxDurability || 0;
    const used = it.durabilityUsed || 0;
    if (!max || used < max * 0.9) have[it.name] = true;
  }
  for (const tool of ['iron_pickaxe', 'iron_axe', 'iron_sword']) if (!have[tool]) await run(bot, `give @s ${tool}`);
  // 발판용 흙 - 구덩이/물에서 쌓아 올라올 때 쓴다 (창고로 가는 돌과 섞이지 않게 흙)
  const dirt = bot.inventory.items().filter((it) => it.name === 'dirt').reduce((a, it) => a + it.count, 0);
  if (dirt < 16) await run(bot, `give @s dirt ${32 - dirt}`);
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

// ------------------------------------------------------------------ 이동 (V6 30.3: 이동 재시도 2~3회, 3회 실패 시 목적지 포기)
// 같은 목적지(8칸 격자)로 3번 못 가면 30분 동안 그 목적지를 포기한다 - 같은 곳을 계속 두드리다 갇히지 않게.
// 반환: 도착했는지
const moveFails = new Map();     // 목적지 키 -> {n, until}
function targetKey(x, z) { return `${Math.round(x / 8)},${Math.round(z / 8)}`; }
function givenUp(x, z) {
  const f = moveFails.get(targetKey(x, z));
  return !!(f && f.until && f.until > Date.now());
}

async function goNear(bot, x, z, range = 3) {
  const key = targetKey(x, z);
  if (givenUp(x, z) || inDanger({ x, y: bot.entity ? bot.entity.position.y : 64, z })) return false;
  for (let attempt = 0; attempt < 2; attempt++) {           // 한 번 부를 때 최대 두 번 시도
    try {
      await withTimeout(bot.pathfinder.goto(new goals.GoalNearXZ(x, z, range)), 90000, () => bot.pathfinder.stop());
    } catch (e) { /* 아래에서 거리로 판단 */ }
    const p = bot.entity && bot.entity.position;
    if (p && Math.hypot(p.x - x, p.z - z) <= range + 2) { moveFails.delete(key); return true; }
  }
  const f = moveFails.get(key) || { n: 0 };
  f.n += 1;
  if (f.n >= 3) {
    f.until = Date.now() + 30 * 60000;
    f.n = 0;
    emit(bot.username, 'gave_up', { x: Math.round(x), z: Math.round(z) });
  }
  moveFails.set(key, f);
  return false;
}

// ------------------------------------------------------------------ 위험 지역 (V6 29.3/22: 낙하·용암·익사, 사망 위치)
// 쓰러진 곳, 갇혀서 구조한 곳은 위험 지역으로 둔다 (반지름 10, 24시간) - 그쪽으로는 길을 찾지 않고 자원도 캐지 않는다.
// poring_food가 장부에 모아 두고 바퀴마다 POST /danger로 다시 알려 준다 (봇 서비스를 다시 띄워도 기억).
let dangerZones = [];            // [{x,y,z,r,why,until}]
function inDanger(p) {
  const now = Date.now();
  return dangerZones.some((d) => (!d.until || d.until > now) && Math.hypot(p.x - d.x, p.z - d.z) <= (d.r || 10)
    && Math.abs((p.y || d.y) - d.y) <= 16);
}
function addDanger(pos, why) {
  if (!pos) return;
  if (VX !== null && Math.hypot(pos.x - VX, pos.z - VZ) < 24) return;   // 마을 안은 위험 지역으로 막지 않는다 (지켜야 할 곳)
  dangerZones.push({ x: Math.round(pos.x), y: Math.round(pos.y), z: Math.round(pos.z), r: 10, why,
    until: Date.now() + 24 * 3600000 });
  dangerZones = dangerZones.slice(-30);
}

function safeMovements(bot) {
  // 길찾기 위험 회피: 용암/불/마그마/선인장은 피하고, 4칸 넘게 뛰어내리지 않고, 물은 되도록 돌아간다,
  // 위험 지역을 지나는 길은 비싸게
  const moves = new Movements(bot);
  moves.canDig = true;
  moves.allow1by1towers = true;       // 구덩이에선 발밑에 흙을 쌓아 올라온다 (흙은 ensureTools가 챙긴다)
  moves.allowSprinting = true;
  moves.maxDropDown = 3;
  moves.infiniteLiquidDropdownDistance = false;
  moves.liquidCost = 8;
  for (const n of ['lava', 'fire', 'soul_fire', 'magma_block', 'cactus', 'sweet_berry_bush', 'powder_snow', 'campfire']) {
    const b = bot.registry.blocksByName[n];
    if (b && moves.blocksToAvoid) moves.blocksToAvoid.add(b.id);
  }
  if (Array.isArray(moves.exclusionAreasStep)) {
    moves.exclusionAreasStep.push((block) => (block && block.position && inDanger(block.position) ? 50 : 0));
  }
  return moves;
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
    const block = bot.findBlock({ matching: logIds, maxDistance: near ? 32 : 64,
      useExtraInfo: (b) => !inDanger(b.position) && !givenUp(b.position.x, b.position.z) });
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
  if (got) forest = bot.entity.position.floored();          // 다음에 나무 베러 갈 곳
  const stored = await depositAll(bot, 'logs');               // 창고로 운반 (실제로 상자에 들어간 수)
  return { logs: stored, cut: got, near: !!near };
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
      useExtraInfo: (b) => b.position.y >= bot.entity.position.y - 6 && !inDanger(b.position)
        && !givenUp(b.position.x, b.position.z) });   // 너무 깊이는 안 판다
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
  const stored = await depositAll(bot, 'stone');
  return { stone: stored, cut: got };
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
  // 거둔 밀은 창고로 (밀 한 포기 = 밀 1)
  const stored = food ? (await ensureStorage(bot), await putIntoStorage('wheat', food)) : 0;
  await goNear(bot, VX, VZ, 6);
  return { food: stored, crops, rebuilt };
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
    // 광석은 용암 옆이면 캐지 않는다 (둘레 2칸에 용암이 있으면 건너뜀)
    const block = bot.findBlock({ matching: ids, maxDistance: 48, useExtraInfo: (b) => !inDanger(b.position)
      && !givenUp(b.position.x, b.position.z) && !nearLava(bot, b.position) });
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
  const coal = got.coal ? await depositAll(bot, 'coal') : 0;
  const iron = got.iron ? await depositAll(bot, 'iron') : 0;
  return { coal, iron };
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

// 나무가 모자라 돌로 지을 때 (material=stone): 나무 블록을 돌 블록으로 바꿔 놓는다 (문은 그대로)
const STONE_SWAP = [[/\b(oak|spruce|dark_oak)_planks\b/g, 'cobblestone'], [/\b(oak|spruce|dark_oak)_slab\b/g, 'cobblestone_slab'],
  [/\boak_fence\b/g, 'cobblestone_wall'], [/\b(oak|spruce)_log\b/g, 'stone_bricks']];

function withMaterial(cmd, material) {
  if (material !== 'stone') return cmd;
  return STONE_SWAP.reduce((c, [re, to]) => c.replace(re, to), cmd);
}

async function doBuild(entry, blueprint, slot, cost, material, alts) {
  const bot = entry.bot;
  const plan = BLUEPRINTS[blueprint];
  if (!plan) throw new Error(`모르는 건물: ${blueprint}`);
  // 재료는 공동 창고에서 꺼낸다 - 다 있을 때만 (없으면 짓지 않고 실패: 재료 부족)
  if (cost && RCON_PASS) {
    await ensureStorage(bot);
    let have = await storageCounts();
    if (!have) {                                     // 창고 청크가 안 보이면 창고로 걸어가서 다시 읽는다
      await goNear(bot, storage[0].x, storage[0].z, 3);
      try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
      have = await storageCounts();
      if (!have) throw new Error('창고를 못 읽음 (청크)');
    }
    // 정해 준 재료가 모자라면 다른 방법(alts: 나무 대신 돌 등)으로 - 실제 상자 기준
    const options = [{ cost, material }, ...(alts || [])];
    const pick = options.find((o) => Object.entries(o.cost).every(([k, n]) => (have[k] || 0) >= n));
    if (!pick) {
      const [k, n] = Object.entries(cost).find(([k2, n2]) => (have[k2] || 0) < n2);
      throw new Error(`창고에 ${k} 부족 (${have[k] || 0}/${n})`);
    }
    cost = pick.cost;
    material = pick.material;
    for (const [k, n] of Object.entries(cost)) await takeFromStorage(k, n);
  }
  const probe = slotOrigin(blueprint, slot, 0);
  await goNear(bot, probe.x, probe.z, 4);
  const o = slotOrigin(blueprint, slot, groundY(bot, probe.x, probe.z));
  for (const cmd of plan(o)) {
    if (entry.cancel) throw new Error('짓다가 멈춤');
    await run(bot, withMaterial(cmd, material));
    await sleep(RCON_PASS ? 100 : 400);
  }
  // 검증: 모든 건물은 (o.x, o.y+1, o.z)에 블록이 있다 - 정말 서 있는지 확인 (청크가 안 보이면 확인 못 함 = 믿는다)
  await sleep(800);
  const check = bot.blockAt(new Vec3(o.x, o.y + 1, o.z));
  if (check && check.name === 'air') throw new Error('명령은 보냈지만 건물이 서 있지 않음 (권한/위치 확인)');
  return { blueprint, slot, x: o.x, y: o.y, z: o.z, verified: !!check, cost, material: material || '' };
}

function nearLava(bot, pos) {
  for (let dx = -2; dx <= 2; dx++) for (let dy = -2; dy <= 2; dy++) for (let dz = -2; dz <= 2; dz++) {
    const b = bot.blockAt(pos.offset(dx, dy, dz));
    if (b && (b.name === 'lava' || b.name === 'flowing_lava')) return true;
  }
  return false;
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
  try { await ensureTools(entry.bot); await ensureStorage(entry.bot); } catch (e) { /* 다음에 */ }
  // repeat: 모으는 일(gather/mine/ore)은 다음 일을 받을 때까지 이어서 한다 (한 번 끝날 때마다 done) -
  // 일을 10분마다 나눠 주므로 금방 끝나면 남는 시간 동안 놀던 것을 없앤다. 빈손이면 멈춘다 (같은 헛수고 반복 X)
  // 농부(farm repeat)는 밭을 돌고 잠깐 기다렸다가(밀이 자라게) 다시 돈다 - 빈손이어도 계속 (밀은 자라는 중)
  const repeat = args.repeat && ['gather', 'mine', 'ore', 'farm'].includes(kind);
  try {
    for (let round = 0; ; round++) {
      entry.roundAt = Date.now();
      let result = {};
      if (kind === 'gather') result = await doGather(entry, Math.min(32, args.count || 8), !!args.near);
      else if (kind === 'build') result = await doBuild(entry, args.blueprint, args.slot || 0, args.cost, args.material, args.alts);
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
    // 모으는 일인데 3분 넘게 제자리면 (길이 막힘/구덩이) 멈춘다
    if (['gather', 'mine', 'ore'].includes(e.task) && e.bot.entity) {
      const p = e.bot.entity.position;
      if (!e.lastPos || p.distanceTo(e.lastPos) > 1.5) { e.lastPos = p.clone(); e.lastMoved = Date.now(); }
      else if (Date.now() - (e.lastMoved || Date.now()) > 180000) {
        e.cancel = true;
        try { e.bot.pathfinder.stop(); } catch (x) { /* 무시 */ }
        emit(name, 'stuck', { task: e.task, minutes: 3, why: '제자리' });
        e.lastMoved = Date.now();
        continue;
      }
    }
    const limit = e.task === 'patrol' ? (e.limitMs || 600000) + 120000 : 720000;
    if (Date.now() - e.roundAt > limit) {
      e.cancel = true;
      try { e.bot.pathfinder.stop(); e.bot.pvp.stop(); } catch (x) { /* 무시 */ }
      emit(name, 'stuck', { task: e.task, minutes: Math.round((Date.now() - e.roundAt) / 60000) });
    }
  }
}, 30000);

// ------------------------------------------------------------------ 구조 (갇힘 대비)
// 깊은 웅덩이/동굴/물속에 갇혀 못 움직이면 하던 일을 멈추고 마을 광장으로 데려온다 (RCON tp - 봇이 OP가 아니어도 됨).
//   - 물속 60초 넘게, 또는 숨(산소)이 거의 다 떨어짐 -> 바로
//   - 마을 바닥보다 12칸 넘게 아래에서 3분 넘게 제자리 (광석 캐기 중엔 5분) -> 동굴/구덩이
//   - 일하는 중인데 3분 넘게 제자리, 마을에서 20칸 넘게 떨어짐 -> 막힌 곳
//   - 일이 없는데 마을에서 80칸 넘게 떨어져 2분 넘게 있음 -> 길을 잃음
// 데려온 뒤엔 'rescued' 이벤트 (poring_food가 다음 일을 바로 다시 정하고, 같은 일이 되풀이되면 교훈으로)
const RESCUE_COOLDOWN = 120000;

async function rescue(name, e, why) {
  if (Date.now() - (e.rescuedAt || 0) < RESCUE_COOLDOWN || VX === null) return;
  e.rescuedAt = Date.now();
  e.cancel = true;
  try { e.bot.pathfinder.stop(); e.bot.pvp.stop(); e.bot.stopDigging(); } catch (x) { /* 무시 */ }
  const y = (VY !== null ? VY : Math.floor(e.bot.entity.position.y)) + 1;
  const from = e.bot.entity ? e.bot.entity.position.floored() : null;
  try {
    await run(e.bot, `tp @s ${VX} ${y} ${VZ + 2}`);
    await run(e.bot, 'effect give @s regeneration 10 1 true');
  } catch (x) { /* 다음에 */ }
  if (from) addDanger(from, why);
  e.lastPos = null;
  e.waterSince = null;
  e.idleFarSince = null;
  emit(name, 'rescued', { why, task: e.task, from: from && { x: from.x, y: from.y, z: from.z } });
}

setInterval(() => {
  for (const [name, e] of bots) {
    const b = e.bot;
    if (!b.entity || VX === null) continue;
    const p = b.entity.position;
    // 물속 / 숨 막힘
    if (b.entity.isInWater) e.waterSince = e.waterSince || Date.now(); else e.waterSince = null;
    if (typeof b.oxygenLevel === 'number' && b.oxygenLevel < 6) { rescue(name, e, '숨이 막힘'); continue; }
    if (e.waterSince && Date.now() - e.waterSince > 60000) { rescue(name, e, '물속에 갇힘'); continue; }
    // 제자리 (일하는 중)
    if (!e.rLast || p.distanceTo(e.rLast) > 1.5) { e.rLast = p.clone(); e.rMoved = Date.now(); }
    const still = Date.now() - (e.rMoved || Date.now());
    const deep = VY !== null && p.y < VY - 12;
    const far = Math.hypot(p.x - VX, p.z - VZ);
    if (deep && still > (e.task === 'ore' ? 300000 : 180000)) { rescue(name, e, '깊은 구덩이/동굴에 갇힘'); continue; }
    // 정체 의심 60초 (V6 30.3): 일하는 중 60초 제자리면 길찾기를 다시 하고 살짝 움직여 본다 - 세 번째면 구조
    if (e.task && !['farm', 'rest', 'build'].includes(e.task) && still > 60000) {
      e.nudges = (e.nudges || 0) + 1;
      e.rMoved = Date.now();
      if (e.nudges >= 3 && far > 20) { e.nudges = 0; rescue(name, e, '막힌 곳에 갇힘'); continue; }
      try {
        b.pathfinder.stop();
        b.setControlState('jump', true);
        b.setControlState('back', true);
        setTimeout(() => { b.setControlState('jump', false); b.setControlState('back', false); }, 1200);
      } catch (x) { /* 무시 */ }
      emit(name, 'stall', { task: e.task, n: e.nudges });
      continue;
    }
    if (still < 15000) e.nudges = 0;
    // 길을 잃음 (일이 없는데 멀리)
    if (!e.task && far > 80) {
      e.idleFarSince = e.idleFarSince || Date.now();
      if (Date.now() - e.idleFarSince > 120000) rescue(name, e, '길을 잃음');
    } else e.idleFarSince = null;
  }
}, 15000);

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
    bot.pathfinder.setMovements(safeMovements(bot));
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
  entry.feeder = setInterval(async () => {
    if (!bot.entity || typeof bot.food !== 'number' || bot.food >= 14) return;
    if (Date.now() - (entry.ateAt || 0) < 120000) return;
    entry.ateAt = Date.now();
    try {
      // 공동 창고에서 밀 3(=빵 1)을 꺼내 먹는다 - 창고가 비면 굶는다 (RCON이 없으면 예전처럼 배급 신호로)
      const ok = RCON_PASS && storage ? await takeFromStorage('food', 3) : rations;
      if (!ok) { emit(name, 'hungry'); return; }
      await run(bot, 'effect give @s saturation 2 1 true');
      emit(name, 'ate');
    } catch (e) { /* 다음에 */ }
  }, 30000);
  // 게임 채팅 -> poring_food (봇끼리 말은 빼고, 여러 봇이 같은 말을 들으니 맨 앞 봇 하나만 전한다)
  bot.on('chat', (username, message) => {
    if (!username || bots.has(username) || username === bot.username) return;
    if ([...bots.keys()][0] !== name) return;
    emit(name, 'chat', { from: String(username).slice(0, 32), text: String(message).slice(0, 200) });
  });
  bot.on('entityDead', (e) => {
    if (isHostile(e) && bot.pvp.target === e) { entry.kills++; emit(name, 'kill', { mob: e.name }); }
  });
  bot.on('death', () => {        // 한 번 쓰러질 때 신호가 여러 번 올 수 있다 - 1분에 한 번만 센다
    if (Date.now() - (entry.diedAt || 0) > 60000) {
      entry.diedAt = Date.now();
      const p = bot.entity ? bot.entity.position.floored() : null;    // 사망 위치 기록 (V6 22.4)
      if (p) addDanger(p, '쓰러진 곳');
      emit(name, 'died', { task: entry.task, pos: p && { x: p.x, y: p.y, z: p.z } });
    }
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
    if (req.method === 'GET' && url.pathname === '/storage') {
      // 공동 창고 실제 재고 (상자 안 아이템) - poring_food 장부의 기준
      const any = [...bots.values()][0];
      if (!storage && any && any.bot.entity && VX !== null) await ensureStorage(any.bot);
      const counts = await storageCounts();
      if (!counts) return send(res, 503, { error: '창고를 아직 못 읽음 (봇이 없거나 RCON 없음)' });
      return send(res, 200, { ok: true, counts, chests: storage });
    }
    if (req.method === 'GET' && url.pathname === '/events') {
      const after = parseInt(url.searchParams.get('after') || '0', 10);
      return send(res, 200, { events: events.filter((e) => e.id > after), last: nextId - 1 });
    }
    if (req.method === 'POST') {
      const b = await body(req);
      const name = String(b.name || '');
      if (url.pathname === '/leave') { leave(name === '*' ? '*' : name); return send(res, 200, { ok: true }); }
      if (url.pathname === '/rations') { rations = !!b.ok; return send(res, 200, { ok: true, rations }); }
      if (url.pathname === '/danger') {
        // poring_food가 기억하는 위험 지역 (봇 서비스를 다시 띄워도 이어지게)
        const zones = Array.isArray(b.zones) ? b.zones.slice(-30) : [];
        dangerZones = zones.filter((z) => Number.isFinite(z.x) && Number.isFinite(z.z))
          .map((z) => ({ x: z.x, y: z.y || 64, z: z.z, r: z.r || 10, why: String(z.why || '').slice(0, 40),
            until: z.until ? Date.parse(z.until) || 0 : 0 }));
        return send(res, 200, { ok: true, zones: dangerZones.length });
      }
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
