// @local/dsh-bullet —— 弹幕姬桥
//
// 职责只有一件事：把宿主里发生的事，压成一行行可读的状态写进
//   ~/.dsh/bullet/state.json
// 供独立的浮窗进程（src/overlay.py）读取渲染。浮窗不碰 DSH，DSH 不碰 UI。
//
// 事件来源（已实测，见 docs/事件地图.md）：
//   turn/start · step/start · assistant/live-chunk · assistant/message
//   tool/call · tool/result · turn/end · session/title
//
// 设计红线：本插件**绝不抛异常**。任何 UI/side-channel 的失败都不能影响会话。

import { appendFileSync, existsSync, mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { spawn } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { createSocket } from 'node:dgram';
import { homedir } from 'node:os';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'bullet-bridge';
export const inject = [];

// 项目根 = 本文件所在目录（plugin/）的上一级。别硬编码盘符，clone 到哪都能跑。
const PROJECT_ROOT = dirname(dirname(fileURLToPath(import.meta.url)));

// BULLET_DIR 只给自检用：让测试不污染真实运行目录
const DIR = process.env.BULLET_DIR || join(homedir(), '.dsh', 'bullet');
const STATE_PATH = join(DIR, 'state.json');
const DEBUG_PATH = join(DIR, 'debug.jsonl');
const CONFIG_PATH = join(DIR, 'config.json');
const PIDS_PATH = join(DIR, 'pids.json');   // {overlay: pid, inputbar: pid}
const PORT_PATH = join(DIR, 'port.txt');

const WRITE_INTERVAL_MS = 100;   // 50Hz 的增量事件压到 10Hz 写盘
const LINGER_MS = 4000;          // 回合结束后"完成"再停留一会儿
const THOUGHT_MAX = 200;
const SAID_MAX = 200;

// ── 状态 ───────────────────────────────────────────────────────────
const state = {
  v: 1,
  seq: 0,
  updatedAt: 0,
  status: 'idle',      // idle | thinking | tool | done
  sessionId: '',
  title: '',
  turn: 0,
  step: 0,
  thought: '',
  said: '',
  tool: '',
  toolDetail: '',
  notice: '',        // 命令通道回执（"已停止"/"已打断并纠正"…）
  noticeAt: 0,
  since: 0,
};
let dirty = false;
let lastWrite = 0;
let lingerTimer = null;
const debugged = new Set();   // 每种未知事件只 dump 一次

// ── 小工具 ─────────────────────────────────────────────────────────
function safeMkdir() {
  try { mkdirSync(DIR, { recursive: true }); } catch { /* 忽略 */ }
}

function clip(text, max) {
  const s = String(text ?? '').replace(/\s+/g, ' ').trim();
  return s.length <= max ? s : `${s.slice(0, max - 1)}…`;
}

/** 把工具参数压成一句人话。 */
function summarizeArgs(raw) {
  if (raw === undefined || raw === null) return '';
  let args = raw;
  if (typeof raw === 'string') {
    try { args = JSON.parse(raw); } catch { return clip(raw, 70); }
  }
  if (typeof args !== 'object') return clip(args, 70);
  for (const key of ['command', 'cmd', 'path', 'file_path', 'filePath', 'query', 'url', 'script']) {
    const v = args[key];
    if (typeof v === 'string' && v.trim() !== '') return clip(v, 70);
  }
  for (const v of Object.values(args)) {
    if (typeof v === 'string' && v.trim() !== '') return clip(v, 70);
  }
  return '';
}

function dumpDebug(kind, event) {
  if (debugged.has(kind)) return;
  debugged.add(kind);
  try {
    safeMkdir();
    appendFileSync(DEBUG_PATH, `${JSON.stringify({ at: Date.now(), kind, event })}\n`, 'utf8');
  } catch { /* 忽略 */ }
}

/** 原子写：先写 tmp 再 rename，避免浮窗读到半个 JSON。 */
function flush(force = false) {
  const now = Date.now();
  if (!force && now - lastWrite < WRITE_INTERVAL_MS) return;
  if (!dirty && !force) return;
  lastWrite = now;
  dirty = false;
  state.updatedAt = now;
  state.seq += 1;
  try {
    safeMkdir();
    const tmp = `${STATE_PATH}.tmp`;
    writeFileSync(tmp, JSON.stringify(state), 'utf8');
    renameSync(tmp, STATE_PATH);
  } catch { /* 写盘失败不能影响会话 */ }
}

// ── 浮窗进程 ───────────────────────────────────────────────────────
function readConfig() {
  try { return JSON.parse(readFileSync(CONFIG_PATH, 'utf8')); } catch { return {}; }
}

function findPython() {
  const cfg = readConfig();
  if (cfg.python && existsSync(cfg.python)) return cfg.python;
  const local = process.env.LOCALAPPDATA || '';
  for (const v of ['Python313', 'Python312', 'Python311']) {
    const p = join(local, 'Programs', 'Python', v, 'pythonw.exe');
    if (existsSync(p)) return p;
  }
  return 'pythonw';
}

function scriptPath(key, fallback) {
  const cfg = readConfig();
  return cfg[key] || fallback;
}

function processAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch { return false; }
}

