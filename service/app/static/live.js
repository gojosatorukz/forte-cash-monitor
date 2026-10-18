/* Live replay of the operation steps: pure time helpers shared by the UI (window.LiveReplay)
 * and the node test (module.exports). No DOM access here. */
(function (root) {
  "use strict";

  /** Max forward step between two playback samples still counted as normal play (else: a seek). */
  var MAX_PLAY_STEP_SEC = 1.5;

  function timeOf(step) {
    if (!step || typeof step !== "object") return null;
    var v = step.t_sec;
    if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
    var n = Number(v);
    return isFinite(n) ? n : null;
  }

  /** The step whose t_sec <= t with the largest t_sec (later array entry wins a tie); null before the first. */
  function stepAtTime(steps, t) {
    var cur = Number(t);
    if (!Array.isArray(steps) || !isFinite(cur)) return null;
    var best = null, bestT = -Infinity;
    for (var i = 0; i < steps.length; i++) {
      var st = timeOf(steps[i]);
      if (st === null || st > cur) continue;
      if (st >= bestT) { best = steps[i]; bestT = st; }
    }
    return best;
  }

  /** Steps with t_sec in (prevT, t], ordered by t_sec — only for a normal forward play step
   *  (0 < t - prevT < 1.5 s); seeks, scrubbing and backward moves return []. */
  function crossedSteps(steps, prevT, t) {
    var a = Number(prevT), b = Number(t);
    if (!Array.isArray(steps) || prevT === null || prevT === undefined || !isFinite(a) || !isFinite(b)) return [];
    var dt = b - a;
    if (!(dt > 0 && dt < MAX_PLAY_STEP_SEC)) return [];
    var out = [];
    for (var i = 0; i < steps.length; i++) {
      var st = timeOf(steps[i]);
      if (st !== null && st > a && st <= b) out.push({ s: steps[i], t: st, i: i });
    }
    out.sort(function (x, y) { return x.t - y.t || x.i - y.i; });
    return out.map(function (x) { return x.s; });
  }

  var api = { stepAtTime: stepAtTime, crossedSteps: crossedSteps, timeOf: timeOf, MAX_PLAY_STEP_SEC: MAX_PLAY_STEP_SEC };
  if (typeof module === "object" && module && module.exports) module.exports = api;
  if (root) root.LiveReplay = api;
})(typeof window !== "undefined" ? window : null);
