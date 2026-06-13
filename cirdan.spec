# PyInstaller spec for the standalone `cirdan` binary (npm / Homebrew / curl).
#
#   pip install -e ".[all]" pyinstaller
#   pyinstaller cirdan.spec
#   -> dist/cirdan        (POSIX)
#   -> dist/cirdan.exe    (Windows)
#
# Cirdan imports almost everything lazily — adapters are pulled in inside
# functions in cirdan/adapters/registry.py, and the engine/UI/MCP modules are
# imported on demand. So `collect_submodules("cirdan")` is load-bearing: without
# it the frozen binary silently loses adapters at runtime. The single bundled
# data file is the Jinja view template; render.py resolves it via
# `Path(__file__).parent / "templates"`, which PyInstaller rewrites into the
# onefile extraction dir (sys._MEIPASS), so no source change is needed.

from PyInstaller.utils.hooks import collect_all, collect_submodules

datas = [("cirdan/ui/templates", "cirdan/ui/templates")]
binaries = []
hiddenimports = []

# Lazy in-package imports (adapters, engine, mcp, ui, report) + networkx's
# lazily-loaded community/louvain algorithms.
hiddenimports += collect_submodules("cirdan")
hiddenimports += collect_submodules("networkx")

# Third-party packages with data files, compiled extensions, or dynamically
# discovered submodules. Built with the `all` extra so mcp (serve-mcp), the
# http transport (fastapi/uvicorn/starlette), and terraform (hcl2 ships a .lark
# grammar) all work from one binary. collect_all degrades to empty lists if an
# optional package is absent, so the spec stays robust.
for pkg in ("mcp", "pydantic", "pydantic_core", "hcl2", "fastapi", "uvicorn", "anyio", "starlette"):
    try:
        d, b, h = collect_all(pkg)
        datas += d
        binaries += b
        hiddenimports += h
    except Exception:
        pass

hiddenimports += [
    "mcp.server.fastmcp",
    "networkx.algorithms.community",
    "networkx.algorithms.community.louvain",
    "sniffio",
]

a = Analysis(
    ["packaging/pyi_entry.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "pytest_asyncio", "_pytest"],
    noarchive=False,
)
pyz = PYZ(a.pure)

# onefile form: a.binaries + a.datas are folded into EXE (no COLLECT step).
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="cirdan",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,  # UPX-packed onefiles trip Windows Defender / SmartScreen false positives.
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,  # required: the stdio MCP transport needs a console app.
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
