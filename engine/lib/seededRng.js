// 可选种子化 RNG（plan 03§3 / 06§3.4）：用于双机字节级可复现 / 回归测试。
// 默认不启用（不影响正常随机比赛）。设环境变量 MIRO_SEED=<int> 时，覆盖 Math.random 为确定性序列。
// 引擎物理零改动——仅在进程启动时按需替换全局 Math.random（注入接缝, 非物理逻辑）。
function mulberry32(seed) {
  let a = seed >>> 0;
  return function () {
    a |= 0; a = (a + 0x6D2B79F5) | 0;
    let t = Math.imul(a ^ (a >>> 15), 1 | a);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

function maybeSeed() {
  const s = process.env.MIRO_SEED;
  if (s === undefined || s === '') return false;
  const seed = parseInt(s, 10);
  if (Number.isNaN(seed)) return false;
  Math.random = mulberry32(seed);
  console.error(`[seededRng] Math.random seeded with MIRO_SEED=${seed} (deterministic mode)`);
  return true;
}

module.exports = { mulberry32, maybeSeed };
