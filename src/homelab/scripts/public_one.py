"""public_one: describe what this script does in one line."""

from homelab_plugin_api import Nudge, Plan, script


@script(
    summary="describe what this script does",
    tags=["todo"],
    status="one-off",  # one-off, then stable once it has run twice with a passing test, then retired
    writes=["{media}"],  # path prefixes or {names} from homelab.yaml; the engine refuses anything else
    nudge=Nudge(
        when="when to reach for this script",
        before="what to check in the plan before applying",
        after="how to verify afterwards",
        avoid="when not to use it, and what to use instead",
    ),
)
def public_one(ctx, options) -> Plan:
    # Return actions instead of doing things: move, write, quarantine or exec. The engine applies exactly
    # the reviewed plan, records it, and can undo it. If this grows past one file, make it a plugin.
    return Plan(notes=["nothing to do yet"])
