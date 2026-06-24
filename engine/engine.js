//------------------------
//    NPM Modules
//------------------------
const common = require('./lib/common')
const setPositions = require('./lib/setPositions')
const setVariables = require('./lib/setVariables')
const playerMovement = require('./lib/playerMovement')
const ballMovement = require('./lib/ballMovement')
const validate = require('./lib/validate')
const actions = require('./lib/actions')

//------------------------
//    Functions
//------------------------
async function initiateGame(team1, team2, pitchDetails) {
  validate.validateArguments(team1, team2, pitchDetails)
  validate.validateTeam(team1)
  validate.validateTeam(team2)
  validate.validatePitch(pitchDetails)
  let matchDetails = setVariables.populateMatchDetails(team1, team2, pitchDetails)
  let kickOffTeam = setVariables.setGameVariables(matchDetails.kickOffTeam)
  let secondTeam = setVariables.setGameVariables(matchDetails.secondTeam)
  kickOffTeam = setVariables.koDecider(kickOffTeam, matchDetails)
  matchDetails.iterationLog.push(`Team to kick off - ${kickOffTeam.name}`)
  matchDetails.iterationLog.push(`Second team - ${secondTeam.name}`)
  setPositions.switchSide(matchDetails, secondTeam)
  matchDetails.kickOffTeam = kickOffTeam
  matchDetails.secondTeam = secondTeam
  return matchDetails
}

