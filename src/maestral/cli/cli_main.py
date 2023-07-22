# external imports
import click

# local imports
from .core import OrderedGroup
from .cli_core import start, stop, gui, pause, resume, auth, sharelink
from .cli_info import status, filestatus, activity, history, ls, config_files
from .cli_settings import autostart, excluded, notify, bandwidth_limit
from .cli_maintenance import (
    move_dir,
    rebuild_index,
    revs,
    diff,
    restore,
    log,
    config,
    completion,
)

from .. import __version__


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
