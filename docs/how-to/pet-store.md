# Pet store

StarPilot can download a companion from [Codex Pets](https://codex-pets.net/) and animate it on the driving screen. There is no bundled pet or fallback artwork. An empty, missing, or damaged selection displays nothing.

## Choose a pet

- On the large UI, open **Settings → StarPilot → Appearance → Driving Widgets & HUD → Pet Store**.
- On Mici, open **Settings → Visuals → pet store**.
- Open **Browse**, or **Search** by name. Use the arrow buttons to browse pets and pages.
- Choose **Download**, then **Use**. Downloading alone does not change the selected pet. Use selects the installed pet and enables **Show Pet** (Mici: **show pet**).
- Open **Installed** to select or remove downloaded pets without a network connection. Removing the selected pet clears its selection. Removal requires a second tap to confirm.

The store is available only while parked and closes, including its search keyboard, when driving starts. Catalog browsing and downloads require internet access; installed pets and their animations work offline. Downloads finish in the background if the panel closes. Pets are stored under `/data/pets` and persist across reboots.

## Display behavior

The selected pet appears at the lower right of the road view when **Show Pet** is enabled. It follows stationary, moving, engaged, override, and turn-signal states. A short animation hold smooths brief state changes; override responds immediately. Mici hides the pet while alerts are showing. Both layouts respect their existing reverse, driver-camera, and hidden-camera overlay gates.

V1 packages use the nine animation rows of a 1536×1872 sprite sheet. V2 packages use a 1536×2288 sheet and add still look poses for stationary turns. Each cell is 192×208; playback uses each animation's frame count instead of treating every row as eight frames. There are no accounts, uploads, or social features in this integration.

## Development and validation

`ShowPet` and `SelectedPetId` are persistent native parameters. Rebuild the native parameter bindings (or install a complete new device build) before running the updated UI; copying only Python files onto an older build is insufficient. Pillow is a runtime dependency for validating packages and converting their WebP sprite sheets to PNG.

Focused tests cover package validation, bounded downloads, atomic installation, offline removal, selection persistence, panel lifecycle, absent/corrupt artwork, and animation state transitions:

```sh
pytest system/ui/tests/test_pet_store.py selfdrive/ui/tests/test_pet_store_panel.py
```

Validation includes live catalog/poster downloads and v1/v2 package installation in temporary storage, plus hidden raylib rendering of the store at both UI sizes. The UI tests stub device messaging and hardware. Rendering smoke tests do not establish device performance or validate touch interaction, driving transitions, and placement on physical hardware; those require a rebuilt device image and a device smoke test.
