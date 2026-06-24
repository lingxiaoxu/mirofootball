// engine/server.js —— 薄 HTTP 壳；引擎 engine.js + lib/*.js 一行不改（复用铁律）
// 静音引擎内的 console.log(matchDetails)（engine.js + ballMovement.js）——进程层 monkeypatch，不动引擎源码。
// 必须在 require 引擎之前覆盖，且保留 console.error（validate 的告警走 stderr）。
console.log = () => {}

// 可选种子化 RNG（plan 03§3 / 06§3.4）：设 MIRO_SEED=<int> 时确定性可复现; 默认不启用。
require('./lib/seededRng').maybeSeed()

const express = require('express')
const { initiateGame, playIteration, startSecondHalf } = require('./engine.js')
const playerMovement = require('./lib/playerMovement')

const app = express()
app.use(express.json({ limit: '8mb' }))

// HTTP/JSON 往返完整性：JSON.stringify 会删掉值为 undefined 的键，导致回传时 validate 报"缺字段"。
// 这里在输出前把引擎要求的必需键补回（仅当缺失/undefined），引擎逻辑一行不改（粘合剂）。
function normalize(md) {
  if (md && md.ball) {
    const b = md.ball
    if (b.direction === undefined) b.direction = 'wait'
    if (b.ballOverIterations === undefined) b.ballOverIterations = []
    if (b.Player === undefined) b.Player = ''
    if (b.withTeam === undefined) b.withTeam = ''
    if (b.withPlayer === undefined) b.withPlayer = false
  }
  for (const k of ['kickOffTeam', 'secondTeam']) {
    const t = md && md[k]
    if (!t || !Array.isArray(t.players)) continue
    for (const p of t.players) {
      if (p.action === undefined) p.action = 'none'
      if (p.intentPOS === undefined) p.intentPOS = p.currentPOS
      if (p.originPOS === undefined) p.originPOS = p.currentPOS
      if (p.offside === undefined) p.offside = false
      if (p.hasBall === undefined) p.hasBall = false
    }
  }
  return md
}

const ok = (res, payload) => res.json(normalize(payload))
const fail = (res, e) => res.status(400).json({ error: String((e && e.stack) || e) })

// matchDetails 里已被编排器注入各球员 action/intentPOS；playIteration 原样解算物理 + 单球不变式
app.post('/initiate', async (req, res) => {
  try { const { team1, team2, pitch } = req.body; ok(res, await initiateGame(team1, team2, pitch)) }
  catch (e) { fail(res, e) }
})
// 引擎偶发边界 bug（如 actions.js setFoul 在 tackle findIndex 返回 -1 时读 undefined.name）。
// 按复用铁律不改引擎源码 → 包装层兜底：克隆输入重试（随机路径变化多半绕开），仍失败则跳过该拍让比赛继续。
app.post('/iterate', async (req, res) => {
  const input = req.body.matchDetails
  for (let k = 0; k < 5; k++) {
    try { return ok(res, await playIteration(structuredClone(input))) }
    catch (e) { if (k === 4) { process.stderr.write(`iterate skip after retries: ${String(e).slice(0,120)}\n`); return ok(res, structuredClone(input)) } }
  }
})
app.post('/secondhalf', async (req, res) => {
  try { ok(res, await startSecondHalf(req.body.matchDetails)) } catch (e) { fail(res, e) }
})
// 第一防守者：调引擎原 closestPlayerToBall，不在编排器重写几何（06 §2.2）
app.post('/closest', (req, res) => {
  try {
    const { matchDetails, team } = req.body
    const closest = { name: '', position: 1e9 }
    playerMovement.closestPlayerToBall(closest, team, matchDetails)
    ok(res, closest)
  } catch (e) { fail(res, e) }
})
// 点球大战单脚解算(wrapper, 用引擎 penalty_taking vs saving 技术值; ~75% 命中)。淘汰赛编排层调。
app.post('/penalty', (req, res) => {
  try {
    const { matchDetails, takerTeam, takerID } = req.body
    const team = takerTeam === 'home' ? matchDetails.kickOffTeam : matchDetails.secondTeam
    const defTeam = takerTeam === 'home' ? matchDetails.secondTeam : matchDetails.kickOffTeam
    const taker = team.players.find(p => p.playerID === takerID) || team.players.find(p => p.position !== 'GK')
    const gk = defTeam.players.find(p => p.position === 'GK') || defTeam.players[0]
    const pt = parseInt((taker.skill && taker.skill.penalty_taking) || 70, 10)
    const sv = parseInt((gk.skill && gk.skill.saving) || 60, 10)
    const rnd = () => Math.floor(Math.random() * 101)
    const onTarget = (pt + 15) > rnd()        // 罚球手命中(penalty_taking 偏置)
    const saved = onTarget && (rnd() < sv * 0.30)   // GK 扑救(saving 折算, 校准约 ~75% 进球)
    const scored = onTarget && !saved
    ok(res, { scored, taker: taker.name, gk: gk.name, onTarget, saved })
  } catch (e) { fail(res, e) }
})
app.get('/health', (_req, res) => res.json({ ok: true }))

const PORT = process.env.ENGINE_PORT || 7000
// console.log 已静音 → 启动信息走 stderr
app.listen(PORT, 'localhost', () => process.stderr.write(`engine on :${PORT}\n`))
