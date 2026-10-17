# StarPilot fork documentation

Start with these guides for `techfoundrynz/StarPilot`:

* [Development and publishing workflow](how-to/development-workflow.md)
* [Maintaining the fork and updating upstream](how-to/upstream-updates.md)
* [Device CI and build artifacts](how-to/device-ci.md)
* [Local device builds](how-to/laptop-device-build.md)
* [Pet store](how-to/pet-store.md)

The remaining documentation includes material inherited from openpilot, whose
upstream documentation is published at [docs.comma.ai](https://docs.comma.ai).
The commands below build a local documentation site; they do not publish to that site.

## Development
NOTE: Those commands must be run in the root directory of openpilot, **not /docs**

**1. Install the docs dependencies**
``` bash
pip install .[docs]
```

**2. Build the new site**
``` bash
mkdocs build
```

**3. Run the new site locally**
``` bash
mkdocs serve
```

References:
* https://www.mkdocs.org/getting-started/
* https://github.com/ntno/mkdocs-terminal
