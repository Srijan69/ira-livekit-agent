# Theme imagery

The six `*-preview.webp` files are 600 × 400 reductions of the user-supplied
dashboard references. They are for the Settings theme gallery only.

| Theme | Reference image |
| --- | --- |
| Silver | `IRA_ Futuristic AI Assistant Dashboard.png` |
| Red | `IRA Neon AI Command Centre.png` |
| Pink | `Neon IRA Cyberpunk Command Centre.png` |
| Gold | `IRA Golden AI Assistant Dashboard.png` |
| Green | `Neon Green AI Command Center.png` |
| Blue | `IRA Holographic Command Deck.png` |

The six `*-deck.webp` files are 1536 × 1024 backgrounds derived from the
existing `../command-deck.png`. They contain only the room and city, without
the interface shown in the references. Blue preserves the source palette.
The other variants recolor illumination and reflections with HSV mappings,
retaining neutral steel, highlight detail, and dark structural geometry.
Silver retains warm city lighting; the other color variants tint that
lighting to coordinate with their reference.

Generated with Pillow and NumPy through `uv run --with pillow --with numpy`.
WebP quality is 84 for previews and 86 for backgrounds, with method 6.
