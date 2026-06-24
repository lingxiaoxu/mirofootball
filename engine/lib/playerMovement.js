/* eslint-disable no-unused-vars */
const common = require(`../lib/common`)
const ballMovement = require(`../lib/ballMovement`)
const setPositions = require(`../lib/setPositions`)
const actions = require(`../lib/actions`)

function decideMovement(closestPlayer, team, opp, matchDetails) {
  const allActions = [`shoot`, `throughBall`, `pass`, `cross`, `tackle`, `intercept`, `slide`]
  Array.prototype.push.apply(allActions, [`run`, `sprint`, `cleared`, `boot`, `penalty`])
  let { position, withPlayer, withTeam } = matchDetails.ball
  let teamActions = []
  for (const thisPlayer of team.players) {
    if (thisPlayer.currentPOS[0] != 'NP') {
      let ballToPlayerX = thisPlayer.currentPOS[0] - position[0]
      let ballToPlayerY = thisPlayer.currentPOS[1] - position[1]
      let possibleActions
      let action
      possibleActions = actions.findPossActions(thisPlayer, team, opp, ballToPlayerX, ballToPlayerY, matchDetails)
      let lastTouchPlayer = (thisPlayer.playerID == matchDetails.ball.lastTouch.playerID)
      let ballRecentlyKicked = (matchDetails.ball.lastTouch.iterations < 4)
      let ballMoving = (matchDetails.ball.ballOverIterations.length > 0)
      if (lastTouchPlayer && ballRecentlyKicked && ballMoving) action = 'wait'
      else {
        action = actions.selectAction(possibleActions)
        action = checkProvidedAction(matchDetails, thisPlayer, action)
      }
      // mirofootball 结构性修复(破 bistable 雪球): 防守时不只最近1人逼抢——近球区(±40)防守者一起压缩,
      // 覆盖传球线+持球者。最近者近身(<15)上抢tackle; 二线压缩sprint封线 → 拦截/抢断频繁 → turnover↑ → 控球不再雪球。
      if (withTeam && withTeam !== team.teamID) {
        let _d = Math.abs(ballToPlayerX) + Math.abs(ballToPlayerY)
        let _isClosest = (closestPlayer.name === thisPlayer.name)
        if (_isClosest || _d < 40) {
          if (action !== `tackle` && action !== `slide` && action !== `intercept`) {
            action = (_isClosest && _d < 15) ? `tackle` : `sprint`   // 最近且近身→上抢; 否则压缩封线
          }
          ballToPlayerX = closestPlayerActionBallX(ballToPlayerX)
          ballToPlayerY = closestPlayerActionBallY(ballToPlayerY)
        }
      }
      let move = getMovement(thisPlayer, action, opp, ballToPlayerX, ballToPlayerY, matchDetails)
      teamActions.push({ player: thisPlayer, action, move })
    }
  }
  return teamActions
}

