"""The settings every effect shares (On the beat, Color tricks, Company, Dreamy lights), and the carry-over from the sliders each effect kept before. Standard library only, and node when it's there:

    python -m unittest discover -s tests
"""
import json
import os
import shutil
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); import _no_real_settings  # noqa: E402,E702,F401  first of all: keeps the tests away from your own settings, music and token

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src", "music_curator", "web")

# The page's own code, run in node: packs.js whole, app.js's block for the shared settings and its fxAdjust(), with stand-ins for what they use from the rest of the page
# (the browser's storage is a Map kept between two starts, which can refuse a write the way a full one does; the effects are only their names and styles, read from effects.js itself;
# a page element is a plain object with its tag, its attributes and what is in it).
HARNESS = r"""
const fs = require("fs"), vm = require("vm"), web = %(web)s;
const text = (name) => fs.readFileSync(web + "/" + name, "utf8").replace(/\r\n/g, "\n");
const app = text("app.js"), packs = text("packs.js"), fx = text("effects.js");
const begin = app.indexOf("/* The settings every effect shares, kept as curator-effects"), end = app.indexOf("\nfunction saveTuning()", begin);
if (begin < 0 || end < 0) throw new Error("the block for the shared settings is not in app.js");
const block = app.slice(begin, end), adjust = app.slice(app.indexOf("function fxAdjust() {"), app.indexOf("\nconst fxBar = ", app.indexOf("function fxAdjust() {")));
if (!adjust.startsWith("function fxAdjust")) throw new Error("fxAdjust() is not in app.js");
const STYLES = fx.slice(fx.indexOf("const STYLES = {"), fx.indexOf("\n  };\n", fx.indexOf("const STYLES = {")) + 5);
const NAMES = fx.match(/\n    names: (\[\[.*?\]\]),/)[1], KNOBS = fx.match(/\n  const KNOBS = (\{.*?\});/)[1];
function start(storage, refused = [], noFx = false) { // one start of the app: what it keeps, what it said (and for how long), what it handed the picture, what it does as the page closes.
  // A key in refused is never written, as in a full browser storage; noFx: a start where effects.js did not load
  const ctx = { storage, refused, noFx, said: [], waits: [], handed: [], closing: [], console };
  vm.createContext(ctx);
  vm.runInContext(`
    const window = globalThis, localStorage = { getItem: () => null, setItem() {} };
    window.addEventListener = (name, f) => { if (name === "pagehide") closing.push(f); };
    ${packs}
    const Packs = window.Packs;
    ${STYLES}
    const KNOBS = ${KNOBS};
    const store = (k, v) => { if (v === undefined) return storage.has(k) ? storage.get(k) : null; if (!refused.includes(k)) storage.set(k, String(v)); return null; };
    const toast = (m) => said.push(m), setTimeout = (f, ms) => { waits.push(ms); f(); }, clearTimeout = () => {}, $ = () => ({});
    let toastTimer;
    const FX = noFx ? null : { names: ${NAMES}, tuning: (v) => handed.push(v), knobs: () => [["light", "Brightness", 0.3, 1.7], ["punch", "Hard kicks", 0, 2], ["cover", "The record's colors", 0, 3], ["quick", "Quick colors come", 0, 2], ["folk", "Visitors", 0, 3], ["sounds", "Answers to sounds", 0, 2], ["dream", "Dreamy lights", 0, 2], ["band", "Crowd and dancers", 0, 2], ["hand", "Answers to the mouse", 0, 2]] }, fxStyles = STYLES;
    const el = (tag, attrs, ...kids) => ({ tag, attrs: attrs || {}, kids: kids.flat(9).filter((k) => k !== null && k !== undefined && k !== false) }), behindKnobs = () => [], paintFxBar = () => {};
    const saveTuning = () => store("curator-fx-tuning", JSON.stringify(fxTuning));
    const familyOf = (name) => Object.keys(fxStyles).find((k) => fxStyles[k].some(([style]) => style === name)) || "";
    let fxName = familyOf(store("curator-fx")) ? store("curator-fx") : "", fxMorph = store("curator-fx-morph") === "1";
    let fxTuning = {};
    try { fxTuning = JSON.parse(store("curator-fx-tuning") || "{}") || {}; } catch { fxTuning = {}; }
    ${block}
    ${adjust}
    const drawn = (values) => Object.fromEntries(Object.entries(KNOBS).map(([key, [, low, high]]) => { const v = Number(values[key]); return [key, Number.isFinite(v) ? Math.min(high, Math.max(low, v)) : 1]; })); // what effects.js makes of what it is handed
    globalThis.api = { Packs, KNOBS, drawn, tuningFor, carryShared, forPack, fromPack, usePackEffects, fxAdjust, countUsed, fxUsed, get fxShared() { return fxShared; }, get fxTuning() { return fxTuning; }, tuningNow, order: FX ? fxOrder() : [] };
  `, ctx);
  return ctx;
}
const out = {};
const SHARED = { punch: ["beat", "own"], spark: ["beat", "shine"], sounds: ["beat", "hear"], tricks: ["tricks", "play"], stay: ["tricks", "hold"], bold: ["tricks", "force"], dream: ["lights", "glow"], folk: ["company", "visit"], acid: ["company", "sing"], band: ["company", "crowd"] }; // each shared slider: its group and the list of each effect's own values
const FIELD = { punch: "punch", spark: "spark", sounds: "sounds", tricks: "often", stay: "long", bold: "strong", dream: "dream", folk: "folk", acid: "acid", band: "band" }; // and the one for every effect
const allOnes = (shared) => Object.entries(SHARED).filter(([key, [group]]) => shared[group][FIELD[key]] !== 1).map(([key]) => key); // the shared sliders that do not start as designed
const movedIn = (C, kept) => Object.fromEntries(Object.keys(SHARED).map((key) => [key, [...new Set([...C.order, ...Object.keys(kept)])].filter((style) => C.drawn(C.tuningFor(C.fxShared, C.fxTuning[style], style))[key] !== C.drawn(kept[style] && typeof kept[style] === "object" ? kept[style] : {})[key])])); // for each shared slider, the styles drawn with another value than they had
// 1. made-up kept values: eight effects set up. Seven turned the visitors off; Julia, showing and set up the most, kept them and moved Color tricks and its own sliders
const old = { julia: { folk: 1.5, tricks: 0.6, punch: 1.4, sounds: 1.6, light: 1.2, speed: 0.7, color: 1.3 }, mandel: { folk: 0, light: 0.9 }, newton: { folk: 0, speed: 1.2 }, kaleido: { folk: 0, vary: 2 }, tube: { folk: 0, light: 1.1 }, runner: { folk: 0, speed: 0.9 }, faces: { folk: 0, hand: 0 }, aero: { folk: 0, light: 0.8 } };
const kept = new Map([["curator-fx-tuning", JSON.stringify(old)], ["curator-fx", "julia"], ["curator-fx-last", "julia"]]);
const first = start(kept), A = first.api, before = new Map(kept);
out.v1 = kept.get("curator-fx-tuning-v1") === JSON.stringify(old);
out.said = first.said;
out.waits = first.waits;
out.shared = A.fxShared;
out.notOne = allOnes(A.fxShared);
out.tuningLeft = kept.get("curator-fx-tuning") === JSON.stringify(old);
out.tuning = A.fxTuning;
const second = start(kept);
out.secondSaid = second.said;
out.secondSame = JSON.stringify([...kept]) === JSON.stringify([...before]);
out.differ = [...new Set([...A.order, ...Object.keys(old)])].filter((style) => JSON.stringify(A.drawn(A.tuningFor(A.fxShared, A.fxTuning[style], style))) !== JSON.stringify(A.drawn(old[style] || {})));
out.ownLost = Object.entries(old).flatMap(([style, knobs]) => Object.entries(knobs).filter(([key]) => !["punch", "spark", "sounds", "tricks", "stay", "bold", "dream", "folk", "acid", "band"].includes(key) && (A.fxTuning[style] || {})[key] !== knobs[key]).map(([key]) => style + "." + key));
out.again = JSON.stringify(A.carryShared({ tuning: A.fxTuning, order: A.order, fx: "julia", last: "julia" }).effects) === JSON.stringify(A.fxShared);
out.handedOnSave = (() => { second.handed.length = 0; second.api.fxShared.beat.spark = 1.8; vm.runInContext("saveShared()", second); return second.handed[0].spark; })(); // (Glints: Julia has Hard kicks of its own)
// 2. nothing ever moved: nothing to carry, nothing said, only curator-effects written
const fresh = new Map(), blank = start(fresh);
out.blank = { keys: [...fresh.keys()], said: blank.said, same: JSON.stringify(blank.api.drawn(blank.api.tuningNow())) === JSON.stringify(blank.api.drawn({})) };
{ // only an effect's own sliders were ever moved: the shared settings are kept, and there is nothing to say
  const keep = new Map([["curator-fx-tuning", JSON.stringify({ ocean: { light: 0.6, vary: 0 }, city: { speed: 2 } })], ["curator-fx", "city"]]), S = start(keep);
  out.ownOnly = { said: S.said, effects: keep.has("curator-effects"), v1: keep.has("curator-fx-tuning-v1") };
}
{ // a start where effects.js did not load only puts the old sliders aside: it carries in memory, and the next one with the pictures carries over for good
  const keep = new Map([["curator-fx-tuning", JSON.stringify(old)], ["curator-fx", "julia"]]), S = start(keep, [], true);
  const keys = [...keep.keys()], v1 = keep.get("curator-fx-tuning-v1") === JSON.stringify(old), S2 = start(keep);
  out.noFx = { keys, v1, said: S.said, own: S.api.fxShared.beat.own, then: { own: S2.api.fxShared.beat.own, said: S2.said.length } };
}
// 3. the switches and How often, as the picture is handed them
const S = A.Packs.cleanEffects({ beat: { on: false }, tricks: { on: false, cover: 3 }, company: { often: 0.5, folk: 2, acidOn: false, band: 1.6 }, lights: { on: false } });
out.switched = A.tuningFor(S, { light: 1.3, folk: 2 });
out.cap = (({ folk, acid, band }) => ({ folk, acid, band }))(A.tuningFor(A.Packs.cleanEffects({ company: { often: 2, folk: 3, acid: 2, band: 1.5 } }), {})); // How often at its top: each kind no higher than its own top
{ // a long list: 120 effects that are gone had their own Hard kicks, and Mandelbrot too (and ten odd names, left out before the 100 are counted). A list keeps 100, the styles in the app first
  const long = Object.fromEntries([...Array.from({ length: 10 }, (_, i) => [`Odd ${i}`, { punch: 0.5 }]), ...Array.from({ length: 120 }, (_, i) => [`gone-${i}`, { punch: 0.5 }]), ["mandel", { punch: 0.5 }]]);
  const own = start(new Map([["curator-fx-tuning", JSON.stringify(long)]])).api.fxShared.beat.own;
  out.longList = { count: Object.keys(own).length, mandel: own.mandel };
}
// 4. packs: one made before this version, and one made by it, read back here; and what an older copy of the app would find in the newer one
const oldPack = A.Packs.make("Before", "", { tuning: old, effect: "julia" }).settings;
out.oldPack = A.fromPack(oldPack, A.order);
const madeNow = A.Packs.make("After", "", { ...A.forPack(S, A.fxTuning, "julia"), effect: "julia" }).settings;
out.newPack = { tuning: madeNow.tuning, back: A.fromPack(madeNow, A.order), same: JSON.stringify(A.fromPack(madeNow, A.order).effects) === JSON.stringify(S) };
// 5. the ranges kept here are the ones effects.js gives each slider
out.ranges = Object.entries({ punch: ["beat", "punch"], spark: ["beat", "spark"], sounds: ["beat", "sounds"], tricks: ["tricks", "often"], stay: ["tricks", "long"], bold: ["tricks", "strong"], dream: ["lights", "dream"], folk: ["company", "folk"], acid: ["company", "acid"], band: ["company", "band"] }).filter(([key, [g, f]]) => JSON.stringify(A.Packs.EFFECTS[g][f].slice(1)) !== JSON.stringify(A.KNOBS[key].slice(1))).map(([key]) => key);
out.odd = A.Packs.cleanEffects({ v: 9, beat: { on: "yes", punch: 99, own: { julia: 9, mandel: -2, "Bad name": 1, gone: "x" } }, tricks: { quick: null, cover: 2.4, long: -1 }, company: { folk: "3", wild: NaN }, from: "<b>" });
// 6. the seconds each style is shown: a sleep counts nothing, and 80 styles at most
const U = start(new Map()), C = U.api;
for (let i = 0; i < 90; i++) vm.runInContext(`fxName = "s${i}"; countUsed(${i * 1000}); countUsed(${i * 1000 + 100 + i});`, U); // each a tenth of a second or a little more, the first the least
vm.runInContext(`fxName = "julia"; countUsed(200000); countUsed(205000);`, U);
out.used = { count: Object.keys(C.fxUsed).length, has0: "s0" in C.fxUsed, has89: "s89" in C.fxUsed, julia: C.fxUsed.julia || 0 };
{ // as the page closes, the seconds not yet written are written; a page that counted nothing writes nothing
  const keep = new Map(), P = start(keep);
  vm.runInContext(`fxName = "julia"; countUsed(1000); countUsed(1200); countUsed(1400);`, P);
  const before = keep.has("curator-fx-used");
  P.closing.forEach((f) => f());
  const idle = new Map(), Q = start(idle);
  Q.closing.forEach((f) => f());
  out.closing = { before, after: JSON.parse(keep.get("curator-fx-used") || "{}"), idle: [...idle.keys()] };
}
// 7. -v1 is never written over: a start without curator-effects that finds one already there leaves it, and then leaves the old sliders where they are too
{
  const there = new Map([["curator-fx-tuning", JSON.stringify(old)], ["curator-fx-tuning-v1", JSON.stringify({ julia: { light: 0.5 } })], ["curator-fx", "julia"]]);
  start(there);
  out.v1There = { v1: there.get("curator-fx-tuning-v1"), tuning: there.get("curator-fx-tuning") === JSON.stringify(old), effects: there.has("curator-effects") };
}
// 8. a full browser storage: when -v1 or curator-effects cannot be written, the old sliders are not taken off the effects, so nothing is lost
out.refused = Object.fromEntries(["curator-fx-tuning-v1", "curator-effects"].map((key) => {
  const keep = new Map([["curator-fx-tuning", JSON.stringify(old)], ["curator-fx", "julia"]]), S = start(keep, [key]), shared = JSON.stringify(S.api.fxShared), again = start(keep, [key]);
  const got = { tuning: keep.get("curator-fx-tuning") === JSON.stringify(old), v1: keep.has("curator-fx-tuning-v1"), sameNext: JSON.stringify(again.api.fxShared) === shared, said: S.said.length + again.said.length };
  const room = start(keep); // then the storage has room again
  return [key, { ...got, v1Later: keep.get("curator-fx-tuning-v1") === JSON.stringify(old), saidLater: room.said.length }];
}));
// 9. a kept null or "": effects.js drew it as 0, so it is kept as 0. Julia and Mandelbrot had Color tricks kept as null, Aero as "", Newton had 1.4; the same with Hard kicks
{
  const C = start(new Map([["curator-fx-tuning", JSON.stringify({ julia: { tricks: null, punch: null, folk: 1.5, light: 1.1 }, mandel: { tricks: null, punch: null, folk: 0 }, newton: { tricks: 1.4, punch: 1.4, folk: 0 }, aero: { tricks: "", punch: "" } })], ["curator-fx", "newton"]])).api;
  out.nulls = { tricks: C.fxShared.tricks.often, play: C.fxShared.tricks.play, folk: C.fxShared.company.folk, visit: C.fxShared.company.visit, punch: C.fxShared.beat.punch, own: C.fxShared.beat.own };
}
// 9b. Hard kicks: no style is drawn with other Hard kicks than it had, set up or not (a louder or a calmer one can each make an effect flash more often)
{
  const kicks = { julia: { punch: null, folk: 1.5 }, mandel: { punch: 1.4, folk: 0 }, newton: { punch: 1.4, folk: 0 }, aero: { punch: 1.4 }, tube: { punch: "loud", light: 0.9 }, kaleido: { punch: 9, speed: 1.2 }, faces: { punch: 1, hand: 0 } };
  const C = start(new Map([["curator-fx-tuning", JSON.stringify(kicks)], ["curator-fx", "julia"]])).api;
  out.kicks = { shared: C.fxShared.beat.punch, own: C.fxShared.beat.own, differ: C.order.filter((style) => C.drawn(C.tuningFor(C.fxShared, C.fxTuning[style], style)).punch !== C.drawn(kicks[style] || {}).punch) };
}
// 10. Adjust: the effect's own sliders, then "For every effect" with the shared ones. A slider the shared settings hand the picture (the record's colors, quick colors) is never shown as
// the effect's own, where it would do nothing. A shared slider writes to the shared settings, an own one to the effect
{
  const keep = new Map([["curator-fx-tuning", JSON.stringify({ julia: { light: 1.2 } })], ["curator-fx", "julia"], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({}))]]), S = start(keep), B = S.api;
  const parts = [];
  const walk = (n) => {
    if (!n || typeof n !== "object") return;
    if (n.attrs.class === "fx-every" || n.tag === "button") parts.push({ line: n.kids.join("") });
    if (n.tag === "label") parts.push({ label: n.kids[0].kids.join(""), input: n.kids[1].attrs }); else n.kids.forEach(walk);
  };
  walk(B.fxAdjust());
  const every = parts.findIndex((p) => p.line === "For every effect"), knob = (name) => parts.find((p) => p.label === name).input;
  knob("Hard kicks").oninput({ target: { value: "1.5" } });
  knob("Brightness").oninput({ target: { value: "0.8" } });
  out.adjust = { own: parts.slice(0, every).filter((p) => p.label).map((p) => p.label), every: parts.slice(every).filter((p) => p.label).map((p) => p.label),
    punch: B.fxShared.beat.punch, kept: JSON.parse(keep.get("curator-effects")).beat.punch, tuning: B.fxTuning, handed: S.handed[S.handed.length - 1].punch };
}
// 10b. an effect with Hard kicks of its own shows them among its own sliders; moving them changes that effect only; "Back to default" lets it follow the one for every effect again
{
  const keep = new Map([["curator-fx-tuning", JSON.stringify({ julia: { light: 1.2 } })], ["curator-fx", "julia"], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({ beat: { punch: 1.3, own: { julia: 0.4, mandel: 1.6 }, hear: { julia: 1.5 } } }))]]), S = start(keep), B = S.api;
  const parts = [];
  const walk = (n) => {
    if (!n || typeof n !== "object") return;
    if (n.attrs.class === "fx-every" || n.tag === "button") parts.push({ line: n.kids.join(""), attrs: n.attrs });
    if (n.tag === "label") parts.push({ label: n.kids[0].kids.join(""), input: n.kids[1].attrs }); else n.kids.forEach(walk);
  };
  walk(B.fxAdjust());
  const every = parts.findIndex((p) => p.line === "For every effect"), labels = (list) => list.filter((p) => p.label).map((p) => p.label), kick = parts.find((p) => p.label === "Hard kicks, this effect only").input;
  const own = labels(parts.slice(0, every)), shared = labels(parts.slice(every)), value = kick.value, heardValue = parts.find((p) => p.label === "Answers to sounds, this effect only").input.value;
  kick.oninput({ target: { value: "0.6" } });
  const moved = { own: { ...B.fxShared.beat.own }, shared: B.fxShared.beat.punch, kept: JSON.parse(keep.get("curator-effects")).beat.own.julia, handed: S.handed[S.handed.length - 1].punch };
  parts.find((p) => p.label === "Answers to sounds, this effect only").input.oninput({ target: { value: "0.7" } });
  const heard = { hear: { ...B.fxShared.beat.hear }, shared: B.fxShared.beat.sounds, kept: JSON.parse(keep.get("curator-effects")).beat.hear.julia, handed: S.handed[S.handed.length - 1].sounds, tuning: JSON.parse(JSON.stringify(B.fxTuning)) };
  const button = parts.find((p) => p.line === "Back to default");
  const wasOn = !button.attrs.disabled;
  button.attrs.onclick();
  out.adjustOwnKick = { own, shared, value, heardValue, moved, heard, wasOn, after: { own: B.fxShared.beat.own, hear: B.fxShared.beat.hear, kept: JSON.parse(keep.get("curator-effects")).beat.own, handed: S.handed[S.handed.length - 1].punch, tuning: B.fxTuning } };
}
// 11. bringing in a pack: one made before this version takes the shared sliders its own effect set, as they are; its own sliders replace this browser's,
// as they always did; one made now with no own sliders leaves this browser's alone. And a pack made while a recipe shows still gives an older app the shared sliders
{
  const keep = new Map([["curator-fx-tuning", JSON.stringify({ tube: { light: 0.5 } })], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({}))]]), B = start(keep).api;
  const before = B.Packs.make("Before", "", { tuning: { julia: { light: 1.2, speed: 0.8, punch: 1.6, folk: 0, stay: 1.5, bold: 0.6, band: 0.7 }, mandel: { light: 0.9, punch: 1.6, folk: 0 }, ocean: { vary: 0 } }, effect: "julia" }).settings;
  B.usePackEffects(before);
  const changed = (o) => Object.fromEntries(Object.entries(o).filter(([key, v]) => v !== 1 && key in B.KNOBS)); // (what differs from the designed 1, among what effects.js reads)
  const got = JSON.parse(JSON.stringify({ shared: B.fxShared, tuning: B.fxTuning, julia: changed(B.tuningFor(B.fxShared, B.fxTuning.julia, "julia")) }));
  B.usePackEffects(B.Packs.make("After", "", { effects: B.Packs.cleanEffects({ beat: { punch: 0.5 } }), effect: "julia" }).settings);
  const recipe = B.Packs.make("Recipe", "", { ...B.forPack(B.Packs.cleanEffects({ beat: { on: false }, from: "julia" }), { julia: { light: 1.2 } }, "recipe-mine-glow-ab12"), effect: "" }).settings;
  const leftOut = B.fromPack(B.Packs.make("Left out", "", { tuning: { julia: { light: 1.2, folk: 0 }, mandel: { stay: 1.6, folk: 2 }, newton: { stay: 1.6, folk: 2 } }, effect: "julia" }).settings, B.order); // (Julia set the visitors, not How long)
  const kicks = B.Packs.make("Kicks", "", { ...B.forPack(B.Packs.cleanEffects({ beat: { punch: 1.2, own: { julia: 0.3, mandel: 1.7 } } }), { julia: { light: 1.2 } }, "julia"), effect: "julia" }).settings;
  out.packIn = { before: got, packTuning: before.tuning, afterTuning: B.fxTuning, afterPunch: B.fxShared.beat.punch, recipe: recipe.tuning, leftOut: { long: leftOut.effects.tricks.long, hold: leftOut.effects.tricks.hold, folk: leftOut.effects.company.folk },
    kicks: { tuning: kicks.tuning, back: B.fromPack(kicks, B.order).effects.beat } };
}
// 12. No shared slider is voted on (one set higher or lower than an effect had can make it flash more often). Most effects set up had Answers to sounds at 2 and Visitors at 3, some had
// them lower; Julia, showing, had its own Answers to sounds, the 303 singer off and Dreamy lights off; an effect that is gone had its own. Every style is drawn as it was;
// packs carry each effect's own both ways, lowered ones too; an older pack keeps them too
{
  const kept3 = { julia: { light: 1.1, sounds: 0.5, acid: 0, dream: 0 }, mandel: { sounds: 2, folk: 3, punch: 1.4, band: 2, dream: 2, spark: 2, acid: 2 }, newton: { sounds: 0.4, punch: 0.3, folk: 3, light: 0.9, band: 2, dream: 2, tricks: 0, stay: 0.2, bold: 2 }, "gone-style": { sounds: 0.5, punch: 0.2, folk: 2, acid: 0 }, kaleido: { sounds: 2, folk: 3, band: 2, dream: 2, spark: 0 } };
  const C = start(new Map([["curator-fx-tuning", JSON.stringify(kept3)], ["curator-fx", "julia"]])).api;
  const made = C.Packs.make("Mine", "", { ...C.forPack(C.fxShared, C.fxTuning, "julia"), effect: "julia" }).settings, back = C.fromPack(made, C.order).effects;
  const older = C.fromPack(C.Packs.make("Older", "", { tuning: kept3, effect: "mandel" }).settings, C.order).effects;
  const off = C.Packs.make("Off", "", { ...C.forPack(C.Packs.cleanEffects({ beat: { on: false, hear: { mandel: 0.4 } } }), {}, "julia"), effect: "julia" }).settings.tuning; // (On the beat off: an older app is given 0)
  const mapsOf = (e) => Object.fromEntries(Object.entries(SHARED).map(([key, [group, map]]) => [key, e[group][map]]));
  out.kept3 = { notOne: allOnes(C.fxShared), maps: mapsOf(C.fxShared), moved: movedIn(C, kept3), handedJulia: (({ acid, dream, sounds }) => ({ acid, dream, sounds }))(C.tuningNow()),
    pack: made.tuning, back: mapsOf(back), off, older: { notOne: allOnes(older), maps: mapsOf(older) } };
}
// 13. "Back to default" can be pressed when the effect has only one of its own kept apart (and nothing else set), and lets it go. And while a pack has switched On the beat off,
// the effect's own Hard kicks and Answers to sounds say Off and rest, as the shared rows do; the same while a kind of company is off; and they work while their switch is on
out.apartOnly = Object.fromEntries(Object.values(SHARED).map(([group, map]) => {
  const keep = new Map([["curator-fx", "julia"], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({ [group]: { [map]: { julia: 0.4 } } }))]]), B = start(keep).api;
  const buttons = [], walk = (n) => { if (!n || typeof n !== "object") return; if (n.tag === "button") buttons.push(n.attrs); else n.kids.forEach(walk); };
  walk(B.fxAdjust());
  const enabled = !buttons[0].disabled;
  buttons[0].onclick();
  return [map, { enabled, after: JSON.parse(keep.get("curator-effects"))[group][map] }];
}));
{
  const keep = new Map([["curator-fx", "julia"], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({ beat: { on: false, own: { julia: 0.4 }, hear: { julia: 1.5 } }, lights: { on: false, glow: { julia: 1.8 } }, company: { folkOn: false, visit: { julia: 2 }, crowd: { julia: 0.5 } } }))]]), B = start(keep).api;
  const rows = [];
  const walk = (n) => { if (!n || typeof n !== "object") return; if (n.tag === "label") rows.push({ label: n.kids[0].kids.join(""), disabled: !!n.kids[1].attrs.disabled, shown: n.kids[2].kids.join("") }); else n.kids.forEach(walk); };
  walk(B.fxAdjust());
  out.beatOff = { rows, handed: (({ punch, sounds, dream, folk, band }) => ({ punch, sounds, dream, folk, band }))(B.tuningNow()) };
}
// 14. -v1 is never written over later either: curator-effects and -v1 are there, and an older copy of the app has put shared sliders back on the effects
{
  const keep = new Map([["curator-effects", JSON.stringify(A.Packs.cleanEffects({}))], ["curator-fx-tuning-v1", '{"julia":{"folk":2}}'], ["curator-fx-tuning", JSON.stringify({ julia: { folk: 0, light: 1.2 } })]]);
  start(keep);
  out.v1Later = keep.get("curator-fx-tuning-v1");
}
// 15. a pack keeps Surprises' main switch (surprises.on)
out.surprisesOn = A.Packs.make("Quiet", "", { surprises: { on: false, clock: true } }).settings.surprises;
const rowsOf = (node) => { const rows = {}, walk = (n) => { if (!n || typeof n !== "object") return; if (n.tag === "label") rows[n.kids[0].kids.join("")] = n.kids[1].attrs; else if (n.tag === "button") rows[n.kids.join("")] = n.attrs; else n.kids.forEach(walk); }; walk(node); return rows; }; // Adjust's sliders and buttons, by their words
// 16. kept values are never lost. This browser: Julia keeps Hard kicks of 0, Mandelbrot Answers to sounds of 0.3, a style that is gone its Visitors of 2.
// A newer pack with no own sliders ("After", and one made where nothing was moved) leaves them, as it leaves the effects' other own sliders, and the next start has them;
// own values of the ten that such a pack brings come in beside them; a newer pack with own sliders replaces both, as packs always did; "Back to default" on Julia leaves
// the others; a write that a full storage refuses loses nothing that was kept; an unreadable curator-effects is carried again from curator-fx-tuning
{
  const mine = { julia: { punch: 0, light: 1.2 }, mandel: { sounds: 0.3 }, "gone-style": { folk: 2 } };
  const browser = (more = []) => new Map([["curator-fx-tuning", JSON.stringify(mine)], ["curator-fx", "julia"], ...more]);
  const lists = (B) => JSON.parse(JSON.stringify({ own: B.fxShared.beat.own, hear: B.fxShared.beat.hear, visit: B.fxShared.company.visit }));
  const keep = browser(), S = start(keep), B = S.api, before = lists(B);
  const roundTrip = start(new Map([["curator-effects", JSON.stringify(A.Packs.cleanEffects({}))]])).api; // a pack of these settings, used in another newer app
  roundTrip.usePackEffects(B.Packs.make("Mine", "", { ...B.forPack(B.fxShared, B.fxTuning, "julia"), effect: "julia" }).settings);
  B.usePackEffects(B.Packs.make("After", "", { effects: B.Packs.cleanEffects({ beat: { punch: 0.5 } }), effect: "julia" }).settings);
  const after = { ...lists(B), punch: B.fxShared.beat.punch, handed: B.tuningNow().punch, tuning: JSON.parse(JSON.stringify(B.fxTuning)), kept: JSON.parse(keep.get("curator-effects")).beat.own, next: start(keep).api.tuningNow().punch };
  const maker = start(new Map()).api, plain = maker.Packs.make("Plain", "", { ...maker.forPack(maker.fxShared, maker.fxTuning, "plasma"), effect: "plasma" }).settings;
  B.usePackEffects(plain);
  const afterPlain = { keys: Object.keys(plain).sort(), ...lists(B), handed: B.tuningNow().punch };
  B.usePackEffects(B.Packs.make("Lists", "", { effects: B.Packs.cleanEffects({ beat: { own: { julia: 0.7, "gone-two": 0.2 } } }), effect: "julia" }).settings); // own values of the ten, but no own sliders (names an older app could not take)
  const withLists = lists(B);
  const full = B.Packs.make("Full", "", { ...B.forPack(B.Packs.cleanEffects({ beat: { own: { mandel: 1.7 } } }), { mandel: { light: 0.9 } }, "mandel"), effect: "mandel" }).settings;
  B.usePackEffects(full);
  const withOwn = { ...lists(B), tuning: JSON.parse(JSON.stringify(B.fxTuning)) };
  const D = start(browser()).api; // "Back to default" on Julia
  rowsOf(D.fxAdjust())["Back to default"].onclick();
  const back = lists(D);
  const fullKeep = browser(); start(fullKeep); // a full storage: curator-effects is refused while a pack with own sliders is used
  const F = start(fullKeep, ["curator-effects"]);
  F.api.usePackEffects(full);
  vm.runInContext("saveTuning()", F);
  const refused = lists(start(fullKeep).api);
  out.neverLost = { before, after, afterPlain, withLists, withOwn, back, refused, roundTrip: lists(roundTrip),
    unreadable: Object.fromEntries(["{broken", "null", "[]", "5", '"words"', "{}", '{"v":2,"beat":5}', '{"beat":{"punch":0.5,"own":3}}'].map((bad) => { const keepBad = browser([["curator-effects", bad], ["curator-fx-tuning-v1", JSON.stringify(mine)]]), K = start(keepBad); return [bad, { ...lists(K.api), handed: K.api.tuningNow().punch, said: K.said.length, punch: K.api.fxShared.beat.punch, left: keepBad.get("curator-effects") === bad }]; })) };
}
// 17. a pack keeps the effects' own sliders in the order they were kept: past 60 effects it leaves out the same ones as before this version
{
  const many = { julia: { punch: 0 } };
  for (const style of [...A.order.filter((style) => /^[a-z]{2,20}$/.test(style) && style !== "julia"), "gonea", "goneb", "gonec", "goned", "gonee"]) many[style] = { light: 1.1 };
  const C = start(new Map([["curator-fx-tuning", JSON.stringify(many)], ["curator-fx", "plasma"]])).api;
  const now = C.Packs.make("Now", "", { ...C.forPack(C.fxShared, C.fxTuning, "plasma"), effect: "plasma" }).settings.tuning, then = C.Packs.make("Then", "", { tuning: many, effect: "plasma" }).settings.tuning;
  out.sixty = { kept: Object.keys(many).length, count: Object.keys(now).length, same: JSON.stringify(now) === JSON.stringify(then), julia: now.julia };
}
// 18. nothing kept on the first start: -v1 is "{}", so a later start never puts other values aside as the old ones
{
  const keep = new Map([["curator-fx", "julia"]]);
  start(keep);
  const first = keep.get("curator-fx-tuning-v1");
  keep.set("curator-fx-tuning", JSON.stringify({ julia: { punch: 0 } })); // what an older copy of the app, or a pack, wrote later
  start(keep);
  out.v1Empty = { first, later: keep.get("curator-fx-tuning-v1") };
}
// 19. kept values are carried as they are, not rounded: never even a little higher than the person's own
{
  const C = start(new Map([["curator-fx-tuning", JSON.stringify({ julia: { punch: 1.0004, sounds: 0.9996, tricks: 0.12345, dream: 1.99999 } })], ["curator-fx", "julia"]])).api, t = C.tuningNow();
  out.unrounded = { punch: t.punch, sounds: t.sounds, tricks: t.tricks, dream: t.dream, own: C.fxShared.beat.own };
}
// 20. a pack that is used is copied: moving an own slider afterwards, or an own value of a shared one, leaves the pack as it was
{
  const B = start(new Map([["curator-fx", "julia"], ["curator-effects", JSON.stringify(A.Packs.cleanEffects({}))]])).api;
  const pack = B.Packs.make("Kept", "", { ...B.forPack(B.Packs.cleanEffects({ beat: { own: { julia: 0.3 } } }), { julia: { light: 1.2 } }, "julia"), effect: "julia" }).settings, text = JSON.stringify(pack);
  B.usePackEffects(pack);
  const rows = rowsOf(B.fxAdjust());
  rows["Brightness"].oninput({ target: { value: "0.5" } });
  rows["Hard kicks, this effect only"].oninput({ target: { value: "1.8" } });
  out.copied = { same: JSON.stringify(pack) === text, light: B.fxTuning.julia.light, own: B.fxShared.beat.own.julia };
}
// 21. a start where effects.js did not load, and a pack used or made on it: each effect's own values are kept, and -v1 is the old text
{
  const mine = { julia: { punch: 0, light: 1.2 }, mandel: { folk: 0 }, "gone-style": { sounds: 0.3 } };
  const browser = () => new Map([["curator-fx-tuning", JSON.stringify(mine)], ["curator-fx", "julia"]]);
  const lists = (B) => JSON.parse(JSON.stringify({ own: B.fxShared.beat.own, hear: B.fxShared.beat.hear, visit: B.fxShared.company.visit }));
  const maker = start(new Map()).api, plain = maker.Packs.make("After", "", maker.forPack(maker.fxShared, {}, "")).settings; // made where nothing was moved
  const use = (S, pack) => vm.runInContext(`usePackEffects(${JSON.stringify(pack)}); saveTuning();`, S);
  const plainKeep = browser(), P = start(plainKeep, [], true);
  use(P, plain);
  const P2 = start(plainKeep).api;
  const olderKeep = browser(), O = start(olderKeep, [], true);
  use(O, { tuning: { neon: { light: 1.3, punch: 1.5 } }, effect: "neon" });
  start(olderKeep);
  const M = start(browser(), [], true).api, made = M.Packs.make("Made", "", { ...M.forPack(M.fxShared, M.fxTuning, "julia"), effect: "julia" }).settings;
  out.noFxThen = { plain: { ...lists(P2), handed: P2.drawn(P2.tuningFor(P2.fxShared, P2.fxTuning.julia, "julia")).punch, v1: plainKeep.get("curator-fx-tuning-v1") === JSON.stringify(mine) },
    olderV1: olderKeep.get("curator-fx-tuning-v1") === JSON.stringify(mine), made: made.tuning };
}
// 22. other kinds of kept values, read as effects.js reads them: true is 1, false is 0, the words "0.5" are 0.5. And an older pack names the effect it was carried from
{
  const C = start(new Map([["curator-fx-tuning", JSON.stringify({ julia: { punch: true, sounds: "0.5", dream: false } })], ["curator-fx", "julia"]])).api;
  const [first, later] = ["julia", "plasma"].sort((a, b) => A.order.indexOf(a) - A.order.indexOf(b)); // (the one later in the app's order, so a draw would not name it)
  out.oddKinds = { own: C.fxShared.beat.own, hear: C.fxShared.beat.hear, glow: C.fxShared.lights.glow, from: A.fromPack(A.Packs.make("Older", "", { tuning: { [first]: { punch: 0.5 }, [later]: { folk: 0 } }, effect: later }).settings, A.order).effects.from, later };
}
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "needs node to run the page's own code")
class TheSharedSettings(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        code = HARNESS % dict(web=json.dumps(os.path.abspath(WEB).replace("\\", "/")))  # (given to node on its input: it is longer than a command line may be on Windows)
        run = subprocess.run(["node", "-"], input=code, capture_output=True, text=True, encoding="utf-8", timeout=120)
        if run.returncode:
            raise AssertionError(run.stderr)
        cls.out = json.loads(run.stdout)

    def test_the_old_sliders_are_put_aside_whole(self):
        self.assertTrue(self.out["v1"], "curator-fx-tuning-v1 is the old text to the letter")
        self.assertTrue(self.out["tuningLeft"], "and curator-fx-tuning is left as it is, so an older copy of the app still draws every effect as before")

    def test_every_shared_slider_starts_as_designed_and_each_effect_keeps_its_own(self):
        shared = self.out["shared"]
        self.assertEqual(self.out["notOne"], [], "all ten for every effect start as designed")
        self.assertEqual(shared["beat"]["own"], {"julia": 1.4}, "Julia keeps its own Hard kicks")
        self.assertEqual(shared["beat"]["hear"], {"julia": 1.6}, "and its own Answers to sounds")
        self.assertEqual(shared["tricks"]["play"], {"julia": 0.6}, "and its own Color tricks")
        self.assertEqual(shared["company"]["visit"], {"julia": 1.5, "mandel": 0, "newton": 0, "kaleido": 0, "tube": 0, "runner": 0, "faces": 0, "aero": 0}, "each effect keeps the Visitors it had")
        self.assertEqual(shared["from"], "julia", "the effect set up the most")
        self.assertEqual(self.out["differ"], [], "no style at all, set up or not, is drawn with other values than it had")

    def test_no_effect_loses_its_own_sliders(self):
        self.assertEqual(self.out["ownLost"], [])
        self.assertEqual(self.out["tuning"]["julia"], {"folk": 1.5, "tricks": 0.6, "punch": 1.4, "sounds": 1.6, "light": 1.2, "speed": 0.7, "color": 1.3}, "kept whole, for an older copy")

    def test_it_is_said_once_and_the_second_start_changes_nothing(self):
        self.assertEqual(self.out["said"], ["Visitors, Color tricks, Hard kicks and Dreamy lights are now one setting for every effect. Effects where you had changed them keep yours."])
        self.assertIn(10000, self.out["waits"], "up for 10 seconds")
        self.assertEqual(self.out["secondSaid"], [])
        self.assertTrue(self.out["secondSame"])
        self.assertTrue(self.out["again"], "carried twice is carried once")

    def test_a_shared_slider_reaches_the_picture(self):
        self.assertEqual(self.out["handedOnSave"], 1.8)

    def test_nothing_moved_nothing_said(self):
        self.assertEqual(self.out["blank"]["keys"], ["curator-fx-tuning-v1", "curator-effects"], "-v1 is {}: there was nothing to put aside")
        self.assertEqual(self.out["blank"]["said"], [])
        self.assertTrue(self.out["blank"]["same"], "drawn exactly as designed")
        self.assertEqual(self.out["ownOnly"], {"said": [], "effects": True, "v1": True}, "only an effect's own sliders were moved: nothing to say")

    def test_a_start_without_the_pictures_only_puts_the_old_aside(self):
        got = self.out["noFx"]
        self.assertEqual(got["keys"], ["curator-fx-tuning", "curator-fx", "curator-fx-tuning-v1"], "no curator-effects, so the carry-over still comes")
        self.assertTrue(got["v1"], "-v1 is the old text, put aside first of all")
        self.assertEqual((got["said"], got["own"]), ([], {"julia": 1.4}), "nothing said, and each effect's own values are carried in memory")
        self.assertEqual((got["then"]["own"], got["then"]["said"]), ({"julia": 1.4}, 1), "and it does on the next start with the pictures")

    def test_a_switch_off_gives_0_and_how_often_scales_the_company(self):
        got = self.out["switched"]
        for key in ("punch", "spark", "sounds", "tricks", "quick", "dream"):
            self.assertEqual(got[key], 0, key)
        self.assertEqual(got["cover"], 1, "the record's colors as they always were")
        self.assertEqual((got["folk"], got["acid"], got["band"]), (1, 0, 0.8))
        self.assertEqual(got["light"], 1.3, "the effect's own sliders are handed as they are")
        self.assertEqual(self.out["cap"], {"folk": 3, "acid": 2, "band": 2}, "How often at its top: each kind no higher than its own top")

    def test_a_long_list_keeps_the_styles_in_the_app(self):
        self.assertEqual(self.out["longList"], {"count": 100, "mandel": 0.5})

    def test_packs_both_ways(self):
        before = self.out["oldPack"]
        self.assertEqual((before["effects"]["tricks"]["often"], before["effects"]["tricks"]["play"]), (1, {"julia": 0.6}), "a pack made before this version: each of its effects keeps its own")
        self.assertEqual((before["effects"]["beat"]["sounds"], before["effects"]["beat"]["hear"]), (1, {"julia": 1.6}))
        self.assertEqual(before["effects"]["company"]["visit"]["julia"], 1.5)
        self.assertEqual(before["tuning"]["julia"], {"folk": 1.5, "tricks": 0.6, "punch": 1.4, "sounds": 1.6, "light": 1.2, "speed": 0.7, "color": 1.3}, "its sliders replace this browser's, as they always did")
        after = self.out["newPack"]
        self.assertTrue(after["same"], "a pack made now comes back with the same shared settings")
        self.assertEqual(after["tuning"]["julia"], {"light": 1.2, "speed": 0.7, "color": 1.3, "punch": 0, "spark": 0, "sounds": 0, "tricks": 0, "acid": 0, "band": 0.8, "dream": 0},
                         "an older app finds on the effect showing what the picture is handed: 0 for a group that is off, the share times How often (Visitors: 2 times 0.5, as designed, so not written)")
        self.assertEqual(after["back"]["tuning"], after["tuning"], "and this version keeps them as they are (it does not use the shared ones there)")

    def test_the_ranges_are_the_pictures_own(self):
        self.assertEqual(self.out["ranges"], [])

    def test_odd_values_are_held_or_left_out(self):
        odd = self.out["odd"]
        self.assertEqual(odd["v"], 2)
        self.assertEqual((odd["beat"]["on"], odd["beat"]["punch"]), (True, 2))
        self.assertEqual(odd["beat"]["own"], {"julia": 2, "mandel": 0}, "an effect's own value held to its range; an odd name or a word left out")
        self.assertEqual((odd["tricks"]["quick"], odd["tricks"]["cover"], odd["tricks"]["long"]), (None, 2, 0))
        self.assertEqual((odd["company"]["folk"], odd["company"]["wild"]), (1, 1))
        self.assertEqual(odd["from"], "")
        self.assertEqual(sorted(odd), ["beat", "company", "from", "lights", "tricks", "v"])

    def test_the_seconds_each_style_is_shown(self):
        used = self.out["used"]
        self.assertEqual(used["count"], 80, "80 styles at most")
        self.assertFalse(used["has0"], "the least used goes")
        self.assertTrue(used["has89"])
        self.assertEqual(used["julia"], 0, "a five second sleep counts nothing")
        closing = self.out["closing"]
        self.assertFalse(closing["before"], "not written on every frame")
        self.assertAlmostEqual(closing["after"]["julia"], 0.4)
        self.assertEqual(closing["idle"], ["curator-fx-tuning-v1", "curator-effects"], "a page that counted nothing writes nothing as it closes")

    def test_v1_is_never_written_over(self):
        there = self.out["v1There"]
        self.assertEqual(there["v1"], '{"julia":{"light":0.5}}', "a -v1 already there is left as it is")
        self.assertTrue(there["tuning"], "and the old sliders stay where they are")
        self.assertTrue(there["effects"])

    def test_a_full_storage_loses_nothing(self):
        for key, got in self.out["refused"].items():
            self.assertTrue(got["tuning"], key + " refused: the effects keep their sliders")
            self.assertTrue(got["sameNext"], key + " refused: the next start comes to the same")
            self.assertTrue(got["v1Later"], key + " refused: once there is room, -v1 is the old text to the letter")
        refused = self.out["refused"]
        self.assertFalse(refused["curator-fx-tuning-v1"]["v1"])
        self.assertEqual(refused["curator-effects"]["said"], 0, "nothing is said while curator-effects cannot be kept: it would be said again next time")
        self.assertEqual(refused["curator-effects"]["saidLater"], 1, "and it is said once it is kept")
        self.assertEqual((refused["curator-fx-tuning-v1"]["said"], refused["curator-fx-tuning-v1"]["saidLater"]), (1, 0))

    def test_a_kept_null_is_0(self):
        nulls = self.out["nulls"]
        self.assertEqual((nulls["tricks"], nulls["play"]), (1, {"julia": 0, "mandel": 0, "newton": 1.4, "aero": 0}), "Color tricks kept as null or as empty words, which effects.js drew as 0, stay 0 on those effects")
        self.assertEqual((nulls["folk"], nulls["visit"]), (1, {"julia": 1.5, "mandel": 0, "newton": 0}), "Visitors kept as null stay 0 on those effects")
        self.assertEqual((nulls["punch"], nulls["own"]), (1, {"julia": 0, "mandel": 0, "newton": 1.4, "aero": 0}), "Hard kicks kept as null or as empty words stay 0 on those effects")

    def test_no_effect_gets_other_hard_kicks(self):
        kicks = self.out["kicks"]
        self.assertEqual(kicks["differ"], [], "every style, set up or not, is drawn with the Hard kicks it had")
        self.assertEqual(kicks["shared"], 1, "the one for every effect starts as designed, though three of seven had 1.4")
        self.assertEqual(kicks["own"], {"julia": 0, "mandel": 1.4, "newton": 1.4, "aero": 1.4, "kaleido": 2})

    def test_adjust_shows_each_slider_once_and_where_it_works(self):
        adjust = self.out["adjust"]
        self.assertEqual(adjust["own"], ["Brightness", "Answers to the mouse"], "the record's colors and quick colors are not the effect's own: they would do nothing there")
        self.assertEqual(adjust["every"], ["Hard kicks", "Visitors", "Answers to sounds", "Dreamy lights", "Crowd and dancers"])
        self.assertEqual((adjust["punch"], adjust["kept"], adjust["handed"]), (1.5, 1.5, 1.5), "a shared slider writes to the shared settings, kept and handed to the picture")
        self.assertEqual(adjust["tuning"], {"julia": {"light": 0.8}}, "and not to the effect; an own slider writes to the effect")

    def test_adjust_with_values_of_its_own(self):
        got = self.out["adjustOwnKick"]
        self.assertEqual(got["own"], ["Brightness", "Hard kicks, this effect only", "Answers to sounds, this effect only", "Answers to the mouse"], "its own values are among its own sliders, and say so")
        self.assertEqual(got["shared"], ["Visitors", "Dreamy lights", "Crowd and dancers"])
        self.assertEqual(got["value"], 0.4)
        self.assertEqual(got["heardValue"], 1.5, "the own Answers to sounds row shows its own value, not the one for every effect")
        self.assertEqual(got["moved"], {"own": {"julia": 0.6, "mandel": 1.6}, "shared": 1.3, "kept": 0.6, "handed": 0.6}, "moving them changes that effect only, kept and handed")
        self.assertEqual(got["heard"], {"hear": {"julia": 0.7}, "shared": 1, "kept": 0.7, "handed": 0.7, "tuning": {"julia": {"light": 1.2}}}, "its own Answers to sounds too, not written among its own sliders where it would do nothing")
        self.assertTrue(got["wasOn"], "Back to default can be pressed")
        self.assertEqual((got["after"]["own"], got["after"]["hear"]), ({"mandel": 1.6}, {}), "Back to default lets it follow the one for every effect")
        self.assertEqual((got["after"]["kept"], got["after"]["handed"], got["after"]["tuning"]), ({"mandel": 1.6}, 1.3, {}))

    def test_bringing_in_a_pack(self):
        got = self.out["packIn"]
        before = got["before"]
        self.assertEqual((before["shared"]["tricks"]["long"], before["shared"]["tricks"]["hold"], before["shared"]["tricks"]["strong"], before["shared"]["tricks"]["force"]), (1, {"julia": 1.5}, 1, {"julia": 0.6}),
                         "an older pack's effects each keep their own Color tricks")
        self.assertEqual((before["shared"]["company"]["folk"], before["shared"]["company"]["visit"]), (1, {"julia": 0, "mandel": 0}), "each effect keeps its Visitors")
        self.assertEqual((before["shared"]["company"]["band"], before["shared"]["company"]["crowd"]), (1, {"julia": 0.7}), "and its Crowd and dancers")
        self.assertEqual((before["shared"]["beat"]["punch"], before["shared"]["beat"]["own"]), (1, {"julia": 1.6, "mandel": 1.6}), "and each effect keeps its Hard kicks")
        self.assertEqual(before["julia"], {"light": 1.2, "speed": 0.8, "punch": 1.6, "folk": 0, "stay": 1.5, "bold": 0.6, "band": 0.7}, "so Julia is drawn as the older app drew it from that pack")
        self.assertEqual(before["tuning"], got["packTuning"], "its sliders replace this browser's, as they always did")
        self.assertEqual(got["afterTuning"], before["tuning"], "a newer pack without own sliders leaves them")
        self.assertEqual(got["afterPunch"], 0.5)
        self.assertEqual(got["recipe"]["julia"]["punch"], 0, "with a recipe showing, an older app still finds the shared sliders on the effect they were carried from")
        self.assertEqual(got["leftOut"], {"long": 1, "hold": {"mandel": 1.6, "newton": 1.6}, "folk": 1}, "nothing is voted on: each effect keeps its own")
        self.assertEqual((got["kicks"]["tuning"]["julia"]["punch"], got["kicks"]["tuning"]["mandel"]), (0.3, {"punch": 1.7}), "an older app finds each effect's own Hard kicks")
        self.assertEqual(got["kicks"]["back"]["own"], {"julia": 0.3, "mandel": 1.7}, "and this version has them back")

    def test_no_shared_slider_is_ever_voted_on(self):
        got = self.out["kept3"]
        self.assertEqual(got["notOne"], [], "the ones for every effect start as designed, though most effects set up had them higher or lower")
        maps = got["maps"]
        self.assertEqual(maps["sounds"], {"julia": 0.5, "mandel": 2, "newton": 0.4, "gone-style": 0.5, "kaleido": 2}, "each effect keeps its own Answers to sounds, lowered or raised, an effect that is gone too")
        self.assertEqual(maps["punch"], {"mandel": 1.4, "newton": 0.3, "gone-style": 0.2}, "and its own Hard kicks")
        self.assertEqual(maps["acid"], {"julia": 0, "mandel": 2, "gone-style": 0}, "and its own 303 singer: Julia's stays off")
        self.assertEqual(maps["spark"], {"mandel": 2, "kaleido": 0}, "and its own Glints")
        self.assertEqual((maps["tricks"], maps["stay"], maps["bold"]), ({"newton": 0}, {"newton": 0.2}, {"newton": 2}), "and its own Color tricks")
        self.assertEqual((maps["folk"], maps["band"], maps["dream"]), ({"mandel": 3, "newton": 3, "gone-style": 2, "kaleido": 3}, {"mandel": 2, "newton": 2, "kaleido": 2}, {"julia": 0, "mandel": 2, "newton": 2, "kaleido": 2}))
        self.assertEqual(got["moved"], {key: [] for key in got["moved"]}, "so no style, set up or not, is drawn with other ones than it had")
        self.assertEqual(got["handedJulia"], {"acid": 0, "dream": 0, "sounds": 0.5}, "the effect showing is handed its own")

    def test_own_values_in_packs_both_ways(self):
        got = self.out["kept3"]
        self.assertEqual(got["pack"]["mandel"], {"sounds": 2, "folk": 3, "punch": 1.4, "band": 2, "dream": 2, "spark": 2, "acid": 2}, "an older app finds each effect's own ones on it")
        self.assertEqual(got["pack"]["newton"], {"light": 0.9, "sounds": 0.4, "punch": 0.3, "folk": 3, "band": 2, "dream": 2, "tricks": 0, "stay": 0.2, "bold": 2}, "lowered ones too")
        self.assertEqual(got["pack"]["julia"], {"light": 1.1, "sounds": 0.5, "acid": 0, "dream": 0}, "and the effect showing its own, not the ones for every effect")
        self.assertEqual(got["off"]["mandel"], {"sounds": 0}, "a group that is off hands 0, own values too")
        self.assertEqual(got["back"], got["maps"], "and this version has them back whole")
        self.assertEqual(got["older"]["notOne"], [], "a pack from before: its own effect (Mandelbrot) does not make them the ones for every effect")
        self.assertEqual((got["older"]["maps"]["sounds"], got["older"]["maps"]["folk"]), ({"julia": 0.5, "mandel": 2, "newton": 0.4, "kaleido": 2}, {"mandel": 3, "newton": 3, "kaleido": 3}),
                         "each of its effects keeps its own, as the older app drew them (a pack never held a name like gone-style)")

    def test_back_to_default_with_only_one_kept_apart(self):
        self.assertEqual(len(self.out["apartOnly"]), 10)
        for name, got in self.out["apartOnly"].items():
            self.assertTrue(got["enabled"], name + ": Back to default can be pressed")
            self.assertEqual(got["after"], {}, name + ": and lets the effect follow the one for every effect")

    def test_own_rows_rest_while_their_switch_is_off(self):
        rows = {row["label"]: row for row in self.out["beatOff"]["rows"]}
        for label in ("Hard kicks, this effect only", "Answers to sounds, this effect only", "Dreamy lights, this effect only", "Visitors, this effect only"):
            self.assertEqual((rows[label]["disabled"], rows[label]["shown"]), (True, "Off"), label + ": a slider that would do nothing rests and says Off")
        self.assertEqual((rows["Crowd and dancers, this effect only"]["disabled"], rows["Crowd and dancers, this effect only"]["shown"]), (False, "50%"), "while its switch is on, it works")
        self.assertEqual((rows["Brightness"]["disabled"], rows["Brightness"]["shown"]), (False, "100%"))
        self.assertEqual(self.out["beatOff"]["handed"], {"punch": 0, "sounds": 0, "dream": 0, "folk": 0, "band": 0.5})

    def test_v1_is_never_written_over_later(self):
        self.assertEqual(self.out["v1Later"], '{"julia":{"folk":2}}', "an older copy put shared sliders back: the -v1 already there stays as it is")

    def test_kept_values_are_never_lost(self):
        got = self.out["neverLost"]
        mine = {"own": {"julia": 0}, "hear": {"mandel": 0.3}, "visit": {"gone-style": 2}}
        self.assertEqual(got["before"], mine)
        after = got["after"]
        self.assertEqual({key: after[key] for key in mine}, mine, "a newer pack with no own sliders leaves each effect's own values here")
        self.assertEqual((after["punch"], after["handed"], after["kept"], after["next"]), (0.5, 0, {"julia": 0}, 0), "Julia is still handed her own 0, kept for the next start, while the pack's Hard kicks for every effect come in")
        self.assertEqual(after["tuning"], {"julia": {"punch": 0, "light": 1.2}, "mandel": {"sounds": 0.3}, "gone-style": {"folk": 2}}, "as it leaves the effects' other own sliders")
        plain = got["afterPlain"]
        self.assertNotIn("tuning", plain["keys"], "a pack from an app where nothing was moved has no own sliders")
        self.assertEqual(({key: plain[key] for key in mine}, plain["handed"]), (mine, 0), "and leaves them too")
        self.assertEqual(got["withLists"], {"own": {"julia": 0.7, "gone-two": 0.2}, "hear": {"mandel": 0.3}, "visit": {"gone-style": 2}}, "own values such a pack brings come in beside this browser's")
        self.assertEqual(got["withOwn"], {"own": {"mandel": 1.7}, "hear": {}, "visit": {}, "tuning": {"mandel": {"light": 0.9, "punch": 1.7}}}, "a pack with own sliders replaces them, own values of the ten too, as packs always did")
        self.assertEqual(got["back"], {"own": {}, "hear": {"mandel": 0.3}, "visit": {"gone-style": 2}}, "Back to default on Julia lets only Julia's go")
        self.assertEqual(got["refused"], mine, "a write a full storage refused: the next start still has what was kept")
        self.assertEqual(got["roundTrip"], mine, "a pack of them brings them whole into another newer app, the style that is gone too")
        for bad, read in got["unreadable"].items():
            self.assertEqual(({key: read[key] for key in mine}, read["handed"], read["said"], read["left"]), (mine, 0, 0, True), "curator-effects kept as " + bad + ": carried again from curator-fx-tuning, nothing said, nothing written back")
            self.assertEqual(read["punch"], 0.5 if "punch" in bad else 1, "what it could give for every effect stays: " + bad)

    def test_a_pack_keeps_the_order_they_were_kept_in(self):
        got = self.out["sixty"]
        self.assertGreater(got["kept"], 60)
        self.assertEqual(got["count"], 60, "a pack keeps 60 effects' own sliders")
        self.assertTrue(got["same"], "the same ones, with the same values, as a pack made before this version")
        self.assertEqual(got["julia"], {"punch": 0}, "Julia, kept first, stays in")

    def test_v1_when_nothing_was_kept(self):
        self.assertEqual(self.out["v1Empty"], {"first": "{}", "later": "{}"}, "put aside as {} at once, and never written later from other values")

    def test_kept_values_are_not_rounded(self):
        self.assertEqual(self.out["unrounded"], {"punch": 1.0004, "sounds": 0.9996, "tricks": 0.12345, "dream": 1.99999, "own": {"julia": 1.0004}})

    def test_a_pack_used_is_a_copy(self):
        self.assertEqual(self.out["copied"], {"same": True, "light": 0.5, "own": 1.8}, "the sliders move this browser's values, never the pack's")

    def test_a_start_without_the_pictures_loses_nothing(self):
        got = self.out["noFxThen"]
        self.assertEqual(got["plain"], {"own": {"julia": 0}, "hear": {"gone-style": 0.3}, "visit": {"mandel": 0}, "handed": 0, "v1": True},
                         "a pack with no own sliders used on that start leaves each effect's own values, and Julia is still handed her own 0 on the next start")
        self.assertTrue(got["olderV1"], "an older pack used on that start: -v1 is still the person's old text, not the pack's")
        self.assertEqual(got["made"]["julia"], {"light": 1.2, "punch": 0}, "a pack made on that start keeps Julia's own Hard kicks")
        self.assertEqual(got["made"]["mandel"], {"folk": 0})

    def test_other_kinds_of_kept_values(self):
        got = self.out["oddKinds"]
        self.assertEqual((got["own"], got["hear"], got["glow"]), ({}, {"julia": 0.5}, {"julia": 0}), "true is 1 (so not kept apart), the words 0.5 are 0.5, false is 0")
        self.assertEqual(got["from"], got["later"], "an older pack is carried from its own effect")

    def test_a_pack_keeps_surprises_main_switch(self):
        self.assertEqual((self.out["surprisesOn"]["on"], self.out["surprisesOn"]["clock"]), (False, True))


class ThePlaces(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(os.path.join(WEB, "app.js"), encoding="utf-8") as f:
            cls.app = f.read()

    def test_the_picture_is_always_handed_the_shared_settings(self):
        self.assertNotIn("FX.tuning(fxTuning[fxName]", self.app)
        self.assertEqual(self.app.count("FX.tuning(tuningNow())"), 3, "on a slider of its own, on a shared one, and whenever the picture is shown")

    def test_seconds_are_counted_only_while_music_plays(self):
        self.assertEqual(self.app.count("countUsed(now);"), 1, "called in one place only")
        self.assertIn("if (picture && hearing) countUsed(now);", self.app)

    def test_the_carry_over_runs_only_without_curator_effects(self):
        start = self.app.index("let fxShared = (() => {")
        aside = self.app.index('store("curator-fx-tuning-v1", old ?? "{}")', start)
        self.assertLess(self.app.index("if (kept !== null)", start), aside)
        self.assertLess(aside, self.app.index("if (!FX) return carryShared({ tuning: fxTuning }).effects;", start), "put aside first, also on a start without the pictures")
        self.assertLess(self.app.index("if (!FX) return", start), self.app.index("const got = carryShared(", start), "and only then the carry-over that is kept")
        self.assertLess(self.app.index("const got = carryShared(", start), self.app.index('store("curator-effects", JSON.stringify(got.effects))', start))

    def test_a_pack_with_neither_leaves_them(self):
        self.assertIn("if (s.tuning || s.effects) { usePackEffects(s); saveTuning(); }", self.app, "a pack with no shared settings and no own sliders does not touch them")


if __name__ == "__main__":
    unittest.main()
