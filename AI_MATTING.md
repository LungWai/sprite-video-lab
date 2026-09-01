# AI Matting Runtime

Sprite Video Lab can optionally use BiRefNet and EZ-CorridorKey for AI background removal:

- `BiRefNet`: subject alpha from the model.
- `Luma`: brightness-derived alpha for glow, fire, lightning, particles, and bright-on-dark VFX.
- `CorridorKey`: uses a selectable Chroma or BiRefNet coarse alpha hint, then EZ-CorridorKey reconstructs foreground color, refines alpha, removes spill, despeckles, and optionally applies a garbage matte.
- `None`: keeps the source image without matting.

The app keeps the chroma-key workflow. AI matting is only used when you select it in step 3.

Chroma, Luma, and BiRefNet run the app's alpha-aware despill. CorridorKey uses EZ-CorridorKey's reconstructed foreground and configurable screen-aware despill instead.

## Model Cache

AI model weights are not bundled with the portable app. Chroma key, Luma-only, and no-matte workflows do not download them. When a BiRefNet or CorridorKey method is selected for the first time, the page asks for confirmation and only downloads the required models after the user accepts. The cache location is controlled by:

```bat
set SPRITE_VIDEO_LAB_AI_MODEL_CACHE=<model-cache-dir>
```

If you do not set it, the app chooses a local default. On Windows, the helper scripts prefer keeping the optional AI runtime and model cache outside the project checkout when possible.

CorridorKey is kept separately from the app checkout. Its location is controlled by:

```bat
set SPRITE_VIDEO_LAB_CORRIDORKEY_ROOT=<corridorkey-dir>
```

The Windows helper uses the same optional AI root as the BiRefNet runtime, clones `edenaion/EZ-CorridorKey`, and stores the green-screen and blue-screen checkpoints under `CorridorKeyModule\checkpoints`.

You can also override the Python runtime used by the launcher:

```bat
set SPRITE_VIDEO_LAB_PYTHON=<python-runtime>
```

## Setup

Run:

```bat
setup_ai_runtime.bat
```

The script installs the base app dependencies, optional AI dependencies, a CUDA-enabled PyTorch wheel for Windows, and clones EZ-CorridorKey when git is available. If CUDA is not available on your machine, the app can still run in compatibility mode, but AI matting will be slower.

Then start the app as usual:

```bat
start_sprite_video_lab.bat
```

## Tuning

- `BiRefNet HR-matting` is the only downloaded BiRefNet model.
- CorridorKey downloads the checkpoint for the selected screen color. Green and blue are separate models.
- CorridorKey defaults to a Chroma coarse mask. Selecting BiRefNet as the coarse mask also requires the pinned HR-matting model.
- Alpha-aware despill is automatic for Chroma, Luma, and BiRefNet. CorridorKey exposes EZ-CorridorKey's despill strength directly.
- If the edge is still dirty, set `halo shrink` to `1` or `2`.
- For green-screen sources, use manual background color and pick the actual background green when auto corner sampling misses the key color.
- Use `Luma` for glow, fire, lightning, particles, and other bright-on-dark VFX material.
- Use `CorridorKey` for true green-screen or blue-screen footage when the edge or foreground still contains screen contamination.

## Security Note

BiRefNet models are loaded through Hugging Face with `trust_remote_code=True`. The app pins the reviewed HR-matting revision; upgrades should change that revision explicitly and rerun the matte tests before release.

CorridorKey is licensed separately from this app and has non-commercial/share-alike restrictions for redistribution and paid inference services. Review its upstream license before shipping it as part of a commercial product.