function movePlayers(moves, team, opp, matchDetails) {
  let { position, withPlayer, withTeam } = matchDetails.ball
  for (const thisPlayerMove of moves) {
    let thisPlayer = thisPlayerMove.player
    let { move } = thisPlayerMove
    let { action } = thisPlayerMove
    thisPlayer.currentPOS = completeMovement(matchDetails, thisPlayer.currentPOS, move)
    // mirofootball 修复(松球73%无人捡→控球仅12%): 松球(withPlayer=false)用更宽捡拾范围±12, 持球争夺仍±6
    // → 落地球快速被控 → 控球时间↑、松球↓、回收时间↓。
    let _rng = (withPlayer === false) ? 12 : 6
    let xPosition = common.isBetween(thisPlayer.currentPOS[0], position[0] - _rng, position[0] + _rng)
    let yPosition = common.isBetween(thisPlayer.currentPOS[1], position[1] - _rng, position[1] + _rng)
    let samePositionAsBall = thisPlayer.currentPOS[0] === position[0] && thisPlayer.currentPOS[1] === position[1]
    let closeWithPlayer = !!((xPosition && yPosition && withPlayer == false))
    if (xPosition && yPosition && withTeam !== team.teamID) {
      if (samePositionAsBall) {
        if (withPlayer === true && thisPlayer.hasBall === false && withTeam !== team.teamID) {
          if (action === `tackle`) matchDetails = completeTackleWhenCloseNoBall(matchDetails, thisPlayer, team, opp)
          if (action === `slide`) matchDetails = completeSlide(matchDetails, thisPlayer, team, opp)
        } else setClosePlayerTakesBall(matchDetails, thisPlayer, team, opp)
      } else if (withPlayer === true && thisPlayer.hasBall === false && withTeam !== team.teamID) {
        // mirofootball 校准: 原抢断要求与球完全同坐标+action===tackle(几乎不触发)→持球者从不被抢→bistable雪球。
        // 改为 ±6 接近持球对手即尝试抢断(不限action; 吃 tackling+strength vs agility+strength 技能对抗,
        // 成功干净转给抢断者)→ 抢断频繁+偏强队 → 控球随质量分化、反 bistable。
        if (action === `slide`) matchDetails = completeSlide(matchDetails, thisPlayer, team, opp)
        else matchDetails = completeTackleWhenCloseNoBall(matchDetails, thisPlayer, team, opp)
      } else {
        setClosePlayerTakesBall(matchDetails, thisPlayer, team, opp)
      }
    } else if (closeWithPlayer) setClosePlayerTakesBall(matchDetails, thisPlayer, team, opp)
  }
  return team
}

function executeBallAction(move, team, opp, matchDetails) {
  if (matchDetails.ball.ballOverIterations.length !== 0) return team
  const { player } = move
  const { action } = move
  handleBallPlayerActions(matchDetails, player, team, opp, action)
  common.removeBallFromAllPlayers(matchDetails)
  matchDetails.ball.Player = ''
  matchDetails.ball.withPlayer = false
  matchDetails.ball.withTeam = ''
  return team
}

function setClosePlayerTakesBall(matchDetails, thisPlayer, team, opp) {
  // mirofootball 校准: 散球归属按【距离】决定(非处理顺序)。movePlayers 先KO后ST, 原本ST后处理→覆盖夺球
  // →away系统性多回收散球(实测8:2)。改为: 对手离球更近则让对手得球, 平距才比回收技能。
  // → 谁近谁得(对称, 与顺序无关) → 消除 home/away 偏差; 同距离时高技术者得 → 仍随质量分化。
  let _b = matchDetails.ball.position
  let _rec = p => (parseInt(p.skill.control, 10) + parseInt(p.skill.agility, 10) + parseInt(p.skill.strength, 10))
  let _myDist = Math.abs(thisPlayer.currentPOS[0] - _b[0]) + Math.abs(thisPlayer.currentPOS[1] - _b[1])
  try {
    let _oi = setPositions.closestPlayerToPosition(thisPlayer, opp, _b)
    let _oc = _oi && _oi.thePlayer
    if (_oc && _oc.currentPOS && _oc.currentPOS[0] !== 'NP') {
      let _oppDist = _oi.proxToBall
      if (_oppDist < _myDist) return
      if (_oppDist === _myDist && _rec(_oc) > _rec(thisPlayer)) return
    }
  } catch (e) { /* 无对手信息则按原逻辑 */ }
  // mirofootball 校准: 引擎越位过敏(~56次/场 vs 真实数次)→ 只 ~12% 真判越位, 其余放行让传球成链
  if (thisPlayer.offside && common.getRandomNumber(0, 100) < 12) {
    matchDetails.iterationLog.push(`${thisPlayer.name} is offside`)
    // mirofootball: 计越位(越位方=被吹的队)
    let _os = (team.name == matchDetails.kickOffTeam.name) ? matchDetails.kickOffTeamStatistics : matchDetails.secondTeamStatistics
    _os.offsides = (_os.offsides || 0) + 1
    if (team.name == matchDetails.kickOffTeam.name) setPositions.setSetpieceKickOffTeam(matchDetails)
    else setPositions.setSetpieceSecondTeam(matchDetails)
  } else {
    // mirofootball: 传球成功率——接球时若有传球进行中, 判传球者 on(接球者同队)/off(异队被断), 每传球只计一次
    if (matchDetails.ball.passInProgress) {
      let _pid = matchDetails.ball.lastTouch.playerID, _ptid = matchDetails.ball.lastTouch.teamID
      if (_pid && _pid !== thisPlayer.playerID) {
        let _pt = (_ptid == matchDetails.kickOffTeam.teamID) ? matchDetails.kickOffTeam : matchDetails.secondTeam
        let _passer = _pt.players.find(p => p.playerID === _pid)
        if (_passer && _passer.stats && _passer.stats.passes) {
          if (_ptid === team.teamID) _passer.stats.passes.on++
          else _passer.stats.passes.off++
        }
      }
      matchDetails.ball.passInProgress = false
    }
    thisPlayer.hasBall = true
    matchDetails.ball.lastTouch.playerName = thisPlayer.name
    matchDetails.ball.lastTouch.playerID = thisPlayer.playerID
    matchDetails.ball.lastTouch.teamID = team.teamID
    matchDetails.ball.lastTouch.deflection = false
    matchDetails.ball.ballOverIterations = []
    matchDetails.ball.position = thisPlayer.currentPOS.map(x => x)
    matchDetails.ball.Player = thisPlayer.playerID
    matchDetails.ball.withPlayer = true
    matchDetails.ball.withTeam = team.teamID
    team.intent = `attack`
    opp.intent = `defend`
  }
}

