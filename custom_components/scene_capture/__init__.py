"""Scene Capture: snapshot the current state of a set of lights into scenes.yaml."""

from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import Any

import voluptuous as vol
import yaml

from homeassistant.const import ATTR_ENTITY_ID
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.service import async_extract_referenced_entity_ids

DOMAIN = "scene_capture"
SERVICE_CAPTURE = "capture"

CONF_SCENE_NAME = "scene_name"
CONF_EXCLUDE = "exclude_lights"
CONF_LIGHT_GROUP = "light_group"  # legacy field, accepted for back-compat

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = vol.Schema({DOMAIN: {}}, extra=vol.ALLOW_EXTRA)

CAPTURE_SERVICE_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_SCENE_NAME): cv.string,
        vol.Optional(CONF_EXCLUDE, default=[]): vol.All(
            cv.ensure_list, [cv.entity_id]
        ),
        vol.Optional(CONF_LIGHT_GROUP): cv.entity_id,
    },
    # Allow target fields (entity_id, area_id, device_id, label_id, floor_id)
    # to flow through; HA puts them in call.data and/or call.target.
    extra=vol.ALLOW_EXTRA,
)

# Module-level lock so concurrent capture calls can't clobber scenes.yaml.
_FILE_LOCK = asyncio.Lock()


def _slugify_id(name: str) -> str:
    """Build a stable scene id from the user-supplied display name."""
    return "captured_" + re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _capture_light_state(state) -> dict[str, Any]:
    """Capture a single light's relevant attributes for a scene entry.

    Uses the light's reported ``color_mode`` to pick the right color attribute,
    so we don't write conflicting color values that the scene engine would have
    to arbitrate between.
    """
    entry: dict[str, Any] = {"state": state.state}
    attrs = state.attributes

    # Off (or unavailable) lights only need the state recorded so the scene
    # turns them off again on apply.
    if state.state != "on":
        return entry

    brightness = attrs.get("brightness")
    if brightness is not None:
        entry["brightness"] = brightness

    color_mode = attrs.get("color_mode")

    if color_mode == "color_temp":
        # Prefer kelvin (the modern unit). Fall back to mireds.
        kelvin = attrs.get("color_temp_kelvin")
        mireds = attrs.get("color_temp")
        if kelvin is not None:
            entry["color_temp_kelvin"] = kelvin
        elif mireds is not None:
            entry["color_temp"] = mireds
    elif color_mode == "hs":
        hs = attrs.get("hs_color")
        if hs is not None:
            entry["hs_color"] = list(hs)
    elif color_mode == "xy":
        xy = attrs.get("xy_color")
        if xy is not None:
            entry["xy_color"] = list(xy)
    elif color_mode in ("rgb", "rgbw", "rgbww"):
        key = f"{color_mode}_color"
        val = attrs.get(key)
        if val is not None:
            entry[key] = list(val)
    elif color_mode == "white":
        # white mode = brightness only on a dedicated white channel; brightness
        # is already captured above.
        pass
    else:
        # Missing / unknown color_mode (older integrations, oddball bulbs).
        # Capture whichever color attr is present, preferring the most
        # specific. Skip color_temp if a true color value is set.
        for attr in ("rgb_color", "hs_color", "xy_color"):
            val = attrs.get(attr)
            if val is not None:
                entry[attr] = list(val)
                break
        else:
            kelvin = attrs.get("color_temp_kelvin")
            mireds = attrs.get("color_temp")
            if kelvin is not None:
                entry["color_temp_kelvin"] = kelvin
            elif mireds is not None:
                entry["color_temp"] = mireds

    effect = attrs.get("effect")
    if effect and str(effect).lower() != "none":
        entry["effect"] = effect

    return entry


async def _resolve_targets(hass: HomeAssistant, call: ServiceCall) -> list[str]:
    """Resolve the call into a sorted list of leaf light entity IDs.

    Supports modern target syntax (areas, devices, labels, entities) and the
    legacy ``light_group`` data field used by earlier versions of the blueprint.
    Any ``light.*`` entity that is itself a light group (its state exposes an
    ``entity_id`` attribute listing child lights) is expanded recursively so
    we capture the individual bulbs, not the aggregate group state.
    """
    selected = async_extract_referenced_entity_ids(hass, call, expand_group=True)
    candidates: set[str] = set(selected.referenced) | set(selected.indirectly_referenced)

    # Legacy: a single light group entity passed as data.
    legacy_group = call.data.get(CONF_LIGHT_GROUP)
    if legacy_group:
        candidates.add(legacy_group)

    # Legacy: bare entity_id list/string in data.
    extra = call.data.get(ATTR_ENTITY_ID)
    if isinstance(extra, str):
        candidates.add(extra)
    elif isinstance(extra, list):
        candidates.update(extra)

    # Iteratively expand light groups down to leaf bulbs. A light is treated
    # as a group when its state attributes include a non-empty ``entity_id``
    # list (the convention used by HA's Light Group helper).
    leaves: set[str] = set()
    seen: set[str] = set()
    queue: list[str] = [e for e in candidates if isinstance(e, str)]
    while queue:
        eid = queue.pop()
        if eid in seen:
            continue
        seen.add(eid)
        # Drop anything that isn't a light — guards against an area target
        # that includes switches or other entities alongside the lights.
        if not eid.startswith("light."):
            continue
        state = hass.states.get(eid)
        members = state.attributes.get("entity_id") if state else None
        if members:
            # Light group: queue its children, don't capture the group itself.
            for m in members:
                if isinstance(m, str):
                    queue.append(m)
        else:
            leaves.add(eid)

    return sorted(leaves)


