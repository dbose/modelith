# Reverse config starter packs

Shareable `reverse:` configs for `mdl reverse` — the git-native equivalent of a team's
inherited modeling standards. Each is a plain YAML file: copy it, or import it directly.

```bash
# import a starter pack (or any team's shared config) and merge it into your model
mdl reverse-config import reverse-configs/kimball.yaml -m model
#   ... then review the git diff and commit.

# or from a git-hosted file — paste the URL you see in the browser (blob URLs are
# auto-converted to raw), or use a shorthand; private repos take a --token:
mdl reverse-config import https://github.com/acme/dbt/blob/main/reverse-configs/kimball.yaml -m model
mdl reverse-config import github:acme/dbt/reverse-configs/kimball.yaml@main -m model
mdl reverse-config import github:acme/private-dbt/reverse.yaml --token "$GITHUB_TOKEN" -m model

# preview before writing (fetches + merges + validates, writes nothing):
mdl reverse-config import github:acme/dbt/reverse.yaml --dry-run -m model
```

Imports are **https-only** (pass `--allow-insecure` for plain http), size-capped, and
reject an HTML page (a common mistake: a repo *blob* URL that wasn't converted).

Import is **additive by default** (layers append, conventions union, exclusions extend);
pass `--replace` to overwrite. A malformed config is rejected wholesale, never
half-applied.

| Pack | Convention |
|---|---|
| `kimball.yaml` | dbt / Kimball — `stg_`/`int_` excluded, `dim_`/`fct_` the entity layer |
| `medallion.yaml` | bronze / silver / gold, by folder or prefix |
| `data-vault.yaml` | hub / link / satellite (roles seed the DV pattern) |

After importing, see exactly how it classifies your warehouse **before** reversing:

```bash
mdl reverse-config explain --manifest transform/warehouse/target/manifest.json -m model
```

Don't have a config yet? Discover one from your warehouse's own structure:

```bash
mdl reverse-config suggest --manifest transform/warehouse/target/manifest.json --apply -m model
```
