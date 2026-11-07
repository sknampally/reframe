# Engineer workstation setup

For first installation, updates and troubleshooting on Mac or Windows/WSL2, follow
[the platform installation guide](platform-install.md). Every install command includes the required trailing dot.

Prepare this workstation once, then use `reframe init` for project setup. The POC
currently supports macOS and Linux; native Windows execution is not supported.
Use your organization's approved software distribution and authentication methods.
The commands below are alternatives when direct vendor installation is permitted.

## What to install

| Tool | When required | Purpose |
| --- | --- | --- |
| Git | Always | Clone and pin source/framework revisions |
| uv and Python 3.10+ | Always for the documented installation | Install the framework and its isolated dependencies |
| AWS CLI v2 | New AWS assessments or S3 findings | Authenticate an AWS profile and access assessment storage |
| Claude Code CLI | Current validation workflow | Separate Sonnet executor and Opus reviewer sessions |
| Codex CLI | When selected for HTML or execution; HTML default | Generate review content using the selected provider |
| Kiro CLI | Only when Kiro executor is selected | Optional company-approved agent runtime |
| AWS Transform MCP server | New assessment/resource setup | Access Transform from our CLI; launched through uvx |

The Kiro IDE alone does not install the CLI. Neither Kiro nor Claude acts as the MCP
client for Phase 1: the framework connects directly. The framework installation
includes the Python MCP SDK; do not repeatedly install it for individual commands.
Existing local findings can bypass AWS assessment setup in the advanced CLI path.
The guided new-assessment setup checks AWS credentials.

## 1. Git, uv and Python

Check Git first:

```bash
git --version
```

