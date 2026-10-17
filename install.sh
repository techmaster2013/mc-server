#!/usr/bin/env bash
# Run: bash install.sh [release-version]
# Explicit EULA acceptance: ACCEPT_EULA=true bash install.sh
# Optional: SERVER_DIR=/workspaces/my-server MC_VERSION=1.21.1 bash install.sh
set -euo pipefail

if [[ "${1:-}" == "--help" ]]; then
  cat <<'HELP'
Usage: bash install.sh [release-version]
Defaults to the latest vanilla release on first install; reruns keep that version.
SERVER_DIR sets the destination (default: minecraft-server beside this script).
ACCEPT_EULA=true explicitly accepts https://aka.ms/MinecraftEULA.
Run the generated start-public.sh for Minecraft + Playit, or start.sh for local only.
Playit needs a one-time browser claim and Minecraft Java tunnel to 127.0.0.1:25565.
Installation never starts the server or accepts the EULA automatically.
HELP
  exit 0
fi
[[ $# -le 1 ]] || { echo "Usage: bash install.sh [release-version]" >&2; exit 1; }
[[ "$(uname -s)" == Linux ]] || { echo "Use this installer inside a Linux Codespace." >&2; exit 1; }
if ! command -v python3 >/dev/null || ! command -v tar >/dev/null || ! command -v flock >/dev/null; then
  command -v apt-get >/dev/null || { echo "Install python3, ca-certificates, tar, gzip and util-linux first." >&2; exit 1; }
  admin=()
  if [[ $EUID -ne 0 ]]; then
    command -v sudo >/dev/null || { echo "sudo is needed to install prerequisites." >&2; exit 1; }
    admin=(sudo)
  fi
  "${admin[@]}" apt-get update
  "${admin[@]}" apt-get install -y python3 ca-certificates tar gzip util-linux
fi
export SERVER_DIR="${SERVER_DIR:-$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/minecraft-server}"
export MC_VERSION="${1:-${MC_VERSION:-}}"
python3 - <<'PY'
import hashlib
import json
import os
import pathlib
import platform
import re
import subprocess
import tempfile
import time
import urllib.parse
import urllib.request

root = pathlib.Path(os.environ["SERVER_DIR"]).expanduser().resolve()
root.mkdir(parents=True, exist_ok=True)

def fetch(url):
    if urllib.parse.urlparse(url).scheme != "https":
        raise RuntimeError("Refusing a non-HTTPS download")
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "Codespaces-Minecraft-Installer/1.0"})
            return urllib.request.urlopen(request, timeout=120)
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)

def metadata(url):
    with fetch(url) as response:
        return json.load(response)

def download(url, target, algorithm, expected):
    digest = hashlib.new(algorithm)
    with fetch(url) as response, open(target, "wb") as output:
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            output.write(chunk)
            digest.update(chunk)
    if digest.hexdigest().lower() != expected.lower():
        raise RuntimeError("Checksum mismatch for " + target.name)

def java_major(binary):
    try:
        output = subprocess.check_output([str(binary), "-version"], stderr=subprocess.STDOUT, text=True)
        match = re.search(r'version "(?:1\.)?(\d+)', output)
        return int(match.group(1)) if match else 0
    except (OSError, subprocess.CalledProcessError):
        return 0

# Refuse implicit upgrades/downgrades and unknown existing jars.
version_file = root / "minecraft-version.txt"
jar = root / "server.jar"
installed = version_file.read_text().strip() if version_file.exists() else ""
requested = os.environ.get("MC_VERSION", "")
if jar.exists() and not installed:
    raise RuntimeError("Existing server.jar has no installer version record; use a fresh SERVER_DIR.")
if jar.exists() and requested and requested != installed:
    raise RuntimeError("Version changes need a fresh SERVER_DIR. Back up/migrate worlds manually.")
manifest = metadata("https://piston-meta.mojang.com/mc/game/version_manifest_v2.json")
version = requested or installed or manifest["latest"]["release"]
entry = next((v for v in manifest["versions"] if v["id"] == version and v["type"] == "release"), None)
if not entry:
    raise RuntimeError("Unknown release version: " + version)
details = metadata(entry["url"])
required = details.get("javaVersion", {}).get("majorVersion", 21)
server = details.get("downloads", {}).get("server")
if not server:
    raise RuntimeError("This version has no dedicated server download.")