function completeSlide(matchDetails, thisPlayer, team, opp) {
  let foul = actions.resolveSlide(thisPlayer, team, opp, matchDetails)
  if (!foul) {
    if (opp.name == matchDetails.kickOffTeam.name) return setPositions.setSetpieceKickOffTeam(matchDetails)
    return setPositions.setSetpieceSecondTeam(matchDetails)
  }
  let intensity = actions.foulIntensity()
  if (common.isBetween(intensity, 65, 90)) {
    thisPlayer.stats.cards.yellow++
    if (thisPlayer.stats.cards.yellow == 2) {
      thisPlayer.stats.cards.red++
      Object.defineProperty(thisPlayer, 'currentPOS', {
        value: ['NP', 'NP'],
        writable: false,
        enumerable: true,
        configurable: false
      })
    }
  } else if (common.isBetween(intensity, 85, 100)) {
    thisPlayer.stats.cards.red++
    Object.defineProperty(thisPlayer, 'currentPOS', {
      value: ['NP', 'NP'],
      writable: false,
      enumerable: true,
      configurable: false
    })
  }
  if (opp.name == matchDetails.kickOffTeam.name) return setPositions.setSetpieceKickOffTeam(matchDetails)
  return setPositions.setSetpieceSecondTeam(matchDetails)
}

function completeTackleWhenCloseNoBall(matchDetails, thisPlayer, team, opp) {
  let foul = actions.resolveTackle(thisPlayer, team, opp, matchDetails)
  if (foul) {
    let intensity = actions.foulIntensity()
    if (common.isBetween(intensity, 75, 90)) {
      thisPlayer.stats.cards.yellow++
      if (thisPlayer.stats.cards.yellow == 2) {
        thisPlayer.stats.cards.red++
        Object.defineProperty(thisPlayer, 'currentPOS', {
          value: ['NP', 'NP'],
          writable: false,
          enumerable: true,
          configurable: false
        })
      }
    } else if (common.isBetween(intensity, 90, 100)) {
      thisPlayer.stats.cards.red++
      Object.defineProperty(thisPlayer, 'currentPOS', {
        value: ['NP', 'NP'],
        writable: false,
        enumerable: true,
        configurable: false
      })
    }
  }
  if (opp.name == matchDetails.kickOffTeam.name) return setPositions.setSetpieceKickOffTeam(matchDetails)
  return setPositions.setSetpieceSecondTeam(matchDetails)
}

