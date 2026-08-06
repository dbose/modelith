# Publishing the Modelith VS Code extension

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

Build and package first (also runs the typecheck):

```bash
npm install
npm run build
npm run package        # produces modelith-vscode-<version>.vsix
```

Then publish to each marketplace:

```bash
# Visual Studio Marketplace
npx @vscode/vsce publish
#   or bump the version at the same time:
#   npx @vscode/vsce publish patch        # 0.1.0 -> 0.1.1
#   npx @vscode/vsce publish minor        # 0.1.0 -> 0.2.0

# Open VSX
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