with tempfile.TemporaryDirectory(prefix=".install-", dir=root) as temporary:
    stage = pathlib.Path(temporary)
    if jar.exists():
        if hashlib.sha1(jar.read_bytes()).hexdigest() != server["sha1"]:
            raise RuntimeError("Existing server.jar differs from Mojang's checksum; refusing to overwrite it.")
    else:
        print("Downloading vanilla Minecraft", version, flush=True)
        download(server["url"], stage / "server.jar", "sha1", server["sha1"])

    # Install an isolated Temurin runtime; never change the system Java selection.
    runtime = root / (".java-" + str(required))
    java = runtime / "bin" / "java"
    if java_major(java) != required:
        if runtime.exists():
            raise RuntimeError("Existing runtime is invalid; inspect " + str(runtime))
        arch = {"x86_64": "x64", "aarch64": "aarch64"}.get(platform.machine())
        if not arch:
            raise RuntimeError("Unsupported CPU architecture: " + platform.machine())
        query = urllib.parse.urlencode({"architecture": arch, "image_type": "jre", "os": "linux", "vendor": "eclipse"})
        assets = metadata("https://api.adoptium.net/v3/assets/latest/" + str(required) + "/hotspot?" + query)
        if not assets:
            raise RuntimeError("No Temurin runtime is available for Java " + str(required))
        package = assets[0]["binary"]["package"]
        print("Installing verified Temurin Java", required, flush=True)
        archive = stage / "java.tar.gz"
        download(package["link"], archive, "sha256", package["checksum"])
        unpacked = stage / "runtime"
        unpacked.mkdir()
        # Archive comes from Adoptium's HTTPS API and is SHA-256 verified.
        subprocess.run(["tar", "-xzf", str(archive), "-C", str(unpacked), "--strip-components=1"], check=True)
        if java_major(unpacked / "bin" / "java") != required:
            raise RuntimeError("Downloaded Java runtime failed its version check")
        unpacked.rename(runtime)
    if not jar.exists():
        (stage / "server.jar").replace(jar)
        version_file.write_text(version + "\n")

def create_only(path, contents):
    try:
        with path.open("x") as output:
            output.write(contents)
    except FileExistsError:
        pass

create_only(root / ".gitignore", "*\n")
create_only(root / "eula.txt", "# Read https://aka.ms/MinecraftEULA before accepting.\neula=false\n")
eula = root / "eula.txt"
if os.environ.get("ACCEPT_EULA", "").lower() == "true":
    # Preserve other lines; replace only the EULA setting.
    lines = [line for line in eula.read_text().splitlines() if not re.match(r"^\s*eula\s*=", line)]
    eula.write_text("\n".join(lines + ["eula=true"]) + "\n")

create_only(root / "server.properties", """server-port=25565
online-mode=true
enable-rcon=false
view-distance=6
simulation-distance=4
max-players=5
""")
create_only(root / "start.sh", """#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if ! grep -Eq '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true[[:space:]]*$' eula.txt; then
  echo 'Read https://aka.ms/MinecraftEULA, then set eula=true in eula.txt.' >&2
  exit 1
fi
memory="${MC_MEMORY:-2G}"
[[ "$memory" =~ ^[1-9][0-9]*[MG]$ ]] || { echo 'MC_MEMORY must look like 2G or 1536M.' >&2; exit 1; }
exec "./.java-JAVA_MAJOR/bin/java" -Xms512M "-Xmx$memory" -jar server.jar nogui "$@"
""".replace("JAVA_MAJOR", str(required)))
(root / "start.sh").chmod(0o755)

# Install the standalone Playit agent locally (no systemd or desktop client).
# Pinned because this version supports foreground operation and --secret_path.
playit_dir = root / ".playit"
playit_dir.mkdir(exist_ok=True)
playit_dir.chmod(0o700)
playit_arch = {"x86_64": "amd64", "aarch64": "aarch64"}.get(platform.machine())
playit_hashes = {
    "amd64": "e78d463d93aa1e3ec36a06ded5a1f4fe879905fdceb865df8f4cef6124f8a555",
    "aarch64": "cd3fa1cedac40a71d80a120e6353e08836308840340b58e659e8f25d00601f66",
}
if not playit_arch:
    raise RuntimeError("Unsupported Playit CPU architecture: " + platform.machine())
