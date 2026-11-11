const fs = require("node:fs/promises");
const path = require("node:path");
(async () => {
  const target = path.join(__dirname, "../ui/fonts");
  await fs.mkdir(target, { recursive: true });
  for (const name of [
    "figtree-regular.ttf",
    "figtree-semibold.ttf",
    "eb-garamond-regular.ttf",
    "eb-garamond-italic.ttf",
    "Figtree-OFL.txt",
    "EBGaramond-OFL.txt",
  ])
    await fs.copyFile(
      path.join(__dirname, "../../frontend/public/fonts", name),
      path.join(target, name),
    );
})().catch((error) => {
  console.error(error.message);
  process.exitCode = 1;
});
