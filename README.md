# Scene Capture

<p align="center">
  <img src="custom_components/scene_capture/brand/icon.png" width="128" alt="Scene Capture icon">
</p>

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://github.com/hacs/integration)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

**Tap a card. Get a scene.** Snapshot the current state of a room's lights into a fully-editable Home Assistant scene with one tap.

Set the room how you want it — adjust each lamp, pick the right color temperature, get the brightness just right — then tap a button. Scene Capture writes the snapshot to `scenes.yaml` (with a stable id, so it shows up in the UI scene editor) and reloads scenes immediately. No restart, no clicking through each light by hand in the editor.

## Why this exists

Home Assistant's built-in scene editor works, but capturing "the room exactly as it is right now" means walking through every light and re-entering its current values. That's tedious for a single scene, painful for twenty rooms, and impossible for the spouse / kids / houseguests you'd actually like to be able to save scenes too.

Scene Capture turns that into a single button press, and the scenes it produces are normal `scenes.yaml` entries — editable in the UI, deletable from the UI, usable in any automation.

## Install

### HACS (recommended)

1. In HACS, go to **Integrations** → menu → **Custom repositories**
2. Add `https://github.com/derwoodums/scene-capture` as an **Integration**
3. Install **Scene Capture** and restart Home Assistant
4. Add the integration to your config (see [Configuration](#configuration))

### Manual

1. Copy `custom_components/scene_capture/` into your Home Assistant `config/custom_components/` directory
2. Restart Home Assistant
3. Add the integration to your config (see [Configuration](#configuration))

## Configuration

Add a single line to `configuration.yaml`:

```yaml
scene_capture:
```

Restart Home Assistant. That's it — no options, no UI flow. The `scene_capture.capture` service is now available.

## Usage

### Option A: import the blueprint (easiest)

The included script blueprint handles the boilerplate. After installing the integration, also import the blueprint:

1. **Settings** → **Automations & scenes** → **Blueprints** → **Import blueprint**
2. Paste:
   `https://github.com/derwoodums/scene-capture/blob/main/blueprints/script/capture_room_scene.yaml`
3. Create one script per room from the blueprint:
   - Pick the lights (an area, a light group, or a multi-select of individual lights — whichever fits the room)
   - Optionally exclude specific lights from the snapshot

The script takes a `scene_name` field at run time, so the same script can capture different scenes by name.

### Option B: call the service directly

In any automation, script, or **Developer Tools → Services**:

```yaml
action: scene_capture.capture
target:
  area_id: living_room
data:
  scene_name: Movie Night
  exclude_lights:
    - light.tv_bias
```

`target` accepts the usual area / device / entity / label fields. Anything that resolves to one or more `light.*` entities will be captured.

### Optional: dashboard cards

The most polished pattern is a single `input_text` helper for the scene name, plus one button card per room. You type the name once, tap any room, and the capture fires — no dialog, no notification, the name stays put for the next tap.

First, create the helper: **Settings → Devices & services → Helpers → Create helper → Text** with name "Scene capture name". This gives you `input_text.scene_capture_name`.

Then drop this into your dashboard (one section, all rooms inside):

```yaml
type: vertical-stack
cards:
  - type: entities
    title: Scene capture
    entities:
      - entity: input_text.scene_capture_name
        name: Scene name
        icon: mdi:format-text
    show_header_toggle: false

  - type: grid
    columns: 4
    square: false
    cards:
      - type: custom:mushroom-template-card
        primary: Great Room
        icon: mdi:palette
        icon_color: amber
        tap_action:
          action: call-service
          service: script.capture_great_room_scene
          data:
            scene_name: "{{ states('input_text.scene_capture_name') }}"
      # …one entry per room, same shape, swap primary + service
```

(Requires [Mushroom cards](https://github.com/piitaya/lovelace-mushroom) for the button look. Any card type that supports `tap_action: call-service` works just as well.)

If you'd rather keep the per-room more-info dialog flow, swap `tap_action` for `action: more-info` and point `entity: script.capture_<room>_scene` — HA will pop the script dialog with the `scene_name` field.

## What gets captured

For each light in the target:

- The on/off state. Off lights are recorded as off, so applying the scene turns them off too.
- `brightness`
- The color attribute matching the light's current `color_mode`:
  - `color_temp` mode → `color_temp_kelvin` (preferred) or `color_temp` (mireds, fallback)
  - `hs` mode → `hs_color`
  - `xy` mode → `xy_color`
  - `rgb` / `rgbw` / `rgbww` → the matching `*_color` attribute
  - `white` mode → brightness only (no color value)
- `effect`, if set to anything other than `none`

Picking the attribute by `color_mode` avoids writing conflicting color values that the scene engine would have to arbitrate between — a common gotcha when capturing scenes "by hand".

## How overwrites work

The scene id is derived from the scene name: `Evening Dim` → `captured_evening_dim`. Re-running with the same name (or any name that slugifies to the same id, like `evening-dim` or `Evening  Dim!`) overwrites the previous capture. This is intentional — it keeps `scenes.yaml` from filling up with near-duplicates.

If you want a separate scene, use a distinct name.

## Deleting scenes

Captured scenes are normal `scenes.yaml` entries with stable ids, so:

- **Settings → Automations & scenes → Scenes** → click the scene → delete

…just like any other UI-managed scene.

## FAQ

**Do I need a light group?**
No. The blueprint and service both accept areas, individual lights, light groups, or any mix.

**Will this work with my Hue / Zigbee2MQTT / WLED / etc. lights?**
Yes — anything that exposes a `light.*` entity is supported. The component reads the standard light attributes, so anything that follows the HA light spec works.

**Will captures overwrite scenes I created in the UI?**
Only if a UI-created scene happens to have an id starting with `captured_` and matching the slugified name. In practice this never happens because UI-created scenes get random ids.

**Can I edit a captured scene afterward?**
Yes — captures are normal scene entries with stable ids, fully editable from the UI scene editor.

**Does it notify on every capture?**
Successes are silent — the scene is immediately usable, so a toast on every tap is noise. Failures still notify (file write errors, no lights matched the target, etc.) so you know when something needs attention. Successful captures are logged at info level.

**What if `scene.reload` fails?**
You'll get a persistent notification telling you so. The scene is already in `scenes.yaml` at that point — restart HA or call `scene.reload` manually to pick it up.

**Two captures fire at the same time. Do I lose data?**
No. The component holds an asyncio lock around the read-modify-write of `scenes.yaml`, so concurrent captures serialize cleanly.

## Development

PRs welcome. The component is a single `__init__.py` plus a `services.yaml` and `manifest.json`. To validate locally, drop the contents of `custom_components/scene_capture/` into a HA dev install and `python -m py_compile __init__.py` for a quick syntax check.

## License

MIT — see [LICENSE](LICENSE).
