# PyInstaller spec for the dashd agent.
#
# Produces a single binary `dashd-agent` (in dist/) that the Electron app
# bundles via electron-builder's extraResources. The binary runs the same
# `python -m dashd` entry point with all collectors compiled in.
#
# Build with:
#   .venv/bin/pyinstaller dashd-agent.spec --clean
#
# The output goes to `dist/dashd-agent` (macOS/Linux) or `dist/dashd-agent.exe`
# (Windows). PyInstaller picks the right format for the current platform.

block_cipher = None

a = Analysis(
    ["dashd/__main__.py"],
    pathex=["."],
    binaries=[],
    datas=[
        # Bundled default pet spritesheet, read at runtime by
        # dashd.pets.preview via importlib.resources.
        ("dashd/pets/data/default-claw-d.webp", "dashd/pets/data"),
    ],
    # MSAL and httpx pull lazily-imported modules; add anything PyInstaller's
    # static analysis misses here.
    hiddenimports=[
        "dashd.collectors.system",
        "dashd.collectors.claude_code",
        "dashd.collectors.codex",
        "dashd.collectors.git",
        "dashd.collectors.github",
        "dashd.collectors.calendar_msgraph",
        "dashd.collectors.email_imap",
        "dashd.collectors.imessage_macos",
        "dashd.collectors.whatsapp",
        "dashd.pets",
        "dashd.pets.catalog",
        "dashd.pets.downloader",
        "dashd.pets.converter",
        "dashd.pets.install",
        "dashd.pets.preview",
        "dashd.pets.data",
        "msal",
        "msal.authority",
        "httpx",
        "aioimaplib",
        "serial.tools.list_ports",
        # Used at runtime by the pet converter (sprite-sheet → .dpet).
        # PyInstaller's static analysis misses these because the imports
        # happen inside dashd.pets.converter at module load.
        "PIL",
        "PIL.Image",
        "PIL.WebPImagePlugin",
        "numpy",
        # `keyring` backends are loaded via entry-points, which PyInstaller's
        # static analysis can't follow — we have to enumerate them. Without
        # these, the Anthropic OAuth opt-in falls through to file-based
        # credential lookups and may silently fail to find the token.
        "keyring",
        "keyring.backends",
        "keyring.backends.macOS",
        "keyring.backends.SecretService",
        "keyring.backends.Windows",
        "keyring.backends.chainer",
        "keyring.backends.fail",
        "keyring.backends.null",
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[
        # Trim heavy deps we don't use. PIL + numpy used to live here when
        # pet conversion was build-time only — they're runtime now.
        "tkinter", "matplotlib", "pandas",
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="dashd-agent",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    target_arch=None,        # PyInstaller uses host arch; build x64 + arm64 separately for macOS
    codesign_identity=None,  # unsigned for v1
    entitlements_file=None,
)
