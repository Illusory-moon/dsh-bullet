// 自检：不需要 DSH，喂合成事件给 apply()，断言 state.json 的内容。
// 运行：  ELECTRON_RUN_AS_NODE=1 "<DeepSeek Harness.exe>" plugin/selftest.mjs
//         （普通 node 亦可）
import { mkdtempSync, readFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import assert from 'node:assert';

process.env.BULLET_NO_SPAWN = '1';
// 自检写到临时目录：别污染 ~/.dsh/bullet 里真实的 state/debug
const TEST_DIR = mkdtempSync(join(tmpdir(), 'bullet-selftest-'));
process.env.BULLET_DIR = TEST_DIR;

const STATE_PATH = join(TEST_DIR, 'state.json');
try { rmSync(STATE_PATH); } catch { /* 可能不存在 */ }

const { apply } = await import('./index.js');

const handlers = [];
const disposers = [];
const ctx = {
  on: (channel, handler) => {
    if (channel === 'session/event') handlers.push(handler);
    if (channel === 'dispose') disposers.push(handler);
    return () => {};
  },
  logger: { info: () => {}, warn: () => {} },
};

apply(ctx);
assert.equal(handlers.length, 1, '应当订阅到 session/event');

const session = { id: 'selftest-session' };
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// 写盘有 100ms 节流（设计如此），所以每次事件后等一个节流窗口再断言
const emit = async (type, data) => {
  handlers[0](session, { type, data, seq: 1, time: Date.now() });
  await sleep(130);
};
const read = () => JSON.parse(readFileSync(STATE_PATH, 'utf8'));

// 1) 回合开始 → 思考中
await emit('turn/start', { turn: 3 });
assert.equal(read().status, 'thinking');
assert.equal(read().turn, 3);

// 2) 流式思考增量（形状 1：block-delta）
await emit('assistant/live-chunk', { chunk: { type: 'block-start', blockType: 'reasoning' } });
await emit('assistant/live-chunk', { chunk: { type: 'block-delta', blockType: 'reasoning', delta: '先看窗口标题' } });
await emit('assistant/live-chunk', { chunk: { type: 'block-delta', blockType: 'reasoning', delta: '，再决定点哪' } });
assert.equal(read().thought, '先看窗口标题，再决定点哪', '思考增量应累积');

// 3) 工具调用 → 执行工具 + 参数摘要
await emit('tool/call', {
  name: 'mcp__sky__screenshot',
  arguments: JSON.stringify({ path: 'C:\\tmp\\shot.png', index: 0 }),
});
const s3 = read();
assert.equal(s3.status, 'tool');
assert.equal(s3.tool, 'mcp__sky__screenshot');
assert.equal(s3.toolDetail, 'C:\\tmp\\shot.png', '应优先取 path 类参数');

// 4) 工具结束 → 回到思考
await emit('tool/result', {});
assert.equal(read().status, 'thinking');

// 5) 一步结束 → 用 message.content 校正思考与回答
await emit('assistant/message', {
  message: {
    role: 'assistant',
    content: [
      { type: 'reasoning', text: '真正的完整思考文本' },
      { type: 'text', text: '我把左边窗口缩到 800px' },
    ],
  },
});
const s5 = read();
assert.equal(s5.thought, '真正的完整思考文本', 'message 应覆盖流式拼出来的内容');
assert.equal(s5.said, '我把左边窗口缩到 800px');

// 6) 对话名
await emit('session/title', { title: '给 DSH 做弹幕姬' });
assert.equal(read().title, '给 DSH 做弹幕姬');

// 7) 回合结束 → done，随后自动转 idle
await emit('turn/end', {});
assert.equal(read().status, 'done');

// 8) 别的会话不应覆盖当前状态
await emit('tool/call', { name: 'ops_bash', arguments: JSON.stringify({ command: 'ls' }) });
assert.equal(read().tool, 'ops_bash');
handlers[0]({ id: 'other-session' }, { type: 'tool/call', data: { name: 'should-be-ignored' } });
await sleep(130);
assert.equal(read().tool, 'ops_bash', '非活跃会话的事件应被忽略');

// 9) 未知事件不应炸（状态保持上一次的值即可）
await emit('something/brand-new', { a: 1 });
assert.equal(read().status, 'tool', '未知事件应被安静忽略');
assert.equal(read().tool, 'ops_bash');

for (const d of disposers) d();
console.log('selftest: OK — 9 项断言全过');
console.log('state:', JSON.stringify(read()));
