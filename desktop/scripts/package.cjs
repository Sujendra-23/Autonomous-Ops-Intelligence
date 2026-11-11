const { spawnSync } = require("node:child_process");
const { packager } = require("@electron/packager");
const path = require("node:path");
(async () => {
  const preparation = spawnSync(
    process.execPath,
    [path.join(__dirname, "prepare.cjs")],
    { stdio: "inherit" },
  );
  if (preparation.status) process.exit(preparation.status);
  const platform = process.argv[2] || process.platform;
  const paths = await packager({
    dir: path.join(__dirname, ".."),
    out: path.join(__dirname, "../dist"),
    name: "AOI Desktop",
    platform,
    arch: process.argv[3] || (platform === "darwin" ? process.arch : "x64"),
    overwrite: true,
    asar: true,
    appBundleId: "com.aoi.desktop",
    appCategoryType: "public.app-category.productivity",
    extendInfo: {
      NSAudioCaptureUsageDescription:
        "AOI records call audio from Teams, Slack, Discord and other apps when you start capture.",
      NSMicrophoneUsageDescription:
        "AOI includes your microphone in meeting recordings when you enable it.",
    },
    ignore: [/^\/dist(?:\/|$)/, /^\/tests(?:\/|$)/, /^\/scripts(?:\/|$)/],
    prune: true,
  });
  for (const output of paths) console.log(output);
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
