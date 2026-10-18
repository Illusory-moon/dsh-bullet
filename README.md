# dsh-bullet · DSH 弹幕姬

给 **DeepSeek Harness（DSH）** 的桌面悬浮窗：**实时显示 agent 正在干什么**。

透明、点击穿透、不进截图、**永不抢焦点**；鼠标移过去会浮出输入条，可以随时打断并纠正它。

> 起因：让 agent 用 MCP 操作电脑时，它把别的应用放到前台，你就看不到它的状态了——
> 不知道它在想、在等、还是卡住了，也不敢乱动鼠标。ChatGPT 那种全屏泛光只能说明"它在动"，
> 但**步内什么都没有**的时候依然一片茫然，而且它连"用完了没有"都答不准。

```
思考中  12s          ●            我的项目 · step 7
思  先看一下窗口标题，再决定点哪个元素
⚒   ops_bash   python shot.py
答   我把左边窗口缩到 800px 再截图
输入： ______________________________  停  纠正
```

## 特性

- **背景全透明**，只有文字浮在屏幕上 —— 不遮住你要点的按钮
- **点击穿透**，并用 `WDA_EXCLUDEFROMCAPTURE` 让自己**不进 agent 的截图**（否则会污染它自己的视觉输入）
- **永不抢焦点**：常驻 `WS_EX_NOACTIVATE`，显示/置顶都不激活；输入条平时不出现，**鼠标移过去才浮出**
- **在动就得看得见**：呼吸点 + 秒级计时器，一步之内没有新文本时也能看出它还活着
- **随手打断**：`[停]` → `agent.cancel()`；回车 → `agent.steer()`（把话插到下一步，不粗暴中断）

## 安装

依赖：**Windows 10 2004+**、**Python 3.10+**（需要 tkinter，官方安装包自带）、**DSH 桌面端**。

```powershell
pwsh -File tools\install.ps1
# 然后重启 DSH 桌面端（插件只在启动时组合）
```

`install.ps1` 做三件事，可重复执行：

1. 把 `plugin/` 链接进 `~/.dsh/profiles/desktop/node_modules/@local/dsh-bullet`（**真 Junction**，并校验）
2. 往 profile 的 `cordis.patch.yml` 追加一段 `bullet-bridge`（追加前自动备份）
3. 生成 `~/.dsh/bullet/config.json`（Python 解释器路径等）

## 使用

| 操作 | 效果 |
|---|---|
| 有任务时 | 浮窗自动出现；结束后显示"完成"，4 秒自动隐藏 |
| `Ctrl+Alt+方向键` | 挪位置（会记住） |
| 鼠标移到浮窗区域 | 浮出输入条 |
| 输入框回车 / `纠正` | `agent.steer(...)`：插到下一步，改航 |
| `停` | `agent.cancel({kind:'user'})`：立刻中断当前回合 |

## 原理

```
DSH 插件 (plugin/)                    两个 Python 进程 (src/)
  ctx.on('session/event')               overlay.py    上半区：穿透、只读
    ↓ 聚合 turn/step/tool/思考           inputbar.py   下半区：可点、可输入
  ~/.dsh/bullet/state.json  ──读──▶
      ▲                                UDP 命令通道
      └──────────  {cmd: steer|cancel} ◀──┘
```

- 插件把会话事件压成一个状态文件（10Hz 节流、原子替换），UI 只读文件 —— 两边完全解耦，UI 崩了不影响会话
- 命令走 `127.0.0.1` 的 UDP（端口写在 `port.txt`），插件收到后调 `agent.steer()` / `agent.cancel()`
- 两个脚本都带**具名互斥体**，插件误判也不会堆出重复窗口

## 已知限制

- **没有 token 级流式**。实测（`probe/oneoff/sampler.py`，19 秒 / 15 个 step）：
  `assistant/live-chunk` 只送块边界，不带文字增量；思考文本是在 `assistant/message` 落盘时**整块**到达的。
  所以一步之内看不到逐字吐字 —— 这正是要加计时器 + 呼吸点的原因。
- 仅 Windows。
- 依赖 DSH 的**内部事件名**（`session/event`、`assistant/message`…），宿主升级可能需要跟着改。
  兜底：未知事件一律忽略，不会影响会话。

## 目录

```
plugin/    DSH 插件桥（含 selftest.mjs，9 项断言，不需要 DSH 就能跑）
src/       overlay.py（穿透可视层）/ inputbar.py（可交互层）
tools/     install / uninstall / restart-ui / smoke-overlay / asar 读取小工具
probe/     test_focus.py（客观验证不抢焦点）、test_inputbar.py（命令通道）
probe/oneoff/  当初摸 DSH 内部结构的探针脚本，留档
docs/      事件与 API 地图（实测）
```

## 卸载

```powershell
pwsh -File tools\uninstall.ps1          # 摘掉插件与链接，保留运行数据
pwsh -File tools\uninstall.ps1 -Purge   # 连 ~/.dsh/bullet 一起删
```

## 参考

- 透明穿透浮窗的实现参考了 [NousResearch/hermes-agent 的 `overlay.py`](https://github.com/NousResearch/hermes-agent/commit/07b57d895930f4e2f85ed4edc4dfcce4817f0176)
  （`WS_EX_TRANSPARENT` + `SetWindowDisplayAffinity` 那套坑它先踩过）
- "泛光不够、需要一行状态"这个判断，和 [openai/codex#40145](https://github.com/openai/codex/issues/40145) 里提的需求一致
