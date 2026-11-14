"""Interactive terminal console over the existing, gated workflow controller."""
import json
import re
import shlex
import sys
from pathlib import Path
from rich import box
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt, Confirm
from rich.table import Table
from rich.text import Text
from prompt_toolkit import PromptSession
from prompt_toolkit.completion import WordCompleter
from prompt_toolkit.history import InMemoryHistory
from pilot import Blocked

COMMANDS = ['flow', 'help', 'menu', 'guide', 'init', 'workspace', 'status', 'assess', 'resume',
            'compare', 'approve', 'preview', 'relocate', 'prepare-plan', 'model-budget', 'validate', 'decide', 'review-site', 'feedback',
            'upgrade-framework', 'artifacts', 'package', 'context', 'design-export', 'package-export', 'register-project',
            'privacy', 'demo', 'live', 'next', 'exit']
ACTIONS = {'1':'flow', '2':'assess', '3':'status', '4':'resume', '5':'compare',
           '6':'validate', '7':'review-site', '8':'package', '9':'guide', '0':'exit'}
DEMO_STAGES = ['READY_TO_ASSESS', 'AWAITING_ASSESSMENT_APPROVAL', 'ASSESSMENT_APPROVED',
               'AWAITING_VALIDATION_APPROVAL', 'VALIDATION_APPROVED',
               'AWAITING_SPECIFICATIONS_APPROVAL', 'SPECIFICATIONS_APPROVED', 'PACKAGED']


def recovery_notice(reason, result=None):
    """Classify a design failure without repeating paths or identifiers."""
    result = result or {}
    if result.get('resumed'):
        return {
            'recovery': 'incomplete-generation',
            'summary': 'Incomplete generation was resumed from the saved selection. No new model call was required.',
            'next_step': 'Review the completed version before packaging.',
        }
    text = str(reason or '')
    if 'integrity failure' in text:
        return {
            'recovery': 'integrity-failure',
            'summary': 'Completed version integrity failure. The saved files do not match the completed version. Nothing was overwritten.',
            'next_step': 'Restore the completed files or choose a new version. Do not reuse the damaged version.',
        }
    if 'immutable' in text:
        return {
            'recovery': 'immutable-version',
            'summary': 'Completed version is immutable. Choose the next revision. No approval carries forward.',
            'next_step': 'Export the next version. The completed version was not changed.',
        }
    return None


def clean(value):
    """Render untrusted paths/model text as literal text, without terminal controls."""
    return re.sub(r'[\x00-\x1f\x7f-\x9f]', ' ', str(value))


