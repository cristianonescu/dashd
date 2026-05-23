# Contributing to dashd

Thanks for taking a look. dashd is a personal project but PRs and issues are welcome.

## Reporting a bug

Open a [GitHub issue](https://github.com/cristianonescu/dashd/issues/new) with:

- What you expected
- What happened instead
- Steps to reproduce
- OS + dashd version (from **Settings → Updates → Current version** or `dashd --version`)
- For agent issues: a snippet from `dashd -v` output covering the moment the bug occurred
- For firmware issues: the device's screen behaviour + any log lines from the dockable Logs panel
- For UI issues: a screenshot helps

If the bug involves Bluetooth, mention macOS / Linux / Windows version and whether you've granted Bluetooth permission to the app.

## Requesting a feature

Open an issue with the **use case first** — what problem are you trying to solve. Implementations follow more easily once the problem is shared.

## Sending a PR

1. Fork the repo and create a topic branch off `main`.
2. Keep the change focused — one feature or one bug fix per PR. Split refactors out.
3. Match the existing style. Both agent (Python) and UI (TypeScript) use minimal formatters — the existing files are the style guide.
4. **Add or update tests.**
   - Agent: `cd agent && pytest -q` (suite is around 110 tests; PRs should keep it green)
   - UI: `cd ui && npm run build` must succeed (TypeScript strict mode is on)
   - Firmware: `cd firmware && pio run -e dashd_ble` must build cleanly
5. Update the relevant doc:
   - User-facing change → `README.md`
   - Wire protocol change → `docs/protocol.md`
   - IPC change → `docs/ipc.md`
   - Packaging / install change → `docs/packaging.md`
6. **No commits with secrets.** Run `git diff` over your branch before pushing; never commit `.env`, API tokens, IMAP passwords, or anything from `~/.config/dashd/`.
7. Push and open a PR against `main`. Describe what the change does and why.

## Working on the firmware

The ESP32-C3 SuperMini connects via its native USB Serial/JTAG — no DTR/RTS dance needed. PlatformIO handles the upload automatically. If something goes wrong:

```bash
cd firmware
pio run -e dashd_ble -t upload     # full build + flash
pio device monitor -b 460800       # tail logs (stop the agent first;
                                   # macOS gives one process the CDC port)
```

If you brick a device with an experimental partition table, hold the BOOT button on the dev kit while plugging it in to force download mode, then `pio run -t upload` to restore a known-good build.

## Working on the OTA flow

Test against your own device before merging. The OTA pipeline includes auto-rollback (the bootloader reverts if a new image doesn't reach `setup()`), but a bad partition layout still requires a USB reflash to recover. See `firmware/partitions.csv` and `firmware/src/ota_link.cpp`.

## Releases

Maintainer-only. The flow is documented in [docs/packaging.md](docs/packaging.md#auto-update); short version: bump the version in `agent/pyproject.toml`, `ui/package.json`, and `firmware/include/config.h`, tag with `vX.Y.Z`, push the tag, GitHub Actions builds + attaches everything.

## Code of conduct

Be kind. See [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

## License

By contributing you agree that your contribution will be licensed under the [MIT License](LICENSE) — same as the rest of the project.