function readPids() {
  try {
    const parsed = JSON.parse(readFileSync(PIDS_PATH, 'utf8'));
    return parsed !== null && typeof parsed === 'object' ? parsed : {};
  } catch { return {}; }
}

function writePids(pids) {
  try { writeFileSync(PIDS_PATH, JSON.stringify(pids), 'utf8'); } catch { /* 忽略 */ }
}

/**
 * 上半区（穿透可视）+ 下半区（可交互输入条），**逐个**检查、缺谁补谁。
 * 早先只看单个 pid：输入条崩了但上半区还活着，就永远不会被重新拉起。
 */
function ensureOverlay() {
  try {
    if (process.env.BULLET_NO_SPAWN === '1') return;  // 自检时别真起浮窗
    const pids = readPids();
    const specs = [
      ['overlay', scriptPath('overlay', join(PROJECT_ROOT, 'src', 'overlay.py'))],
      ['inputbar', scriptPath('inputbar', join(PROJECT_ROOT, 'src', 'inputbar.py'))],
    ];
    let changed = false;
    for (const [key, script] of specs) {
      if (processAlive(Number(pids[key]))) continue;   // 这个还活着
      if (!existsSync(script)) continue;
      try {
        const proc = spawn(findPython(), [script], {
          detached: true, stdio: 'ignore', windowsHide: true,
        });
        proc.on('error', () => { /* 忽略 */ });
        proc.unref();
        if (typeof proc.pid === 'number') {
          pids[key] = proc.pid;
          changed = true;
        }
      } catch { /* 单个失败不影响另一个 */ }
    }
    if (changed) writePids(pids);
  } catch { /* 起不来也不能影响会话 */ }
}

function stopOverlay() {
  const pids = readPids();
  for (const pid of Object.values(pids)) {
    const n = Number(pid);
    if (Number.isInteger(n) && n > 0) {
      try { process.kill(n); } catch { /* 已退出 */ }
    }
  }
  writePids({});
}

// ── 事件解析 ───────────────────────────────────────────────────────
/** 从一步末尾的 message.content 里取"最后一块思考"和"最后一块回答"。 */
function fromMessageContent(content) {
  if (!Array.isArray(content)) return {};
  let thought = '';
  let said = '';
  for (const block of content) {
    if (block === null || typeof block !== 'object') continue;
    const text = typeof block.text === 'string' ? block.text : '';
    if (text === '') continue;
    if (block.type === 'reasoning') thought = text;
    else if (block.type === 'text') said = text;
  }
  return { thought, said };
}

/**
 * 造一条 user 消息。等价于宿主 @deepseek-ai/dsh-llm 的 createUserMessage
 * （createMessage = deepFreeze(structuredClone({...input, id: randomUUID()}))）。
 * 不能直接 import 那个包：它住在 asar 里，本插件的模块解析路径够不到。
 */
function makeUserMessage(text) {
  return Object.freeze({
    content: [{ type: 'text', text: String(text) }],
    source: { kind: 'user', rpcId: randomUUID(), clientTimeZone: 'Asia/Shanghai' },
    role: 'user',
    id: randomUUID(),
  });
}

