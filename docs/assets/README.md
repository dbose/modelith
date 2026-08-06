# README assets

The main README references two screenshots that live here:

- `canvas.png`: the full ER canvas, a seven-entity model with crow's-foot relationships.
- `inspector.png`: the entity inspector showing attributes, named keys, an enumerated
  domain, user-defined properties, and relationships.
- `vscode-split.png`: a VS Code window with a model YAML file open on the left and the
  live canvas preview on the right. Capture this from VS Code: open a `.yaml` model
  file, then run "Modelith: Open Model Preview to the Side" from the command palette.

To regenerate them, serve any model and capture the browser:

```bash
mdl serve -m <your-model>       # opens the canvas at http://127.0.0.1:4800
```

For `canvas.png`, fit the whole graph to view. For `inspector.png`, click an entity to
open the detail panel. A model that carries a named key group, an enumerated domain, and
user-defined properties shows the inspector at its fullest.
