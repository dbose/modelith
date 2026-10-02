// Record each served standalone demo as an MP4 (+GIF) by driving headless Chrome over CDP,
// starting a screencast, and piping JPEG frames to ffmpeg at a fixed fps.
// Usage: node record.mjs <chromePath> <page> <seconds>
import puppeteer from "puppeteer-core";
import { spawn } from "node:child_process";
import { mkdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const CHROME = process.argv[2];
const PAGE = process.argv[3];          // e.g. Main.html
const SECONDS = Number(process.argv[4] || 16);
const FPS = 20;
const outDir = join(here, "out");
mkdirSync(outDir, { recursive: true });
const name = PAGE.replace(/\.html$/, "");
const mp4 = join(outDir, name + ".mp4");

const browser = await puppeteer.launch({
  executablePath: CHROME,
  headless: "new",
  args: ["--no-sandbox", "--force-color-profile=srgb", "--hide-scrollbars",
         "--window-size=1920,1080"],
  defaultViewport: { width: 1920, height: 1080, deviceScaleFactor: 1 },
});
const page = await browser.newPage();
await page.goto("http://localhost:8777/" + PAGE, { waitUntil: "networkidle0" });
await new Promise(r => setTimeout(r, 1200));   // let fonts settle + 1st tick

// Collect frames with their arrival time, then mux at a constant FPS by duplicating/dropping
// to the wall-clock timeline, so static stretches don't compress the video.
const frames = [];
const t0 = Date.now();
const client = await page.target().createCDPSession();
client.on("Page.screencastFrame", async ({ data, sessionId }) => {
  frames.push({ t: Date.now() - t0, buf: Buffer.from(data, "base64") });
  try { await client.send("Page.screencastFrameAck", { sessionId }); } catch {}
});
await client.send("Page.startScreencast", { format: "jpeg", quality: 92, everyNthFrame: 1 });
await new Promise(r => setTimeout(r, SECONDS * 1000));
await client.send("Page.stopScreencast");

// build a constant-FPS stream from the timestamped frames
const ff = spawn("ffmpeg", [
  "-y", "-f", "image2pipe", "-framerate", String(FPS), "-i", "pipe:0",
  "-vf", "scale=1920:1080:flags=lanczos,format=yuv420p",
  "-c:v", "libx264", "-preset", "medium", "-crf", "20",
  "-movflags", "+faststart", mp4,
], { stdio: ["pipe", "inherit", "inherit"] });
const total = SECONDS * 1000;
let fi = 0;
for (let k = 0; k < Math.floor(SECONDS * FPS); k++) {
  const want = (k / FPS) * 1000;
  while (fi + 1 < frames.length && frames[fi + 1].t <= want) fi++;
  if (frames[fi]) ff.stdin.write(frames[fi].buf);
}
ff.stdin.end();
await new Promise(res => ff.on("close", res));
await browser.close();
console.log("wrote", mp4);
