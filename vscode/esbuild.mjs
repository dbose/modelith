import { build, context } from "esbuild";

const opts = {
  entryPoints: ["src/extension.ts"],
  bundle: true,
  outfile: "dist/extension.js",
  external: ["vscode"],
  format: "cjs",
  platform: "node",
  target: "node18",
  sourcemap: true,
  minify: process.argv.includes("--minify"),
};

if (process.argv.includes("--watch")) {
  // Local iteration: rebuild on save. Reload the Extension Development Host
  // (Cmd+R in that window) to pick up each rebuild.
  const ctx = await context(opts);
  await ctx.watch();
  console.log("watching src/ — rebuilding dist/extension.js on change");
} else {
  await build(opts);
  console.log("built dist/extension.js");
}
