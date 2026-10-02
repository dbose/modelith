// Convert the 6 .dc.html Design artboards into self-contained, animated HTML files that need
// no Design runtime, so they can be screen-recorded and embedded on the landing page.
//
// The .dc.html files bind {{holes}} to DCLogic.renderVals() stepped by setInterval. Holes are
// applied IN PLACE (never by rebuilding innerHTML, which would restart CSS transitions): each
// {{x}} in the template is turned into a marker the shim updates on the live element:
//   class="base {{x}}"        -> the element gets data-cls="x"; shim sets className = "base "+val
//   style="…:{{x}};…"         -> the element gets data-style-<prop>="x"; shim sets that style prop
//   >{{x}}<  (text)           -> wrapped in <span data-text="x"></span>; shim sets textContent
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const SRC = process.argv[2];
if (!SRC) { console.error("usage: node build.mjs <path-to-project-dir>"); process.exit(1); }

const files = ["Main.dc.html","Adopt.dc.html","Drift.dc.html","Ontology.dc.html","Erwin.dc.html","Compile.dc.html"];

for (const f of files) {
  const raw = readFileSync(join(SRC, f), "utf8");
  const css = (raw.match(/<helmet>[\s\S]*?<style>([\s\S]*?)<\/style>[\s\S]*?<\/helmet>/) || [,""])[1];
  const bodyM = raw.match(/<\/helmet>([\s\S]*?)<script type="text\/x-dc"/);
  let body = (bodyM ? bodyM[1] : "").replace(/<\/x-dc>\s*$/, "").trim();
  const scriptM = raw.match(/<script type="text\/x-dc"[^>]*>([\s\S]*?)<\/script>/);
  let klass = (scriptM ? scriptM[1] : "")
    .replace(/class Component extends DCLogic\s*{/, "class Component {")
    .replace(/constructor\(p\)\s*{\s*super\(p\);\s*/, "constructor(){ ")
    .replace(/componentWillUnmount\(\)\s*{[\s\S]*?}\n/, "");
  const fontLink = (raw.match(/<link rel="stylesheet" href="https:\/\/fonts[^>]*>/) || [""])[0];
  const title = (raw.match(/<title>([^<]*)<\/title>/) || [,"Modelith"])[1];

  // --- rewrite holes into markers ---
  // 1) class="… {{x}}"  (hole is a class suffix)
  body = body.replace(/class="([^"]*?)\s*\{\{\s*([\w]+)\s*\}\}([^"]*)"/g, (m, pre, k, post) => {
    const base = (pre + post).replace(/\s+/g, " ").trim();
    return `class="${base}" data-cls="${k}" data-clsbase="${base}"`;
  });
  // 2) style="…:{{x}}; …"  -> capture prop:hole
  body = body.replace(/style="([^"]*)"/g, (m, s) => {
    const holes = [...s.matchAll(/([a-zA-Z-]+)\s*:\s*\{\{\s*([\w]+)\s*\}\}/g)];
    if (!holes.length) return m;
    let clean = s;
    const attrs = [];
    for (const h of holes){
      clean = clean.replace(h[0], "");             // drop the hole declaration from static style
      attrs.push(`data-style-${h[1]}="${h[2]}"`);
    }
    clean = clean.replace(/;\s*;/g,";").replace(/^\s*;?\s*/,"").trim();
    return `style="${clean}" ${attrs.join(" ")}`;
  });
  // 3) bare text holes >{{x}}<  -> span
  body = body.replace(/>\s*\{\{\s*([\w]+)\s*\}\}\s*</g, (m,k)=>`><span data-text="${k}"></span><`);
  // any remaining inline {{x}} in text -> span too
  body = body.replace(/\{\{\s*([\w]+)\s*\}\}/g, (m,k)=>`<span data-text="${k}"></span>`);

  const out = `<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=1920">
<title>${title}</title>
${fontLink}
<style>
html,body{margin:0;background:#14110f}
${css}
</style>
</head>
<body>
<div id="root">${body}</div>
<script>
${klass}
const comp = new Component();
const root = document.getElementById('root');
function apply(){
  const v = comp.renderVals();
  root.querySelectorAll('[data-cls]').forEach(el=>{
    const k = el.getAttribute('data-cls'); const base = el.getAttribute('data-clsbase')||'';
    el.className = (base + ' ' + (v[k]||'')).trim();
  });
  root.querySelectorAll('*').forEach(el=>{
    for (const a of el.attributes){
      if (a.name.startsWith('data-style-')){
        const prop = a.name.slice('data-style-'.length);
        el.style.setProperty(prop, v[a.value] != null ? v[a.value] : '');
      }
    }
  });
  root.querySelectorAll('[data-text]').forEach(el=>{
    const k = el.getAttribute('data-text'); el.textContent = v[k] != null ? v[k] : '';
  });
}
comp.setState = function(fn){ comp.state = Object.assign({}, comp.state, typeof fn==='function'?fn(comp.state):fn); apply(); };
apply();
comp.componentDidMount && comp.componentDidMount();
</script>
</body>
</html>`;
  writeFileSync(join(here, f.replace(".dc.html",".html")), out);
  console.log("wrote", f.replace(".dc.html",".html"));
}