/** 执行一条来自浮窗的命令。返回给 UI 一句人话（写进 state.notice）。 */
function handleCommand(ctx, sessionId, msg, touch, flush) {
  const reply = (notice) => {
    touch({ notice, noticeAt: Date.now() });
    flush(true);
  };
  try {
    const agents = typeof ctx.get === 'function' ? ctx.get('agents') : undefined;
    const agent = agents !== undefined && agents !== null && typeof agents.get === 'function' && sessionId
      ? agents.get(sessionId)
      : undefined;
    if (agent === undefined || agent === null) return reply('没有正在进行的会话');

    const cmd = String(msg.cmd ?? '');
    if (cmd === 'cancel') {
      agent.cancel({ kind: 'user' }, { keepInbox: true });
      return reply('已停止');
    }
    if (cmd === 'steer' || cmd === 'stop-and-steer') {
      const text = String(msg.text ?? '').trim();
      if (cmd === 'stop-and-steer') agent.cancel({ kind: 'user' }, { keepInbox: true });
      if (text !== '') agent.steer(makeUserMessage(text));
      return reply(cmd === 'stop-and-steer' ? '已打断并纠正' : '已插入下一步');
    }
    return reply(`未知命令 ${cmd}`);
  } catch (error) {
    return reply(`命令失败：${String(error).slice(0, 60)}`);
  }
}

/** UDP 命令通道：端口写进 port.txt，UI 侧读它。 */
function startCommandChannel(ctx, getSessionId, touch, flush) {
  let sock = null;
  try {
    sock = createSocket('udp4');
    sock.on('error', () => { /* 端口冲突等：静默关闭 */ });
    sock.on('message', (buf) => {
      let msg;
      try { msg = JSON.parse(buf.toString('utf8')); } catch { return; }
      handleCommand(ctx, getSessionId(), msg, touch, flush);
    });
    sock.bind(0, '127.0.0.1', () => {
      try {
        safeMkdir();
        writeFileSync(PORT_PATH, String(sock.address().port), 'utf8');
      } catch { /* 忽略 */ }
    });
    if (typeof sock.unref === 'function') sock.unref();
  } catch {
    sock = null;
  }
  return () => {
    try { sock?.close(); } catch { /* 忽略 */ }
    try { writeFileSync(PORT_PATH, '', 'utf8'); } catch { /* 忽略 */ }
  };
}
function fromLiveChunk(data) {
  if (data === null || typeof data !== 'object') return null;
  const chunk = data.chunk ?? data;
  if (chunk === null || typeof chunk !== 'object') return null;
  const kind = chunk.blockType ?? chunk.block ?? data.blockType;
  const delta = chunk.delta ?? chunk.text ?? chunk.content;
  const text = typeof delta === 'string'
    ? delta
    : (delta && typeof delta.text === 'string' ? delta.text : '');
  if (kind === undefined && text === '') return null;
  return { kind, text };
}