playit = playit_dir / "playit"
expected = playit_hashes[playit_arch]
if playit.exists():
    if hashlib.sha256(playit.read_bytes()).hexdigest() != expected:
        raise RuntimeError("Existing Playit binary has an unexpected checksum; inspect " + str(playit))
else:
    with tempfile.TemporaryDirectory(prefix=".download-", dir=playit_dir) as temporary:
        binary = pathlib.Path(temporary) / "playit"
        print("Downloading verified Playit standalone agent 0.17.1", flush=True)
        download("https://github.com/playit-cloud/playit-agent/releases/download/v0.17.1/playit-linux-" + playit_arch,
                 binary, "sha256", expected)
        binary.chmod(0o700)
        binary.replace(playit)
playit.chmod(0o700)
create_only(root / "tunnel.sh", """#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
umask 077
echo 'First run: open the claim link below in your browser and link your Playit account.'
echo 'In Playit, add a Minecraft Java tunnel to local address 127.0.0.1, port 25565.'
echo 'Join using the public address Playit assigns, including its port if shown.'
echo 'Keep your claim link and .playit/playit.toml private.'
exec ./.playit/playit --secret_path ./.playit/playit.toml --stdout "$@"
""")
create_only(root / "start-public.sh", """#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")"
if ! grep -Eq '^[[:space:]]*eula[[:space:]]*=[[:space:]]*true[[:space:]]*
print("\nInstalled Minecraft " + version + " in " + str(root))
print("Read https://aka.ms/MinecraftEULA and set eula=true in eula.txt to accept.")
print("Start: cd " + __import__("shlex").quote(str(root)) + " && bash start.sh")
print("Optional memory limit: MC_MEMORY=1536M bash start.sh")
print("Stop safely by typing stop in the server console.")
print("Codespaces browser preview URLs are not Minecraft TCP addresses.")
print("Browser-only public server: cd " + __import__("shlex").quote(str(root)) + " && bash start-public.sh")
print("First run: claim the Playit agent using its printed browser link.")
print("In Playit, create a Minecraft Java tunnel to 127.0.0.1:25565.")
print("Use the public address from Playit in Minecraft Multiplayer; keep the launcher running.")
print("You can also run bash tunnel.sh separately alongside bash start.sh.")
print("Codespace suspension stops the server. Back up worlds before deleting the Codespace.")
print("Generated server files are ignored by Git; backups must be stored separately.")
PY
 eula.txt; then
  echo 'Read https://aka.ms/MinecraftEULA, then set eula=true in eula.txt.' >&2
  exit 1
fi
# One launcher per server directory; prevent duplicate tunnels/server processes.
exec 9>.playit/launcher.lock
flock -n 9 || { echo 'A public launcher is already running.' >&2; exit 1; }
tunnel_pid=
cleanup() {
  if [[ -n "$tunnel_pid" ]]; then
    kill "$tunnel_pid" 2>/dev/null || true
    wait "$tunnel_pid" 2>/dev/null || true
  fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
bash tunnel.sh &
tunnel_pid=$!
echo 'Starting Minecraft and Playit. Type stop to save the world and stop both.'
bash start.sh "$@"
""")
for name in ("tunnel.sh", "start-public.sh"):
    (root / name).chmod(0o755)

print("\nInstalled Minecraft " + version + " in " + str(root))
print("Read https://aka.ms/MinecraftEULA and set eula=true in eula.txt to accept.")
print("Start: cd " + __import__("shlex").quote(str(root)) + " && bash start.sh")
print("Optional memory limit: MC_MEMORY=1536M bash start.sh")
print("Stop safely by typing stop in the server console.")
print("Codespaces browser preview URLs are not Minecraft TCP addresses.")
print("Browser-only public server: cd " + __import__("shlex").quote(str(root)) + " && bash start-public.sh")
print("First run: claim the Playit agent using its printed browser link.")
print("In Playit, create a Minecraft Java tunnel to 127.0.0.1:25565.")
print("Use the public address from Playit in Minecraft Multiplayer; keep the launcher running.")
print("You can also run bash tunnel.sh separately alongside bash start.sh.")
print("Codespace suspension stops the server. Back up worlds before deleting the Codespace.")
print("Generated server files are ignored by Git; backups must be stored separately.")
PY
