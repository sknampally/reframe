"""Fail closed when a test reaches an unmocked provider process or network call.

Local Git stays available. The guard installs only while unittest is already loaded,
so the installed command is unchanged.
"""
import socket
import subprocess
from pathlib import Path

BLOCKED_PROGRAMS = {'aws', 'claude', 'codex', 'terraform', 'tofu', 'uvx', 'kiro', 'kiro-cli'}
BLOCKED_HOSTS = ('amazonaws.com', 'anthropic.com', 'openai.com')


def install():
    """Replace process and socket entry points for the rest of this test process."""
    if getattr(install, 'active', False):
        return
    install.active = True
    real_popen = subprocess.Popen
    real_run = subprocess.run

    def argv_of(command):
        if isinstance(command, (list, tuple)):
            return [str(part) for part in command]
        return [str(command)]

    def refuse(argv):
        if not argv:
            return
        name = Path(argv[0]).name
        if name in BLOCKED_PROGRAMS or name.startswith('claude'):
            raise AssertionError('unmocked provider process: ' + name)
        text = ' '.join(argv)
        for host in BLOCKED_HOSTS:
            if host in text:
                raise AssertionError('unmocked provider network request: ' + host)

    class GuardedPopen(real_popen):
        """Refuse a provider command, then start the real process. Git stays allowed."""

        def __init__(self, command, *args, **kwargs):
            refuse(argv_of(command))
            super().__init__(command, *args, **kwargs)

    def run(command, *args, **kwargs):
        refuse(argv_of(command))
        return real_run(command, *args, **kwargs)

    subprocess.Popen = GuardedPopen
    subprocess.run = run

    def connect(self, address):
        raise AssertionError('unmocked network request')

    socket.socket.connect = connect

    def create_connection(*args, **kwargs):
        raise AssertionError('unmocked network request')

    socket.create_connection = create_connection