async function playIteration(matchDetails) {
  let closestPlayerA = {
    'name': '',
    'position': 100000
  }
  let closestPlayerB = {
    'name': '',
    'position': 100000
  }
  validate.validateMatchDetails(matchDetails)
  validate.validateTeamSecondHalf(matchDetails.kickOffTeam)
  validate.validateTeamSecondHalf(matchDetails.secondTeam)
  validate.validatePlayerPositions(matchDetails)
  matchDetails.iterationLog = []
  matchDetails.iterationLog.push(`Ball start position: ${matchDetails.ball.position}`)
  let { kickOffTeam, secondTeam } = matchDetails
  // mirofootball 修复(控球状态一致性, 放每拍开头→movePlayers/测量都看到正确 withTeam; 覆盖 endIteration 早返回路径):
  // withTeam 以 hasBall(控球绝对真相)为准。原 withTeam 常空(hasBall=true却空~485拍/场)→ movePlayers(行 withTeam!==team)
  // 误判"自己人争自己球"、LLM dside 误判、测量失真。有球员 hasBall 但 withTeam 空 → 按其所属队补全。
  if (!matchDetails.ball.withTeam) {
    if (kickOffTeam.players.some(p => p.hasBall)) matchDetails.ball.withTeam = kickOffTeam.teamID
    else if (secondTeam.players.some(p => p.hasBall)) matchDetails.ball.withTeam = secondTeam.teamID
  }
  common.matchInjury(matchDetails, kickOffTeam)
  common.matchInjury(matchDetails, secondTeam)
  matchDetails = ballMovement.moveBall(matchDetails)
  if (matchDetails.endIteration == true) {
    delete matchDetails.endIteration
    return matchDetails
  }
  playerMovement.closestPlayerToBall(closestPlayerA, kickOffTeam, matchDetails)
  playerMovement.closestPlayerToBall(closestPlayerB, secondTeam, matchDetails)
  let koTeamMoves = playerMovement.decideMovement(closestPlayerA, kickOffTeam, secondTeam, matchDetails)
  let stMoves = playerMovement.decideMovement(closestPlayerB, secondTeam, kickOffTeam, matchDetails)
  let koTeamMovesBallMoves = actions.extractBallActions(koTeamMoves, 'ball')
  let koTeamAllOtherMoves = actions.extractBallActions(koTeamMoves, 'movement')
  let stMovesBallMoves = actions.extractBallActions(stMoves, 'ball')
  let stAllOtherMoves = actions.extractBallActions(stMoves, 'movement')
  // mirofootball 设计修复(控球与home/away脱钩): 处理顺序按【持球状态】而非固定slot——
  // 持球队先处理、防守队后处理(防守队得"最后争夺权")。原固定先KO后ST→ST(away)永远最后→away系统性多控球。
  // 改后: 谁防守谁最后(对称, 不依赖slot); 防守好的队(能力)夺球多→控球随能力分化, 不随home/away。
  const _homeHolds = kickOffTeam.players.some(p => p.hasBall)
  if (_homeHolds) {
    matchDetails.kickOffTeam = playerMovement.movePlayers(koTeamAllOtherMoves, kickOffTeam, secondTeam, matchDetails)
    matchDetails.secondTeam = playerMovement.movePlayers(stAllOtherMoves, secondTeam, kickOffTeam, matchDetails)
  } else {
    matchDetails.secondTeam = playerMovement.movePlayers(stAllOtherMoves, secondTeam, kickOffTeam, matchDetails)
    matchDetails.kickOffTeam = playerMovement.movePlayers(koTeamAllOtherMoves, kickOffTeam, secondTeam, matchDetails)
  }

  let allBallMoves = [...koTeamMovesBallMoves, ...stMovesBallMoves]
  let validBallMoves = allBallMoves.filter(m => m.player.hasBall === true)
  if (validBallMoves.length > 0) {
    // mirofootball 校准: 多人争球时按【技能】选(control+strength 高者), 不随机
    const chosenMove = validBallMoves.reduce((best, m) => {
      const sc = p => (parseInt(p.skill.control, 10) + parseInt(p.skill.strength, 10))
      return sc(m.player) > sc(best.player) ? m : best
    }, validBallMoves[0])
    const { player } = chosenMove
    let team, opp
    if (player.teamID === kickOffTeam.teamID) {
      team = kickOffTeam
      opp = secondTeam
    } else {
      team = secondTeam
      opp = kickOffTeam
    }
    if (team.teamID === kickOffTeam.teamID) {
      matchDetails.kickOffTeam = playerMovement.executeBallAction(chosenMove, team, opp, matchDetails)
    } else {
      matchDetails.secondTeam = playerMovement.executeBallAction(chosenMove, team, opp, matchDetails)
    }
  }
  if (matchDetails.ball.ballOverIterations.length == 0 || matchDetails.ball.withTeam != '') {
    playerMovement.checkOffside(kickOffTeam, secondTeam, matchDetails)
  }
  // mirofootball 修复(控球状态一致性): withTeam 以 hasBall(控球绝对真相)为准同步。
  // 原 withTeam 常空(hasBall=true 却 withTeam='' 达~485拍/场)→ movePlayers(行50 withTeam!==team)误判"自己人争自己球"
  // + LLM dside 误判 + 测量失真。有球员 hasBall 但 withTeam 空 → 按其所属队补全。
  if (!matchDetails.ball.withTeam) {
    if (kickOffTeam.players.some(p => p.hasBall)) matchDetails.ball.withTeam = kickOffTeam.teamID
    else if (secondTeam.players.some(p => p.hasBall)) matchDetails.ball.withTeam = secondTeam.teamID
  }
  applyPossessionDecay(matchDetails, kickOffTeam, secondTeam)   // mirofootball: 控球均值回归 turnover(破bistable)
  matchDetails.iterationLog.push(`Ball end position: ${matchDetails.ball.position}`)
    console.log(JSON.stringify(matchDetails))
  return matchDetails
}