async def _notify(
    hass: HomeAssistant, title: str, message: str, notification_id: str
) -> None:
    """Pop a persistent notification in the HA frontend."""
    await hass.services.async_call(
        "persistent_notification",
        "create",
        {
            "title": title,
            "message": message,
            "notification_id": notification_id,
        },
    )


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the scene_capture integration and register the capture service."""

    async def handle_capture(call: ServiceCall) -> None:
        scene_name = (call.data.get(CONF_SCENE_NAME) or "").strip()
        exclude_raw = call.data.get(CONF_EXCLUDE) or []
        exclude = [exclude_raw] if isinstance(exclude_raw, str) else list(exclude_raw)

        if not scene_name:
            _LOGGER.error("scene_capture: scene_name is required")
            await _notify(
                hass,
                "Scene Capture Failed",
                "No scene name was provided.",
                "scene_capture_error",
            )
            return

        light_entities = [
            e for e in await _resolve_targets(hass, call) if e not in exclude
        ]

        if not light_entities:
            _LOGGER.error(
                "scene_capture: no light entities resolved from target for '%s'",
                scene_name,
            )
            await _notify(
                hass,
                "Scene Capture Failed",
                f"No light entities were found in the target for '{scene_name}'.",
                "scene_capture_error",
            )
            return

        entities_data: dict[str, dict[str, Any]] = {}
        for entity_id in light_entities:
            state = hass.states.get(entity_id)
            if not state:
                continue
            entities_data[entity_id] = _capture_light_state(state)

        unique_id = _slugify_id(scene_name)
        if not unique_id.strip("_") or unique_id == "captured_":
            _LOGGER.error("scene_capture: scene name '%s' produced empty id", scene_name)
            await _notify(
                hass,
                "Scene Capture Failed",
                f"Scene name '{scene_name}' contains no usable characters.",
                "scene_capture_error",
            )
            return

        new_scene = {
            "id": unique_id,
            "name": scene_name,
            "entities": entities_data,
        }
        scenes_file = hass.config.path("scenes.yaml")

        def write_scene() -> None:
            if os.path.exists(scenes_file) and os.path.getsize(scenes_file) > 0:
                with open(scenes_file, "r", encoding="utf-8") as f:
                    scenes = yaml.safe_load(f) or []
            else:
                scenes = []
            if not isinstance(scenes, list):
                # Defensive: scenes.yaml should always be a list. If something
                # else is in there, refuse rather than overwrite.
                raise ValueError(
                    f"Unexpected structure in {scenes_file}: expected a list"
                )
            scenes = [s for s in scenes if s.get("id") != unique_id]
            scenes.append(new_scene)
            tmp = scenes_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                yaml.dump(
                    scenes,
                    f,
                    default_flow_style=False,
                    allow_unicode=True,
                    sort_keys=False,
                )
            os.replace(tmp, scenes_file)

        async with _FILE_LOCK:
            try:
                await hass.async_add_executor_job(write_scene)
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("scene_capture: failed to write scenes.yaml")
                await _notify(
                    hass,
                    "Scene Capture Failed",
                    f"Could not write scenes.yaml: {err}",
                    "scene_capture_error",
                )
                return

            try:
                await hass.services.async_call("scene", "reload", blocking=True)
            except Exception as err:  # noqa: BLE001
                _LOGGER.exception("scene_capture: scene.reload failed")
                await _notify(
                    hass,
                    "Scene Capture: Reload Failed",
                    (
                        f"Scene '{scene_name}' was written to scenes.yaml but the "
                        f"reload failed: {err}. Restart Home Assistant or call "
                        "scene.reload manually to pick it up."
                    ),
                    "scene_capture_error",
                )
                return

        # Success is silent — the scene is immediately usable and a toast on
        # every tap is noisy. Failures still notify so the user knows when
        # something needs attention.
        _LOGGER.info(
            "scene_capture: captured %d light(s) into scene '%s'",
            len(entities_data),
            scene_name,
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_CAPTURE,
        handle_capture,
        schema=CAPTURE_SERVICE_SCHEMA,
    )
    return True
