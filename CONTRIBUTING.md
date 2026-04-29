# Contributing to TrailPrint3D

Thanks for wanting to help! Here's everything you need to know.

---

## Before you start

- **Check existing issues** — someone may have already reported the bug or requested the feature.
- **Open an issue first** for anything significant so we can discuss the approach before you spend time coding.
- Small fixes (typos, obvious one-line bugs) can go straight to a PR.

---

## Setting up locally

1. Clone the repo:
   ```bash
   git clone https://github.com/EmGi96/TrailPrint3D.git
   ```
2. Symlink or copy the folder into your Blender addons directory:
   - **Windows:** `%APPDATA%\Blender Foundation\Blender\<version>\scripts\addons\TrailPrint3D`
   - **macOS:** `~/Library/Application Support/Blender/<version>/scripts/addons/TrailPrint3D`
   - **Linux:** `~/.config/blender/<version>/scripts/addons/TrailPrint3D`
3. Enable **TrailPrint3D** in Edit → Preferences → Add-ons.
4. After making changes, reload the addon with **Edit → Preferences → Add-ons → Reload** (or use the [Blender Development](https://marketplace.visualstudio.com/items?itemName=JacquesLucke.blender-development) VS Code extension).

---

## Code style

- Follow the existing style in each file — no enforced formatter, just keep it consistent.
- Keep operator logic in `operators.py`, UI in `panels.py`, shared helpers in `utils.py`.
- Avoid adding new dependencies; the addon is intentionally self-contained (except for optional third-party addons like the 3MF one).

---

## Submitting a pull request

1. Fork the repo and create a branch from `main`:
   ```bash
   git checkout -b fix/my-bug-description
   ```
2. Make your changes and test them in **Blender 5.0 or newer**.
3. Push your branch and open a PR against `main`.
4. Fill in the PR template — especially the "How was it tested?" section.

---

## Reporting bugs

Use the **Bug Report** issue template. The more detail you include (Blender version, OS, steps to reproduce, GPX file if relevant), the faster it can be fixed.

---

## Questions?

Open a [Discussion](../../discussions) or leave a comment on the relevant issue.