// mirofootball 控球模型重设计(破 bistable, 参考真实分布): 每拍给持球队"被断球" hazard。
// 真实: 控球段右偏分布(89%<30s, 均值~20s, 短/中/长=4/10/18s); 段长=对方回收时间(强队压迫→对手段短, 如哥伦比亚9s/刚果金36s)。
// hazard = 基率 × (对方逼抢 tackling+perception / 本方控球 control)^2 → 强逼抢队令对手频繁丢球 → 控球随【能力】分化、段长右偏(指数)、且频繁换手破雪球。
// 久控加压(spell>6) = 均值回归, 防偶发超长控球。触发 → 球权给最近逼抢者(presser 赢球)。
function _avgSkill(team, fields) {
  let outs = team.players.filter(p => p.currentPOS && p.currentPOS[0] !== 'NP' && p.position !== 'GK')
  if (!outs.length) return 70
  let s = 0
  for (let p of outs) for (let f of fields) s += parseInt((p.skill || {})[f], 10) || 70
  return s / (outs.length * fields.length)
}
function applyPossessionDecay(matchDetails, kickOffTeam, secondTeam) {
  let ball = matchDetails.ball
  let holdTeam = kickOffTeam.players.some(p => p.hasBall) ? kickOffTeam
    : (secondTeam.players.some(p => p.hasBall) ? secondTeam : null)
  if (!holdTeam) { ball.possSpell = 0; ball.possSpellTeam = ''; return }
  let defTeam = (holdTeam.teamID === kickOffTeam.teamID) ? secondTeam : kickOffTeam
  ball.possSpell = (ball.possSpellTeam === holdTeam.teamID) ? (ball.possSpell || 0) + 1 : 1
  ball.possSpellTeam = holdTeam.teamID
  let press = _avgSkill(defTeam, ['tackling', 'perception'])
  let retain = _avgSkill(holdTeam, ['control'])
  let ratio = press / Math.max(retain, 1)
  let hazard = 0.18 * ratio * ratio
  if (ball.possSpell > 6) hazard += (ball.possSpell - 6) * 0.03   // 均值回归: 久控加压
  hazard = Math.max(0.04, Math.min(0.65, hazard))
  if (common.getRandomNumber(1, 1000) > Math.round(hazard * 1000)) return
  // 被逼抢断球 → 最近的防守球员(presser)得球
  let bp = ball.position
  let presser = null, best = 1e9
  for (let p of defTeam.players) {
    if (!p.currentPOS || p.currentPOS[0] === 'NP' || p.position === 'GK') continue
    let d = Math.abs(p.currentPOS[0] - bp[0]) + Math.abs(p.currentPOS[1] - bp[1])
    if (d < best) { best = d; presser = p }
  }
  if (!presser) return
  common.removeBallFromAllPlayers(matchDetails)
  presser.hasBall = true
  ball.Player = presser.playerID; ball.withPlayer = true; ball.withTeam = defTeam.teamID
  ball.position = [presser.currentPOS[0], presser.currentPOS[1], 0]
  ball.lastTouch.playerName = presser.name; ball.lastTouch.playerID = presser.playerID
  ball.lastTouch.teamID = defTeam.teamID; ball.lastTouch.deflection = false
  ball.ballOverIterations = []
  ball.possSpell = 1; ball.possSpellTeam = defTeam.teamID
  matchDetails.iterationLog.push(`Possession won by ${presser.name}`)
}

async function startSecondHalf(matchDetails) {
  validate.validateMatchDetails(matchDetails)
  validate.validateTeamSecondHalf(matchDetails.kickOffTeam)
  validate.validateTeamSecondHalf(matchDetails.secondTeam)
  validate.validatePlayerPositions(matchDetails)
  let { kickOffTeam, secondTeam } = matchDetails
  setPositions.switchSide(matchDetails, kickOffTeam)
  setPositions.switchSide(matchDetails, secondTeam)
  common.removeBallFromAllPlayers(matchDetails)
  setVariables.resetPlayerPositions(matchDetails)
  setPositions.setBallSpecificGoalScoreValue(matchDetails, matchDetails.secondTeam)
  matchDetails.iterationLog = [`Second Half Started: ${matchDetails.secondTeam.name} to kick offs`]
  matchDetails.kickOffTeam.intent = `defend`
  matchDetails.secondTeam.intent = `attack`
  matchDetails.half++
  return matchDetails
}

module.exports = {
  initiateGame,
  playIteration,
  startSecondHalf
}
