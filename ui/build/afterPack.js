/**
 * electron-builder afterPack hook.
 *
 * Runs once per arch right after the .app bundle is laid out (before
 * the DMG is wrapped). We ad-hoc codesign the bundle so that:
 *
 *   1. macOS lets the app run without quarantine-stripping each
 *      executable inside the app's Frameworks/ + Resources/ tree.
 *   2. electron-updater's Squirrel.Mac path can verify the new bundle
 *      after a download — without a signature (even ad-hoc), Squirrel
 *      bails out before doing anything.
 *
 * On Windows + Linux this is a no-op.
 *
 * The `-` identity is the macOS convention for ad-hoc signing — same
 * thing `codesign --sign - …` does at the CLI. It produces a valid
 * but locally-rooted signature: Gatekeeper still warns ("identified
 * developer cannot be verified") but the app boots and updates work.
 */
const { execFileSync } = require("node:child_process");
const path = require("node:path");

/** @param {import('electron-builder').AfterPackContext} ctx */
exports.default = async function afterPack(ctx) {
  if (ctx.electronPlatformName !== "darwin") return;

  const appPath = path.join(ctx.appOutDir, `${ctx.packager.appInfo.productFilename}.app`);
  // --force overrides any existing signature electron leaves on the
  // ChromiumNativeFramework. --deep recursively signs everything inside.
  // --options=runtime keeps the hardened-runtime flag off (we set
  // hardenedRuntime: false in electron-builder.yml). --timestamp=none
  // because ad-hoc can't talk to Apple's timestamp server anyway.
  const args = [
    "--force",
    "--deep",
    "--sign", "-",
    "--timestamp=none",
    appPath,
  ];
  // eslint-disable-next-line no-console
  console.log(`[afterPack] codesign ${args.join(" ")}`);
  try {
    execFileSync("codesign", args, { stdio: "inherit" });
  } catch (err) {
    // Don't fail the whole build for a signing failure — print and
    // continue so the user can still ship an unsigned DMG if they have
    // to. electron-updater will degrade to its fallback path.
    // eslint-disable-next-line no-console
    console.warn(`[afterPack] ad-hoc codesign failed: ${err.message}`);
  }
};