On macOS, if Git is missing, `xcode-select --install` installs Apple's command-line
tools through the system prompt. On Linux, install Git with the distribution's
approved package manager. See [Git installation](https://git-scm.com/book/en/v2/Getting-Started-Installing-Git).

Install uv on macOS/Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Reopen the terminal, then:

```bash
uv --version
uvx --version
uv python install 3.12
```

Python 3.12 is a supported choice for the POC. uv manages this interpreter; replacing
system Python is unnecessary. See [uv installation](https://docs.astral.sh/uv/getting-started/installation/)
and [Python installation](https://docs.astral.sh/uv/guides/install-python/).

## 2. AWS CLI v2

On macOS, download and install the vendor package:

```bash
curl "https://awscli.amazonaws.com/AWSCLIV2.pkg" -o "/tmp/reframe-AWSCLIV2.pkg"
sudo installer -pkg /tmp/reframe-AWSCLIV2.pkg -target /
aws --version
```

On Linux, use the matching x86_64 or ARM64 installer and verification instructions
in the [official AWS CLI v2 installation guide](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html).
The same guide covers macOS installation without administrator privileges. Confirm
`aws --version` reports `aws-cli/2`.

## 3. Configure an AWS profile and sign in

Guided `reframe init` can perform this step with you. After you select the profile
and region, it checks access. For an expired/missing SSO session it offers to run
`aws sso login` for that exact profile, waits for the AWS browser flow and verifies
identity access again before continuing. If no SSO configuration is detected, it
offers `aws configure sso` first. Existing non-SSO profiles are changed only if you
explicitly choose SSO configuration. In unattended mode, no login browser is opened.

To configure/sign in manually instead:

For an organization using IAM Identity Center, obtain the access portal/start URL,
SSO region, allowed account and permission set from its AWS administrator. These are
organization-specific inputs; the framework cannot invent them.

```bash
aws configure sso --profile modernization-poc
aws sso login --profile modernization-poc
aws configure list-profiles
aws sts get-caller-identity --profile modernization-poc
```

Choose a permitted AWS workload region and enter the profile name `modernization-poc`
when `reframe init` asks. The profile name is illustrative; reuse an existing
approved profile if available. After a session expires, rerun `aws sso login`.

Keep identity output private. Do not paste access keys into the framework, commit
AWS configuration or share cached SSO tokens. If your organization uses another
credential method, follow its process rather than creating long-lived keys just for
this POC. See [AWS profile/SSO setup](https://docs.aws.amazon.com/cli/latest/userguide/cli-configure-sso.html).

The selected identity needs permissions for the chosen operations: read/write its
assessment S3 location, optionally create/configure the selected bucket, and use
Transform workspaces/jobs/connectors. Connector activation may require a separate
administrator and approved role. `get-caller-identity` verifies login, not all these
permissions. Transform also requires its own application connection; see step 7.

## 4. Claude Code CLI and authentication

Install the native CLI on macOS/Linux:

```bash
curl -fsSL https://claude.ai/install.sh | bash
claude --version
claude auth login
claude auth status
```

If `claude` is not found, fix PATH as described below before retrying. Use the approved
subscription or provider login. Our default Claude adapter supports subscription
login; an Anthropic API key is not required for that mode. Make sure the account can
access both pinned models in the [CLI guide](cli-guide.md); installation alone does
not establish model availability. See [Claude installation](https://code.claude.com/docs/en/setup)
and [authentication](https://code.claude.com/docs/en/authentication).

## 5. Optional agent runtimes

### Codex — required for the default HTML provider

Use an approved installation from the [official Codex CLI guide](https://learn.chatgpt.com/docs/codex/cli).
For example, with an existing Node.js/npm installation:

```bash
npm install -g @openai/codex
codex --version
codex login
codex login status
```

Do not install a second copy if your managed installation already exposes `codex`.
Alternatively select Claude for HTML in `reframe init`; that path needs no Codex CLI.

### Kiro — optional executor

```bash
curl -fsSL https://cli.kiro.dev/install | bash
kiro-cli --version
kiro-cli
```

Complete the interactive login with the company-approved method. Selecting Kiro for
this POC also requires the documented headless authentication (`KIRO_API_KEY`) and
compatible CLI/profile behavior. Interactive login alone does not prove that headless
execution is ready. Keep keys in approved environment/secret management; never put
them in repository settings or print them to verify setup.
See [Kiro CLI](https://kiro.dev/docs/cli/) and [headless setup](https://kiro.dev/docs/cli/headless/).
Claude Code is still required for the independent reviewer when Kiro is the executor.

## 6. Fix PATH if installed commands are not found

Native user installers commonly place executables in `~/.local/bin`. Add it to the
current session:

```bash
export PATH="$HOME/.local/bin:$PATH"
```

For persistence, add that line **once** to the startup file your shell uses:

- Bash login shells on macOS: `~/.bash_profile`.
- Interactive Bash on Linux: `~/.bashrc` (your login profile may source it).
- Zsh: `~/.zshrc`.

Reopen Terminal and restart any coding-agent application launched before the PATH
change. For uv-managed tools, `uv tool update-shell` can configure its executable
directory. Diagnose without exposing credentials:

```bash
command -v git uv uvx aws claude
# Check only when selected:
command -v codex kiro-cli
```

## 7. Install the framework and run guided setup

Clone the framework, then install its CLI in an isolated uv tool environment:

```bash
git clone https://github.com/sknampally/reframe.git
cd reframe
uv tool install --force --python 3.12 --editable .
reframe --help
reframe
```

Keep this editable checkout available and clean. The [terminal console](terminal-console.md) opens a menu; choose setup or type `/init`. `init` asks for source location,
scope, private output workspace, AWS profile/region/bucket and runtime choices. It
checks the relevant CLIs and logins, offers AWS resource setup and suggests the next
command. If a prerequisite is missing, follow the reported installation/login hint
and rerun `reframe init`; saved evidence is retained.

For new AWS assessments, the framework starts the official Transform MCP server
through uvx. First use may download the server; approved network/proxy access is
needed. Use an approved pinned server version in private settings for repeatability.
AWS CLI login and the Transform application connection are distinct. Guided setup
checks the connection and reuses it when valid; otherwise it guides a separate
Transform browser SSO login and verifies that connection before resource setup.
The advanced file option is for a **path** to private authentication arguments,
not pasted JSON or a client `mcpServers` configuration. Follow the [official Transform MCP setup](https://github.com/awslabs/mcp/tree/main/src/aws-transform-mcp-server)
for its supported authentication flow. It does not automatically create company SSO
or bypass connector administrator activation.

Do not create a dummy configuration file or paste tokens into the public repo when
connection setup is incomplete. Resolve that connection, rerun `reframe init`, and
continue only when its next step is `reframe assess`.

See the [execution guide](cli-guide.md) for assessment, comparison, approval,
validation, HTML review and packaging. Installing tools is not a live workflow
acceptance result; CardDemo/AWS acceptance remains pending.

If MCP fails to load on a Mac with mixed Intel/Apple Silicon tools, use a uv-managed
Python and reinstall native dependencies:

```bash
uv python install 3.12
uv tool install --force --reinstall --managed-python --python 3.12 --editable .
```

The runtime and native extension architectures must match. Reframe's MCP preflight
checks this before cloud setup and provides a safe repair hint. Detailed SDK errors
and MCP server diagnostics are kept in the private workspace, not printed as raw
cloud identity output.
