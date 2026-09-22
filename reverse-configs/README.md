# Reverse config starter packs

Shareable `reverse:` configs for `mdl reverse` — the git-native equivalent of a team's
inherited modeling standards. Each is a plain YAML file: copy it, or import it directly.

```bash
# import a starter pack (or any team's shared config) and merge it into your model
mdl reverse-config import reverse-configs/kimball.yaml -m model
#   ... then review the git diff and commit.

# or from a URL a team publishes:
mdl reverse-config import https://example.com/our-team/reverse.yaml -m model
```

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
