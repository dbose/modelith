# Building, testing, and publishing the Modelith VS Code extension

`dist/extension.js` is build output (esbuild bundles `src/*.ts` into it) and is
gitignored — source is committed, the bundle is built on demand. Neither local
testing nor the marketplace needs the bundle in git: `vscode:prepublish` rebuilds it
(minified) whenever `vsce package`/`publish` runs, so a `.vsix` is always fresh.

## Test locally

All commands run from the `vscode/` directory with Node 20 on PATH.

**F5 debug (recommended).** Open the `vscode/` folder in VS Code and press F5 — the
"Run Extension" launch config builds (`preLaunchTask: npm: build`) and opens an
Extension Development Host window with the extension loaded. Edit `src/`, then either
re-press F5 or run `npm run watch` in a terminal and hit Cmd+R in the host window to
reload after each rebuild.

**Install the packaged build into your real VS Code.** To exercise the extension
exactly as a marketplace install (right-click menus, activation, packaging):

```bash
npm run package                                   # builds + produces modelith-vscode-<version>.vsix
code --install-extension modelith-vscode-0.1.0.vsix
# reload VS Code; uninstall a test build with:
#   code --uninstall-extension modelith.modelith-vscode
```

## Publish to the marketplaces

There are two marketplaces. Publish to both so the extension is discoverable in VS Code
and in the open-source forks (VSCodium, Cursor, Windsurf, Gitpod, code-server).

| Marketplace | Reaches | Tool |
|---|---|---|
| Visual Studio Marketplace | VS Code | `vsce` |
| Open VSX | VSCodium, Cursor, Windsurf, Gitpod, code-server | `ovsx` |

All commands below run from the `vscode/` directory with Node 20 on PATH.

## One-time setup

### Before anything

- The manifest already sets `publisher: modelith`, `repository`, `icon`, `keywords`, and
  `galleryBanner`. If you publish under a different publisher id, change `publisher` in
  `package.json` to match the id you own on each marketplace.
- Confirm the `repository.url` (`https://github.com/dbose/modelith.git`) is correct and the
  repo is public before publishing, since both marketplaces link to it.

### Visual Studio Marketplace

1. Create an Azure DevOps organization at https://dev.azure.com (free).
2. Create a Personal Access Token: Azure DevOps -> User settings -> Personal Access Tokens ->
   New Token. Set Organization to "All accessible organizations" and the scope to
   **Marketplace -> Manage**. Copy the token; you will not see it again.
3. Create the publisher at https://marketplace.visualstudio.com/manage. The publisher id must
   equal the `publisher` field in `package.json` (`modelith`).
4. Log in once:

   ```bash
   npx @vscode/vsce login modelith
   # paste the Azure PAT when prompted
   ```

### Open VSX

1. Sign in at https://open-vsx.org with GitHub and accept the publisher agreement.
2. Create an access token under your Open VSX account settings.
3. Claim the `modelith` namespace (must match the `publisher` field):

   ```bash
   npm install -g ovsx
   ovsx create-namespace modelith -p <open-vsx-token>
   ```

## Publish

`vsce package` and `vsce publish` both run `vscode:prepublish` first, which builds a
fresh minified bundle — so you never publish a stale `dist/`. `npm install` once, then:

```bash
# Visual Studio Marketplace (builds via vscode:prepublish, then uploads)
npm run publish
#   or bump the version at the same time:
#   npx @vscode/vsce publish patch        # 0.1.0 -> 0.1.1
#   npx @vscode/vsce publish minor        # 0.1.0 -> 0.2.0

# Open VSX — package first, then upload that .vsix
npm run package
ovsx publish modelith-vscode-<version>.vsix -p <open-vsx-token>
```

Both marketplaces render `vscode/README.md` as the extension's landing page and
`vscode/CHANGELOG.md` in the Changelog tab.

## Verifying a release

- Visual Studio Marketplace: https://marketplace.visualstudio.com/items?itemName=modelith.modelith-vscode
- Open VSX: https://open-vsx.org/extension/modelith/modelith-vscode
- Install from the marketplace to confirm:

  ```bash
  code --install-extension modelith.modelith-vscode
  ```

## Automating with CI (optional)

Both publish steps can run from GitHub Actions on a tagged release. Store the tokens as
repository secrets (`VSCE_PAT`, `OVSX_PAT`) and run `vsce publish -p $VSCE_PAT` and
`ovsx publish -p $OVSX_PAT` in the workflow. Never commit a token.

## Notes

- The icon shipped is `icon.png` (256x256), rasterized from `icon.svg`. To change it, edit
  the SVG and regenerate the PNG, keeping it at least 128x128. `icon.svg` is excluded from
  the package via `.vscodeignore`.
- The extension requires the `mdl` CLI at runtime. That is documented in the extension README
  and detected automatically; it is not bundled, so the package stays small.
- Bump `version` in `package.json` for each release (or let `vsce publish patch|minor` do it),
  and add a matching entry to `CHANGELOG.md`.
