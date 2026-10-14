# Browser worker

Run with a project virtual environment containing `requirements.txt`:

```powershell
$env:CLOAK_WORKBENCH_TOKEN = '<random secret of at least 24 characters>'
python worker/server.py
```

The desktop shell generates its token and launches this process automatically.
Default listen address is `127.0.0.1:8766`. All JSON API requests require
`Authorization: Bearer <token>`. The only public route is the offline `/probe`
HTML page. No CORS access is granted. The worker never logs the token or request
bodies. Remote debugging is local-only and is accessible to other local processes.

| Method and route | Body | Result |
| --- | --- | --- |
| GET `/health` | | availability, capabilities |
| GET `/profiles` or `/state` | | `{profiles: [...]}` |
| GET `/profiles/{id}/state` | | profile plus runtime state |
| POST `/profiles` | profile fields | create, or update a closed profile |
| POST `/profiles/{id}/launch` | `{}` | profile plus runtime state |
| POST `/profiles/{id}/close` | `{}` | closed profile |
| POST `/profiles/{id}/navigate` | `{url: "https://..."}` | profile plus runtime state |
| POST `/profiles/{id}/action` | `{action: "back"}` | back, forward, or reload |
| POST `/shutdown` | `{}` | gracefully close owned browsers; server remains alive |

Profile fields: `id`, `name`, `proxy`, `locale`, `timezone`, integer `seed`,
`startUrl`, integer `screenWidth`, integer `screenHeight`. The last two fields are
**initial window dimensions**, not a screen fingerprint override in this v146
runtime. Seed is generated once on creation and retained on partial updates.
Updating a running profile returns HTTP 409. Proxy credentials must be provided
by a separate local relay, not embedded in Chromium's proxy flag.

Runtime fields: `state` (`closed`, `starting`, `running`), `pid`, `windowHandle`,
`cdpPort`, `url`, `title`. The shell must retain a discovered window handle after
reparenting because it will no longer be a top-level window.

Persistence is in `../data/profiles.json` and `../data/profiles/{id}/`. Browser
exit never deletes profiles or cookies. A native file lease prevents two workers
from opening the same profile. A Windows job owns each launched process tree;
closing or terminating the worker does not kill unrelated browser instances.

The existing installed v146 Cloak executable is used without downloading an
engine or requiring a new release. `CLOAK_WORKBENCH_BROWSER` can override its path.
Confirmed flags from the installed Cloak package: `--fingerprint=<seed>`,
`--fingerprint-platform=windows`, `--lang`, `--fingerprint-locale`,
`--fingerprint-timezone`. No arbitrary Canvas/GPU/CPU overrides are claimed.

Run unit checks: `python -m unittest discover -s worker -v`.
Set `CLOAK_WORKBENCH_REAL_TEST=1` to also test real profile storage, locale,
timezone and Canvas stability against the offline probe.
