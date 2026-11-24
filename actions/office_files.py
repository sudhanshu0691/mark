"""Local document path policy, verified saves and optional Office conversion."""
import os
import shutil
import subprocess
from pathlib import Path
from tempfile import TemporaryDirectory

from core.permissions import check


def permitted(path, *, write=False):
    path = Path(path).expanduser().resolve()
    allowed, reason = check(path, write=write)
    if not allowed:
        raise PermissionError(reason)
    return path


def save_verified(path, save, verify, *, replace=False):
    path = permitted(path, write=True)
    if path.exists() and not replace:
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix=".mark-office-", dir=path.parent) as directory:
        temporary = Path(directory) / path.name
        save(temporary)
        verify(temporary)
        if replace:
            os.replace(temporary, path)
        else:
            # Exclusive publication also handles another creator racing this save.
            os.link(temporary, path)


def convert_pdf(source, destination, *, internal_source=False):
    from pypdf import PdfReader

    source = Path(source) if internal_source else permitted(source)
    candidates = [shutil.which("soffice"), shutil.which("libreoffice")]
    if os.name == "nt":
        candidates.extend(Path(os.environ.get(key, "C:/Program Files")) /
                          "LibreOffice/program/soffice.com" for key in ("ProgramFiles", "ProgramFiles(x86)"))
    engine = next((str(path) for path in candidates if path and Path(path).is_file()), None)
    if not engine:
        raise RuntimeError("LibreOffice is required for this conversion.")

    def convert(temporary):
        with TemporaryDirectory(prefix="mark-office-engine-") as directory:
            profile = (Path(directory) / "profile").as_uri()
            result = subprocess.run(
                [engine, f"-env:UserInstallation={profile}", "--headless", "--convert-to", "pdf",
                 "--outdir", directory, str(source)], capture_output=True, timeout=90, check=False,
            )
            output = Path(directory) / (source.stem + ".pdf")
            if result.returncode or not output.is_file():
                raise RuntimeError("LibreOffice did not produce a PDF.")
            shutil.copyfile(output, temporary)

    save_verified(destination, convert, lambda p: len(PdfReader(p).pages))
