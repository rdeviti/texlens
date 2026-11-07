import os
import shutil
import tempfile
import unittest
from pathlib import Path

from texlens.words import TeXcountError, texcount_command

TESTS = Path(__file__).resolve().parent


class ProjectCase(unittest.TestCase):
    """Test case that can write small LaTeX projects to a temporary dir."""

    def project(self, files: dict) -> Path:
        directory = Path(tempfile.mkdtemp(prefix="texlens-test-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        for name, content in files.items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return directory / "main.tex"


def texcount_available() -> bool:
    try:
        texcount_command()
        return True
    except TeXcountError:
        return False


# Set TEXLENS_REQUIRE_TEXCOUNT=1 to make these tests fail, instead of being
# skipped, when TeXcount is missing (as in continuous integration).
needs_texcount = unittest.skipUnless(
    texcount_available() or os.environ.get("TEXLENS_REQUIRE_TEXCOUNT"),
    "TeXcount not found (set TEXLENS_TEXCOUNT)")


def toml_available() -> bool:
    try:
        import tomllib  # noqa: F401
        return True
    except ModuleNotFoundError:
        try:
            import tomli  # noqa: F401
            return True
        except ModuleNotFoundError:
            return False


needs_toml = unittest.skipUnless(toml_available(),
                                 "needs Python 3.11+ or tomli")
