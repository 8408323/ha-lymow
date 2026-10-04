// Lists every translation key used by the panel (t("…") literals plus the
// label tables translated through variables). `npm run i18n:check`
// fails if a language file is missing any key.
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const files = [];
(function walk(d) {
  for (const f of readdirSync(d)) {
    const p = join(d, f);
    if (statSync(p).isDirectory()) walk(p);
    else if (/\.tsx?$/.test(f) && !f.endsWith(".test.ts")) files.push(p);
  }
})("src");

const keys = new Set();
const lit = (s) => JSON.parse(s);
for (const f of files) {
  const src = readFileSync(f, "utf8");
  for (const m of src.matchAll(/\bt\(\s*("(?:[^"\\]|\\.)*")/g)) keys.add(lit(m[1]));
  if (/status\.ts$|App\.tsx$/.test(f)) for (const m of src.matchAll(/label: ("(?:[^"\\]|\\.)*")/g)) keys.add(lit(m[1]));
  if (/status\.ts$/.test(f)) for (const m of src.matchAll(/^\s+\d+: ("(?:[^"\\]|\\.)*"),?$/gm)) keys.add(lit(m[1]));
  if (/Diagnostics\.tsx$/.test(f)) for (const m of src.matchAll(/\[("(?:[^"\\]|\\.)*"), \//g)) keys.add(lit(m[1]));
}
const sorted = [...keys].sort();
if (process.argv.includes("--check")) {
  let bad = 0;
  for (const f of readdirSync("src/i18n").filter((x) => x.endsWith(".json"))) {
    const dict = JSON.parse(readFileSync(join("src/i18n", f), "utf8"));
    const missing = sorted.filter((k) => !(k in dict));
    const extra = Object.keys(dict).filter((k) => !keys.has(k));
    if (missing.length || extra.length) {
      bad++;
      console.error(`${f}: ${missing.length} missing, ${extra.length} unused`, missing.slice(0, 5), extra.slice(0, 5));
    }
  }
  process.exit(bad ? 1 : 0);
} else console.log(JSON.stringify(sorted, null, 1));
