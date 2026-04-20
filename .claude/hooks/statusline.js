#!/usr/bin/env node
// Context gauge statusline script
// Displays context usage + optional rate limit gauges (when plan provides rate_limits data)

let raw = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', chunk => (raw += chunk));
process.stdin.on('end', () => {
  let data = {};
  try {
    data = JSON.parse(raw);
  } catch {
    // fallback to empty object
  }

  // ANSI color codes
  const GREEN  = '\x1b[32m';
  const RED    = '\x1b[31m';
  const YELLOW = '\x1b[33m';
  const ORANGE = '\x1b[38;5;208m';
  const WHITE  = '\x1b[97m';
  const DIM    = '\x1b[2m';
  const RESET  = '\x1b[0m';

  const BLOCK       = '█';
  const BLOCK_EMPTY = '░';
  const TOTAL_CELLS = 10;

  function pctColor(pct) {
    if (pct > 90)      return RED;
    else if (pct > 75) return ORANGE;
    else if (pct > 60) return YELLOW;
    else               return GREEN;
  }

  function buildGauge(pct) {
    const filledCells = Math.min(Math.floor(pct / 10), TOTAL_CELLS);
    const emptyCells  = TOTAL_CELLS - filledCells;
    const color = pctColor(pct);
    return (
      DIM + '[' + RESET +
      color + BLOCK.repeat(filledCells) + RESET +
      DIM   + BLOCK_EMPTY.repeat(emptyCells) + RESET +
      DIM + ']' + RESET
    );
  }

  function formatResetTime(resetsAt) {
    if (!resetsAt) return '';
    // Claude Code passes resets_at as Unix timestamp (seconds), not milliseconds
    const tsMs = typeof resetsAt === 'number' ? resetsAt * 1000 : new Date(resetsAt).getTime();
    const diffMs = tsMs - Date.now();
    if (diffMs <= 0) return '';
    const diffSec = Math.floor(diffMs / 1000);
    const days  = Math.floor(diffSec / 86400);
    const hours = Math.floor((diffSec % 86400) / 3600);
    const mins  = Math.floor((diffSec % 3600) / 60);
    if (days > 0)  return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${mins}m`;
    return `${mins}m`;
  }

  // --- context usage ---
  const ctxPct = Math.round(data?.context_window?.used_percentage ?? 0);
  const parts = [
    DIM + 'context usage:' + RESET + ' ' +
    buildGauge(ctxPct) + ' ' +
    pctColor(ctxPct) + ctxPct + '%' + RESET
  ];

  // --- rate limits (only when data is present — plan-dependent) ---
  const rateLimits = data?.rate_limits;
  if (rateLimits) {
    // Field name candidates for 5-hour window
    const fiveHour =
      rateLimits.five_hour ??
      rateLimits['5h']     ??
      rateLimits.fiveHour  ??
      null;

    if (fiveHour) {
      const pct      = Math.round(fiveHour.used_percentage ?? 0);
      const resetStr = formatResetTime(fiveHour.resets_at);
      let part =
        DIM + '5hour limits:' + RESET + ' ' +
        buildGauge(pct) + ' ' +
        pctColor(pct) + pct + '%' + RESET;
      if (resetStr) part += ' ' + DIM + resetStr + RESET;
      parts.push(part);
    }

    // Field name candidates for 7-day window
    const sevenDay =
      rateLimits.seven_day ??
      rateLimits['7d']     ??
      rateLimits.sevenDay  ??
      null;

    if (sevenDay) {
      const pct      = Math.round(sevenDay.used_percentage ?? 0);
      const resetStr = formatResetTime(sevenDay.resets_at);
      let part =
        DIM + '7day limits:' + RESET + ' ' +
        buildGauge(pct) + ' ' +
        pctColor(pct) + pct + '%' + RESET;
      if (resetStr) part += ' ' + DIM + resetStr + RESET;
      parts.push(part);
    }
  }

  process.stdout.write(parts.join('  ') + '\n');
});
