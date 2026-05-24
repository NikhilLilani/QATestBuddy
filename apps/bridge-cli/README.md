# qa-bridge

Local CLI that runs Playwright tests on your machine on behalf of QAtestbuddy.

## Install

```powershell
npm install -g qa-bridge        # after the package is published
# or run without installing:
npx qa-bridge --help
```

## Usage

```powershell
qa-bridge login --token <bridge-token-from-settings>
qa-bridge link                  # from inside your project repo
qa-bridge run <runId>           # execute a run streamed from the server
qa-bridge codegen <url>         # capture selectors
qa-bridge status
qa-bridge logout
```

Config lives in `~/.qatb/config.json` (mode 0600).
