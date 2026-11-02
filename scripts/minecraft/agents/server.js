// 오아시스 마을 인물 봇 - mineflayer로 인물마다 한 명씩 접속해서 시킨 일(나무 모으기 / 마을 짓기 / 몬스터 순찰)을 한다.
// discord_bot_v2 poring_food/minecraft.py가 이 HTTP API로만 부른다 (127.0.0.1에만 열림).
//   GET  /health                  {ok, bots:[이름]}
//   GET  /state                   {day, time, bots:[{name, health, food, pos, task}]}
//   POST /join  {name}            접속 (이름은 영문 3~16자)
//   POST /leave {name|"*"}        나가기
//   POST /task  {name, kind, args, say}   kind: gather(args.count) / mine(args.count) / farm(args.plots) / ore(args.count) /
//                                         build(args.blueprint, args.slot) / patrol(args.seconds) / rest
//     결과(events done.result): gather {logs} / mine {stone} / farm {food} / ore {coal, iron} / build {blueprint, ...} / patrol {kills}
//     짓기는 세운 뒤 블록을 다시 확인해서 실제로 서 있을 때만 done (아니면 failed - 장부/연대기에 거짓이 안 남게)
//   POST /say   {name, text}          게임 채팅 한마디 (개척지 회의)
//   POST /raid  {count, mob}          몬스터 습격 - 마을 둘레에 몬스터 소환
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

async function goNear(bot, x, z, range = 3) {
  try {
    await bot.pathfinder.goto(new goals.GoalNearXZ(x, z, range));
  } catch (e) { /* 막혀도 근처에서 진행 */ }
}

// ------------------------------------------------------------------ 일
async function doGather(entry, count) {
  const bot = entry.bot;
  const logIds = ['oak_log', 'birch_log', 'spruce_log', 'jungle_log', 'acacia_log', 'dark_oak_log', 'cherry_log', 'mangrove_log']
    .map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 일부만 불러와도 진행 */ }
  let got = 0;
  let misses = 0;
  while (got < count && !entry.cancel && misses < 5) {
    const block = bot.findBlock({ matching: logIds, maxDistance: 64 });
    if (!block) {
      // 근처에 나무가 없으면 아무 쪽으로나 40칸 걸어가서 다시 찾는다 (최대 4번)
      misses++;
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goNear(bot, Math.round(p.x + 40 * Math.cos(a)), Math.round(p.z + 40 * Math.sin(a)), 4);
      try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
      continue;
    }
    try { await bot.collectBlock.collect(block); got++; } catch (e) { misses++; }
  }
  // 모은 통나무는 마을 창고로 (가방에서 빼서 장부로 - 실제 건물은 /fill)
  if (got) bot.chat(`/clear @s #minecraft:logs ${got}`);
  await goNear(bot, VX, VZ, 6);
  return { logs: got };
}

async function doMine(entry, count) {
  // 돌 캐기 - 땅 위나 얕은 곳의 돌을 캐서 마을 창고로 (조약돌로 셈)
  const bot = entry.bot;
  const ids = ['stone', 'cobblestone', 'andesite', 'diorite', 'granite', 'deepslate', 'tuff']
    .map((n) => bot.registry.blocksByName[n] && bot.registry.blocksByName[n].id).filter(Boolean);
  try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
  let got = 0;
  let misses = 0;
  while (got < count && !entry.cancel && misses < 5) {
    const block = bot.findBlock({ matching: ids, maxDistance: 32,
      useExtraInfo: (b) => b.position.y >= bot.entity.position.y - 6 });   // 너무 깊이는 안 판다
    if (!block) {
      misses++;
      const a = Math.random() * Math.PI * 2;
      const p = bot.entity.position;
      await goNear(bot, Math.round(p.x + 32 * Math.cos(a)), Math.round(p.z + 32 * Math.sin(a)), 4);
      continue;
    }
    try { await bot.collectBlock.collect(block); got++; } catch (e) { misses++; }
  }
  if (got) bot.chat(`/clear @s #minecraft:stone_tool_materials ${got}`);
  await goNear(bot, VX, VZ, 6);
  return { stone: got };
}

async function doFarm(entry, plots) {
  // 밭 수확 - 지은 밭(자리 0..plots-1)을 돌며 다 익은 밀(age=7)을 거두고 그 자리에 다시 심는다. 거둔 밀 한 포기 = 식량 1
  const bot = entry.bot;
  let food = 0;
  for (let slot = 0; slot < plots && !entry.cancel; slot++) {
    const probe = slotOrigin('farm', slot, 0);
    await goNear(bot, probe.x + 3, probe.z + 3, 4);
    try { await bot.waitForChunksToLoad(); } catch (e) { /* 계속 */ }
    const y = groundY(bot, probe.x + 1, probe.z + 1);
    for (let dx = 0; dx < 7; dx++) {
      for (let dz = 0; dz < 7; dz++) {
        const b = bot.blockAt(new Vec3(probe.x + dx, y + 1, probe.z + dz));
        if (b && b.name === 'wheat' && (b.getProperties().age === 7 || b.metadata === 7)) {
          bot.chat(`/setblock ${probe.x + dx} ${y + 1} ${probe.z + dz} wheat[age=0]`);
          food++;
          await sleep(120);
        }
      }
    }
    // 비어 버린 칸(밟혀 망가진 곳)은 다시 심는다
    bot.chat(`/fill ${probe.x} ${y + 1} ${probe.z} ${probe.x + 6} ${y + 1} ${probe.z + 6} wheat[age=0] replace air`);
    bot.chat(`/setblock ${probe.x + 3} ${y + 1} ${probe.z + 3} air`);
    await sleep(400);
  }
  await goNear(bot, VX, VZ, 6);
  return { food };
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
    try { await bot.collectBlock.collect(block); got[kind]++; } catch (e) { misses++; }
  }
  if (got.coal) bot.chat(`/clear @s coal ${got.coal}`);
  if (got.iron) bot.chat(`/clear @s raw_iron ${got.iron}`);
  await goNear(bot, VX, VZ, 6);
  return got;
}

async function doRaid(count, mob) {
  // 몬스터 습격 - 마을 둘레(반지름 30) 여기저기에 몬스터가 나타난다 (서버 부하 급등 = 결계가 얇아진 틈)
  const any = [...bots.values()][0];
  if (!any) throw new Error('개척지에 아무도 없음');
  for (let i = 0; i < count; i++) {
    const a = (2 * Math.PI * i) / count;
    const x = Math.round(VX + 30 * Math.cos(a));
    const z = Math.round(VZ + 30 * Math.sin(a));
    any.bot.chat(`/execute positioned ${x} 0 ${z} positioned over motion_blocking_no_leaves run summon ${mob} ~ ~ ~`);
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
    bot.chat(`/${cmd}`);
    await sleep(400);
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
    else if (kind === 'mine') result = await doMine(entry, Math.min(32, args.count || 8));
    else if (kind === 'farm') result = await doFarm(entry, Math.max(1, Math.min(8, args.plots || 1)));
    else if (kind === 'ore') result = await doOre(entry, Math.min(16, args.count || 4));
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
    // 맨손으로는 돌이 안 나온다 - 개척지 공용 도구 (봇은 OP)
    for (const item of ['iron_pickaxe', 'iron_axe', 'iron_sword', 'bread 8']) bot.chat(`/give @s ${item}`);
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
        if (!['gather', 'mine', 'farm', 'ore', 'build', 'patrol', 'rest'].includes(b.kind)) return send(res, 400, { error: '모르는 일' });
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
