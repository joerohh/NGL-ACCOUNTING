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
// electron-builder requires strict 3-part SemVer (X.Y.Z), no more, no less.
// VERSION may be stored as 2-part (X.Y, the historical format for the X.Y.0
// release cycle) or 3-part (X.Y.Z, used for patch releases like 2.69.1).
const parts = ver.split(".");
if (parts.length === 2) {
  pkg.version = ver + ".0";
} else if (parts.length === 3) {
  pkg.version = ver;
} else {
  console.error("  [ERROR] Unexpected VERSION format: " + ver + " (expected X.Y or X.Y.Z)");
  process.exit(1);
}
fs.writeFileSync(pkgPath, JSON.stringify(pkg, null, 2) + "\n");
console.log("  [INFO] Set package version to " + pkg.version);
