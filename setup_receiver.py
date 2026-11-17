"""Install the matching upstream MediaMTX executable before going into the field."""
import hashlib
import io
import os
import platform
from pathlib import Path
import tarfile
import tempfile
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parent
VERSION = 'v1.21.4'


def install():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {'x86_64': 'amd64', 'amd64': 'amd64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(machine)
    windows = system == 'windows'
    name = 'mediamtx.exe' if windows else 'mediamtx'
    rel = 'server/' + name if windows else f'server/bin/{system}-{arch}/{name}'
    hashes = {line.split()[1]: line.split()[0] for line in
              (ROOT / 'obs/BINARY-SHA256.txt').read_text().splitlines()}
    if rel not in hashes or (windows and arch != 'amd64'):
        raise SystemExit(f'Unsupported platform: {system} {machine}')
    destinations = [ROOT / 'obs' / rel]
    if windows:
        destinations += [ROOT / 'MobileCameraHub/bin' / name, ROOT / 'Portable_OBS_Field_Hub/receiver' / name]
    for path in destinations:
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == hashes[rel]:
            data = path.read_bytes()
            break
    else:
        ext = 'zip' if windows else 'tar.gz'
        url = f'https://github.com/bluenviron/mediamtx/releases/download/{VERSION}/mediamtx_{VERSION}_{system}_{arch}.{ext}'
        with urlopen(url, timeout=120) as response:
            archive = io.BytesIO(response.read())
        if windows:
            with zipfile.ZipFile(archive) as package:
                data = package.read(name)
        else:
            with tarfile.open(fileobj=archive) as package:
                item = next(m for m in package if m.isfile() and m.name.removeprefix('./') == name)
                data = package.extractfile(item).read()
    if hashlib.sha256(data).hexdigest() != hashes[rel]:
        raise SystemExit('MediaMTX binary checksum mismatch')
    for path in destinations:
        if path.is_file() and path.read_bytes() == data:
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
            temp.write(data)
        os.chmod(temp.name, 0o755)
        os.replace(temp.name, path)
    print('Receiver verified:', *destinations, sep='\n')


if __name__ == '__main__':
    install()
