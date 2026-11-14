# Reframe terminal console

For first installation, updates and troubleshooting on Mac or Windows/WSL2, follow
[the platform installation guide](platform-install.md). Every install command includes the required trailing dot.

**Reframe** is the product name and the `reframe` command. The current POC prepares mainframe modernization specifications and a
reviewed implementation package. It does not implement or deploy an application.

## Launch

From your clean framework checkout, install or refresh the editable tool:

```bash
uv tool install --force --managed-python --python 3.12 --editable .
reframe
```

`reframe ui` opens the same console. `reframe init` in an interactive terminal
opens the console and starts guided setup. Explicit commands and redirected output
retain the existing JSON/script interface. The console requires a TTY and macOS/Linux;
it adapts to terminal width and does not require a web server.

![Reframe console with synthetic preview data](images/terminal-preview.svg)

## Navigation

Choose a numbered menu item, or enter a slash command. Tab completes command names;
Up/Down browse the session's in-memory command history. Ctrl+C cancels an action and
returns to the console; Ctrl+D or `/exit` leaves it. Commands do not execute arbitrary
shell text. Setup input uses bracketed-paste handling: a multi-line paste is received as one
value and rejected instead of feeding later questions. Do not paste MCP client JSON
into setup fields; the normal Transform connection path is guided browser SSO.
Existing controller budgets, source checks and approval gates still apply.

| Command | Action |
| --- | --- |
| `/flow` or menu **1** | Guided setup, assessment, approval, then skip or review |
| `/menu` or `/status` | Refresh the project dashboard and saved assessment states |
| `/init` | Start or continue guided setup |
| `/preview` | After assessment approval, write the wireframe site and AI-DLC ZIP |
| `/upgrade-framework` | Record the installed revision on this saved run. Reinstall from a clean committed checkout first. A previous prompt migration does not authorize a new revision. Add `--adopt-prompts` when this revision changes the preparation prompts. |
| `/workspace` | Select an existing private project |
| `/assess` | Confirm a source upload and start a new assessment |
| `/resume` | Continue saved AWS, validation or HTML work |
| `/compare` | Prompt for two assessment labels |
| `/approve` | Choose assessment, validation or specification approval |
| `/validate` | Run executor and independent reviewer |
| `/decide` | Record an SME answer or explicit deferral |
| `/review-site` | Generate the stakeholder HTML document |
| `/feedback` | Request changes and invalidate downstream approvals |
| `/artifacts` | List preparation/review artifact paths with privacy off |
| `/package` | Create the approved implementation package |
| `/help` | Show commands and responsibilities |
| `/guide` | List setup, workflow and comparison guides |
| `/privacy` | Toggle dashboard/result detail visibility |
| `/demo` or `/live` | Switch simulated preview / active live workspace |

Existing flags work, for example `/assess --label run-a --detach`,
`/review-site --provider claude`, or `/compare --left run-a --right run-b --semantic`.
Long foreground actions occupy the console until completion or cancellation. There
is no model token streaming, estimated completion percentage or always-on cloud
monitoring. AWS job polling and detached local watchers retain their documented
behavior; `/status` refreshes saved state rather than independently polling AWS.

## Client presentation preview

```bash
reframe demo
```

Preview is explicitly labeled **simulated data · no service calls**. `/next` illustrates
successive phase gates, including approvals and packaging. These transitions are
in-memory examples, not genuine jobs, agent calls, decisions, runtime tests or saved
workflow results. Other workflow actions are disabled while preview is active. Preview
does not read the active private project. Use `/live` deliberately to return to real
work. Show the preview as a product walkthrough, not evidence of a completed CardDemo
assessment. Full CardDemo/AWS acceptance remains pending.

Presentation privacy starts enabled and masks private project details in dashboards,
result panels and artifact lists. It is a display convenience, not a security boundary:
setup questions, typed commands, authentication tools and existing workflow output
may still reveal local/private information. Do not use a real setup session with
confidential inputs as the client demonstration. Use preview for that purpose.

## Setup and live execution

Follow the [engineer installation guide](engineer-setup.md) before a new live setup.
The [CLI guide](cli-guide.md) documents the source, cloud, evidence and approval
requirements. Reframe uses the same gated controller; the menu does not bypass
missing permissions, business decisions or source verification.


## Guided start / continue

Choose menu **1** or `/flow` (also `reframe flow` from your shell). The guide
continues saved setup and the same assessment. The source prompt is empty. The
connector question asks for your name. After the findings are ready, approve them
with your name, a short rationale, and a confirmation that they match the pinned
source. The next prompt is `skip` or `review`. Enter selects **skip**. Skip asks
for an owner, a reason, `claude` or `codex`, and an export name, then writes the
HTML wireframes and the AI-DLC ZIP. **review** runs the executor and reviewer.
Reuse the export name when resuming. No reviewer PASS or stakeholder approval is
fabricated. `/privacy` and `/artifacts` reveal the local report and package paths.
If the Reframe checkout changed, `/flow` records that update before approval.
Fresh runs use a separate output parent; previous evidence and cloud jobs are not
deleted. See the README for the question order.
