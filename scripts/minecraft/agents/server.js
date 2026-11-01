// 오아시스 마을 인물 봇 - mineflayer로 인물마다 한 명씩 접속해서 시킨 일(나무 모으기 / 마을 짓기 / 몬스터 순찰)을 한다.
// discord_bot_v2 poring_food/minecraft.py가 이 HTTP API로만 부른다 (127.0.0.1에만 열림).
//   GET  /health                  {ok, bots:[이름]}
//   GET  /state                   {day, time, bots:[{name, health, food, pos, task}]}
//   POST /join  {name}            접속 (이름은 영문 3~16자)
//   POST /leave {name|"*"}        나가기
//   POST /task  {name, kind, args, say}   kind: gather(args.count) / build(args.blueprint, args.slot) / patrol(args.seconds) / rest
//   GET  /events?after=N          [{id, at, name, kind, ...}] - 끝난 일, 잡은 몬스터, 죽음
// 건물은 마을 중심(VILLAGE_X/Z) 둘레의 자리(slot)에 /fill 명령으로 세운다 (봇은 OP - docker-compose OPS).
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
const VX = parseInt(process.env.VILLAGE_X || '0', 10);
const VZ = parseInt(process.env.VILLAGE_Z || '0', 10);
const NAME_RE = /^[A-Za-z0-9_]{3,16}$/;

const bots = new Map();      // 이름 -> {bot, task, kills}
const events = [];           // 최근 500개
let nextId = 1;

function emit(name, kind, data = {}) {
  events.push({ id: nextId++, at: new Date().toISOString(), name, kind, ...data });
  if (events.length > 500) events.splice(0, events.length - 500);
}

// ------------------------------------------------------------------ 건물 (마을 중심 기준 자리마다)
// 자리: 반지름 18 둘레에 8칸 (건물 종류마다 같은 자리 목록을 따로 쓴다 - slot은 poring_food가 센다)
function slotOrigin(blueprint, slot, groundY) {
  const ring = { hut: 18, farm: 26, tower: 34, wall: 40 }[blueprint] || 18;
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

async function goNear(bot, x, z, range = 3) {
  try {
    await bot.pathfinder.goto(new goals.GoalNearXZ(x, z, range));
  } catch (e) { /* 막혀도 근처에서 진행 */ }
}

// ------------------------------------------------------------------ 일
async function doGather(entry, count) {
  const bot = entry.bot;
  const logIds = ['oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log']
    .map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  let got = 0;
  for (let i = 0; i < count; i++) {
    if (entry.cancel) break;
    const block = bot.findBlock({ matching: logIds, maxDistance: 48 });
    if (!block) break;
    try { await bot.collectBlock.collect(block); got++; } catch (e) { break; }
  }
  return { logs: got };
}

async function doBuild(entry, blueprint, slot) {
  const bot = entry.bot;
  const plan = BLUEPRINTS[blueprint];
  if (!plan) throw new Error(`모르는 건물: ${blueprint}`);
  const probe = slotOrigin(blueprint, slot, 0);
  await goNear(bot, probe.x, probe.z, 4);
  const o = slotOrigin(blueprint, slot, groundY(bot, probe.x, probe.z));
  for (const cmd of plan(o)) {
    if (entry.cancel) break;
    bot.chat(`/${cmd}`);
    await sleep(400);
  }
  return { blueprint, slot, x: o.x, y: o.y, z: o.z };
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
    if (bot.food < 10) bot.chat('/effect give @s saturation 3 1 true');
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
  try {
    let result = {};
    if (kind === 'gather') result = await doGather(entry, Math.min(32, args.count || 8));
    else if (kind === 'build') result = await doBuild(entry, args.blueprint, args.slot || 0);
    else if (kind === 'patrol') result = await doPatrol(entry, Math.min(900, args.seconds || 300));
    else if (kind === 'rest') { await goNear(entry.bot, VX, VZ, 4); result = {}; }
    emit(name, 'done', { task: kind, result });
  } catch (e) {
    emit(name, 'failed', { task: kind, error: String(e.message || e).slice(0, 200) });
  } finally {
    entry.task = null;
  }
}

// ------------------------------------------------------------------ 접속
function join(name) {
  if (bots.has(name)) return;
  const bot = mineflayer.createBot({ host: MC_HOST, port: MC_PORT, username: name, version: MC_VERSION || undefined, auth: 'offline' });
  const entry = { bot, task: null, kills: 0, cancel: false };
  entry.ready = new Promise((resolve) => bot.once('spawn', resolve));   // 일은 들어간 뒤에 (접속 직후 받은 일은 기다린다)
  bots.set(name, entry);
  bot.loadPlugin(pathfinder);
  bot.loadPlugin(pvp);
  bot.loadPlugin(collectBlock);
  bot.once('spawn', () => {
    const moves = new Movements(bot);
    moves.canDig = true;
    bot.pathfinder.setMovements(moves);
    emit(name, 'joined');
  });
  bot.on('entityDead', (e) => {
    if (isHostile(e) && bot.pvp.target === e) { entry.kills++; emit(name, 'kill', { mob: e.name }); }
  });
  bot.on('death', () => emit(name, 'died'));
  bot.on('kicked', (reason) => emit(name, 'kicked', { reason: String(reason).slice(0, 120) }));
  bot.on('error', (e) => emit(name, 'error', { error: String(e.message || e).slice(0, 120) }));
  bot.on('end', () => { bots.delete(name); emit(name, 'left'); });
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
    if (req.method === 'GET' && url.pathname === '/health') return send(res, 200, { ok: true, bots: [...bots.keys()] });
    if (req.method === 'GET' && url.pathname === '/state') return send(res, 200, state());
    if (req.method === 'GET' && url.pathname === '/events') {
      const after = parseInt(url.searchParams.get('after') || '0', 10);
      return send(res, 200, { events: events.filter((e) => e.id > after), last: nextId - 1 });
    }
    if (req.method === 'POST') {
      const b = await body(req);
      const name = String(b.name || '');
      if (url.pathname === '/leave') { leave(name === '*' ? '*' : name); return send(res, 200, { ok: true }); }
      if (!NAME_RE.test(name)) return send(res, 400, { error: '이름은 영문/숫자/_ 3~16자' });
      if (url.pathname === '/join') { join(name); return send(res, 200, { ok: true }); }
      if (url.pathname === '/task') {
        if (!['gather', 'build', 'patrol', 'rest'].includes(b.kind)) return send(res, 400, { error: '모르는 일' });
        if (!bots.has(name)) return send(res, 409, { error: '접속 안 함' });
        runTask(name, b.kind, b.args || {}, b.say);
        return send(res, 202, { ok: true });
      }
    }
    send(res, 404, { error: 'not found' });
  } catch (e) {
    send(res, 500, { error: String(e.message || e) });
  }
}).listen(API_PORT, '0.0.0.0', () => console.log(`mc-agents API :${API_PORT} -> ${MC_HOST}:${MC_PORT}`));