// ── 插件主体 ───────────────────────────────────────────────────────
export function apply(ctx) {
  const disposers = [];
  const seen = new Set();
  let activeSession = '';

  const touch = (patch) => {
    Object.assign(state, patch);
    dirty = true;
  };

  const endLinger = () => {
    if (lingerTimer !== null) {
      clearTimeout(lingerTimer);
      lingerTimer = null;
    }
    lingerTimer = setTimeout(() => {
      lingerTimer = null;
      touch({ status: 'idle' });
      flush(true);
    }, LINGER_MS);
  };

  try {
    safeMkdir();

    disposers.push(ctx.on('session/event', (session, event) => {
      try {
        if (event === null || typeof event !== 'object') return;
        const type = event.type;
        const data = event.data ?? {};
        const sid = session && session.id ? String(session.id) : 'default';

        // 事件普查：按「事件类型 + 流式块类型」去重记样张，
        // 这样 block-start / block-delta / block-end 每种都能留一份。
        const sub = type === 'assistant/live-chunk' && data !== null && typeof data === 'object'
          && data.chunk !== null && typeof data.chunk === 'object'
          ? `${data.chunk.type ?? '?'}/${data.chunk.blockType ?? '-'}`
          : '';
        const key = sub === '' ? type : `${type}|${sub}`;
        if (!seen.has(key)) {
          seen.add(key);
          dumpDebug(`type:${key}`, { data });
        }

        // 只跟随最近活跃的会话，避免子代理/多会话互相覆盖
        if (type === 'turn/start' || type === 'user/message') {
          activeSession = sid;
        } else if (activeSession !== '' && sid !== activeSession) {
          return;
        }
        if (sid !== activeSession && activeSession !== '') return;

        switch (type) {
          case 'turn/start': {
            touch({
              status: 'thinking', sessionId: sid, turn: data.turn ?? 0,
              step: 0, thought: '', said: '', tool: '', toolDetail: '', since: Date.now(),
            });
            if (lingerTimer !== null) { clearTimeout(lingerTimer); lingerTimer = null; }
            ensureOverlay();
            break;
          }
          case 'step/start':
            touch({ status: 'thinking', step: data.step ?? 0 });
            break;
          case 'assistant/live-chunk': {
            const d = fromLiveChunk(data);
            if (d === null) { dumpDebug('live-chunk-shape', { data }); break; }
            if (d.kind === 'reasoning' || d.kind === 'thinking') {
              touch({ status: 'thinking', thought: clip(`${state.thought}${d.text}`, THOUGHT_MAX) });
            } else if (d.kind === 'text') {
              touch({ status: 'thinking', said: clip(`${state.said}${d.text}`, SAID_MAX) });
            }
            break;
          }
          case 'assistant/message': {
            const msg = data.message ?? {};
            const got = fromMessageContent(msg.content);
            const patch = { status: 'thinking', tool: '', toolDetail: '' };
            if (got.thought) patch.thought = clip(got.thought, THOUGHT_MAX);
            if (got.said) patch.said = clip(got.said, SAID_MAX);
            touch(patch);
            break;
          }
          case 'tool/call':
            touch({
              status: 'tool',
              tool: String(data.name ?? ''),
              toolDetail: summarizeArgs(data.arguments),
            });
            ensureOverlay();
            break;
          case 'tool/result':
            touch({ status: 'thinking' });
            break;
          case 'turn/end':
            touch({ status: 'done', step: state.step });
            endLinger();
            break;
          case 'session/title': {
            const t = data.title ?? data.name ?? (typeof data === 'string' ? data : '');
            if (t) touch({ title: String(t) });
            break;
          }
          default:
            return;
        }
        flush();
      } catch { /* 单个事件解析失败不影响会话 */ }
    }));

    // 兜底定时刷盘：把节流窗口内最后一次状态落地
    const timer = setInterval(() => {
      try { if (dirty) flush(true); } catch { /* 忽略 */ }
    }, 500);
    if (typeof timer.unref === 'function') timer.unref();
    disposers.push(() => clearInterval(timer));

    // ── 命令通道（浮窗/输入条 → 宿主）────────────────────────────
    // UDP 单向 fire-and-forget，跟 NousResearch/hermes-agent 的 overlay 同款思路：
    // 通道坏了只影响按钮，绝不影响会话。
    disposers.push(startCommandChannel(ctx, () => state.sessionId || activeSession, touch, flush));

    ctx.logger?.info?.('bullet-bridge: watching session events → ~/.dsh/bullet/state.json');
  } catch (error) {
    try { ctx.logger?.warn?.(`bullet-bridge: disabled (${String(error)})`); } catch { /* 忽略 */ }
  }

  const cleanup = () => {
    for (const dispose of disposers) {
      try { dispose(); } catch { /* 忽略 */ }
    }
    if (lingerTimer !== null) clearTimeout(lingerTimer);
    touch({ status: 'idle' });
    flush(true);
    stopOverlay();
  };

  try {
    ctx.on('dispose', cleanup);
  } catch {
    /* 老宿主没有 dispose 事件也无所谓，进程退出时自然回收 */
  }

  return undefined;
}
