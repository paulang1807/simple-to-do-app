import os
import subprocess
import sys
import shutil
from pathlib import Path

APP_NAME = "Checkpoint"
MAIN_SCRIPT = "launcher.py"
ICON_PNG = "app_icon.png" 
ICON_ICNS = "icon.icns"

def run(cmd):
    print(f"Running: {cmd}")
    subprocess.check_call(cmd, shell=True)

def build():
    # 1. Clean up old builds
    for d in ["build", "dist"]:
        if os.path.exists(d):
            shutil.rmtree(d)
    
    # 2. Prepare Icon (if PNG exists)
    if os.path.exists(ICON_PNG):
        print("Creating .icns from PNG...")
        iconset = "icon.iconset"
        os.makedirs(iconset, exist_ok=True)
        sizes = [16, 32, 64, 128, 256, 512]
        for size in sizes:
            run(f"sips -s format png -z {size} {size} {ICON_PNG} --out {iconset}/icon_{size}x{size}.png")
            if size <= 512:
                run(f"sips -s format png -z {size*2} {size*2} {ICON_PNG} --out {iconset}/icon_{size}x{size}@2x.png")
        run(f"iconutil -c icns {iconset}")
        shutil.rmtree(iconset)
    else:
        print("Warning: app_icon.png not found, building without icon.")

    # 4. Run PyInstaller
    sep = ":" if sys.platform != "win32" else ";"
    cmd = [
        "pyinstaller",
        "--noconsole",
        "--windowed",
        f"--name=\"{APP_NAME}\"",
        f"--add-data=\"index.html{sep}.\"",
        "--hidden-import=anthropic",
        "--hidden-import=openai",
        "--hidden-import=google.genai",
        "--hidden-import=server",
        "--collect-all=anthropic",
        "--collect-all=openai",
        "--collect-all=google",
    ]
    
    if os.path.exists(ICON_ICNS):
        cmd.append(f"--icon=\"{ICON_ICNS}\"")
    
    cmd.append(MAIN_SCRIPT)
    
    run(" ".join(cmd))

    print("\nBuild complete! Check the 'dist' folder for Checkpoint.app")

if __name__ == "__main__":
    build()
