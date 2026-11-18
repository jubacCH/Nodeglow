// Audits the full dependency tree (runtime + build tooling) at --audit-level=high,
// minus a short, explicit list of accepted advisories.
//
// Every accepted advisory must still be reported by `npm audit`. Once upstream ships
// a fix and the advisory disappears, this script fails so the exception gets removed
// instead of silently outliving the problem.
//
// Usage: node scripts/audit-tooling.mjs   (run from frontend/)

import { execFileSync } from "node:child_process";

const ACCEPTED = {
  // picomatch ReDoS via single-branch repeated extglobs. No patched release exists
  // (affects <= 4.0.7, latest is 4.0.7). Only reached through dev tooling
  // (vite/vitest via tinyglobby/fdir, eslint-config-next); the runtime audit is clean.
  // Remove this entry once picomatch publishes a fix and the lockfile picks it up.
  "GHSA-fc5q-39xc-vjpf": "picomatch ReDoS, no upstream fix yet, dev tooling only",
};

const FAILING = new Set(["high", "critical"]);

let raw;
try {
  raw = execFileSync("npm", ["audit", "--package-lock-only", "--json"], {
    encoding: "utf8",
    maxBuffer: 64 * 1024 * 1024,
  });
} catch (err) {
  // npm audit exits non-zero when it finds anything; the JSON is still on stdout.
  raw = err.stdout;
  if (!raw) throw err;
}

const report = JSON.parse(raw);
if (report.error) {
  console.error("npm audit failed:", report.error);
  process.exit(2);
}

// Collect the underlying advisories. Entries whose `via` is a plain string are
// packages affected only transitively; their root advisory is listed elsewhere.
const advisories = new Map();
for (const vuln of Object.values(report.vulnerabilities ?? {})) {
  for (const via of vuln.via) {
    if (typeof via !== "object") continue;
    const id = via.url?.split("/").pop() ?? String(via.source);
    advisories.set(id, via);
  }
}

const blocking = [...advisories]
  .filter(([id, adv]) => FAILING.has(adv.severity) && !(id in ACCEPTED))
  .map(([id, adv]) => `  ${adv.severity.padEnd(8)} ${adv.name}: ${adv.title} (${id})`);

const stale = Object.keys(ACCEPTED).filter((id) => !advisories.has(id));

for (const id of Object.keys(ACCEPTED)) {
  if (advisories.has(id)) console.log(`accepted ${id}: ${ACCEPTED[id]}`);
}

let failed = false;
if (blocking.length) {
  console.error(`\n${blocking.length} high/critical advisories:\n${blocking.join("\n")}`);
  failed = true;
}
if (stale.length) {
  console.error(
    `\nAccepted advisories no longer reported, remove them from ACCEPTED in ` +
      `scripts/audit-tooling.mjs:\n  ${stale.join("\n  ")}`,
  );
  failed = true;
}
if (failed) process.exit(1);
console.log("\nNo unaccepted high/critical advisories.");
