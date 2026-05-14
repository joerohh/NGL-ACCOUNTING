// Pre-build JS syntax validator.
//
// Walks every .js file under app/assets/js/ and runs `node --check` on it.
// If any file has a SyntaxError, prints the file + error and exits non-zero,
// which aborts the build before PyInstaller or electron-builder run.
//
// Catches the class of bug that shipped in v2.62 (duplicate function
// declaration → entire app.js module graph fails to load → no buttons work).

const fs = require("fs");
const path = require("path");
const { spawnSync } = require("child_process");

const appJsDir = path.resolve(__dirname, "..", "app", "assets", "js");

function walk(dir) {
  const out = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) out.push(...walk(p));
    else if (entry.isFile() && entry.name.endsWith(".js")) out.push(p);
  }
  return out;
}

if (!fs.existsSync(appJsDir)) {
  console.error(`[check-js] ERROR: directory not found: ${appJsDir}`);
  process.exit(1);
}

const files = walk(appJsDir);
console.log(`[check-js] Validating ${files.length} JS files under app/assets/js/...`);

const failures = [];
for (const file of files) {
  const res = spawnSync(process.execPath, ["--check", file], { encoding: "utf8" });
  if (res.status !== 0) {
    failures.push({ file, stderr: res.stderr || res.stdout || "(no output)" });
  }
}

if (failures.length > 0) {
  console.error(`\n[check-js] ❌ ${failures.length} file(s) failed syntax check:\n`);
  for (const f of failures) {
    console.error(`  ${path.relative(path.resolve(__dirname, ".."), f.file)}`);
    console.error(`    ${f.stderr.trim().split("\n").join("\n    ")}\n`);
  }
  console.error(`Aborting build — fix the syntax errors above and re-run.`);
  process.exit(1);
}

console.log(`[check-js] ✓ All ${files.length} files parse cleanly.`);
