# -*- coding: utf-8 -*-

from typing import Set

import os
import re
import glob
import shutil
import tempfile
import schema
import shutil
import urllib.parse
import urllib.request

from . import plugins


class AptPackagesPlugin(plugins.AbstractPlugin):
    key = 'apt-packages'
    schema = [str]

    def perform(self):
        packages = self.config
        self.run_command_sudo('apt-get', '-y', '-q', 'install', *packages)


class CopyPlugin(plugins.AbstractPlugin):
    key = 'copy'
    schema = {str: str}

    def perform(self):
        for src, dst in self.config.items():
            src = self._expand_path(src)
            dst = self._expand_path(dst)
            sources = glob.glob(src)
            for current_source in sources:
                try:
                    if os.path.isfile(current_source):
                        try:
                            shutil.copy2(current_source, dst)
                        except PermissionError:
                            self.run_command_sudo('cp', '-p', current_source, dst)
                    elif os.path.isdir(current_source):
                        try:
                            shutil.copytree(current_source, dst)
                        except PermissionError:
                            self.run_command_sudo('cp', '-rp', current_source, dst)
                except FileExistsError as e:
                    # Ignore already existing files or directories
                    if self._verbose:
                        print(e)


class CreateFoldersPlugin(plugins.AbstractPlugin):
    key = 'folders'
    schema = [str]

    def perform(self):
        folders = self.config
        for folder in folders:
            folder = self._expand_path(folder)
            if not os.path.exists(folder):
                try:
                    os.makedirs(folder, exist_ok=True)
                except PermissionError:
                    self.run_command_sudo('mkdir', '-p', folder)


class FlatpakPackagesPlugin(plugins.AbstractPlugin):
    key = 'flatpak-packages'
    schema = [
        str,
        {
            schema.Or('bundle', 'from'): str,
            schema.Optional('target'): schema.Or('system', 'user'),
            schema.Optional('runtime'): str,
        }
    ]

    @staticmethod
    def _download_flatpakref(url: str) -> str:
        file = tempfile.NamedTemporaryFile('wb', prefix='download_',
                                           suffix='.flatpakref', delete=False)
        with urllib.request.urlopen(url) as response, open(file.name, 'wb') as out_file:
            shutil.copyfileobj(response, out_file)

        return file.name

    @staticmethod
    def _get_flatpakref_application_name(filepath: str) -> str:
        with open(filepath) as file:
            text = file.read()
            for line in text.splitlines():
                if line.startswith('Name='):
                    return line.split('=')[1].strip()
        raise Exception('Error parsing flatpakref file: {}'.format(filepath))

    def _check_is_flatpak_installed(self) -> bool:
        try:
            self.run_command('flatpak', '--version')
            return True
        except FileNotFoundError:
            return False

    def _check_is_application_installed(self, name: str) -> bool:
        output = self.run_command('flatpak', 'list')
        return output.find(name) != -1

    def _install_flatpak(self, bundle_or_ref: str, runtime: str=None, target: str='system'):
        # Flatpak considers it an error to install already
        # installed applications
        # Workaround by using "--reinstall" which uninstalls
        # the application first if it is already installed.
        cmd = ['flatpak', 'install', '--reinstall', '-y']
        if target == 'user':
            cmd += ['--user']
        else:
            cmd += ['--system']
        if runtime is not None:
            cmd += ['--runtime', runtime]
        cmd += [bundle_or_ref]
        if target == 'system':
            self.run_command_sudo(*cmd)
        else:
            self.run_command(*cmd)

    def perform(self):
        # Install flatpak if not already installed
        if not self._check_is_flatpak_installed():
            # Adding this PPA is actually the officially recommended
            # method for installing Flatpak on Ubuntu (as of 2017-12-30).
            # Source: https://flatpak.org/getting
            ppa_plg = PPAsPlugin(config=['alexlarsson/flatpak'],
                                 verbose=self._verbose)
            ppa_plg.perform()
            self.run_command_sudo('apt', 'install', '-y', 'flatpak')
        assert self._check_is_flatpak_installed()
        for flatpak in self.config:
            target = 'system'
            runtime = None
            bundle = None
            ref = None
            if isinstance(flatpak, dict):
                if 'target' in flatpak:
                    target = flatpak['target']
                if 'runtime' in flatpak:
                    runtime = flatpak['runtime']
                if 'bundle' in flatpak:
                    bundle = flatpak['bundle']
                if 'from' in flatpak:
                    ref = flatpak['from']
            else:
                if flatpak[flatpak.rfind('.')+1:] == 'flatpakref':
                    ref = flatpak
                else:
                    bundle = flatpak
            # Install a .flatpakref
            # Will only perform the installation if the application
            # is not already installed
            if ref is not None:
                # Check if the .flatpakref is remote
                remote = bool(urllib.parse.urlparse(ref).scheme)
                filepath = self._download_flatpakref(ref) if remote else ref
                app_name = self._get_flatpakref_application_name(filepath)
                if self._check_is_application_installed(app_name):
                    continue
                self._install_flatpak(filepath, runtime, target)
            # Install a .flatpak
            # NOTE: Doesn't check whether the application is
            # already installed or not. Will perform a reinstall
            # if the application is already installed.
            else:
                self._install_flatpak(bundle, runtime, target)