function completeMovement(matchDetails, currentPOS, move) {
  if (currentPOS[0] != 'NP') {
    let intendedMovementX = currentPOS[0] + move[0]
    let intendedMovementY = currentPOS[1] + move[1]
    if (intendedMovementX < matchDetails.pitchSize[0] + 1 && intendedMovementX > -1) currentPOS[0] += move[0]
    if (intendedMovementY < matchDetails.pitchSize[1] + 1 && intendedMovementY > -1) currentPOS[1] += move[1]
  }
  return currentPOS
}

function closestPlayerActionBallX(ballToPlayerX) {
  if (common.isBetween(ballToPlayerX, -30, 30) === false) {
    if (ballToPlayerX > 29) return 29
    return -29
  } return ballToPlayerX
}

function closestPlayerActionBallY(ballToPlayerY) {
  if (common.isBetween(ballToPlayerY, -30, 30) === false) {
    if (ballToPlayerY > 29) return 29
    return -29
  } return ballToPlayerY
}

function checkProvidedAction(matchDetails, thisPlayer, action) {
  const ballActions = [`shoot`, `throughBall`, `pass`, `cross`, `cleared`, `boot`, `penalty`]
  const allActions = [`shoot`, `throughBall`, `pass`, `cross`, `tackle`, `intercept`, `slide`]
  Array.prototype.push.apply(allActions, [`run`, `sprint`, `cleared`, `boot`, `penalty`])
  let providedAction = (thisPlayer.action) ? thisPlayer.action : `unassigned`
  if (providedAction === `none`) return action
  if (allActions.includes(providedAction)) {
    if (thisPlayer.playerID !== matchDetails.ball.Player) {
      if (ballActions.includes(providedAction)) {
        const notice = `${thisPlayer.name} doesnt have the ball so cannot ${providedAction} -action: run`
        console.error(notice)
        return `run`
      } return providedAction
    } else if (providedAction === `tackle` || providedAction === `slide` || providedAction === `intercept`) {
      action = ballActions[common.getRandomNumber(0, 5)]
      const notice = `${thisPlayer.name} has the ball so cannot ${providedAction} -action: ${action}`
      console.error(notice)
      return action
    } return providedAction
  } else if (thisPlayer.action !== `none`) throw new Error(`Invalid player action for ${thisPlayer.name}`)
}

function handleBallPlayerActions(matchDetails, thisPlayer, team, opp, action) {
  const ballActions = [`shoot`, `throughBall`, `pass`, `cross`, `cleared`, `boot`, `penalty`]
  ballMovement.getBallDirection(matchDetails, thisPlayer.currentPOS)
  let tempArray = thisPlayer.currentPOS
  matchDetails.ball.position = tempArray.map(x => x)
  matchDetails.ball.position[2] = 0
  if (ballActions.includes(action)) {
    ballMoved(matchDetails, thisPlayer, team, opp)
    if (action === `cleared` || action === `boot`) {
      let newPosition = ballMovement.ballKicked(matchDetails, team, thisPlayer)
      updateInformation(matchDetails, newPosition)
    } else if (action === `pass`) {
      let newPosition = ballMovement.ballPassed(matchDetails, team, thisPlayer)
      matchDetails.iterationLog.push(`passed to new position: ${newPosition}`)
      updateInformation(matchDetails, newPosition)
    } else if (action === `cross`) {
      let newPosition = ballMovement.ballCrossed(matchDetails, team, thisPlayer)
      matchDetails.iterationLog.push(`crossed to new position: ${newPosition}`)
      updateInformation(matchDetails, newPosition)
    } else if (action === `throughBall`) {
      let newPosition = ballMovement.throughBall(matchDetails, team, thisPlayer)
      updateInformation(matchDetails, newPosition)
    } else if (action === `shoot`) {
      let newPosition = ballMovement.shotMade(matchDetails, team, thisPlayer)
      updateInformation(matchDetails, newPosition)
    } else if (action === `penalty`) {
      let newPosition = ballMovement.penaltyTaken(matchDetails, team, thisPlayer)
      updateInformation(matchDetails, newPosition)
    }
  }
}

