# Releasing Grimwatch

Use this checklist for every public release.

## Pre-release

1. Update `grimwatch.__version__`, `pyproject.toml`, and `CHANGELOG.md` to the same version.
2. Run the test suite:

   ```bash
   python -m pytest
   ```

3. Build the Python distributions and validate their metadata:

   ```bash
   python -m pip install --upgrade build twine
   python -m build
   twine check dist/*
   ```

4. Install the wheel in a fresh virtual environment and confirm:

   ```bash
   grimwatch --version
   grimwatch --help
   ```

5. Publish to TestPyPI first, then install that build in a fresh environment as a smoke test.

## Public release

1. Commit the release and push the matching Git tag.
2. Create the Windows executable:

   ```bash
   python -m pip install --upgrade pyinstaller
   python -m PyInstaller --onefile --name grimwatch --console grimwatch/cli.py
   ```

3. Calculate and publish a SHA-256 checksum for `dist/grimwatch.exe`.
4. Create a GitHub Release from the tag, add concise release notes, and upload the executable and checksum.
5. Upload only the Python source distribution and wheel to PyPI:

   ```bash
   twine upload dist/*.tar.gz dist/*.whl
   ```

6. Verify the public PyPI install from a clean environment.

Never commit `dist/`, API tokens, personal archive files, or generated configuration.
