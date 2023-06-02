# SPDX-License-Identifier: Apache-2.0
# Copyright Contributors to the Rez Project


"""Git Bash (for Windows) shell."""
import os
import re
import os.path
import subprocess

from rez.config import config
from rezplugins.shell.bash import Bash
from rez.utils.cygpath import convert_path
from rez.utils.execution import Popen
from rez.utils.platform_ import platform_
from rez.utils.logging_ import print_debug, print_error, print_warning
from rez.util import dedup
from ._utils.windows import get_syspaths_from_registry


class GitBash(Bash):
    """Git Bash shell plugin."""
    pathsep = ':'

    _drive_regex = re.compile(r'([A-Za-z]):\\')

    @classmethod
    def name(cls):
        return 'gitbash'

    @classmethod
    def executable_name(cls):
        return "bash"

    @classmethod
    def find_executable(cls, name, check_syspaths=False):
        # If WSL is installed, it's probably safest to assume System32 bash is
        # on the path and the default bash location for gitbash is on the path
        # and appears after System32. In this scenario, we don't want to get the
        # executable path from the parent class because it is configured
        # differently and it seems like the best option to get the executable is
        # through configuration, unless there's a way to get the gitbash executable
        # using the registry.
        settings = config.plugins.shell[cls.name()]
        if settings.executable_fullpath:
            if not os.path.exists(settings.executable_fullpath):
                raise RuntimeError(
                    "Couldn't find executable '%s'." % settings.executable_fullpath
                )
            else:
                return settings.executable_fullpath

        # Find the gitbash bash executable using the windows registry.
        exepath = Bash.find_executable(name, check_syspaths=check_syspaths)

        if exepath and "system32" in exepath.lower():
            print_warning(
                "Git-bash executable has been detected at %s, but this is "
                "probably not correct (google Windows Subsystem for Linux). "
                "Consider adjusting your searchpath, or use rez config setting "
                "plugins.shell.gitbash.executable_fullpath.",
                exepath
            )
            raise ValueError("Gitbash executable is not correct: %s" % exepath)

        exepath = exepath.replace('\\', '\\\\')

        return exepath

    @classmethod
    def get_syspaths(cls):
        if cls.syspaths is not None:
            return cls.syspaths

        if config.standard_system_paths:
            cls.syspaths = config.standard_system_paths
            return cls.syspaths

        # get default PATH from bash
        exepath = cls.executable_filepath()
        environ = os.environ.copy()
        environ.pop("PATH", None)
        p = Popen(
            [exepath, cls.norc_arg, cls.command_arg, 'echo __PATHS_ $PATH'],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=environ,
            text=True
        )

        out_, _ = p.communicate()
        if p.returncode == 0:
            lines = out_.split('\n')
            line = [x for x in lines if '__PATHS_' in x.split()][0]
            # note that we're on windows, but pathsep in bash is ':'
            paths = line.strip().split()[-1].split(':')
        else:
            paths = []

        # combine with paths from registry
        paths = get_syspaths_from_registry() + paths

        paths = dedup(paths)
        paths = [x for x in paths if x]

        cls.syspaths = paths
        return cls.syspaths

    def as_path(self, path):
        """Return the given path as a system path.
        Used if the path needs to be reformatted to suit a specific case.
        Args:
            path (str): File path.

        Returns:
            (str): Transformed file path.
        """
        # Prevent path conversion if normalization is disabled in the config.
        if config.disable_normalization:
            return path

        return path

    def as_shell_path(self, path):
        """Return the given path as a shell path.
        Used if the shell requires a different pathing structure.

        Args:
            path (str): File path.

        Returns:
            (str): Transformed file path.
        """
        converted_path = self.normalize_path(path, mode="mixed")
        return converted_path

    def normalize_path(self, path, mode="unix"):
        """Normalize the path to fit the environment.
        For example, POSIX paths, Windows path, etc. If no transformation is
        necessary, just return the path.

        Args:
            path (str): File path.

        Returns:
            (str): Normalized file path.
        """
        # Prevent path conversion if normalization is disabled in the config.
        if config.disable_normalization:
            return path

        # ! CODE HERE IS EXPERIMENTAL AND FOR EXPLANATORY PURPOSES !

        # It's possible configuration settings need to be rethought.
        # If we go with this type of solution the code should be refactored
        # to be more readable and maintainable.

        # Working through the cases here to determine what works best. The
        # amount of commenting suggests that this is not a good solution. I'm
        # tempted to remove `shell_pathed_env_vars` and the `as_shell_path`
        # method and just normalizing paths in `normalize_path()` and
        # `normalize_paths()`. `shell_pathed_env_vars` seems to be a
        # configuration that can be done away with but I do see the appeal for
        # users to be able to add their own environment variable paths that use
        # custom separators.

        # Determine if `shell_pathed_env_vars` is configured and if the current
        # `path` contains a shell-pathed env var. If so, for the special case for
        # `PYTHONPATH` in gitbash, we need to ensure `;` pathseps are being used
        # and check the pathsep configured by `env_var_separators` and ensure
        # it's value is correct.

        # Rez should only care about the case of `PYTHONPATH` in gitbash, and
        # allow users to configure the pathsep for other env vars. Also, rez
        # should not configure `env_var_separators` with `"PYTHONPATH": ";"`
        # because `env_var_separators` is a general configuration and setting
        # would obviously not work in all scenarios.

        # `shell_pathed_env_vars` configuration is required for this special
        # case to work correctly therefore rez should be shipped with it set
        # automatically
        shell = GitBash.name()
        shell_pathed_vars = config.shell_pathed_env_vars.get(shell)

        # If `shell_pathed_vars` is configured and contains `PYTHONPATH`, check
        # if `env_var_separators` is also configured and that it is set accurately
        env_var_sep = None
        if shell_pathed_vars:
            valid_sep = ";"
            for idx, var in enumerate(shell_pathed_vars):
                env_var_sep = config.env_var_separators.get(var)
                if var == "PYTHONPATH":
                    # If `env_var_sep` is valid, everything works great, but if
                    # it is not valid rez should override it in this special
                    # case so everything functions as expected.
                    #
                    # Logging this to the user doesn't make sense because rez
                    # shouldn't expect them to know `env_var_separators` should
                    # also be set. This indicates that gitbash should just
                    # handle `PYTHONPATH` annonymously.
                    if env_var_sep != valid_sep:
                        print_debug(
                            "env_var_separators['PYTHONPATH'] must be set to "
                            "'%s' for %s if shell_pathed_vars['%s'] "
                            "contains 'PYTHONPATH'. The current value will be "
                            "overridden for this session with '%s'.",
                            valid_sep,
                            shell,
                            shell,
                            valid_sep
                        )
                        env_var_sep = valid_sep
                        break
                # If `shell_pathed_vars` does not contain `PYTHONPATH` ensure
                # `;` pathseps are being used and communicate to the user that
                # `shell_pathed_vars` should be configured and contains `PYTHONPATH`
                # and that `env_var_separators` should also be configured and set to `;`
                if idx == len(shell_pathed_vars) - 1:
                    print_debug(
                        "shell_pathed_vars['%s'] should contain 'PYTHONPATH' for %s "
                        "but it does not. Ideally env_var_separators['PYTHONPATH'] "
                        "should be set to '%s'. The current value will be overridden "
                        "for this session with '%s'.",
                        shell,
                        shell,
                        valid_sep,
                        valid_sep
                    )
                    env_var_sep = valid_sep
        else:
            print_error(
                "%s is improperly configured! `shell_pathed_env_vars` must be "
                "configured and contain 'PYTHONPATH' for environments to resolve correctly.",
                shell
            )

        # Ensure the correct env var separator is being used
        # Logging here only while developing, this is a temporary solution.
        if env_var_sep:
            print_debug("path before (normalize_path): %s", path)
            env_var_regex = r"(\$\{[^\}]+\})([:;])"
            env_sep_subst = "\\1%s" % env_var_sep
            path = re.sub(env_var_regex, env_sep_subst, path, 0)
            print_debug("path after (normalize_path): %s", path)

        normalized_path = convert_path(path, mode=mode, force_fwdslash=True)
        if path != normalized_path:
            print_debug(
                "path normalized: {!r} -> {}".format(path, normalized_path)
            )
            self._addline(
                "# path normalized: {!r} -> {}".format(path, normalized_path)
            )

        return normalized_path

    def normalize_paths(self, path):
        """
        This is a bit tricky in the case of gitbash. The problem we hit is that
        our pathsep is ':', _but_ pre-normalised paths also contain ':' (eg
        C:\foo). In other words we have to deal with values like  'C:\foo:C:\bah'.

        To get around this, we do the drive-colon replace here instead of in
        normalize_path(), so we can then split the paths correctly. Note that
        normalize_path() still does drive-colon replace also - it needs to
        behave correctly if passed a string like C:\foo.
        """
        if config.disable_normalization:
            return path

        def lowrepl(match):
            if match:
                return "/{}/".format(match.group(1).lower())

        # C:\ ==> /c/
        normalized_path = self._drive_regex.sub(lowrepl, path).replace("\\", "/")

        if path != normalized_path:
            print_debug(
                "path normalized: {!r} -> {}".format(path, normalized_path)
            )
            self._addline(
                "# path normalized: {!r} -> {}".format(path, normalized_path)
            )

        return normalized_path

    def shebang(self):
        self._addline('#! /usr/bin/env bash')


def register_plugin():
    if platform_.name == 'windows':
        return GitBash
