# Open Source Checklist

Publish `director-workflow/` as the repository root. Do not publish the parent `视频/` folder directly because it contains local runs, cloned upstream projects, and generated media.

## Must Do Before First Push

- Create `.env.local` from `.env.example` on each machine; never commit real keys.
- Keep DeepSeek keys in env, `.env.local`, or macOS Keychain service `codex.deepseek.api_key`.
- Keep Codex auth in the user's Codex CLI config/login. Do not store Codex tokens in this project.
- Document that Codex built-in image generation is a Codex session handoff, not a Python API call from this repository.
- Keep TTS provider credentials in MoneyPrinterTurbo or the provider's own setup; this project only passes the selected MPT `voice_name`.
- Exclude `director-runs/`, MPT `storage/`, upload cookies, browser profiles, rendered videos, and contact sheets from Git.
- Add a real open-source license file before public release.
- Document that MoneyPrinterTurbo is a peer runtime dependency, not vendored source.
- Run unit tests and compile checks before each release.

## Local Validation

```bash
python -m unittest discover -s tests
python -m compileall -q director_workflow tools
python tools/smoke_strict_flow.py
python tools/webui.py --host 127.0.0.1 --port 8765
```

## Secret Policy

The project may report whether an API key is configured, but it must never print key values. WebUI status and job commands redact sensitive-looking fields.
