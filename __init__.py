"""ComfyUI-Character-Helper: a tag prompt builder with character presets."""

WEB_DIRECTORY = "./web"
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

if __package__:  # loaded by ComfyUI as a package (not when a test runner imports this file on its own)
    from .nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

    try:  # the routes need a running ComfyUI server
        from . import routes  # noqa: F401
    except Exception as e:  # pragma: no cover
        print(f"[Character Helper] API routes not registered: {e}")

__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]
