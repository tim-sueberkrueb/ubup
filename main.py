#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sys
import os
import click
import random
import subprocess
import time
import threading
import simpleflock

from src import config
from src import log
from src import options


LOCK_FILE_DIR = os.path.expanduser('~/.cache/ubup')
LOCK_FILE_PATH = '{}/lock'.format(LOCK_FILE_DIR)


@click.group(invoke_without_command=True)
@click.pass_context
@options.setup_options
def cli(ctx: click.Context, **_):
    if ctx.invoked_subcommand is None:
        ctx.forward(setup)


@cli.command()
@options.setup_options
def setup(config_path: str, no_roots: bool=False, verbose: bool=False, rerun: bool=False):
    _require_root()

    os.makedirs(LOCK_FILE_DIR, exist_ok=True)

    try:
        with simpleflock.SimpleFlock(LOCK_FILE_PATH, timeout=0.1):
            refresh_sudo_thread = threading.Thread(target=_refresh_sudo, daemon=True)
            refresh_sudo_thread.start()

            if os.path.isdir(config_path):
                setup_filename = os.path.join(config_path, 'setup.yaml')
            else:
                setup_filename = config_path

            if not os.path.isfile(setup_filename):
                raise click.ClickException('The file {} does not exist.'.format(setup_filename))
            if not setup_filename[setup_filename.rfind('.')+1:] in ('yaml', 'yml'):
                raise click.ClickException('The file {} has an unsupported extension. '
                                           'Supported extensions are *.yaml and *.yml.'
                                           .format(setup_filename))

            config_dir = os.path.dirname(setup_filename)

            if no_roots and random.SystemRandom().randrange(0, 100) == 42:
                print('🎶 https://youtu.be/PUdyuKaGQd4 🎶')   # *Totally no easter 🥚*

            log.success('🚀 Performing your setup.', bold=True)

            setup = config.Setup(config_dir, rerun)
            setup.load_plugins()
            setup.load_config_file(setup_filename)

            setup.perform(indent=not (no_roots or verbose), verbose=verbose)

            if setup.skipped_steps_count > 0:
                if setup.skipped_steps_count == 0:
                    log.warning('1 step was skipped because it was already run.')
                else:
                    log.warning('{} steps were skipped because they were already run.'.format(setup.skipped_steps_count))

            log.regular('Run with --rerun to run all steps even if they were already run.')

            log.success('✓ Setup completed.', bold=True)
    except BlockingIOError:
        log.error('Another ubup process seems to be running.')
        log.error('Please wait for the other process to complete.')
        sys.exit(1)


def _require_root():
    root_mode = 'UBUP_STAGE_1' not in os.environ
    user_mode = 'UBUP_STAGE_1' in os.environ \
                and 'UBUP_STAGE_2' not in os.environ
    if root_mode or user_mode:
        launch_env = os.environ
        if root_mode:
            launch_env['UBUP_STAGE_1'] = '1'
        if user_mode:
            launch_env['UBUP_STAGE_2'] = '1'

        try:
            u = os.environ['SUDO_USER']
        except KeyError:
            u = os.environ['USER']

        args = ['sudo', '-E']
        if user_mode:
            args += ['-u', u]
        args += [sys.executable]

        if getattr(sys, 'frozen', False):
            # Running in a bundle created by PyInstaller
            # sys.executable already points to the bundle
            args += [launch_env]
        else:
            # Running in live mode. sys.executable points
            # to the Python interpreter
            args += sys.argv + [launch_env]

        # Replace the current process
        os.execlpe('sudo', *args)


def _refresh_sudo():
    # Update the sudo timestamp each minute
    # This makes sure the user doesn't need to reenter
    # his root password after a long-running process
    while True:
        subprocess.call(['sudo', '-v'])
        time.sleep(60)


if __name__ == '__main__':
    cli()
