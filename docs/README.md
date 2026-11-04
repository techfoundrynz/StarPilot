# Docs development

Fork guides: [development](how-to/development-workflow.md), [upstream updates](how-to/upstream-updates.md), [pet store](how-to/pet-store.md).

The `docs/` tree is the source for [docs.comma.ai](https://docs.comma.ai).
The site is updated on pushes to master by this [workflow](../.github/workflows/docs.yaml).

**1. Build the site**
``` bash
op docs --build
```

**2. Run the site locally** (rebuilds on change)
``` bash
op docs
```