function ballMoved(matchDetails, thisPlayer, team, opp) {
  thisPlayer.hasBall = false
  matchDetails.ball.withPlayer = false
  team.intent = `attack`
  opp.intent = `attack`
  matchDetails.ball.Player = ``
  matchDetails.ball.withTeam = ``
}

function updateInformation(matchDetails, newPosition) {
  if (matchDetails.endIteration == true) return
  let tempPosition = newPosition.map(x => x)
  matchDetails.ball.position = tempPosition
  matchDetails.ball.position[2] = 0
}

function getMovement(player, action, opposition, ballX, ballY, matchDetails) {
  const { position } = matchDetails.ball
  const ballActions = [`shoot`, `throughBall`, `pass`, `cross`, `cleared`, `boot`, `penalty`]
  if (action === `wait` || ballActions.includes(action)) return [0, 0]
  else if (action === `tackle` || action === `slide`) {
    return getTackleMovement(ballX, ballY)
  } else if (action === `intercept`) {
    return getInterceptMovement(player, opposition, position, matchDetails.pitchSize)
  } else if (action === `run`) {
    return getRunMovement(matchDetails, player, ballX, ballY)
  } else if (action === `sprint`) {
    return getSprintMovement(matchDetails, player, ballX, ballY)
  }
}

function getTackleMovement(ballX, ballY) {
  let move = [0, 0]
  if (ballX > 0) move[0] = -1
  else if (ballX === 0) move[0] = 0
  else if (ballX < 0) move[0] = 1
  if (ballY > 0) move[1] = -1
  else if (ballY === 0) move[1] = 0
  else if (ballY < 0) move[1] = 1
  return move
}

function getInterceptMovement(player, opposition, ballPosition, pitchSize) {
  let move = [0, 0]
  let intcptPos = getInterceptPosition(player.currentPOS, opposition, ballPosition, pitchSize)
  let intcptPosX = player.currentPOS[0] - intcptPos[0]
  let intcptPosY = player.currentPOS[1] - intcptPos[1]
  if (intcptPosX === 0) {
    if (intcptPosY === 0) move = [0, 0]
    else if (intcptPosY < 0) move = [0, 1]
    else if (intcptPosY > 0) move = [0, -1]
  } else if (intcptPosY === 0) {
    if (intcptPosX < 0) move = [1, 0]
    else if (intcptPosX > 0) move = [-1, 0]
  } else if (intcptPosX < 0 && intcptPosY < 0) move = [1, 1]
  else if (intcptPosX > 0 && intcptPosY > 0) move = [-1, -1]
  else if (intcptPosX > 0 && intcptPosY < 0) move = [-1, 1]
  else if (intcptPosX < 0 && intcptPosY > 0) move = [1, -1]
  return move
}

function getInterceptPosition(currentPOS, opposition, ballPosition, pitchSize) {
  let BallPlyTraj = getInterceptTrajectory(opposition, ballPosition, pitchSize)
  let intcptPos = getClosestTrajPosition(currentPOS, BallPlyTraj, false)
  if (JSON.stringify(intcptPos) === JSON.stringify(currentPOS)) {
    let index = getClosestTrajPosition(currentPOS, BallPlyTraj, true)
    if (index > 0) return BallPlyTraj[getClosestTrajPosition(currentPOS, BallPlyTraj, true) - 1]
  }
  return intcptPos
}

function getClosestTrajPosition(playerPos, BallPlyTraj, getIndex) {
  let intcptPos = []
  let theDiff = 10000000
  let index = 0
  for (let thisPos of BallPlyTraj) {
    let xDiff = Math.abs(playerPos[0] - thisPos[0])
    let yDiff = Math.abs(playerPos[1] - thisPos[1])
    let totalDiff = xDiff + yDiff
    if (totalDiff < theDiff) {
      theDiff = totalDiff
      intcptPos = thisPos
    }
    if (JSON.stringify(thisPos) == JSON.stringify(playerPos) && getIndex) return index
    index++
  }
  return intcptPos
}

