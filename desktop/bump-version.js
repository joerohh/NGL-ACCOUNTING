// Reads VERSION file and updates package.json version to match
const fs = require("fs");
const path = require("path");

const dir = path.join(__dirname);
const versionFile = path.join(dir, "VERSION");
const pkgPath = path.join(dir, "package.json");
const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));

// VERSION file holds e.g. "1.1", "1.2", etc.
// --bump flag increments the minor: 1.1 → 1.2, 1.9 → 1.10
if (process.argv.includes("--bump")) {
  const cur = fs.readFileSync(versionFile, "utf8").trim();
  const [major, minor] = cur.split(".").map(Number);
  const next = major + "." + (minor + 1);
  fs.writeFileSync(versionFile, next + "\n");
  console.log("  [INFO] Bumped version: " + cur + " -> " + next);
}

const ver = fs.readFileSync(versionFile, "utf8").trim();
pkg.version = ver + ".0";
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
console.log("  [INFO] Set package version to " + pkg.version);