class PPAsPlugin(plugins.AbstractPlugin):
    key = 'ppas'
    schema = [str]
    # Source: https://askubuntu.com/a/148968
    _ppa_regex = re.compile(r'^deb http://ppa.launchpad.net/[a-z0-9-]+/[a-z0-9-]+')

    def _get_existing_ppas(self) -> Set[str]:
        """
        Get a set of PPAs already active on the system
        :return: Set of active PPAs
        """
        existing_ppas = set()
        sources_lists = glob.iglob('/etc/apt/**/*.list', recursive=True)
        for sources_list in sources_lists:
            with open(sources_list) as file:
                text = file.read()
            for line in text.splitlines():
                match = self._ppa_regex.match(line)
                if match is not None:
                    split_line = match.group(0).split('/')
                    existing_ppas.add(split_line[3] + '/' + split_line[4])
        return existing_ppas

    def perform(self):
        existing_ppas = self._get_existing_ppas()
        any_new_ppas = False
        for ppa in self.config:
            if ppa not in existing_ppas:
                any_new_ppas = True
                self.run_command_sudo('apt-add-repository', '-y', 'ppa:' + ppa)
        if any_new_ppas:
            self.run_command_sudo('apt-get', 'update')


class ScriptletPlugin(plugins.AbstractPlugin):
    key = 'scriptlet'
    schema = str

    def perform(self):
        with tempfile.NamedTemporaryFile('w', prefix='scriptlet_',
                                         suffix='.sh', encoding='utf-8', delete=False) as file:
            file.write(self.config)
        self.run_command('/bin/bash', file.name)


class ScriptsPlugin(plugins.AbstractPlugin):
    key = 'scripts'
    schema = [str]

    def perform(self):
        scripts = self.config
        for filename in scripts:
            filename = self._expand_path(filename)
            if not os.path.isfile(filename):
                raise FileNotFoundError('The file {} doesn\'t exist'.format(filename), self.key)
            self.run_command('/bin/bash', filename)


class SnapPackagesPlugin(plugins.AbstractPlugin):
    key = 'snap-packages'
    schema = [
        str,
        {
            str: {
                schema.Optional('channel'): str,
                schema.Optional('classic'): bool,
                schema.Optional('devmode'): bool,
                schema.Optional('jailmode'): bool,
            }
        },
    ]

    def perform(self):
        packages = self.config
        for package in packages:
            # Using type(package) == dict here is not enough because
            # while a subclass of dict will be passed as config,
            # it is not guaranteed what concrete subclass
            # that will be. Currently, CommentedMap from ruamel.yaml
            # is used, but this may change in the future!
            if isinstance(package, dict):
                for package_name in package.keys():
                    options = package[package_name]
                    cmd = ['snap', 'install']
                    if 'channel' in options:
                        cmd += ['--channel', options['channel']]
                    if 'classic' in options and options['classic']:
                        cmd += ['--classic']
                    if 'devmode' in options and options['devmode']:
                        cmd += ['--devmode']
                    if 'jailmode' in options and options['jailmode']:
                        cmd += ['--jailmode']
                    cmd += [package_name]
                    self.run_command_sudo(*cmd)
            else:
                self.run_command_sudo('snap', 'install', package)


BUILTIN_PLUGINS = (
    AptPackagesPlugin,
    CopyPlugin,
    CreateFoldersPlugin,
    FlatpakPackagesPlugin,
    PPAsPlugin,
    ScriptletPlugin,
    ScriptsPlugin,
    SnapPackagesPlugin
)