function getInterceptTrajectory(opposition, ballPosition, pitchSize) {
  let [pitchWidth, pitchHeight] = pitchSize
  let playerInformation = setPositions.closestPlayerToPosition(`name`, opposition, ballPosition)
  let interceptPlayer = playerInformation.thePlayer
  let targetX = pitchWidth / 2
  let targetY = (interceptPlayer.originPOS[1] < pitchHeight / 2) ? pitchHeight : 0
  let moveX = targetX - interceptPlayer.currentPOS[0]
  let moveY = targetY - interceptPlayer.currentPOS[1]
  let highNum = (Math.abs(moveX) <= Math.abs(moveY)) ? Math.abs(moveY) : Math.abs(moveX)
  let xDiff = moveX / highNum
  let yDiff = moveY / highNum
  let POI = []
  POI.push(interceptPlayer.currentPOS)
  for (let i of new Array(Math.round(highNum))) {
    let lastArrayPOS = POI.length - 1
    let lastXPOS = POI[lastArrayPOS][0]
    let lastYPOS = POI[lastArrayPOS][1]
    POI.push([common.round(lastXPOS + xDiff, 0), common.round(lastYPOS + yDiff, 0)])
  }
  return POI
}

function getRunMovement(matchDetails, player, ballX, ballY) {
  let move = [0, 0]
  if (player.fitness > 20) player.fitness = common.round(player.fitness - 0.005, 6)
  let side = (player.originPOS[1] > matchDetails.pitchSize[1] / 2) ? `bottom` : `top`
  if (player.hasBall && side == `bottom`) return [common.getRandomNumber(0, 2), common.getRandomNumber(0, 2)]
  if (player.hasBall && side == `top`) return [common.getRandomNumber(-2, 0), common.getRandomNumber(-2, 0)]
  let movementRun = [-1, 0, 1]
  if (common.isBetween(ballX, -60, 60) && common.isBetween(ballY, -60, 60)) {
    if (common.isBetween(ballX, -60, 0)) move[0] = movementRun[common.getRandomNumber(2, 2)]
    else if (common.isBetween(ballX, 0, 60)) move[0] = movementRun[common.getRandomNumber(0, 0)]
    else move[0] = movementRun[common.getRandomNumber(1, 1)]
    if (common.isBetween(ballY, -60, 0)) move[1] = movementRun[common.getRandomNumber(2, 2)]
    else if (common.isBetween(ballY, 0, 60)) move[1] = movementRun[common.getRandomNumber(0, 0)]
    else move[1] = movementRun[common.getRandomNumber(1, 1)]
    return move
  }
  let formationDirection = setPositions.formationCheck(player.intentPOS, player.currentPOS)
  if (formationDirection[0] === 0) move[0] = movementRun[common.getRandomNumber(1, 1)]
  else if (formationDirection[0] < 0) move[0] = movementRun[common.getRandomNumber(0, 1)]
  else if (formationDirection[0] > 0) move[0] = movementRun[common.getRandomNumber(1, 2)]
  if (formationDirection[1] === 0) move[1] = movementRun[common.getRandomNumber(1, 1)]
  else if (formationDirection[1] < 0) move[1] = movementRun[common.getRandomNumber(0, 1)]
  else if (formationDirection[1] > 0) move[1] = movementRun[common.getRandomNumber(1, 2)]
  return move
}

