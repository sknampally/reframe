# Install, launch and update Reframe

Choose one environment and install **all selected tools in that environment**.

| Workstation | Run Reframe in | Status |
| --- | --- | --- |
| Mac | Terminal using Bash or Zsh | Current development/test platform |
| Windows 10/11 | Ubuntu under WSL2 | Linux execution path; end-to-end WSL acceptance pending |
| Linux | Native Bash-compatible terminal | Supported by the current POSIX implementation; live acceptance pending |
| Windows native | PowerShell or Command Prompt | Not supported by this POC runtime |

Native Windows uv/Claude/Codex installations do not make the current Reframe runtime
Windows-compatible. It uses POSIX file locks and process-group operations. Do not
mix a Windows Python environment with Linux/WSL tools or reuse a Mac virtual environment.

## Mac: first installation

Install Git if missing (`xcode-select --install`), then install uv:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
uvx --version
uv python install 3.12
```

Add the PATH line once to `~/.bash_profile` for a Mac Bash login shell or `~/.zshrc`
for Zsh, then reopen Terminal. See [uv's official installation guide](https://docs.astral.sh/uv/getting-started/installation/).

Install AWS CLI v2 and the selected agent tools using the [engineer guide](engineer-setup.md).
Claude Code is required for review. Codex is required when selected for HTML (the
default); Kiro is optional. Ensure each selected command works in **this terminal**.
Having an application installed does not necessarily expose its CLI on PATH.

Clone into your chosen working folder and install Reframe:

```bash
git clone https://github.com/sknampally/reframe.git
cd reframe
uv tool install --force --python 3.12 --editable .
reframe
```

If the repository already exists, use the update instructions below instead of
cloning another copy.

## Windows: first installation using WSL2

In an **administrator PowerShell**, install WSL/Ubuntu:

```powershell
wsl --install -d Ubuntu
```

Restart Windows if requested, open Ubuntu from Windows Terminal and complete its
Linux user setup. Check the distribution is using WSL2:

```powershell
wsl --list --verbose
```

Follow [Microsoft's WSL installation guide](https://learn.microsoft.com/en-us/windows/wsl/install)
if WSL2 is unavailable or your organization requires a managed installation.

**Switch to Ubuntu Bash for all commands below.** Install Linux prerequisites and uv:

```bash
sudo apt-get update
sudo apt-get install -y git curl ca-certificates unzip
curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv --version
uvx --version
uv python install 3.12
```

Add the PATH line once to `~/.bashrc`, then reopen Ubuntu. Install AWS CLI v2 using
the Linux instructions in the [engineer guide](engineer-setup.md), selecting the
Linux archive matching `uname -m`. Install the Linux Claude Code, Codex and optional
Kiro CLIs **inside Ubuntu**, not just their Windows versions. Complete their logins
from Ubuntu. Windows-side profiles and logins are not assumed to exist inside WSL.

Keep the checkout and private project directory in the Linux home filesystem:

```bash
mkdir -p "$HOME/projects"
cd "$HOME/projects"
git clone https://github.com/sknampally/reframe.git
cd reframe
uv tool install --force --python 3.12 --editable .
reframe
```

For example, a private project directory could be `~/modernization-poc/carddemo`;
keep it outside the framework and source checkouts. Do not paste a Mac `/Users/...`
path or Windows `C:\...` path into an Ubuntu setup prompt.

## What the installation command means

```bash
uv tool install --force --python 3.12 --editable .
```

- `uv tool install`: create an isolated tool environment and expose `reframe`.
- `--force`: replace the existing tool installation/executable registration.
- `--python 3.12`: use the selected Python version for the tool environment.
- `--editable .`: install the framework in the **current repository directory**.

**The final dot is required.** Without it, uv reports `<PACKAGE>` missing and installs
nothing. Keep the editable checkout available; it contains the versioned prompts
and contracts used at runtime. Runtime dependencies, including Rich and
prompt-toolkit for the terminal interface, are installed with the framework.

## Updates on Mac

Exit Reframe with `/exit`, then in Terminal change to your framework checkout:

```bash
git status --short
git pull --ff-only
uv tool install --force --python 3.12 --editable .
reframe
```

## Updates on Windows / WSL2

Exit Reframe, open **Ubuntu**, then change to your Linux framework checkout:

```bash
cd "$HOME/projects/reframe"
git status --short
git pull --ff-only
uv tool install --force --python 3.12 --editable .
reframe
```

Use your actual clone location if different. Apply updates from the normal main
checkout; do not overwrite local changes or force a branch switch. Stop on an error
rather than continuing through failed installation.

Updating editable source alone may leave newly added dependencies missing. Rerun the
full installation command after pulling a release that changes dependencies. Install
updates **before** initializing an experiment. Existing runs pin the framework
revision: keep their checkout unchanged until they finish, or start a new private
workspace for a new revision. Do not delete evidence or edit pinned revision records
to bypass this check.

## Verify tools before setup

Run inside Mac Terminal or Ubuntu, as applicable:

```bash
command -v git uv uvx aws claude reframe
git --version
uv --version
aws --version
claude --version
claude auth status
reframe --help
```

When Codex is selected:

```bash
command -v codex
codex --version
codex login
codex login status
```

If Codex is missing, use an approved vendor installation. For example, with Node/npm
already available, `npm install -g @openai/codex` works on Mac or inside Ubuntu.
On Mac with Homebrew installed, `brew install --cask codex` is another option. The
[official Codex repository](https://github.com/openai/codex#installing-and-running-codex-cli)
also provides standalone installers. Reopen the terminal after PATH changes.
Alternatively select Claude as the HTML provider during a fresh setup.

When Kiro is selected, also verify `kiro-cli --version` and its headless authentication
as described in the [engineer guide](engineer-setup.md). No Kiro installation is needed
for the default Claude executor/reviewer pair.

## Guided AWS login and next steps

Launch `reframe` and choose **1** or `/init`. Enter your existing AWS profile name
and region. Reframe checks identity access; if the SSO session needs refreshing, it
offers to launch AWS's login flow and verifies access again afterward. Missing SSO
configuration gets a separate configuration choice. Manual fallback, in the same shell:

```bash
aws configure sso --profile YOUR_PROFILE
aws sso login --profile YOUR_PROFILE
aws sts get-caller-identity --profile YOUR_PROFILE
```

Use `aws configure sso` only when configuration is needed. Keep identity output,
account/role choices, credentials and private settings out of the public repository.
AWS login does not prove Transform application connection or connector authorization;
`init` guides those separately and stops until requirements are satisfied.

Once setup reports readiness, `/assess` is the next step. For a service-free interface
walkthrough use `reframe demo`. WSL setup is documented, not a claim that the live
CardDemo/AWS workflow has been verified on Windows.

## Common errors

| Error | Remedy |
| --- | --- |
| `<PACKAGE>` missing | Run the full install command including the final `.` |
| `ModuleNotFoundError: rich` or `prompt_toolkit` | From the updated framework checkout, rerun the full installation command |
| `reframe: command not found` | Run `uv tool update-shell`, reopen the terminal and check PATH |
| `Install missing tools: codex` | Install the Codex CLI in the same terminal environment, then verify version/login |
| AWS authentication unavailable | Complete the offered SSO login; check that the selected profile exists in this environment |
| Native Windows `fcntl`/process error | Use the Ubuntu/WSL2 instructions; native Windows runtime is not supported |
| Error reason hidden | Use `/privacy` locally to reveal diagnostics; avoid sharing private output during a presentation |

Continue with the [terminal guide](terminal-console.md) and [execution guide](cli-guide.md).

## Mac CPU architecture and MCP dependency failures

On Apple Silicon, a universal system Python can launch with a different architecture
than Intel uv installed for Rosetta. Native packages such as pydantic-core then fail
to import before the MCP server starts. Prefer uv-managed Python to keep the runtime
and installed wheels consistent:

```bash
uv python install 3.12
uv tool install --force --reinstall --managed-python --python 3.12 --editable .
```

Reframe checks MCP imports early and shows this repair hint even with presentation
privacy enabled. Technical import details and MCP server stderr stay in private
workspace logs; they are not client-presentation output. Never share these logs
without reviewing/redacting account and authentication information.
