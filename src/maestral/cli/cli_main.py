# external imports
import click

from .. import __version__
from .cli_core import auth, gui, pause, resume, sharelink, start, stop
from .cli_info import activity, config_files, filestatus, history, ls, status
from .cli_maintenance import (
    completion,
    config,
    diff,
    log,
    move_dir,
    rebuild_index,
    restore,
    revs,
)
from .cli_settings import autostart, bandwidth_limit, excluded, notify

# local imports
from .core import OrderedGroup


@click.group(cls=OrderedGroup, help="Dropbox client for Linux and macOS.")
@click.version_option(version=__version__, message="%(version)s")
def main() -> None:
    pass


main.add_command(start, section="Core Commands")
main.add_command(stop, section="Core Commands")
main.add_command(gui, section="Core Commands")
main.add_command(pause, section="Core Commands")
main.add_command(resume, section="Core Commands")
main.add_command(auth, section="Core Commands")
main.add_command(sharelink, section="Core Commands")

main.add_command(status, section="Information")
main.add_command(filestatus, section="Information")
main.add_command(activity, section="Information")
main.add_command(history, section="Information")
main.add_command(ls, section="Information")
main.add_command(config_files, section="Information")

main.add_command(autostart, section="Settings")
main.add_command(excluded, section="Settings")
main.add_command(notify, section="Settings")
main.add_command(bandwidth_limit, section="Settings")

main.add_command(move_dir, section="Maintenance")
main.add_command(rebuild_index, section="Maintenance")
main.add_command(revs, section="Maintenance")
main.add_command(diff, section="Maintenance")
main.add_command(restore, section="Maintenance")
main.add_command(log, section="Maintenance")
main.add_command(config, section="Maintenance")
main.add_command(completion, section="Maintenance")