function getSprintMovement(matchDetails, player, ballX, ballY) {
  let move = [0, 0]
  if (player.fitness > 30) player.fitness = common.round(player.fitness - 0.01, 6)
  let side = (player.originPOS[1] > matchDetails.pitchSize[1] / 2) ? `bottom` : `top`
  if (player.hasBall && side == `bottom`) return [common.getRandomNumber(-4, 4), common.getRandomNumber(-4, -2)]
  if (player.hasBall && side == `top`) return [common.getRandomNumber(-4, 4), common.getRandomNumber(2, 4)]
  let movementSprint = [-2, -1, 0, 1, 2]
  if (common.isBetween(ballX, -60, 60) && common.isBetween(ballY, -60, 60)) {
    if (common.isBetween(ballX, -60, 0)) move[0] = movementSprint[common.getRandomNumber(3, 4)]
    else if (common.isBetween(ballX, 0, 60)) move[0] = movementSprint[common.getRandomNumber(0, 1)]
    else move[0] = movementSprint[common.getRandomNumber(2, 2)]
    if (common.isBetween(ballY, -60, 0)) move[1] = movementSprint[common.getRandomNumber(3, 4)]
    else if (common.isBetween(ballY, 0, 60)) move[1] = movementSprint[common.getRandomNumber(0, 1)]
    else move[1] = movementSprint[common.getRandomNumber(2, 2)]
    return move
  }
  let formationDirection = setPositions.formationCheck(player.intentPOS, player.currentPOS)
  if (formationDirection[0] === 0) move[0] = movementSprint[common.getRandomNumber(2, 2)]
  else if (formationDirection[0] < 0) move[0] = movementSprint[common.getRandomNumber(0, 2)]
  else if (formationDirection[0] > 0) move[0] = movementSprint[common.getRandomNumber(2, 4)]
  if (formationDirection[1] === 0) move[1] = movementSprint[common.getRandomNumber(2, 2)]
  else if (formationDirection[1] < 0) move[1] = movementSprint[common.getRandomNumber(0, 2)]
  else if (formationDirection[1] > 0) move[1] = movementSprint[common.getRandomNumber(2, 4)]
  return move
}

function closestPlayerToBall(closestPlayer, team, matchDetails) {
  let closestPlayerDetails
  let { position } = matchDetails.ball
  for (let thisPlayer of team.players) {
    let ballToPlayerX = Math.abs(thisPlayer.currentPOS[0] - position[0])
    let ballToPlayerY = Math.abs(thisPlayer.currentPOS[1] - position[1])
    let proximityToBall = ballToPlayerX + ballToPlayerY
    if (proximityToBall < closestPlayer.position) {
      closestPlayer.name = thisPlayer.name
      closestPlayer.position = proximityToBall
      closestPlayerDetails = thisPlayer
    }
  }

  setPositions.setIntentPosition(matchDetails, closestPlayerDetails)
  matchDetails.iterationLog.push(`Closest Player to ball: ${closestPlayerDetails.name}`)
}

function checkOffside(team1, team2, matchDetails) {
  const { ball } = matchDetails
  const { pitchSize } = matchDetails
  const team1side = (team1.players[0].originPOS[1] < (pitchSize[1] / 2)) ? `top` : `bottom`
  if (ball.withTeam == false) return matchDetails
  if (team1side == `bottom`) {
    team1atBottom(team1, team2, pitchSize[1])
  } else {
    team1atTop(team1, team2, pitchSize[1])
  }
}

function getTopMostPlayer(team, pitchHeight) {
  let player
  for (let thisPlayer of team.players) {
    let topMostPosition = pitchHeight
    let [, plyrX] = thisPlayer.currentPOS
    if (thisPlayer.currentPOS[1] < topMostPosition) {
      topMostPosition = plyrX
      player = thisPlayer
    }
  }
  return player
}

function getBottomMostPlayer(team) {
  let player
  for (let thisPlayer of team.players) {
    let topMostPosition = 0
    let [, plyrX] = thisPlayer.currentPOS
    if (thisPlayer.currentPOS[1] > topMostPosition) {
      topMostPosition = plyrX
      player = thisPlayer
    }
  }
  return player
}