class Terminal:
    def __init__(self, console=None, demo=False, session=None):
        """Start a console. Privacy is on, and a demo console does not call AWS."""
        self.console = console or Console()
        self.demo = demo
        self.demo_index = 0
        self.private = True
        self.session = session
        self.last_report = None

    def say(self, value, style=None):
        """Print one line. Control characters in the text are removed first."""
        self.console.print(Text(clean(value), style=style))

    def ask(self, label, default=None):
        """Ask for one value. Pasted JSON or several lines stop setup."""
        # prompt_toolkit brackets a multiline paste as one event rather than feeding
        # its lines into subsequent setup answers.
        value = PromptSession(history=InMemoryHistory()).prompt(clean(label)+': ',default=default or '').strip()
        from pilot import require
        require('\n' not in value and '\r' not in value and not value.startswith(('{','[')),
                'Enter a single value, not pasted JSON or multiple lines. Setup has stopped safely.')
        return value

    def optional(self, label, default=''):
        """Ask for one value and allow the default."""
        return self.ask(label, default)

    def yes(self, label, default=False):
        """Ask a yes or no question."""
        value = self.ask(label+' [y/n]', 'y' if default else 'n').lower()
        from pilot import require
        require(value in {'y','yes','n','no'}, 'Answer y or n; pasted content is not an answer')
        return value in {'y','yes'}

    def project(self):
        """Load the active project. A demo console returns an illustrative project."""
        if self.demo:
            return {'stage': DEMO_STAGES[self.demo_index], 'scope':'Illustrative sign-on journey',
                    'providers':{'executor':'claude', 'reviewer':'claude','wireframes':'codex'},
                    'settings':{'executor_model':'claude-sonnet-5-5','reviewer_model':'claude-opus-5-5'},
                    'assessments':{}, 'approvals':{}, 'source':{'commit':'illustrative'}}
        from reframe_setup import workspace
        try:
            return json.loads((workspace()/'project.json').read_text())
        except (Blocked, OSError, ValueError, KeyError, RuntimeError):
            return None

    def header(self):
        """Print the title and whether this console is live or a preview."""
        content = Text('REFRAME\n', style='bold bright_cyan')
        content.append('Reverse-engineer mainframe source into an AI-DLC handoff for any language\n',style='bold white')
        content.append('Assess  →  Validate  →  Business review  →  Package',style='dim')
        self.console.print(Panel(content, border_style='cyan', padding=(1,2),
            subtitle='PREVIEW · simulated data · no service calls' if self.demo else 'LIVE WORKSPACE · explicit human gates'))

    def dashboard(self):
        """Print the current stage and the menu."""
        self.header()
        project = self.project()
        if not project:
            self.console.print(Panel('Choose 1. If setup is not finished, Reframe creates the sandbox, checks the production deny, and connects Transform.\nNo assessment starts until you confirm an upload. Installation help: /guide setup', title='Welcome',border_style='blue'))
        else:
            stage = project.get('stage','UNKNOWN')
            self.say('Current state  '+stage.replace('_',' '),'bold yellow')
            if not self.private or self.demo:
                self.say('Scope  '+project.get('scope','Not recorded'))
            else:
                self.say('Presentation privacy ON · project details hidden · /privacy to reveal','dim')
            table = Table(box=box.SIMPLE, expand=True)
            table.add_column('Phase',style='bold');table.add_column('Responsibility');table.add_column('Gate')
            table.add_row('01  Assess','AWS Transform or imported findings','Assessment owner')
            table.add_row('02  Validate','Executor + independent reviewer','SME + architect')
            table.add_row('03  Review','Codex / Claude → HTML','Business owner')
            table.add_row('04  Package','Specifications · prompts · backlog','Approved version')
            self.console.print(table)
            config=project.get('settings',{})
            executor=project.get('providers',{}).get('executor','claude')
            self.say('Agents  '+executor+' / '+str(config.get('executor_model') or 'configured default')+'  →  Claude / '+str(config.get('reviewer_model') or 'configured default'),'dim')
            if not self.demo:
                from reframe_setup import next_step
                self.say('Suggested next step  '+next_step(project),'bright_cyan')
                self.assessment_status(project)
        self.menu()

    def show_status(self):
        """Show the saved plan without redrawing the welcome screen or taking the workspace lock."""
        project = self.project()
        if not project:
            self.console.print(Panel('No saved project is selected. Choose 1 to set one up, or /workspace to select one.',
                                     title='Project status', border_style='blue'))
            return
        from reframe_setup import workspace
        import reframe
        try:
            root = workspace()
            progress = reframe.saved_progress(root)
            owned = reframe.watcher_owns(root)
        except (Blocked, OSError, ValueError, KeyError, RuntimeError) as error:
            self.report({'status': 'BLOCKED', 'reason': str(error), 'next_step': 'Choose 1 or /workspace'})
            return
        from reframe import budget_summary
        summary = budget_summary(project, root)
        lines = ['State  ' + str(project.get('stage', 'UNKNOWN')).replace('_', ' ')]
        call_cap = 'no call cap' if summary['preparation_cap'] is None else 'cap ' + str(summary['preparation_cap'])
        lines.append('Preparation calls  ' + str(summary['preparation_calls']) + ' with ' + call_cap)
        lines.append('Completed units  ' + str(summary.get('completed_units', 0)) + '  Remaining units  ' + str(summary.get('remaining_units', 0)))
        lines.append('Wireframe calls  ' + str(summary['wireframe_calls']) + ' with ' + ('no call cap' if summary['wireframe_cap'] is None else 'cap ' + str(summary['wireframe_cap'])))
        lines.append('Skip-draft calls  ' + str(summary['skip_draft_calls'])
                     + '  Failed attempts  ' + str(summary.get('skip_failures', 0)) + ' of limit ' + str(summary.get('skip_failure_limit', 3)))
        lines.append('Measured model dollars  ' + str(summary['dollars_measured']) + ' of cap ' + str(summary['dollar_cap']))
        if summary.get('next_call_estimate') is not None:
            lines.append('Estimated next billed call  ' + str(summary['next_call_estimate']))
        label = progress.get('active_assessment') or 'none'
        lines.append('Assessment  ' + str(label) + '  ' + str(progress.get('saved_status') or 'not started'))
        lines.append('Watcher  already staying with this job' if owned else 'Watcher  not running')
        steps = progress.get('steps') or []
        if steps:
            for step in steps:
                lines.append('  ' + str(step.get('name')) + '  ' + str(step.get('status')))
        else:
            lines.append('No plan steps recorded yet.')
        if not self.private and progress.get('reason'):
            lines.append('Detail  ' + str(progress.get('reason')))
        elif self.private:
            lines.append('Presentation privacy ON · /privacy reveals the saved detail')
        self.console.print(Panel('\n'.join(clean(line) for line in lines), title='Project status', border_style='blue'))

    def assessment_status(self, project):
        """Return the saved status of each assessment."""
        from reframe import read
        rows=[]
        for label, record in project.get('assessments',{}).items():
            try: state=read(Path(record['directory'])/'run.json')['status']
            except (OSError,ValueError,KeyError):state='Record unavailable'
            rows.append((clean(label),clean(state)))
        if rows:
            table=Table('Assessment','Saved service state',box=box.SIMPLE)
            for row in rows:table.add_row(*row)
            self.console.print(table)

    def menu(self):
        """Print the numbered actions."""
        table=Table.grid(expand=True,padding=(0,2))
        table.add_column();table.add_column()
        for left,right in [('1  Guided start / continue','6  Validate findings'),('2  Start assessment','7  Generate HTML review'),
                           ('3  Project status','8  Package for engineering'),('4  Resume saved work','9  Guides and help'),
                           ('5  Compare assessments','0  Exit')]:table.add_row(left,right)
        self.console.print(Panel(table,title='Workspace menu',border_style='blue'))
        self.say('/help  commands   /approve  human gates   /demo  safe preview   Tab  complete   Ctrl+C  cancel','dim')

    def report(self, result):
        """Print a command result. Paths stay hidden while privacy is on."""
        state=clean(result.get('status','RESULT'))
        failed=state in {'BLOCKED','REWORK_LIMIT','ASSESSMENT_FAILED','NEEDS_RECONCILIATION','SETUP_INCOMPLETE','CANCELLED'}
        table=Table.grid(padding=(0,2));table.add_column(style='bold');table.add_column(overflow='fold')
        notice = recovery_notice(result.get('reason'), result)
        if self.private and notice:
            table.add_row('Recovery', clean(notice['summary']))
            table.add_row('Next Step', clean(notice['next_step']))
        for key,value in result.items():
            if key=='status':continue
            # Hide values that can contain paths, resource identifiers or arbitrary error text.
            if self.private and key not in {'next_step','model_calls','calls','round','changed_files','semantic_review','hint','units','unit','attempts','minimum_calls','calls_consumed','calls_remaining','reviewed_units','opaque_segments','remaining_calls','minimum_additional_calls','total_cap'}:
                value='Details hidden by presentation privacy; /privacy reveals local results.'
            table.add_row(clean(key.replace('_',' ').title()),clean(value))
        self.last_report = dict(result)
        self.console.print(Panel(table,title=Text(state.replace('_',' ')),border_style='red' if failed else 'green'))

    def help(self):
        """Print the command list."""
        table=Table('Command','Purpose',box=box.SIMPLE,expand=True)
        for command,purpose in [('flow','Guided assessment, verification choice and draft/package generation'),('init','Guided setup; continue incomplete setup'),('workspace','Select an existing private project'),
             ('status / resume','Inspect saved state / continue interrupted work'),('assess','Authorize source upload and start another assessment'),
             ('preview --skip-validation --by NAME --note REASON','Generate an AWS-assumed draft UI and AI-DLC package'),('relocate --output-parent PATH','Move outputs into a verified repository-named assessment folder'),('prepare-plan','Preview evidence units and minimum model calls; no AI calls'),('model-budget','Record an explicitly approved total call cap'),('compare','Compare two preserved assessment snapshots'),('approve','Record assessment, validation or business approval'),
             ('validate','Run executor and independent reviewer'),('decide / feedback','Record SME answers or request a revision'),
             ('review-site','Generate stakeholder HTML using the chosen provider'),('artifacts','List saved preparation and review files'),
             ('package','Create the approved implementation package'),('context','Import, decide, or approve one future-state delta'),
             ('design-export','Write the canonical versioned design'),('package-export','Write the draft mode ZIP'),
             ('register-project','Record a project locally without an AWS job'),
             ('upgrade-framework --note TEXT','Record a Reframe update on this saved run'),('guide [setup|workflow|comparison]','Read built-in documentation'),
             ('privacy','Toggle project/result detail visibility'),('demo / live','Switch simulated preview / saved live project'),
             ('menu / exit','Refresh dashboard / leave the console')]:table.add_row('/'+command,purpose)
        self.console.print(table)
        self.say('Commands accept existing CLI flags, for example /assess --label run-a or /review-site --provider claude.')
        self.say('No arbitrary shell commands are executed. Preview never creates genuine approvals or workflow records.')

    def guide(self, topic='workflow'):
        """Print one setup, workflow, or comparison guide."""
        from reframe import FRAMEWORK
        files={'setup':'engineer-setup.md','workflow':'cli-guide.md','comparison':'assessment-comparison.md'}
        if topic not in files:
            self.say('Choose /guide setup, /guide workflow or /guide comparison.','yellow');return
        self.console.print(Markdown((FRAMEWORK/'docs'/files[topic]).read_text()))

    def invoke(self, args):
        """Run one reframe command and use this console for its questions."""
        import reframe as app
        import reframe_setup as setup
        # UI input adapters are local to this synchronous console action.
        original_ask, original_yes, original_optional = setup.ask, setup.yes, setup.optional
        try:
            setup.ask, setup.yes, setup.optional = self.ask, self.yes, self.optional
            return app.main(args,reporter=self.report)
        finally:
            setup.ask, setup.yes, setup.optional = original_ask, original_yes, original_optional

    def artifacts(self):
        """List saved reports and packages. Paths stay hidden while privacy is on."""
        from reframe_setup import workspace
        if self.demo:
            self.say('Preview outputs: specification.json · decisions.json · review.html · implementation-backlog.json','cyan');return
        if self.private:
            self.say('Artifact paths are hidden. Use /privacy if you want to reveal them locally.','yellow');return
        root=workspace()
        allowed=['reports/index.html','runs/*/assessment-review.html','previews/*/handoff/index.html','previews/*/index.html','packages/**/*.zip','preparation/candidate-*.json','preparation/review-*.json','review-site-*/index.html','package-*.zip','comparison-*.json']
        paths=sorted({str(p.relative_to(root)) for pattern in allowed for p in root.glob(pattern) if p.is_file() and not p.is_symlink()})
        for path in paths:self.say(path)
        if not paths:self.say('No preparation or review outputs yet.')

    def dispatch(self, line):
        """Run one typed command or menu number."""
        tokens=shlex.split(line.strip().lstrip('/'))
        if not tokens:return True
        cmd=ACTIONS.get(tokens[0],tokens[0]);args=tokens[1:]
        if cmd in {'exit','quit'}:return False
        if cmd in {'help','?'}:self.help();return True
        if cmd=='guide':
            if args:self.guide(args[0])
            else:
                self.console.print(Panel('/guide setup       Installation, authentication and PATH\n/guide workflow    Setup through implementation package\n/guide comparison  Two-run assessment experiment',title='Built-in guides',border_style='blue'))
            return True
        if cmd=='privacy':
            self.private=not self.private
            if self.last_report:
                self.report(self.last_report)
            self.show_status()
            return True
        if cmd=='menu':
            self.dashboard();return True
        if cmd=='status':
            self.show_status();return True
        if cmd in {'demo','live'}:
            self.demo=cmd=='demo';self.dashboard();return True
        if cmd=='artifacts':self.artifacts();return True
        if self.demo:
            if cmd=='next':
                self.demo_index=min(self.demo_index+1,len(DEMO_STAGES)-1)
                self.say('SIMULATED transition only. No job, agent invocation or human approval was recorded.','yellow')
                self.dashboard()
            else:self.say('Preview is read-only. Use /next to illustrate the next gate, or /live for real actions.','yellow')
            return True
        if cmd in {'flow', 'next'}:
            from reframe_flow import run
            run(self);return True
        if cmd=='workspace':
            from reframe_setup import select_workspace
            from pilot import require
            path=Path(self.ask('Existing private workspace path')).expanduser().resolve()
            require((path/'project.json').is_file(),'No saved project at that path')
            select_workspace(path);self.dashboard();return True
        if cmd not in {'init','assess','resume','compare','approve','preview', 'relocate','prepare-plan','model-budget','validate','review-site','package','decide','feedback','upgrade-framework','context','design-export','package-export','register-project'}:
            self.say('Unknown command. Type /help or choose a menu number.','yellow');return True
        if cmd=='compare' and not args:
            args=['--left',self.ask('First assessment label'),'--right',self.ask('Second assessment label')]
        if cmd=='approve' and not args:
            args=[Prompt.ask('Approval phase',choices=['assessment','validation','specifications'],console=self.console)]
        if cmd=='decide' and not args:
            args=['--question',self.ask('SME question ID'),'--by',self.ask('Decision owner'),'--answer',self.ask('Answer and rationale'),
                  '--disposition',Prompt.ask('Disposition',choices=['resolved','deferred'],console=self.console)]
        if cmd=='feedback' and not args:args=['--by',self.ask('Feedback owner'),'--note',self.ask('Requested change')]
        if cmd=='upgrade-framework' and '--note' not in args:
            args=['--note', self.ask('Note for this Reframe update'), *args]
        if cmd in {'assess','resume'} and '--wait' not in args and '--detach' not in args:
            args=[*args,'--wait']
        if cmd in {'assess','resume','validate','review-site','package'}:
            self.say('Running '+cmd+'. Existing evidence and approval checks remain enforced.','cyan')
        self.invoke([cmd,*args])
        return True

    def run(self, initial=None):
        """Show the dashboard and read commands until the person exits."""
        self.dashboard()
        if self.demo:self.say('Use /next to walk through simulated stages. No live readiness or modernization result is implied.','yellow')
        if initial:
            try:self.dispatch(initial)
            except (KeyboardInterrupt, EOFError):self.say('Setup cancelled. Use /flow to continue when ready.','yellow')
            except (Blocked,OSError,ValueError,RuntimeError,KeyError) as error:
                self.report({'status':'BLOCKED','reason':str(error),'next_step':'Use /flow to continue'})
        if self.session is None:
            self.session=PromptSession(history=InMemoryHistory(),completer=WordCompleter(['/'+x for x in COMMANDS]),complete_while_typing=False)
        while True:
            try:
                mode='preview' if self.demo else 'workspace'
                line=self.session.prompt([('ansicyan bold','reframe'),('',' · '+mode+' › ')])
                if not self.dispatch(line):break
            except KeyboardInterrupt:
                self.say('Action cancelled. Saved work remains available; /flow can continue interrupted work.','yellow')
            except EOFError:break
            except SystemExit:
                self.say('Check command options with /help. The console remains open.','yellow')
            except (Blocked,OSError,ValueError,RuntimeError,KeyError) as error:
                self.report({'status':'BLOCKED','reason':str(error),'next_step':'Use /help or /guide workflow'})
        self.say('Session closed. Saved live work can be resumed later.','dim')
        return 0


def launch(demo=False, initial=None):
    """Open the console. A non-interactive terminal is told to use the scriptable commands."""
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        print('The terminal console needs an interactive terminal. Use reframe --help for scriptable commands.',file=sys.stderr)
        return 2
    return Terminal(demo=demo).run(initial)
