# Scene Capture

Snapshot the current state of a room's lights into a named, editable Home Assistant scene with a single tap.

Solves a common pain point: the built-in scene editor is fiddly, and capturing "whatever the room looks like right now" usually means picking each light by hand. With Scene Capture, you set the room how you like it, tap a card (or call a script), and a fully-editable scene appears in `scenes.yaml`.

## What it does

- Adds a `scene_capture.capture` service that reads the current state of a target (area, light group, or list of lights) and writes a scene to `scenes.yaml`
- Picks the right color attribute based on each light's `color_mode` (so HS / XY / RGB / color-temp lights all restore correctly)
- Calls `scene.reload` automatically — no restart needed
- Re-using a scene name overwrites the previous capture, keeping `scenes.yaml` clean
- Includes a script blueprint so you can wire up per-room capture buttons in your dashboard

See the [README](https://github.com/derwoodums/scene-capture) for installation, the blueprint, and a dashboard card example.