function team1atBottom(team1, team2, pitchHeight) {
  let offT1Ypos = offsideYPOS(team2, `top`, pitchHeight)
  let topPlayer = getTopMostPlayer(team1, pitchHeight)
  let topPlayerOffsidePosition = common.isBetween(topPlayer.currentPOS[1], offT1Ypos.pos1, offT1Ypos.pos2)
  if (topPlayerOffsidePosition && topPlayer.hasBall) return
  for (let thisPlayer of team1.players) {
    thisPlayer.offside = false
    if (common.isBetween(thisPlayer.currentPOS[1], offT1Ypos.pos1, offT1Ypos.pos2)) {
      if (!thisPlayer.hasBall) thisPlayer.offside = true
    }
  }
  let offT2Ypos = offsideYPOS(team1, `bottom`, pitchHeight)
  let btmPlayer = getBottomMostPlayer(team2)
  let btmPlayerOffsidePosition = common.isBetween(btmPlayer.currentPOS[1], offT2Ypos.pos2, offT2Ypos.pos1)
  if (btmPlayerOffsidePosition && btmPlayer.hasBall) return
  for (let thisPlayer of team2.players) {
    thisPlayer.offside = false
    if (common.isBetween(thisPlayer.currentPOS[1], offT2Ypos.pos2, offT2Ypos.pos1)) {
      if (!thisPlayer.hasBall) thisPlayer.offside = true
    }
  }
}

function team1atTop(team1, team2, pitchHeight) {
  let offT1Ypos = offsideYPOS(team2, `bottom`, pitchHeight)
  let btmPlayer = getBottomMostPlayer(team1)
  let btmPlayerOffsidePosition = common.isBetween(btmPlayer.currentPOS[1], offT1Ypos.pos2, offT1Ypos.pos1)
  if (btmPlayerOffsidePosition && btmPlayer.hasBall) return
  for (let thisPlayer of team1.players) {
    thisPlayer.offside = false
    if (common.isBetween(thisPlayer.currentPOS[1], offT1Ypos.pos2, offT1Ypos.pos1)) {
      if (!thisPlayer.hasBall) thisPlayer.offside = true
    }
  }
  let offT2Ypos = offsideYPOS(team1, `top`, pitchHeight)
  let topPlayer = getTopMostPlayer(team2, pitchHeight)
  let topPlayerOffsidePosition = common.isBetween(topPlayer.currentPOS[1], offT2Ypos.pos1, offT2Ypos.pos2)
  if (topPlayerOffsidePosition && topPlayer.hasBall) return
  for (let thisPlayer of team2.players) {
    thisPlayer.offside = false
    if (common.isBetween(thisPlayer.currentPOS[1], offT2Ypos.pos1, offT2Ypos.pos2)) {
      if (!thisPlayer.hasBall) thisPlayer.offside = true
    }
  }
}

function offsideYPOS(team, side, pitchHeight) {
  let offsideYPOS = {
    'pos1': 0,
    'pos2': pitchHeight / 2
  }
  for (let thisPlayer of team.players) {
    if (thisPlayer.position == `GK`) {
      let [, position1] = thisPlayer.currentPOS
      offsideYPOS.pos1 = position1
      if (thisPlayer.hasBall) {
        offsideYPOS.pos2 = position1
        return offsideYPOS
      }
    } else if (side == `top`) {
      if (thisPlayer.currentPOS[1] < offsideYPOS.pos2) {
        let [, position2] = thisPlayer.currentPOS
        offsideYPOS.pos2 = position2
      }
    } else if (thisPlayer.currentPOS[1] > offsideYPOS.pos2) {
      let [, position2] = thisPlayer.currentPOS
      offsideYPOS.pos2 = position2
    }
  }
  return offsideYPOS
}

module.exports = {
  decideMovement,
  getMovement,
  closestPlayerToBall,
  closestPlayerActionBallX,
  closestPlayerActionBallY,
  setClosePlayerTakesBall,
  team1atBottom,
  team1atTop,
  handleBallPlayerActions,
  updateInformation,
  ballMoved,
  getSprintMovement,
  getRunMovement,
  checkProvidedAction,
  checkOffside,
  completeSlide,
  movePlayers,
  executeBallAction
}
