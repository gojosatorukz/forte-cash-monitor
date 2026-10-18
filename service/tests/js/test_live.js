// node tests/js/test_live.js — edge cases of app/static/live.js (run by tests/test_live_replay.py).
"use strict";
const assert = require("node:assert/strict");
const path = require("node:path");
const L = require(path.join(__dirname, "..", "..", "app", "static", "live.js"));

const a = { id: "a", t_sec: 0.5 }, b = { id: "b", t_sec: 7.27 }, c = { id: "c", t_sec: 10.4 };
const noT = { id: "n", t_sec: null }, bad = { id: "x", t_sec: "abc" }, undef = { id: "u" };
const steps = [a, b, c, noT, bad, undef];
const id = (s) => (s ? s.id : null);
const ids = (xs) => xs.map((s) => s.id).join(",");

// stepAtTime
assert.equal(id(L.stepAtTime(steps, 0)), null, "before first");
assert.equal(id(L.stepAtTime(steps, 0.49)), null, "just before first");
assert.equal(id(L.stepAtTime(steps, 0.5)), "a", "exact t_sec of first");
assert.equal(id(L.stepAtTime(steps, 3)), "a", "between");
assert.equal(id(L.stepAtTime(steps, 7.27)), "b", "exact t_sec");
assert.equal(id(L.stepAtTime(steps, 99)), "c", "after last");
assert.equal(id(L.stepAtTime([c, noT, a, b], 8)), "b", "unsorted input");
assert.equal(id(L.stepAtTime([noT, bad, undef], 5)), null, "missing t_sec ignored");
assert.equal(id(L.stepAtTime([{ id: "z", t_sec: 0 }], 0)), "z", "t_sec 0 at t 0");
assert.equal(id(L.stepAtTime([{ id: "p", t_sec: 2 }, { id: "q", t_sec: 2 }], 2)), "q", "tie -> later entry");
assert.equal(id(L.stepAtTime([{ id: "s", t_sec: "4" }], 5)), "s", "numeric string t_sec");
assert.equal(id(L.stepAtTime([{ id: "bool", t_sec: true }], 5)), null, "boolean t_sec ignored");
assert.equal(L.stepAtTime(null, 5), null, "no steps");
assert.equal(L.stepAtTime(steps, NaN), null, "NaN time");

// crossedSteps
assert.equal(ids(L.crossedSteps(steps, 7.0, 7.3)), "b", "normal play crosses b");
assert.equal(ids(L.crossedSteps(steps, 7.0, 7.27)), "b", "landing exactly on t_sec counts");
assert.equal(ids(L.crossedSteps(steps, 7.27, 7.5)), "", "prevT == t_sec is not a crossing (half-open)");
assert.equal(ids(L.crossedSteps(steps, 0, 0.4)), "", "nothing crossed");
assert.equal(ids(L.crossedSteps(steps, 3, 7.3)), "", "seek jump ignored");
assert.equal(ids(L.crossedSteps(steps, 6, 7.5)), "", "exactly 1.5 s is a seek");
assert.equal(ids(L.crossedSteps(steps, 7.3, 7.0)), "", "backward ignored");
assert.equal(ids(L.crossedSteps(steps, 7.3, 7.3)), "", "no movement");
assert.equal(ids(L.crossedSteps([c, { id: "d", t_sec: 10.1 }, noT], 10.0, 10.5)), "d,c", "ordered by t_sec");
assert.equal(ids(L.crossedSteps([noT, bad, undef], 0, 1)), "", "missing t_sec ignored");
assert.equal(ids(L.crossedSteps(steps, null, 0.6)), "", "no previous sample");
assert.equal(ids(L.crossedSteps(null, 0, 1)), "", "no steps");

console.log("live.js: all assertions passed");
